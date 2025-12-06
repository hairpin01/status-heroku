import os
import time
import subprocess
import psutil
import json
import tempfile
import re
import asyncio
import requests
import socket
import aiohttp
from telegram.helpers import escape_markdown
from aiohttp import ClientError, ClientConnectorError
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    Application, CommandHandler, ContextTypes, InlineQueryHandler,
    CallbackQueryHandler, ChosenInlineResultHandler, MessageHandler, filters
)
from telegram.error import TimedOut, NetworkError, RetryAfter, BadRequest

# Загрузка конфигурации
def load_config():
    """Загружает конфигурацию из файла config.json"""
    config_path = "config.json"
    default_config = {
        "BOT_TOKEN": "",
        "OWNER_ID": "",
        "USERBOT_DIR": os.path.expanduser("~/Heroku-dev"),
        "VENV_PYTHON": "/home/alina/.venv/bin/python",
        "PROXYCHAINS_PATH": "/usr/bin/proxychains",
        "GITHUB_REPO": "hairpin01/status-heroku",
        "GITHUB_RAW_URL": "https://raw.githubusercontent.com/hairpin01/status-heroku/main/status-heroku-bot.py",
        "BOT_VERSION": "1.0.6",
        "USER_IDS_FILE": "users.json",
        "LOG_FILE": "heroku.log"
    }

    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                # Обновляем недостающие значения по умолчанию
                for key in default_config:
                    if key not in config:
                        config[key] = default_config[key]
                return config
        else:
            # Создаем файл конфигурации с значениями по умолчанию
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=4)
            print(f"Создан файл конфигурации: {config_path}")
            print("Пожалуйста, заполните BOT_TOKEN и OWNER_ID в config.json")
            return default_config
    except Exception as e:
        print(f"Ошибка загрузки конфигурации: {e}")
        return default_config

# Загружаем конфигурацию
CONFIG = load_config()

# Инициализация переменных из конфигурации
BOT_TOKEN = CONFIG["BOT_TOKEN"]
OWNER_ID = CONFIG["OWNER_ID"]
USERBOT_DIR = CONFIG["USERBOT_DIR"]
VENV_PYTHON = CONFIG["VENV_PYTHON"]
PROXYCHAINS_PATH = CONFIG["PROXYCHAINS_PATH"]
GITHUB_REPO = CONFIG["GITHUB_REPO"]
GITHUB_RAW_URL = CONFIG["GITHUB_RAW_URL"]
BOT_VERSION = CONFIG["BOT_VERSION"]
USER_IDS_FILE = CONFIG["USER_IDS_FILE"]
LOG_FILE = os.path.join(USERBOT_DIR, CONFIG["LOG_FILE"])

# Команды для запуска
USERBOT_CMD = f"{VENV_PYTHON} -m heroku --no-web"
PROXY_CMD = f"{PROXYCHAINS_PATH} {VENV_PYTHON} -m heroku --no-web"

# Глобальные переменные
USER_IDS = set()
DEBUG_CHATS = set()
monitor_task = None
start_time = time.time()
reconnect_attempts = 0
is_reconnecting = True
application_instance = None

# Буфер для дебаг-сообщений
debug_message_buffer = []
debug_buffer_lock = asyncio.Lock()
debug_buffer_size = 5
debug_buffer_timeout = 3

# Конфигурация переподключения
RECONNECT_CONFIG = {
    'max_retries': float('inf'),
    'retry_delay': 5,
    'max_delay': 300,
    'backoff_factor': 1.5,
    'health_check_interval': 10
}

def load_users():
    """Загружает список пользователей из файла"""
    global USER_IDS
    try:
        if os.path.exists(USER_IDS_FILE):
            with open(USER_IDS_FILE, 'r') as f:
                USER_IDS = set(json.load(f))
        else:
            # Создаем файл с владельцем по умолчанию
            USER_IDS = {OWNER_ID} if OWNER_ID else set()
            save_users(USER_IDS)
    except Exception as e:
        print(f"Ошибка загрузки пользователей: {e}")
        USER_IDS = {OWNER_ID} if OWNER_ID else set()

def save_users(users):
    """Сохраняет список пользователей в файл"""
    global USER_IDS
    try:
        with open(USER_IDS_FILE, 'w') as f:
            json.dump(list(users), f)
        USER_IDS = users
    except Exception as e:
        print(f"Ошибка сохранения пользователей: {e}")

# Загружаем пользователей при старте
load_users()

# Проверка прав
def is_owner(user_id):
    return str(user_id) == str(OWNER_ID)

def is_user(user_id):
    return str(user_id) in [str(uid) for uid in USER_IDS] or is_owner(user_id)

def get_system_info():
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime = time.time() - psutil.boot_time()

    # Дополнительная информация
    boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
    load_avg = os.getloadavg() if hasattr(os, 'getloadavg') else "N/A"

    # Информация о сети
    net_io = psutil.net_io_counters()

    # Информация о боте 
    bot_uptime = 0
    bot_start_time = "N/A"
    if 'start_time' in globals():
        bot_uptime = time.time() - start_time
        bot_start_time = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")

    info = (
        f"🤖 **Bot Information:**\n"
        f"• Version: {BOT_VERSION}\n"
        f"• Uptime: {int(bot_uptime // 3600)}h {int((bot_uptime % 3600) // 60)}m\n"
        f"• Started: {bot_start_time}\n\n"

        f"🖥 **System Information:**\n"
        f"• CPU: {cpu}%\n"
        f"• Load: {load_avg}\n"
        f"• RAM: {ram.percent}% ({ram.used // (1024**3)}/{ram.total // (1024**3)} GB)\n"
        f"• Disk: {disk.percent}% ({disk.used // (1024**3)}/{disk.total // (1024**3)} GB)\n"
        f"• Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m\n"
        f"• Boot: {boot_time}\n\n"

        f"🌐 **Network Information:**\n"
        f"• Sent: {net_io.bytes_sent // (1024**2)} MB\n"
        f"• Received: {net_io.bytes_recv // (1024**2)} MB\n\n"

        f"👥 **User Information:**\n"
        f"• Total Users: {len(USER_IDS)}\n"
        f"• Debug Chats: {len(DEBUG_CHATS)}"
    )

    return info

async def send_debug_message(message, bot=None):
    """Буферизирует и отправляет дебаг-сообщения группами"""
    if not DEBUG_CHATS or not bot:
        return

    async with debug_buffer_lock:
        debug_message_buffer.append(message)

        # Если буфер заполнен или это первое сообщение, запускаем таймер отправки
        if len(debug_message_buffer) >= debug_buffer_size:
            await flush_debug_buffer(bot)
        elif len(debug_message_buffer) == 1:
            # Запускаем отложенную отправку для первого сообщения
            asyncio.create_task(delayed_flush(bot))

async def delayed_flush(bot):
    """Отложенная отправка буфера"""
    await asyncio.sleep(debug_buffer_timeout)
    async with debug_buffer_lock:
        if debug_message_buffer:
            await flush_debug_buffer(bot)

async def flush_debug_buffer(bot):
    """Отправляет текущий буфер сообщений"""
    if not debug_message_buffer:
        return

    # Объединяем сообщения
    combined_message = "\n".join(debug_message_buffer)

    # Ограничиваем длину
    if len(combined_message) > 4000:
        combined_message = combined_message[:4000] + "..."

    # Отправляем во все дебаг-чаты
    for chat_id in DEBUG_CHATS.copy():
        try:
            await bot.send_message(chat_id=chat_id, text=f"🔍 Логи:\n{combined_message}")
        except Exception as e:
            print(f"Не удалось отправить дебаг-сообщение в {chat_id}: {e}")

    # Очищаем буфер
    debug_message_buffer.clear()

async def force_flush_debug_buffer(bot):
    """Принудительно отправляет буфер при завершении работы"""
    async with debug_buffer_lock:
        if debug_message_buffer:
            await flush_debug_buffer(bot)

# Системные функции
def get_system_info():
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime = time.time() - psutil.boot_time()

    return (
        f"CPU: {cpu}%\n"
        f"RAM: {ram.percent}% ({ram.used // (1024**3)}/{ram.total // (1024**3)} GB)\n"
        f"Disk: {disk.percent}% ({disk.used // (1024**3)}/{disk.total // (1024**3)} GB)\n"
        f"Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"
    )

def get_userbot_status():
    """Проверяет статус юзербота с улучшенной логикой"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()
            if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                return True, proc.info['create_time']
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    return False, None

async def check_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверить обновления бота на GitHub"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        if update.callback_query:
            await update.callback_query.answer("❌ Доступ запрещен", show_alert=True)
        else:
            await update.message.reply_text("❌ Доступ запрещен")
        return

    # Определяем, откуда пришел запрос
    if update.callback_query:
        message = await update.callback_query.message.reply_text("🔍 Проверяю обновления на GitHub...")
        chat_id = message.chat_id
    else:
        await update.message.reply_text("🔍 Проверяю обновления на GitHub...")
        chat_id = update.message.chat_id

    try:
        # Получаем информацию о последнем релизе
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            latest_release = response.json()
            latest_version = latest_release['tag_name']
            release_name = latest_release['name']
            release_notes = latest_release['body'][:500] + "..." if len(latest_release['body']) > 500 else latest_release['body']
            published_at = latest_release['published_at']

            if latest_version != BOT_VERSION:
                message = (
                    f"🔄 **Доступно обновление!**\n\n"
                    f"• Текущая версия: `{BOT_VERSION}`\n"
                    f"• Новая версия: `{latest_version}`\n"
                    f"• Релиз: {release_name}\n"
                    f"• Опубликован: {published_at[:10]}\n\n"
                    f"**Что нового:**\n{release_notes}\n\n"
                    f"Для обновления используйте /update_bot"
                )
            else:
                message = f"✅ **Бот обновлен до последней версии** `{BOT_VERSION}`"

        elif response.status_code == 404:
            # Если нет релизов, проверяем последний коммит
            url = f"https://api.github.com/repos/{GITHUB_REPO}/commits?per_page=1"
            response = requests.get(url, timeout=10)

            if response.status_code == 200:
                commits = response.json()
                if commits:
                    latest_commit = commits[0]
                    commit_hash = latest_commit['sha'][:7]
                    commit_message = latest_commit['commit']['message']
                    commit_date = latest_commit['commit']['committer']['date']

                    message = (
                        f"📝 **Информация о репозитории:**\n\n"
                        f"• Текущая версия: `{BOT_VERSION}`\n"
                        f"• Последний коммит: `{commit_hash}`\n"
                        f"• Дата: {commit_date[:10]}\n"
                        f"• Сообщение: {commit_message}\n\n"
                        f"Релизы не найдены, но есть новые коммиты."
                    )
                else:
                    message = "❌ Не удалось получить информацию о коммитах"
            else:
                message = f"❌ Ошибка при запросе к GitHub: {response.status_code}"
        else:
            message = f"❌ Ошибка при проверке обновлений: {response.status_code}"

    except requests.exceptions.RequestException as e:
        message = f"❌ Ошибка сети при проверке обновлений: {str(e)}"
    except Exception as e:
        message = f"❌ Неожиданная ошибка: {str(e)}"

    await context.bot.send_message(chat_id, message, parse_mode='Markdown')

async def update_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновить бота с GitHub"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        if update.callback_query:
            await update.callback_query.answer("❌ Доступ запрещен", show_alert=True)
        else:
            await update.message.reply_text("❌ Доступ запрещен")
        return

    if update.callback_query:
        message = await update.callback_query.message.reply_text("🔄 Начинаю обновление бота...")
        chat_id = message.chat_id
    else:
        await update.message.reply_text("🔄 Начинаю обновление бота...")
        chat_id = update.message.chat_id

    try:

        save_users(USER_IDS)


        current_file = os.path.abspath(__file__)
        backup_file = current_file + ".backup"


        temp_file = current_file + ".new"

        # Скачиваем новый код по raw ссылке
        await context.bot.send_message(chat_id, "📥 Скачиваю обновление...")

        try:
            response = requests.get(GITHUB_RAW_URL, timeout=30)
            response.raise_for_status()
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Ошибка загрузки обновления: {str(e)}")
            return

        # Сохраняем новый код во временный файл
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(response.text)

        # Проверяем синтаксис нового кода
        await context.bot.send_message(chat_id, "🔍 Проверяю синтаксис...")
        try:
            check_result = subprocess.run(
                [VENV_PYTHON, "-m", "py_compile", temp_file],
                capture_output=True,
                text=True,
                timeout=10
            )

            if check_result.returncode != 0:
                error_msg = check_result.stderr[:500] if check_result.stderr else "Неизвестная ошибка синтаксиса"
                await context.bot.send_message(
                    chat_id,
                    f"❌ Ошибка синтаксиса в новом коде:\n```\n{error_msg}\n```",
                    parse_mode='Markdown'
                )
                os.remove(temp_file)
                return
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Ошибка проверки синтаксиса: {str(e)}")
            os.remove(temp_file)
            return

        # Создаем резервную копию текущего файла
        await context.bot.send_message(chat_id, "💾 Создаю резервную копию...")
        try:
            import shutil
            shutil.copy2(current_file, backup_file)
        except Exception as e:
            await context.bot.send_message(chat_id, f"⚠️ Не удалось создать резервную копию: {str(e)}")

        # Заменяем текущий файл новым
        await context.bot.send_message(chat_id, "🔄 Применяю обновление...")
        try:
            # Закрываем все файловые дескрипторы перед заменой
            import sys
            sys.stdout.flush()
            sys.stderr.flush()

            # Заменяем файл
            os.replace(temp_file, current_file)

            # Устанавливаем правильные права доступа
            os.chmod(current_file, 0o755)

        except Exception as e:
            # Восстанавливаем из резервной копии при ошибке
            if os.path.exists(backup_file):
                try:
                    os.replace(backup_file, current_file)
                    await context.bot.send_message(chat_id, "🔄 Восстановлен из резервной копии")
                except:
                    pass

            await context.bot.send_message(chat_id, f"❌ Ошибка применения обновления: {str(e)}")
            return

        await context.bot.send_message(
            chat_id,
            "✅ Бот успешно обновлен!\n\n"
            "Перезапускаю бота..."
        )

        # Перезапускаем бота
        await restart_bot(update, context)

    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Ошибка при обновлении: {str(e)}")



async def delete_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню удаления пользователя"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Эта функция доступна только владельцу", show_alert=True)
        return

    users_list = ["🗑 Удалить пользователя:\n"]
    for uid in USER_IDS:
        if uid != OWNER_ID:
            users_list.append(f"👤 {uid} - /del_user_{uid}")

    if len(users_list) == 1:
        users_list.append("Нет пользователей для удаления")

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="users_menu")]]
    await query.edit_message_text("\n".join(users_list), reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_specific_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаление конкретного пользователя через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    if user_id in USER_IDS and user_id != OWNER_ID:
        USER_IDS.remove(user_id)
        save_users(USER_IDS)
        await query.answer(f"✅ Пользователь {user_id} удален", show_alert=True)
        await asyncio.sleep(1)
        await show_users_menu(update, context)
    else:
        await query.answer("❌ Нельзя удалить этого пользователя", show_alert=True)

async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статус через кнопку"""
    query = update.callback_query
    is_running, start_time = get_userbot_status()
    status_text = "✅ Запущен" if is_running else "❌ Остановлен"
    if is_running:
        uptime = time.time() - start_time
        status_text += f"\n⏱ Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"

    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="status"), InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
    await query.edit_message_text(f"📊 **Статус юзербота:**\n\n{status_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def system_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о системе через кнопку"""
    query = update.callback_query
    info = get_system_info()
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="system_info"), InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
    await query.edit_message_text(f"🖥 **Информация о системе:**\n\n{info}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# Функция для получения подробной информации о системе
async def detailed_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подробная информация о системе"""
    if not is_user(update.effective_user.id):
        return

    # Определяем, откуда пришел запрос
    if update.callback_query:
        chat_id = update.callback_query.message.chat_id
        message_id = update.callback_query.message.message_id
    else:
        chat_id = update.message.chat_id
        message_id = None

    # Информация о процессах
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            processes.append(proc.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Сортируем по использованию CPU
    processes.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)
    top_processes = processes[:5]  # Топ-5 процессов

    # Информация о дисках
    disks = []
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            disks.append({
                'device': partition.device,
                'mountpoint': partition.mountpoint,
                'total': usage.total // (1024**3),
                'used': usage.used // (1024**3),
                'percent': usage.percent
            })
        except PermissionError:
            continue

    # Строим сообщение
    message = get_system_info() + "\n\n"

    message += "🔥 **Топ процессов по CPU:**\n"
    for proc in top_processes:
        message += f"• {proc['name']}: {proc['cpu_percent'] or 0:.1f}% CPU, {proc['memory_percent'] or 0:.1f}% RAM\n"

    message += "\n💾 **Диски:**\n"
    for disk in disks:
        message += f"• {disk['device']} ({disk['mountpoint']}): {disk['percent']}% ({disk['used']}/{disk['total']} GB)\n"

    # Информация о юзерботе
    is_running, start_time = get_userbot_status()
    if is_running:
        uptime = time.time() - start_time
        message += f"\n🤖 **Юзербот:** Запущен (Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m)"
    else:
        message += f"\n🤖 **Юзербот:** Остановлен"

    # Отправляем сообщение
    if update.callback_query:
        await update.callback_query.edit_message_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text(message, parse_mode='Markdown')



async def del_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить пользователя"""
    if update.message.chat.type != "private":
        await update.message.reply_text("❌ Эта команда доступна только в ЛС")
        return

    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    if context.args:
        try:
            user_id = int(context.args[0])
            if user_id == OWNER_ID:
                await update.message.reply_text("❌ Нельзя удалить владельца")
                return

            if user_id in USER_IDS:
                USER_IDS.remove(user_id)
                save_users(USER_IDS)  # Сохраняем изменения
                await update.message.reply_text(f"✅ Пользователь {user_id} удален")
            else:
                await update.message.reply_text("❌ Пользователь не найден")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID пользователя")
    else:
        await update.message.reply_text("❌ Укажите ID пользователя: /del_user <id>")

# Упрощенный дебаг-режим
async def send_debug_message(message, bot=None):
    """Отправляет дебаг-сообщение во все чаты с включенным дебагом"""
    if not DEBUG_CHATS:
        return

    if len(message) > 4000:
        message = message[:4000] + "..."

    for chat_id in DEBUG_CHATS.copy():
        try:
            if bot:
                await bot.send_message(chat_id=chat_id, text=f"🔍 {message}")
        except Exception as e:
            print(f"Не удалось отправить дебаг-сообщение в {chat_id}: {e}")


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню"""
    if not is_user(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer("❌ У вас нет доступа к этому боту", show_alert=True)
            return
        else:
            await update.message.reply_text("❌ У вас нет доступа к этому боту")
            return

    keyboard = [
        [
            InlineKeyboardButton("🔄 Статус", callback_data="status"),
            InlineKeyboardButton("📊 Система", callback_data="system_info")
        ],
        [
            InlineKeyboardButton("🚀 Запустить", callback_data="start_userbot"),
            InlineKeyboardButton("🛑 Остановить", callback_data="stop_userbot")
        ],
        [
            InlineKeyboardButton("🔧 Управление", callback_data="management"),
            InlineKeyboardButton("📋 Логи", callback_data="logs_menu")
        ],
        [
            InlineKeyboardButton("🌐 Соединение", callback_data="connection_status"),
            InlineKeyboardButton("🔄 Обновления", callback_data="updates_menu")
        ],
        [
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
            InlineKeyboardButton("❓ Помощь", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    is_running, start_time = get_userbot_status()
    status_text = "✅ Запущен" if is_running else "❌ Остановлен"
    if is_running:
        uptime = time.time() - start_time
        status_text += f" (Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m)"

    message_text = f"🤖 **Главное меню v{BOT_VERSION}**\n\n📊 Статус юзербота: {status_text}\n\nВыберите действие:"

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню настроек"""
    debug_status = "✅ Включен" if update.effective_chat.id in DEBUG_CHATS else "❌ Выключен"

    keyboard = [
        [
            InlineKeyboardButton(f"🔍 Дебаг: {debug_status}", callback_data="toggle_debug")
        ],
        [
            InlineKeyboardButton("🌐 Статус соединения", callback_data="connection_status"),
            InlineKeyboardButton("🖥 Терминал", callback_data="terminal_menu")
        ],
        [
            InlineKeyboardButton("🌐 Ping", callback_data="ping_menu"),
            InlineKeyboardButton("👥 Пользователи", callback_data="users_menu")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        f"⚙️ **Меню настроек**\n\nТекущие настройки:\n- Дебаг-режим: {debug_status}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_logs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню логов"""
    keyboard = [
        [
            InlineKeyboardButton("📄 Все логи", callback_data="logs_ALL"),
            InlineKeyboardButton("⚠️ WARNING", callback_data="logs_WARNING")
        ],
        [
            InlineKeyboardButton("ℹ️ INFO", callback_data="logs_INFO"),
            InlineKeyboardButton("❌ ERROR", callback_data="logs_ERROR")
        ],
        [
            InlineKeyboardButton("🐛 DEBUG", callback_data="logs_DEBUG"),
            InlineKeyboardButton("📁 Папка логов", callback_data="open_logs_dir")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "📋 **Меню логов**\n\nВыберите уровень логов для просмотра:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню настроек"""
    debug_status = "✅ Включен" if update.effective_chat.id in DEBUG_CHATS else "❌ Выключен"

    keyboard = [
        [
            InlineKeyboardButton(f"🔍 Дебаг: {debug_status}", callback_data="toggle_debug")
        ],
        [
            InlineKeyboardButton("🖥 Терминал", callback_data="terminal_menu"),
            InlineKeyboardButton("🌐 Ping", callback_data="ping_menu")
        ],
        [
            InlineKeyboardButton("👥 Пользователи", callback_data="users_menu"),
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        f"⚙️ **Меню настроек**\n\nТекущие настройки:\n- Дебаг-режим: {debug_status}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_terminal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню терминала"""
    keyboard = [
        [
            InlineKeyboardButton("📊 free -h", callback_data="terminal_free"),
            InlineKeyboardButton("💻 top -n 1", callback_data="terminal_top")
        ],
        [
            InlineKeyboardButton("📁 ls -la", callback_data="terminal_ls"),
            InlineKeyboardButton("🔍 ps aux | grep python", callback_data="terminal_ps")
        ],
        [
            InlineKeyboardButton("🔄 Обновить пакеты", callback_data="terminal_update"),
            InlineKeyboardButton("🧹 Очистить кэш pip", callback_data="terminal_clean")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="settings")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "🖥 **Меню терминала**\n\nБыстрые команды для управления системой:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_ping_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню ping"""
    keyboard = [
        [
            InlineKeyboardButton("🌐 Google", callback_data="ping_google.com"),
            InlineKeyboardButton("🎵 Spotify", callback_data="ping_open.spotify.com")
        ],
        [
            InlineKeyboardButton("📱 Telegram", callback_data="ping_telegram.org"),
            InlineKeyboardButton("🚀 GitHub", callback_data="ping_github.com")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="settings")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "🌐 **Меню Ping**\n\nПроверка доступности серверов:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def restart_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перезапустить юзербота"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    await update.message.reply_text("🔄 Перезапускаю юзербота...")

    # Сначала останавливаем
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()
            if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    if processes:
        for proc in processes:
            try:
                proc.terminate()
            except:
                pass

        # Ждем завершения
        timeout = 10
        start_time = time.time()
        while time.time() - start_time < timeout:
            await asyncio.sleep(2)
            still_running = []
            for proc in processes:
                try:
                    if proc.is_running():
                        still_running.append(proc)
                except:
                    pass

            if not still_running:
                break
                proc.kill()
            except:
                pass

    # Запускаем заново
    try:
        cmd = f"cd {USERBOT_DIR} && {USERBOT_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await update.message.reply_text("✅ Юзербот успешно перезапущен!")

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await update.message.reply_text("❌ Не удалось перезапустить юзербота. Проверьте логи.")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка перезапуска: {str(e)}")

async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перезапустить бота"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        if update.callback_query:
            await update.callback_query.answer("❌ Доступ запрещен", show_alert=True)
        else:
            await update.message.reply_text("❌ Доступ запрещен")
        return

    # Определяем, откуда пришел запрос
    if update.callback_query:
        message = await update.callback_query.message.reply_text("🔄 Перезапускаю бота...")
        chat_id = message.chat_id
    else:
        await update.message.reply_text("🔄 Перезапускаю бота...")
        chat_id = update.message.chat_id

    try:
        # Пытаемся перезапустить через systemd
        process = await asyncio.create_subprocess_shell(
            "sudo systemctl restart status-heroku",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            await safe_send_message(context.bot, chat_id, "✅ Бот перезапускается...")
        else:
            # Если systemd не сработал, просто выходим и надеемся на перезапуск
            await safe_send_message(context.bot, chat_id, "⚠️ Перезапуск через systemd не удался. Пытаюсь перезапуститься...")
            # Используем sys.exit только если systemd недоступен
            import sys
            sys.exit(0)

    except Exception as e:
        await safe_send_message(context.bot, chat_id, f"❌ Ошибка перезапуска: {str(e)}")
        import sys
        sys.exit(1)

async def show_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню пользователей"""
    users_count = len(USER_IDS)

    keyboard = [
        [
            InlineKeyboardButton("👤 Добавить меня", callback_data="add_me")
        ],
        [
            InlineKeyboardButton("📊 Список пользователей", callback_data="list_users")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="settings")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        f"👥 **Меню пользователей**\n\nВсего пользователей: {users_count}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать помощь"""
    help_text = f"""
🤖 **Бот мониторинга системы и юзербота v{BOT_VERSION}**

**Основные команды:**
/menu - Главное меню
/start_userbot - Запустить юзербота
/stop_userbot - Остановить юзербота
/restart_userbot - Перезапустить юзербота
/restart_bot - Перезапустить бота
/status - Статус юзербота
/info - Информация о системе
/detailed_info - Подробная информация
/connection_status - Статус соединения (с кнопкой обновления)

**Обновления:**
/check_updates - Проверить обновления
/update_bot - Обновить бота
/install_git - Установить git (если не установлен)

**Управление:**
/install_requirements - Установить зависимости
/update_heroku - Обновить HerokuTL
/logs <уровень> - Получить логи
/debug_on - Включить дебаг
/debug_off - Выключить дебаг

**Мониторинг:**
/ram - Информация о RAM
/cpu - Информация о CPU
/disk - Информация о диске
/uptime - Аптайм системы
/ping [хост] - Ping хоста
/terminal [команда] - Выполнить команду

**Пользователи:**
/get_owner - Добавить себя
/get_user [id] - Добавить пользователя
/del_user [id] - Удалить пользователя

**Инлайн-режим:**
Напишите @username_бота в любом чате и начните вводить команду

Используйте кнопки меню для удобного управления!
"""

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
    await update.callback_query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard))


async def about_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о боте"""
    query = update.callback_query

    about_text = (
        f"🤖 **Heroku Monitor Bot v{BOT_VERSION}**\n\n"
        f"**Разработчик:** hairpin01\n"
        f"**Репозиторий:** {GITHUB_REPO}\n"
        f"**Назначение:** Мониторинг системы и управление юзерботом HerokuTL\n\n"
        f"**Возможности:**\n"
        f"• Управление юзерботом\n"
        f"• Мониторинг системы\n"
        f"• Просмотр логов\n"
        f"• Управление пользователями\n"
        f"• Автоматические обновления\n\n"
        f"**Технологии:** Python, python-telegram-bot, psutil"
    )

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
    await query.edit_message_text(about_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def connection_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус соединения бота с кнопкой обновления"""
    user_id = update.effective_user.id

    if not is_user(user_id):
        if update.callback_query:
            await update.callback_query.answer("❌ У вас нет доступа к этому боту", show_alert=True)
        else:
            await update.message.reply_text("❌ Доступ запрещен")
        return

    # Определяем, откуда пришел запрос
    if update.callback_query:
        message = update.callback_query.message
        chat_id = message.chat_id
        message_id = message.message_id
        is_callback = True
    else:
        chat_id = update.message.chat_id
        message_id = None
        is_callback = False

    # Показываем "Проверяем..." при обновлении
    if is_callback:
        try:
            await update.callback_query.edit_message_text("🔍 Проверяем соединение...")
        except Exception as e:
            print(f"Ошибка при обновлении сообщения: {e}")

    try:
        # Проверяем соединение с Telegram API
        start_time = time.time()
        try:
            bot_info = await asyncio.wait_for(context.bot.get_me(), timeout=10)
            api_response_time = (time.time() - start_time) * 1000  # в миллисекундах

            # Дополнительные проверки
            start_time_ping = time.time()
            ping_process = await asyncio.create_subprocess_shell(
                "ping -c 1 api.telegram.org",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await ping_process.communicate()
            ping_time = (time.time() - start_time_ping) * 1000

            # Получаем информацию о системе
            cpu_usage = psutil.cpu_percent(interval=0.1)
            memory_usage = psutil.virtual_memory().percent

            # Определяем качество соединения
            if api_response_time < 500:
                connection_quality = "🚀 Отличное"
            elif api_response_time < 1000:
                connection_quality = "✅ Хорошее"
            elif api_response_time < 2000:
                connection_quality = "⚠️ Медленное"
            else:
                connection_quality = "❌ Плохое"

            # Форматируем сообщение БЕЗ Markdown
            status_message = (
                "🌐 Статус соединения\n\n"
                "🤖 Информация о боте:\n"
                f"• Имя: {bot_info.first_name}\n"
                f"• Юзернейм: @{bot_info.username if bot_info.username else 'N/A'}\n"
                f"• ID: {bot_info.id}\n\n"

                "📊 Производительность:\n"
                f"• Время ответа API: {api_response_time:.0f} мс\n"
                f"• Ping до Telegram: {ping_time:.0f} мс\n"
                f"• Качество соединения: {connection_quality}\n"
                f"• Загрузка CPU: {cpu_usage:.1f}%\n"
                f"• Использование RAM: {memory_usage:.1f}%\n\n"

                "🔄 Статистика переподключений:\n"
                f"• Попыток переподключения: {reconnect_attempts}\n"
                "• Статус: ✅ Онлайн и стабильный"
            )

        except asyncio.TimeoutError:
            status_message = (
                "🌐 Статус соединения\n\n"
                "❌ Таймаут при проверке соединения:\n"
                "• Не удалось получить ответ от Telegram API за 10 секунд\n"
                f"• Попыток переподключения: {reconnect_attempts}\n\n"

                "🔄 Автоматическое восстановление:\n"
                "Бот пытается восстановить соединение.\n"
                "Следующая попытка через несколько секунд."
            )

    except Exception as e:
        status_message = (
            "🌐 Статус соединения\n\n"
            "⚠️ Неизвестная ошибка:\n"
            f"• Ошибка: {str(e)}\n"
            f"• Попыток переподключения: {reconnect_attempts}\n\n"

            "🔄 Рекомендации:\n"
            "1. Проверьте интернет-соединение\n"
            "2. Убедитесь, что бот запущен\n"
            "3. Попробуйте перезапустить бота"
        )

    # Создаем клавиатуру с кнопками
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить снова", callback_data="connection_status")],
        [
            InlineKeyboardButton("📊 Система", callback_data="system_info"),
            InlineKeyboardButton("🤖 Юзербот", callback_data="status")
        ],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем или обновляем сообщение БЕЗ parse_mode
    try:
        if is_callback:
            await update.callback_query.edit_message_text(
                status_message,
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                status_message,
                reply_markup=reply_markup
            )
    except Exception as e:
        # Если возникает ошибка, пытаемся отправить максимально простое сообщение
        error_message = "Не удалось отобразить статус соединения. Попробуйте еще раз."
        if is_callback:
            await update.callback_query.edit_message_text(error_message)
        else:
            await update.message.reply_text(error_message)
        print(f"Ошибка при отправке статуса соединения: {e}")

async def check_telegram_connection(bot):
    """Проверяет соединение с Telegram API"""
    try:
        await asyncio.wait_for(bot.get_me(), timeout=10)
        return True
    except (asyncio.TimeoutError, Exception):
        return False

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    error = context.error

    # Логируем ошибку
    print(f"Произошла ошибка: {error}")

    # Обрабатываем разные типы ошибок
    if isinstance(error, BadRequest):
        if "Can't parse entities" in str(error):
            print("Ошибка разбора Markdown. Проверьте форматирование сообщений.")
        elif "Message is not modified" in str(error):
            # Игнорируем эту ошибку - сообщение не изменилось
            return
    elif isinstance(error, TimedOut):
        print("Таймаут запроса к Telegram API")
    elif isinstance(error, NetworkError):
        print("Ошибка сети при подключении к Telegram API")

    # Отправляем сообщение об ошибке пользователю, если это возможно
    try:
        if update and update.effective_chat:
            error_message = (
                "⚠️ Произошла ошибка при обработке запроса.\n"
                "Попробуйте еще раз или обратитесь к администратору."
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=error_message
            )
    except Exception as e:
        print(f"Не удалось отправить сообщение об ошибке: {e}")


async def force_connection_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительная проверка соединения"""
    user_id = update.effective_user.id

    if not is_user(user_id):
        if update.callback_query:
            await update.callback_query.answer("❌ У вас нет доступа к этому боту", show_alert=True)
        else:
            await update.message.reply_text("❌ Доступ запрещен")
        return

    # Определяем, откуда пришел запрос
    if update.callback_query:
        message = update.callback_query.message
        chat_id = message.chat_id
        is_callback = True
    else:
        chat_id = update.message.chat_id
        is_callback = False

    await safe_send_message(context.bot, chat_id, "🔍 Принудительно проверяю соединение...")

    # Выполняем проверки
    internet_status = await check_internet_connection()
    telegram_status = await check_telegram_connection(context.bot)

    if internet_status and telegram_status:
        status_message = "✅ Все проверки пройдены!\n• Интернет: Доступен\n• Telegram API: Доступен"
    elif internet_status and not telegram_status:
        status_message = "⚠️ Проблемы с Telegram\n• Интернет: Доступен\n• Telegram API: Недоступен"
    elif not internet_status and telegram_status:
        status_message = "❌ Нет интернета\n• Интернет: Недоступен\n• Telegram API: Доступен"
    else:
        status_message = "💥 Критический сбой\n• Интернет: Недоступен\n• Telegram API: Недоступен"

    # Добавляем информацию о переподключениях
    status_message += f"\n\n📊 Статистика:\n• Попыток переподключения: {reconnect_attempts}"

    if connection_lost_time:
        downtime = time.time() - connection_lost_time
        status_message += f"\n• Соединение потеряно: {int(downtime)} сек. назад"

    keyboard = [
        [InlineKeyboardButton("🔄 Проверить снова", callback_data="force_connection_check")],
        [InlineKeyboardButton("🌐 Статус соединения", callback_data="connection_status")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_callback:
        await update.callback_query.edit_message_text(status_message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(status_message, reply_markup=reply_markup)

async def send_connection_status_update(bot, status, downtime=None):
    """Отправляет обновление статуса соединения всем пользователям"""
    message_map = {
        'lost': "❌ Потеряно соединение с интернетом!\n\nБот пытается восстановить подключение...",
        'restored': f"✅ Соединение восстановлено!\n\nВремя простоя: {int(downtime)} секунд",
        'degraded': "⚠️ Проблемы с соединением Telegram\n\nБот работает в ограниченном режиме"
    }

    message = message_map.get(status, "❓ Неизвестный статус соединения")

    for user_id in USER_IDS.copy():
        try:
            await safe_send_message(bot, user_id, message)
        except Exception as e:
            print(f"Не удалось отправить уведомление о соединении пользователю {user_id}: {e}")


async def check_updates_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка обновлений через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.answer()
    await check_updates(update, context)

async def update_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновление бота через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.answer()
    await update_bot(update, context)

async def detailed_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подробная информация через кнопку"""
    query = update.callback_query
    await query.answer()
    await detailed_info(update, context)

async def show_updates_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню обновлений"""
    keyboard = [
        [
            InlineKeyboardButton("🔍 Проверить обновления", callback_data="check_updates"),
            InlineKeyboardButton("🔄 Обновить бота", callback_data="update_bot")
        ],
        [
            InlineKeyboardButton("📊 Подробная информация", callback_data="detailed_info"),
            InlineKeyboardButton("ℹ️ О боте", callback_data="about_bot")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        f"🔄 **Меню обновлений v{BOT_VERSION}**\n\nУправление обновлениями и информация о боте:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления"""
    query = update.callback_query

    keyboard = [
        [
            InlineKeyboardButton("📦 Установить зависимости", callback_data="install_requirements"),
            InlineKeyboardButton("🔄 Обновить HerokuTL", callback_data="update_heroku")
        ],
        [
            InlineKeyboardButton("🚀 Запуск с прокси", callback_data="start_proxy"),
            InlineKeyboardButton("🐞 Диагностика", callback_data="debug_userbot")
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🔧 Меню управления\n\nДополнительные функции управления юзерботом:",
        reply_markup=reply_markup
    )

async def install_requirements_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установка зависимостей через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Эта функция доступна только владельцу", show_alert=True)
        return

    await query.edit_message_text("📦 Устанавливаю зависимости...")

    try:
        cmd = f"cd {USERBOT_DIR} && {VENV_PYTHON} -m pip install -r requirements.txt"

        # Устанавливаем правильные переменные окружения
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)

        await process.wait()

        if process.returncode == 0:
            await query.edit_message_text("✅ Зависимости установлены успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await query.edit_message_text(f"❌ Ошибка установки:\n{error_output}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_management_menu(update, context)

async def update_heroku_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновление HerokuTL через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Эта функция доступна только владельцу", show_alert=True)
        return

    await query.edit_message_text("🔄 Обновляю HerokuTL...")

    try:
        cmd = f"{VENV_PYTHON} -m pip install heroku-tl-new -U"

        # Устанавливаем правильные переменные окружения
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)

        await process.wait()

        if process.returncode == 0:
            await query.edit_message_text("✅ HerokuTL обновлен успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await query.edit_message_text(f"❌ Ошибка обновления:\n{error_output}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_management_menu(update, context)

async def debug_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика юзербота через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Эта функция доступна только владельцу", show_alert=True)
        return

    await query.edit_message_text("🐞 Выполняю диагностику...")

    diagnostic_messages = []

    if os.path.exists(VENV_PYTHON):
        diagnostic_messages.append("✅ Виртуальное окружение найдено")
    else:
        diagnostic_messages.append("❌ Виртуальное окружение не найдено")

    if os.path.exists(USERBOT_DIR):
        diagnostic_messages.append("✅ Директория юзербота найдена")
    else:
        diagnostic_messages.append("❌ Директория юзербота не найдена")

    is_running, start_time = get_userbot_status()
    if is_running:
        uptime = time.time() - start_time
        diagnostic_messages.append(f"✅ Юзербот запущен (Uptime: {int(uptime // 60)}m {int(uptime % 60)}s)")
    else:
        diagnostic_messages.append("❌ Юзербот не запущен")

    log_file_path = os.path.join(USERBOT_DIR, "userbot_output.log")
    if os.path.exists(log_file_path):
        file_size = os.path.getsize(log_file_path)
        diagnostic_messages.append(f"✅ Файл логов существует ({file_size} bytes)")
    else:
        diagnostic_messages.append("❌ Файл логов не существует")

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="management")]]
    await query.edit_message_text("\n".join(diagnostic_messages), reply_markup=InlineKeyboardMarkup(keyboard))

# Обработчики кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_user(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    data = query.data

    # Основное меню
    if data == "main_menu":
        await show_main_menu(update, context)

    # Статус и информация
    elif data == "status":
        is_running, start_time = get_userbot_status()
        status_text = "✅ Запущен" if is_running else "❌ Остановлен"
        if is_running:
            uptime = time.time() - start_time
            status_text += f"\n⏱ Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"

        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="status"), InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
        await query.edit_message_text(f"📊 **Статус юзербота:**\n\n{status_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("del_user_"):
        try:
            user_id = int(data.split("_")[2])
            if user_id in USER_IDS and user_id != OWNER_ID:
              USER_IDS.remove(user_id)
              save_users(USER_IDS)
              await query.edit_message_text(f"✅ Пользователь {user_id} удален")
              await asyncio.sleep(2)
              await show_users_menu(update, context)
            else:
              await query.edit_message_text("❌ Нельзя удалить этого пользователя")
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Ошибка удаление пользователя")

    elif data == "system_info":
        info = get_system_info()
        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="system_info"), InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
        await query.edit_message_text(f"🖥 **Информация о системе:**\n\n{info}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    # Управление юзерботом
    elif data == "start_userbot":
        await start_userbot_callback(update, context)

    elif data == "start_proxy":
        await start_userbot_proxy_callback(update, context)

    elif data == "stop_userbot":
        await stop_userbot_callback(update, context)

    elif data == "management":
        await show_management_menu(update, context)

    elif data == "delete_user":
      await delete_user_callback(update, context)

    elif data == "install_requirements":
        await install_requirements_callback(update, context)

    elif data == "update_heroku":
        await update_heroku_callback(update, context)

    elif data == "debug_userbot":
        await debug_userbot_callback(update, context)

    # Логи
    elif data == "logs_menu":
        await show_logs_menu(update, context)

    elif data.startswith("logs_"):
        level = data.split("_")[1]
        await send_logs_callback(update, context, level)

    elif data == "open_logs_dir":
        await open_logs_dir_callback(update, context)

    # Настройки
    elif data == "connection_status":
        await connection_status(update, context)

    elif data == "updates_menu":
        await show_updates_menu(update, context)

    elif data == "check_updates":
        await check_updates_callback(update, context)

    elif data == "update_bot":
        await update_bot_callback(update, context)

    elif data == "detailed_info":
        await detailed_info_callback(update, context)

    elif data == "about_bot":
        await about_bot(update, context)

    elif data == "settings":
        await show_settings_menu(update, context)

    elif data == "toggle_debug":
        await toggle_debug_callback(update, context)

    elif data == "terminal_menu":
        await show_terminal_menu(update, context)

    elif data.startswith("terminal_"):
        command = data.split("_")[1]
        await execute_terminal_command(update, context, command)

    elif data == "ping_menu":
        await show_ping_menu(update, context)

    elif data.startswith("ping_"):
        host = data.split("_")[1]
        await ping_host_callback(update, context, host)

    elif data == "users_menu":
        await show_users_menu(update, context)

    elif data == "add_me":
        await add_me_callback(update, context)

    elif data == "list_users":
        await list_users_callback(update, context)

    # Помощь
    elif data == "help":
        await show_help(update, context)

# Функции-обработчики для кнопок
async def start_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск юзербота через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text("🔄 Запускаю юзербота...")

    is_running, _ = get_userbot_status()
    if is_running:
        await query.edit_message_text("⚠️ Юзербот уже запущен")
        await show_main_menu(update, context)
        return

    try:
        cmd = f"cd {USERBOT_DIR} && {USERBOT_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await query.edit_message_text("✅ Юзербот успешно запущен!")

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await query.edit_message_text("❌ Не удалось запустить юзербота. Проверьте логи.")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка запуска: {str(e)}")

    await asyncio.sleep(2)
    await show_main_menu(update, context)

async def start_userbot_proxy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск юзербота с прокси через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text("🔄 Запускаю юзербота с прокси...")

    if not os.path.exists(PROXYCHAINS_PATH):
        await query.edit_message_text(f"❌ proxychains не найден по пути {PROXYCHAINS_PATH}")
        await show_main_menu(update, context)
        return

    is_running, _ = get_userbot_status()
    if is_running:
        await query.edit_message_text("⚠️ Юзербот уже запущен")
        await show_main_menu(update, context)
        return

    try:
        cmd = f"cd {USERBOT_DIR} && {PROXY_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await query.edit_message_text("✅ Юзербот успешно запущен с прокси!")

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await query.edit_message_text("❌ Не удалось запустить юзербота с прокси. Проверьте логи.")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка запуска: {str(e)}")

    await asyncio.sleep(2)
    await show_main_menu(update, context)




async def handle_chosen_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбранные инлайн-результаты"""
    chosen_result = update.chosen_inline_result
    result_id = chosen_result.result_id
    user_id = chosen_result.from_user.id

    if not is_owner(user_id):
        return

    try:
        # Запуск юзербота
        if result_id == "start_userbot":
            await execute_inline_start_userbot(chosen_result, context)

        # Остановка юзербота
        elif result_id == "stop_userbot":
            await execute_inline_stop_userbot(chosen_result, context)

        # Перезапуск юзербота
        elif result_id == "restart_userbot":
            await execute_inline_restart_userbot(chosen_result, context)

    except Exception as e:
        print(f"Ошибка в handle_chosen_inline: {e}")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Ошибка при выполнении инлайн-команды: {str(e)}"
            )
        except:
            pass

async def execute_inline_start_userbot(chosen_result, context):
    """Выполняет запуск юзербота из инлайн-режима"""
    user_id = chosen_result.from_user.id

    # Отправляем уведомление о начале операции
    await context.bot.send_message(
        chat_id=user_id,
        text="🔄 Запускаю юзербота через инлайн-режим..."
    )

    # Проверяем, не запущен ли уже юзербот
    is_running, _ = get_userbot_status()
    if is_running:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Юзербот уже запущен"
        )
        return

    try:
        cmd = f"cd {USERBOT_DIR} && {USERBOT_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Юзербот успешно запущен через инлайн-режим!"
            )

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Не удалось запустить юзербота через инлайн-режим. Проверьте логи."
            )

    except Exception as e:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Ошибка запуска через инлайн-режим: {str(e)}"
        )

async def execute_inline_stop_userbot(chosen_result, context):
    """Выполняет остановку юзербота из инлайн-режима"""
    user_id = chosen_result.from_user.id

    await context.bot.send_message(
        chat_id=user_id,
        text="🛑 Останавливаю юзербота через инлайн-режим..."
    )

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()
            if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    if not processes:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Юзербот не был запущен"
        )
        return

    for proc in processes:
        try:
            proc.terminate()
        except:
            pass

    timeout = 15
    start_time = time.time()

    while time.time() - start_time < timeout:
        await asyncio.sleep(2)
        still_running = []
        for proc in processes:
            try:
                if proc.is_running():
                    still_running.append(proc)
            except:
                pass

        if not still_running:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Юзербот корректно остановлен через инлайн-режим"
            )
            return

        processes = still_running

    for proc in processes:
        try:
            proc.kill()
        except:
            pass

    await context.bot.send_message(
        chat_id=user_id,
        text="✅ Юзербот остановлен (принудительно) через инлайн-режим"
    )

async def execute_inline_restart_userbot(chosen_result, context):
    """Выполняет перезапуск юзербота из инлайн-режима"""
    user_id = chosen_result.from_user.id

    await context.bot.send_message(
        chat_id=user_id,
        text="🔄 Перезапускаю юзербота через инлайн-режим..."
    )

    # Сначала останавливаем
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()
            if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    if processes:
        for proc in processes:
            try:
                proc.terminate()
            except:
                pass

        # Ждем завершения
        timeout = 10
        start_time = time.time()
        while time.time() - start_time < timeout:
            await asyncio.sleep(2)
            still_running = []
            for proc in processes:
                try:
                    if proc.is_running():
                        still_running.append(proc)
                except:
                    pass

            if not still_running:
                break

            processes = still_running

        # Если процессы все еще работают, убиваем принудительно
        for proc in processes:
            try:
                proc.kill()
            except:
                pass

    # Запускаем заново
    try:
        cmd = f"cd {USERBOT_DIR} && {USERBOT_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Юзербот успешно перезапущен через инлайн-режим!"
            )

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Не удалось перезапустить юзербота через инлайн-режим. Проверьте логи."
            )

    except Exception as e:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Ошибка перезапуска через инлайн-режим: {str(e)}"
        )





async def stop_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка юзербота через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return
    await query.edit_message_text("🛑 Останавливаю юзербота...")

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()
            if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    if not processes:
        await query.edit_message_text("⚠️ Юзербот не был запущен")
        await show_main_menu(update, context)
        return

    for proc in processes:
        try:
            proc.terminate()
        except:
            pass

    timeout = 15
    start_time = time.time()

    while time.time() - start_time < timeout:
        await asyncio.sleep(2)
        still_running = []
        for proc in processes:
            try:
                if proc.is_running():
                    still_running.append(proc)
            except:
                pass

        if not still_running:
            await query.edit_message_text("✅ Юзербот корректно остановлен")
            await asyncio.sleep(2)
            await show_main_menu(update, context)
            return

        processes = still_running

    for proc in processes:
        try:
            proc.kill()
        except:
            pass

    await query.edit_message_text("✅ Юзербот остановлен (принудительно)")
    await asyncio.sleep(2)
    await show_main_menu(update, context)

async def install_requirements_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установка зависимостей через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return
    await query.edit_message_text("📦 Устанавливаю зависимости...")

    try:
        cmd = f"cd {USERBOT_DIR} && {VENV_PYTHON} -m pip install -r requirements.txt"

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)

        await process.wait()

        if process.returncode == 0:
            await query.edit_message_text("✅ Зависимости установлены успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await query.edit_message_text(f"❌ Ошибка установки:\n{error_output}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_management_menu(update, context)

async def update_heroku_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновление HerokuTL через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text("🔄 Обновляю HerokuTL...")

    try:
        cmd = f"{VENV_PYTHON} -m pip install heroku-tl-new -U"

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)

        await process.wait()

        if process.returncode == 0:
            await query.edit_message_text("✅ HerokuTL обновлен успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await query.edit_message_text(f"❌ Ошибка обновления:\n{error_output}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_management_menu(update, context)

async def debug_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика юзербота через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text("🐞 Выполняю диагностику...")

    diagnostic_messages = []

    if os.path.exists(VENV_PYTHON):
        diagnostic_messages.append("✅ Виртуальное окружение найдено")
    else:
        diagnostic_messages.append("❌ Виртуальное окружение не найдено")

    if os.path.exists(USERBOT_DIR):
        diagnostic_messages.append("✅ Директория юзербота найдена")
    else:
        diagnostic_messages.append("❌ Директория юзербота не найдена")

    is_running, start_time = get_userbot_status()
    if is_running:
        uptime = time.time() - start_time
        diagnostic_messages.append(f"✅ Юзербот запущен (Uptime: {int(uptime // 60)}m {int(uptime % 60)}s)")
    else:
        diagnostic_messages.append("❌ Юзербот не запущен")

    log_file_path = os.path.join(USERBOT_DIR, "userbot_output.log")
    if os.path.exists(log_file_path):
        file_size = os.path.getsize(log_file_path)
        diagnostic_messages.append(f"✅ Файл логов существует ({file_size} bytes)")
    else:
        diagnostic_messages.append("❌ Файл логов не существует")

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="management")]]
    await query.edit_message_text("\n".join(diagnostic_messages), reply_markup=InlineKeyboardMarkup(keyboard))

async def send_logs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, level: str):
    """Отправка логов через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text(f"📋 Подготавливаю логи уровня {level}...")

    if not os.path.exists(LOG_FILE):
        await query.edit_message_text("❌ Файл логов не найден")
        await show_logs_menu(update, context)
        return

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
            temp_path = temp_file.name

            if level == "ALL":
                with open(LOG_FILE, 'r') as log_file:
                    temp_file.write(log_file.read())
            else:
                with open(LOG_FILE, 'r') as log_file:
                    for line in log_file:
                        if re.search(f'\\b{level}\\b', line, re.IGNORECASE):
                            temp_file.write(line)

        file_size = os.path.getsize(temp_path)
        if file_size == 0:
            await query.edit_message_text(f"❌ Логи уровня {level} не найдены")
            os.unlink(temp_path)
            await show_logs_menu(update, context)
            return

        with open(temp_path, 'rb') as file:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=file,
                filename=f"logs-{level}.txt",
                caption=f"Логи уровня: {level}"
            )

        os.unlink(temp_path)
        await query.edit_message_text(f"✅ Логи уровня {level} отправлены")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка при обработке логов: {str(e)}")
        try:
            if 'temp_path' in locals():
                os.unlink(temp_path)
        except:
            pass

    await asyncio.sleep(2)
    await show_logs_menu(update, context)

async def open_logs_dir_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открытие папки логов через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    if not os.path.exists(USERBOT_DIR):
        await query.edit_message_text("❌ Директория юзербота не найдена")
        return

    try:
        process = await asyncio.create_subprocess_shell(
            f"cd {USERBOT_DIR} && ls -la",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            files_list = stdout.decode()[:4000]  # Ограничиваем длину
            await query.edit_message_text(f"📁 Содержимое папки логов:\n```\n{files_list}\n```", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Не удалось получить список файлов")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def toggle_debug_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение дебаг-режима через кнопку"""
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    if chat_id in DEBUG_CHATS:
        DEBUG_CHATS.discard(chat_id)
        await query.edit_message_text("❌ Дебаг-режим выключен")
    else:
        DEBUG_CHATS.add(chat_id)
        await query.edit_message_text("✅ Дебаг-режим включен")

    await asyncio.sleep(2)
    await show_settings_menu(update, context)

async def execute_terminal_command(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
    """Выполнение терминальной команды через кнопку"""
    query = update.callback_query
    await query.edit_message_text("🖥 Выполняю команду...")

    commands_map = {
        'free': 'free -h',
        'top': 'top -bn1',
        'ls': f'cd {USERBOT_DIR} && ls -la',
        'ps': 'ps aux | grep python',
        'update': f'{VENV_PYTHON} -m pip list --outdated',
        'clean': f'{VENV_PYTHON} -m pip cache purge'
    }

    if command not in commands_map:
        await query.edit_message_text("❌ Неизвестная команда")
        return

    try:
        cmd = commands_map[command]

        # Устанавливаем правильные переменные окружения как в текстовой команде
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~"),
            env=env
        )

        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        output = stdout.decode() + stderr.decode()

        if not output:
            output = "Команда выполнена (нет вывода)"

        # Обрезаем слишком длинный вывод
        if len(output) > 4000:
            output = output[:4000] + "\n... (вывод обрезан)"

        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="terminal_menu")]]
        await query.edit_message_text(f"```\n{output}\n```", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    except asyncio.TimeoutError:
        await query.edit_message_text("⏰ Таймаут выполнения команды")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def open_logs_dir_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открытие папки логов через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text("📁 Получаю список файлов...")

    if not os.path.exists(USERBOT_DIR):
        await query.edit_message_text("❌ Директория юзербота не найдена")
        return

    try:
        # Устанавливаем правильные переменные окружения
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            f"cd {USERBOT_DIR} && ls -la",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            files_list = stdout.decode()[:4000]  # Ограничиваем длину
            await query.edit_message_text(f"📁 Содержимое папки логов:\n```\n{files_list}\n```", parse_mode='Markdown')
        else:
            error_msg = stderr.decode()[:1000] if stderr else "Неизвестная ошибка"
            await query.edit_message_text(f"❌ Не удалось получить список файлов: {error_msg}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

async def ping_host_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, host: str):
    """Ping хоста через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    await query.edit_message_text(f"🌐 Пингую {host}...")

    try:
        process = await asyncio.create_subprocess_shell(
            f"ping -c 3 {host}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            result = stdout.decode()
            # Извлекаем время пинга из вывода
            ping_times = re.findall(r'time=(\d+\.?\d*) ms', result)
            if ping_times:
                avg_ping = sum(float(t) for t in ping_times) / len(ping_times)
                await query.edit_message_text(f"✅ {host} доступен\nСреднее время: {avg_ping:.1f} ms")
            else:
                await query.edit_message_text(f"✅ {host} доступен")
        else:
            await query.edit_message_text(f"❌ {host} недоступен")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_ping_menu(update, context)

async def add_me_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление пользователя через кнопку"""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    if user_id == OWNER_ID:
        await query.edit_message_text("❌ Вы уже являетесь владельцем")
        return

    USER_IDS.add(user_id)
    save_users(USER_IDS)  # Сохраняем изменения
    await query.edit_message_text("✅ Вы добавлены как пользователь")
    await asyncio.sleep(2)
    await show_users_menu(update, context)

async def list_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей через кнопку"""
    query = update.callback_query

    users_list = ["👥 Список пользователей:"]
    users_list.append(f"👑 Владелец: {OWNER_ID}")

    for uid in USER_IDS:
        if uid != OWNER_ID:
            users_list.append(f"👤 Пользователь: {uid}")

    users_list.append(f"\nВсего: {len(USER_IDS)} пользователей")

    keyboard = [
        [InlineKeyboardButton("🗑 Удалить пользователя", callback_data="delete_user")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="users_menu")]
    ]
    await query.edit_message_text("\n".join(users_list), reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    await show_main_menu(update, context)

async def install_requirements_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установка зависимостей через кнопку"""
    query = update.callback_query
    await query.edit_message_text("📦 Устанавливаю зависимости...")

    try:
        cmd = f"cd {USERBOT_DIR} && {VENV_PYTHON} -m pip install -r requirements.txt"

        # Устанавливаем правильные переменные окружения
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)

        await process.wait()

        if process.returncode == 0:
            await query.edit_message_text("✅ Зависимости установлены успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await query.edit_message_text(f"❌ Ошибка установки:\n{error_output}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_management_menu(update, context)

async def update_heroku_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновление HerokuTL через кнопку"""
    query = update.callback_query
    await query.edit_message_text("🔄 Обновляю HerokuTL...")

    try:
        cmd = f"{VENV_PYTHON} -m pip install heroku-tl-new -U"

        # Устанавливаем правильные переменные окружения
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)

        await process.wait()

        if process.returncode == 0:
            await query.edit_message_text("✅ HerokuTL обновлен успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await query.edit_message_text(f"❌ Ошибка обновления:\n{error_output}")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_management_menu(update, context)

async def ping_host_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, host: str):
    """Ping хоста через кнопку"""
    query = update.callback_query
    await query.edit_message_text(f"🌐 Пингую {host}...")
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ Нильзя жмакать на эти кнопачки", show_alert=True)
        return

    try:
        # Устанавливаем правильные переменные окружения
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            f"ping -c 3 {host}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            result = stdout.decode()
            # Извлекаем время пинга из вывода
            ping_times = re.findall(r'time=(\d+\.?\d*) ms', result)
            if ping_times:
                avg_ping = sum(float(t) for t in ping_times) / len(ping_times)
                await query.edit_message_text(f"✅ {host} доступен\nСреднее время: {avg_ping:.1f} ms")
            else:
                await query.edit_message_text(f"✅ {host} доступен")
        else:
            await query.edit_message_text(f"❌ {host} недоступен")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")

    await asyncio.sleep(2)
    await show_ping_menu(update, context)

async def update_heroku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновить HerokuTL (оригинальная команда)"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    await update.message.reply_text("🔄 Обновляю HerokuTL...")

    try:
        cmd = f"{VENV_PYTHON} -m pip install heroku-tl-new -U"

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)
            if DEBUG_CHATS:
                await send_debug_message(line, context.bot)

        await process.wait()

        if process.returncode == 0:
            await update.message.reply_text("✅ HerokuTL обновлен успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await update.message.reply_text(f"❌ Ошибка обновления:\n{error_output}")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def install_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить зависимости юзербота"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    await update.message.reply_text("📦 Устанавливаю зависимости...")

    try:
        cmd = f"cd {USERBOT_DIR} && {VENV_PYTHON} -m pip install -r requirements.txt"

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR
        )

        output_lines = []
        async for line in process.stdout:
            line = line.decode().strip()
            output_lines.append(line)
            if DEBUG_CHATS:
                await send_debug_message(line, context.bot)

        await process.wait()

        if process.returncode == 0:
            await update.message.reply_text("✅ Зависимости установлены успешно!")
        else:
            error_output = "\n".join(output_lines[-10:])
            await update.message.reply_text(f"❌ Ошибка установки:\n{error_output}")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def start_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск юзербота (оригинальная команда)"""
    user_id = update.effective_user.id

    if update.message.chat.type != "private" and not is_owner(user_id):
        await update.message.reply_text("❌ Эта команда доступна только создателю в группах")
        return

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    use_proxy = context.args and '--proxy' in context.args

    if use_proxy and not os.path.exists(PROXYCHAINS_PATH):
        await update.message.reply_text(f"❌ proxychains не найден по пути {PROXYCHAINS_PATH}")
        return

    is_running, _ = get_userbot_status()
    if is_running:
        await update.message.reply_text("⚠️ Юзербот уже запущен")
        return

    if not os.path.exists(VENV_PYTHON):
        await update.message.reply_text("❌ Виртуальное окружение не найдено")
        return

    cmd = f"cd {USERBOT_DIR} && {PROXY_CMD if use_proxy else USERBOT_CMD}"

    try:
        await update.message.reply_text("🔄 Запускаю юзербота...")

        log_file_path = os.path.join(USERBOT_DIR, "userbot_output.log")

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(3)

        is_running, _ = get_userbot_status()
        if is_running:
            await update.message.reply_text(f"✅ Юзербот запущен (PID: {process.pid})")

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            try:
                if os.path.exists(log_file_path):
                    with open(log_file_path, 'r') as f:
                        error_output = f.read().strip()
                        if error_output:
                            error_msg = error_output[-500:]
                            await update.message.reply_text(f"❌ Ошибка запуска: {error_msg}")
                        else:
                            await update.message.reply_text("❌ Юзербот не запустился (неизвестная ошибка)")
                else:
                    await update.message.reply_text("❌ Юзербот не запустился (не удалось прочитать логи)")
            except:
                await update.message.reply_text("❌ Юзербот не запустился (не удалось прочитать логи)")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def stop_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка юзербота (оригинальная команда)"""
    user_id = update.effective_user.id

    if update.message.chat.type != "private" and not is_owner(user_id):
        await update.message.reply_text("❌ Эта команда доступна только создателю в группах")
        return

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()
            if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    if not processes:
        await update.message.reply_text("⚠️ Юзербот не был запущен")
        return

    if DEBUG_CHATS:
        await send_debug_message("🛑 Останавливаю юзербота...", context.bot)

    for proc in processes:
        try:
            proc.terminate()
        except:
            pass

    await update.message.reply_text("⏳ Останавливаю юзербота (ожидаю завершения работы...)")

    timeout = 15
    start_time = time.time()

    while time.time() - start_time < timeout:
        await asyncio.sleep(2)
        still_running = []
        for proc in processes:
            try:
                if proc.is_running():
                    still_running.append(proc)
            except:
                pass

        if not still_running:
            if DEBUG_CHATS:
                await send_debug_message("✅ Юзербот корректно остановлен", context.bot)
            await update.message.reply_text("✅ Юзербот корректно остановлен")
            return

        processes = still_running

    for proc in processes:
        try:
            proc.kill()
        except:
            pass

    if DEBUG_CHATS:
        await send_debug_message("✅ Юзербот остановлен (принудительно)", context.bot)
    await update.message.reply_text("✅ Юзербот остановлен (принудительно)")

async def system_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    await update.message.reply_text(get_system_info())

async def ram_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    ram = psutil.virtual_memory()
    await update.message.reply_text(
        f"RAM: {ram.percent}%\n"
        f"Used: {ram.used // (1024**3)} GB\n"
        f"Total: {ram.total // (1024**3)} GB"
    )

async def cpu_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    cpu = psutil.cpu_percent(interval=1)
    await update.message.reply_text(f"CPU: {cpu}%")

async def disk_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    disk = psutil.disk_usage('/')
    await update.message.reply_text(
        f"Disk: {disk.percent}%\n"
        f"Used: {disk.used // (1024**3)} GB\n"
        f"Total: {disk.total // (1024**3)} GB"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    is_running, start_time = get_userbot_status()
    status_text = "✅ Запущен" if is_running else "❌ Остановлен"
    if is_running:
        uptime = time.time() - start_time
        status_text += f"\nUptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"
    await update.message.reply_text(status_text)

async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    system_uptime = time.time() - psutil.boot_time()
    text = f"System: {int(system_uptime // 3600)}h {int((system_uptime % 3600) // 60)}m"
    await update.message.reply_text(text)

async def uptime_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    is_running, start_time = get_userbot_status()
    if is_running:
        bot_uptime = time.time() - start_time
        text = f"Userbot: {int(bot_uptime // 3600)}h {int((bot_uptime % 3600) // 60)}m"
    else:
        text = "❌ Юзербот не запущен"
    await update.message.reply_text(text)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    host = context.args[0] if context.args else "open.spotify.com"
    try:
        result = await asyncio.create_subprocess_shell(
            f"ping -c 1 {host}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await result.communicate()

        if result.returncode == 0:
            await update.message.reply_text(f"✅ {host} доступен")
        else:
            await update.message.reply_text(f"❌ {host} недоступен")
    except asyncio.TimeoutError:
        await update.message.reply_text(f"⏰ Таймаут ping для {host}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def terminal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if update.message.chat.type != "private" and not is_owner(user_id):
        await update.message.reply_text("❌ Эта команда доступна только в ЛС")
        return

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    cmd = ' '.join(context.args)
    if not cmd:
        await update.message.reply_text("Введите команду")
        return

    try:
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/alina/.venv/bin:/home/alina/.local/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~"),
            env=env
        )

        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        output = stdout.decode() + stderr.decode()

        if not output:
            output = "Команда выполнена (нет вывода)"

        await update.message.reply_text(f"Результат:\n{output[:4000]}")
    except asyncio.TimeoutError:
        await update.message.reply_text("⏰ Таймаут выполнения команды")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")

async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    if not context.args:
        await update.message.reply_text("❌ Укажите уровень логов: /logs <ALL/WARNING/INFO/ERROR/DEBUG>")
        return

    log_level = context.args[0].upper()
    valid_levels = ["ALL", "WARNING", "INFO", "ERROR", "DEBUG"]

    if log_level not in valid_levels:
        await update.message.reply_text(f"❌ Неверный уровень. Допустимые: {', '.join(valid_levels)}")
        return

    if not os.path.exists(LOG_FILE):
        await update.message.reply_text("❌ Файл логов не найден")
        return

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
            temp_path = temp_file.name

            if log_level == "ALL":
                with open(LOG_FILE, 'r') as log_file:
                    temp_file.write(log_file.read())
            else:
                with open(LOG_FILE, 'r') as log_file:
                    for line in log_file:
                        if re.search(f'\\b{log_level}\\b', line, re.IGNORECASE):
                            temp_file.write(line)

        file_size = os.path.getsize(temp_path)
        if file_size == 0:
            await update.message.reply_text(f"❌ Логи уровня {log_level} не найдены")
            os.unlink(temp_path)
            return

        with open(temp_path, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename=f"logs-{log_level}.txt",
                caption=f"Логи уровня: {log_level}"
            )

        os.unlink(temp_path)

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при обработке логов: {str(e)}")
        try:
            if 'temp_path' in locals():
                os.unlink(temp_path)
        except:
            pass

async def safe_send_message(bot, chat_id, text, **kwargs):
    """Безопасная отправка сообщения с обработкой ошибок сети и форматирования"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Пробуем отправить с указанным parse_mode
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return True
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                # Если ошибка форматирования, пробуем без разметки
                if 'parse_mode' in kwargs:
                    kwargs_without_markdown = kwargs.copy()
                    kwargs_without_markdown.pop('parse_mode', None)
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, **kwargs_without_markdown)
                        return True
                    except Exception as fallback_error:
                        print(f"Ошибка при отправке без разметки: {fallback_error}")
                        return False
            elif "Message is not modified" in str(e):
                # Игнорируем эту ошибку
                return True
            else:
                print(f"BadRequest при отправке сообщения: {e}")
                return False
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"Ошибка сети при отправке сообщения, попытка {attempt + 1}/{max_retries}. Жду {wait_time} сек.")
                await asyncio.sleep(wait_time)
            else:
                print(f"Не удалось отправить сообщение после {max_retries} попыток: {e}")
                return False
        except Exception as e:
            print(f"Неожиданная ошибка при отправке сообщения: {e}")
            return False
    return False

async def handle_network_errors(func, *args, **kwargs):
    """Обработчик сетевых ошибок для любых функций"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"Сетевая ошибка в {func.__name__}, попытка {attempt + 1}/{max_retries}. Жду {wait_time} сек.")
                await asyncio.sleep(wait_time)
            else:
                print(f"Не удалось выполнить {func.__name__} после {max_retries} попыток: {e}")
                raise
        except RetryAfter as e:
            wait_time = e.retry_after
            print(f"Telegram просит подождать {wait_time} сек. перед повторной попыткой.")
            await asyncio.sleep(wait_time)
            if attempt < max_retries - 1:
                continue
            else:
                raise
        except BadRequest as e:
            print(f"Некорректный запрос в {func.__name__}: {e}")
            raise

async def send_startup_notification(application):
    """Отправляет уведомление о запуске бота с обработкой ошибок"""
    try:
        # Используем asyncio.wait_for вместо handle_network_errors
        bot_info = await asyncio.wait_for(application.bot.get_me(), timeout=10)
        message = f"🤖 Бот {bot_info.first_name} запущен и готов к работе!\n\n" \
                 f"Используйте /start для просмотра команд"

        sent_count = 0
        for user_id in USER_IDS.copy():
            try:
                success = await safe_send_message(application.bot, user_id, message)
                if success:
                    sent_count += 1
                    print(f"Уведомление отправлено пользователю {user_id}")
                else:
                    print(f"Не удалось отправить уведомление пользователю {user_id}")
            except Exception as e:
                print(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

        print(f"Уведомления отправлены {sent_count} пользователям из {len(USER_IDS)}")
    except asyncio.TimeoutError:
        print("Таймаут при получении информации о боте для уведомления о запуске")
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")

async def check_connection_health(bot):
    """Проверяет здоровье соединения с Telegram"""
    try:
        # Простая проверка - получаем информацию о боте
        await handle_network_errors(bot.get_me, timeout=10)
        return True
    except Exception as e:
        print(f"Проверка соединения не удалась: {e}")
        return False

async def restart_application(application):
    """Безопасно перезапускает приложение"""
    global reconnect_attempts, is_reconnecting

    if is_reconnecting:
        print("Переподключение уже выполняется...")
        return

    is_reconnecting = True

    try:
        print("Останавливаю приложение...")
        if application.updater and application.updater.running:
            await application.updater.stop()

        if application.running:
            await application.stop()

        if application.running:
            await application.shutdown()

        print("Запускаю приложение заново...")
        await application.initialize()
        await application.start()

        if application.updater:
            await application.updater.start_polling(
                poll_interval=1.0,
                timeout=10.0,
                drop_pending_updates=True
            )

        # Сбрасываем счетчик попыток при успешном переподключении
        reconnect_attempts = 0
        print("Приложение успешно перезапущено!")

        # Отправляем уведомление о восстановлении соединения
        await send_reconnection_notification(application)

    except Exception as e:
        print(f"Ошибка при перезапуске приложения: {e}")
        reconnect_attempts += 1
    finally:
        is_reconnecting = False


async def connection_watchdog(application):
    """Фоновая задача для мониторинга соединения"""
    global application_instance
    application_instance = application

    check_interval = 200  # Проверяем соединение каждые 30 секунд
    consecutive_failures = 0
    max_consecutive_failures = 3

    while True:
        try:
            await asyncio.sleep(check_interval)

            is_healthy = await check_connection_health(application.bot)

            if is_healthy:
                consecutive_failures = 0
                continue

            consecutive_failures += 1
            print(f"Проблемы с соединением. Неудачных проверок подряд: {consecutive_failures}")

            if consecutive_failures >= max_consecutive_failures:
                print("Критическое количество неудачных проверок. Инициирую переподключение...")
                await restart_application(application)
                consecutive_failures = 0

        except Exception as e:
            print(f"Ошибка в connection_watchdog: {e}")
            consecutive_failures += 1

async def robust_polling(application):
    """Устойчивый запуск polling с обработкой ошибок"""
    global reconnect_attempts

    while reconnect_attempts < RECONNECT_CONFIG['max_retries']:
        try:
            print("Запускаю polling...")
            await application.updater.start_polling(
                poll_interval=1.0,
                timeout=20.0,  # Увеличиваем таймаут
                drop_pending_updates=True
            )

            # Если polling запущен успешно, сбрасываем счетчик
            reconnect_attempts = 0
            print("Polling успешно запущен!")

            # Запускаем watchdog для мониторинга соединения
            asyncio.create_task(connection_watchdog(application))

            # Бесконечный цикл ожидания
            while True:
                await asyncio.sleep(3600)

        except (TimedOut, NetworkError) as e:
            reconnect_attempts += 1
            current_delay = min(
                RECONNECT_CONFIG['retry_delay'] * (RECONNECT_CONFIG['backoff_factor'] ** (reconnect_attempts - 1)),
                RECONNECT_CONFIG['max_delay']
            )

            print(f"Сетевая ошибка при polling (попытка {reconnect_attempts}/{RECONNECT_CONFIG['max_retries']}): {e}")
            print(f"Повторная попытка через {current_delay} сек.")

            await asyncio.sleep(current_delay)

        except Exception as e:
            print(f"Критическая ошибка при polling: {e}")
            reconnect_attempts += 1

            if reconnect_attempts >= RECONNECT_CONFIG['max_retries']:
                print("Достигнуто максимальное количество попыток. Завершаю работу.")
                raise

            current_delay = min(
                RECONNECT_CONFIG['retry_delay'] * (RECONNECT_CONFIG['backoff_factor'] ** (reconnect_attempts - 1)),
                RECONNECT_CONFIG['max_delay']
            )

            print(f"Повторная попытка через {current_delay} сек.")
            await asyncio.sleep(current_delay)



async def start_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить дебаг-режим"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    DEBUG_CHATS.add(update.effective_chat.id)
    await update.message.reply_text("✅ Дебаг-режим включен. Все сообщения юзербота будут пересылаться сюда.")

async def stop_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выключить дебаг-режим"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    DEBUG_CHATS.discard(update.effective_chat.id)
    await update.message.reply_text("❌ Дебаг-режим выключен.")

async def debug_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика юзербота (оригинальная команда)"""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    diagnostic_messages = []

    if os.path.exists(VENV_PYTHON):
        diagnostic_messages.append("✅ Виртуальное окружение найдено")
    else:
        diagnostic_messages.append("❌ Виртуальное окружение не найдено")

    if os.path.exists(USERBOT_DIR):
        diagnostic_messages.append("✅ Директория юзербота найдена")
    else:
        diagnostic_messages.append("❌ Директория юзербота не найдена")

    is_running, start_time = get_userbot_status()
    if is_running:
        uptime = time.time() - start_time
        diagnostic_messages.append(f"✅ Юзербот запущен (Uptime: {int(uptime // 60)}m {int(uptime % 60)}s)")
    else:
        diagnostic_messages.append("❌ Юзербот не запущен")

    log_file_path = os.path.join(USERBOT_DIR, "userbot_output.log")
    if os.path.exists(log_file_path):
        file_size = os.path.getsize(log_file_path)
        diagnostic_messages.append(f"✅ Файл логов существует ({file_size} bytes)")

        try:
            with open(log_file_path, 'r') as f:
                lines = f.readlines()
                if lines:
                    last_lines = lines[-5:]
                    diagnostic_messages.append("Последние логи:")
                    diagnostic_messages.extend([f"  {line.strip()}" for line in last_lines])
        except Exception as e:
            diagnostic_messages.append(f"❌ Ошибка чтения логов: {e}")
    else:
        diagnostic_messages.append("❌ Файл логов не существует")

    await update.message.reply_text("\n".join(diagnostic_messages))

async def get_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.reply_text("❌ Эта команда доступна только в ЛС")
        return

    if not is_owner(update.effective_user.id):
        return

    user_id = update.effective_user.id
    USER_IDS.add(user_id)
    save_users(USER_IDS)  # Сохраняем изменения
    await update.message.reply_text("✅ Вы добавлены как пользователь")

async def get_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.reply_text("❌ Эта команда доступна только в ЛС")
        return

    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return

    if context.args:
        try:
            user_id = int(context.args[0])
            USER_IDS.add(user_id)
            save_users(USER_IDS)  # Сохраняем изменения
            await update.message.reply_text(f"✅ Пользователь {user_id} добавлен")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID пользователя")
    else:
        await update.message.reply_text("❌ Укажите ID пользователя: /get_user <id>")

# Мониторинг логов юзербота
async def monitor_userbot_logs(bot):
    """Мониторит вывод юзербота и отправляет в дебаг-чаты"""
    log_file_path = os.path.join(USERBOT_DIR, "userbot_output.log")

    for i in range(30):
        if os.path.exists(log_file_path):
            break
        await asyncio.sleep(1)

    if not os.path.exists(log_file_path):
        await send_debug_message("❌ Файл логов не создался", bot)
        return

    last_position = 0
    buffer = []
    buffer_size = 10  # Количество строк в одном сообщении
    last_flush_time = time.time()
    flush_interval = 5  # Секунды между отправками

    while True:
        try:
            if not os.path.exists(log_file_path):
                await send_debug_message("⚠️ Файл логов удален", bot)
                break

            with open(log_file_path, 'r') as f:
                f.seek(last_position)
                new_lines = f.readlines()
                last_position = f.tell()

                for line in new_lines:
                    line = line.strip()
                    if line:
                        buffer.append(line)

                        # Отправляем если буфер заполнен или прошло достаточно времени
                        current_time = time.time()
                        if (len(buffer) >= buffer_size or
                            current_time - last_flush_time >= flush_interval):
                            if buffer:
                                combined = "\n".join(buffer[-buffer_size:])  # Берем последние N строк
                                await send_debug_message(combined, bot)
                                buffer.clear()
                                last_flush_time = current_time

            is_running, _ = get_userbot_status()
            if not is_running:
                # При завершении отправляем оставшиеся логи
                if buffer:
                    combined = "\n".join(buffer)
                    await send_debug_message(f"🔴 Юзербот завершил работу\nПоследние логи:\n{combined}", bot)
                else:
                    await send_debug_message("🔴 Юзербот завершил работу", bot)
                break

            await asyncio.sleep(2)

        except Exception as e:
            print(f"Ошибка чтения логов: {e}")
            await asyncio.sleep(5)


async def handle_chosen_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбранные инлайн-результаты"""
    chosen_result = update.chosen_inline_result
    result_id = chosen_result.result_id
    user_id = chosen_result.from_user.id

    if not is_owner(user_id):
        return

    # Запуск юзербота
    if result_id == "start_userbot":
        await execute_inline_start_userbot(chosen_result, context)

    # Остановка юзербота
    elif result_id == "stop_userbot":
        await execute_inline_stop_userbot(chosen_result, context)

    # Перезапуск юзербота
    elif result_id == "restart_userbot":
        await execute_inline_restart_userbot(chosen_result, context)

async def execute_inline_start_userbot(chosen_result, context):
    """Выполняет запуск юзербота из инлайн-режима"""
    try:
        # Отправляем уведомление о начале операции
        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text="🔄 Запускаю юзербота через инлайн-режим..."
        )

        # Проверяем, не запущен ли уже юзербот
        is_running, _ = get_userbot_status()
        if is_running:
            await context.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="⚠️ Юзербот уже запущен"
            )
            return

        # Запускаем юзербота
        cmd = f"cd {USERBOT_DIR} && {USERBOT_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await context.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="✅ Юзербот успешно запущен через инлайн-режим!"
            )

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await context.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="❌ Не удалось запустить юзербота через инлайн-режим. Проверьте логи."
            )

    except Exception as e:
        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text=f"❌ Ошибка запуска через инлайн-режим: {str(e)}"
        )

async def execute_inline_stop_userbot(chosen_result, context):
    """Выполняет остановку юзербота из инлайн-режима"""
    try:
        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text="🛑 Останавливаю юзербота через инлайн-режим..."
        )

        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline'] or []
                cmdline_str = ' '.join(cmdline).lower()
                if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                    processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                continue

        if not processes:
            await context.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="⚠️ Юзербот не был запущен"
            )
            return

        for proc in processes:
            try:
                proc.terminate()
            except:
                pass

        timeout = 15
        start_time = time.time()

        while time.time() - start_time < timeout:
            await asyncio.sleep(2)
            still_running = []
            for proc in processes:
                try:
                    if proc.is_running():
                        still_running.append(proc)
                except:
                    pass

            if not still_running:
                await context.bot.send_message(
                    chat_id=chosen_result.from_user.id,
                    text="✅ Юзербот корректно остановлен через инлайн-режим"
                )
                return

            processes = still_running

        for proc in processes:
            try:
                proc.kill()
            except:
                pass

        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text="✅ Юзербот остановлен (принудительно) через инлайн-режим"
        )

    except Exception as e:
        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text=f"❌ Ошибка остановки через инлайн-режим: {str(e)}"
        )

async def execute_inline_restart_userbot(chosen_result, context):
    """Выполняет перезапуск юзербота из инлайн-режима"""
    try:
        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text="🔄 Перезапускаю юзербота через инлайн-режим..."
        )

        # Сначала останавливаем
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline'] or []
                cmdline_str = ' '.join(cmdline).lower()
                if ('python' in cmdline_str and 'heroku' in cmdline_str and '--no-web' in cmdline_str):
                    processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                continue

        if processes:
            for proc in processes:
                try:
                    proc.terminate()
                except:
                    pass

            # Ждем завершения
            timeout = 10
            start_time = time.time()
            while time.time() - start_time < timeout:
                await asyncio.sleep(2)
                still_running = []
                for proc in processes:
                    try:
                        if proc.is_running():
                            still_running.append(proc)
                    except:
                        pass

                if not still_running:
                    break

                processes = still_running

            # Если процессы все еще работают, убиваем принудительно
            for proc in processes:
                try:
                    proc.kill()
                except:
                    pass

        # Запускаем заново
        cmd = f"cd {USERBOT_DIR} && {USERBOT_CMD}"

        env = os.environ.copy()
        env['GIT_PYTHON_REFRESH'] = 'quiet'
        env['PATH'] = '/usr/bin:/bin:/usr/local/bin:/home/alina/.venv/bin'

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=USERBOT_DIR,
            env=env
        )

        await asyncio.sleep(5)

        is_running, _ = get_userbot_status()
        if is_running:
            await context.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="✅ Юзербот успешно перезапущен через инлайн-режим!"
            )

            global monitor_task
            if DEBUG_CHATS:
                if monitor_task:
                    monitor_task.cancel()
                monitor_task = asyncio.create_task(monitor_userbot_logs(context.bot))
        else:
            await context.bot.send_message(
                chat_id=chosen_result.from_user.id,
                text="❌ Не удалось перезапустить юзербота через инлайн-режим. Проверьте логи."
            )

    except Exception as e:
        await context.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text=f"❌ Ошибка перезапуска через инлайн-режим: {str(e)}"
        )
# Инлайн-режим
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.inline_query.from_user.id):
        return

    query = update.inline_query.query.lower().strip()
    results = []

    # Статус юзербота
    if query.startswith("status") or "status" in query or not query:
        is_running, start_time = get_userbot_status()
        status_text = "✅ Запущен" if is_running else "❌ Остановлен"
        if is_running:
            uptime = time.time() - start_time
            status_text += f"\nUptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"

        results.append(InlineQueryResultArticle(
            id="status",
            title="Userbot Status",
            input_message_content=InputTextMessageContent(status_text),
            description="Статус юзербота"
        ))

    # Запуск юзербота (только для владельца)
    if (query.startswith("start") or "start" in query or not query) and is_owner(update.inline_query.from_user.id):
        is_running, _ = get_userbot_status()
        if not is_running:
            results.append(InlineQueryResultArticle(
                id="start_userbot",
                title="Start Userbot",
                input_message_content=InputTextMessageContent("🔄 Запускаю юзербота..."),
                description="Запустить юзербота"
            ))

    # Остановка юзербота (только для владельца)
    if (query.startswith("stop") or "stop" in query or not query) and is_owner(update.inline_query.from_user.id):
        is_running, _ = get_userbot_status()
        if is_running:
            results.append(InlineQueryResultArticle(
                id="stop_userbot",
                title="Stop Userbot",
                input_message_content=InputTextMessageContent("🛑 Останавливаю юзербота..."),
                description="Остановить юзербота"
            ))

    # Перезапуск юзербота (только для владельца)
    if (query.startswith("restart") or "restart" in query) and is_owner(update.inline_query.from_user.id):
        results.append(InlineQueryResultArticle(
            id="restart_userbot",
            title="Restart Userbot",
            input_message_content=InputTextMessageContent("🔄 Перезапускаю юзербота..."),
            description="Перезапустить юзербота"
        ))

    # Информация о системе
    if query.startswith("info") or "info" in query or not query:
        info_text = get_system_info()
        results.append(InlineQueryResultArticle(
            id="info",
            title="System Info",
            input_message_content=InputTextMessageContent(info_text),
            description="Информация о системе"
        ))

    # Если нет результатов для запроса, показываем основные опции
    if not results and query:
        results.append(InlineQueryResultArticle(
            id="no_results",
            title="Ничего не найдено",
            input_message_content=InputTextMessageContent(f"❌ Не найдено команд для: {query}"),
            description="Попробуйте другую команду"
        ))

    await update.inline_query.answer(results, cache_time=1, is_personal=True)

# Функция для отправки уведомлений при запуске
async def send_startup_notification(application):
    """Отправляет уведомление о запуске бота всем авторизованным пользователям"""
    try:
        bot_info = await application.bot.get_me()
        message = f"🤖 Бот {bot_info.first_name} запущен и готов к работе!\n\n" \
                 f"Используйте /menu для просмотра меню"

        for user_id in USER_IDS:
            try:
                await application.bot.send_message(chat_id=user_id, text=message)
                print(f"Уведомление отправлено пользователю {user_id}")
            except Exception as e:
                print(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")

async def main():
    """Главная асинхронная функция"""
    global application_instance

    print("Инициализация бота...")

    # Создаем application
    application = Application.builder()\
        .token(BOT_TOKEN)\
        .connect_timeout(30.0)\
        .read_timeout(30.0)\
        .write_timeout(30.0)\
        .pool_timeout(30.0)\
        .build()

    application_instance = application

    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("start_userbot", start_userbot))
    application.add_handler(CommandHandler("stop_userbot", stop_userbot))
    application.add_handler(CommandHandler("restart_userbot", restart_userbot))
    application.add_handler(CommandHandler("restart_bot", restart_bot))
    application.add_handler(CommandHandler("install_requirements", install_requirements))
    application.add_handler(CommandHandler("update_heroku", update_heroku))
    application.add_handler(CommandHandler("info", system_info))
    application.add_handler(CommandHandler("detailed_info", detailed_info))
    application.add_handler(CommandHandler("ram", ram_info))
    application.add_handler(CommandHandler("cpu", cpu_info))
    application.add_handler(CommandHandler("disk", disk_info))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("uptime", uptime))
    application.add_handler(CommandHandler("uptime_userbot", uptime_userbot))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("terminal", terminal))
    application.add_handler(CommandHandler("logs", logs))
    application.add_handler(CommandHandler("debug_on", start_debug))
    application.add_handler(CommandHandler("debug_off", stop_debug))
    application.add_handler(CommandHandler("debug_userbot", debug_userbot))
    application.add_handler(CommandHandler("get_owner", get_owner))
    application.add_handler(CommandHandler("get_user", get_user))
    application.add_handler(CommandHandler("del_user", del_user))
    application.add_handler(CommandHandler("check_updates", check_updates))
    application.add_handler(CommandHandler("update_bot", update_bot))
    application.add_handler(CommandHandler("connection_status", connection_status))


    # Обработчик кнопок
    application.add_handler(CallbackQueryHandler(button_handler))

    # Инлайн-режим
    application.add_handler(InlineQueryHandler(inline_query))

    # Обработчик выбранных инлайн-результатов
    application.add_handler(ChosenInlineResultHandler(handle_chosen_inline))

    application.add_error_handler(error_handler)

    print("Бот запускается...")

    try:
        # Инициализируем приложение
        await application.initialize()

        # Запускаем приложение
        await application.start()
        print("Бот успешно запущен")

        # Загружаем пользователей при старте
        global USER_IDS
        USER_IDS = load_users()
        print(f"Загружено {len(USER_IDS)} пользователей")

        # Запускаем polling
        await application.updater.start_polling(
            poll_interval=1.0,
            timeout=20.0,
            drop_pending_updates=True
        )
        print("Polling запущен")

        # Отправляем уведомление о запуске
        await send_startup_notification(application)

        # Бесконечный цикл для поддержания работы бота
        while True:
            await asyncio.sleep(3600)  # Спим 1 час

    except Exception as e:
        print(f"Критическая ошибка в main: {e}")
        # Ждем перед перезапуском
        await asyncio.sleep(10)

    finally:
        print("Завершаем работу бота...")
        try:
            # Останавливаем updater
            if application.updater and application.updater.running:
                await application.updater.stop()

            # Останавливаем приложение
            if application.running:
                await application.stop()

            # Завершаем работу приложения
            if hasattr(application, '_initialized') and application._initialized:
                await application.shutdown()

        except Exception as e:
            print(f"Ошибка при завершении работы: {e}")

if __name__ == "__main__":
    try:
        import sys
        # Запускаем главную функцию
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен пользователем")
    except Exception as e:
        print(f"Критическая ошибка: {e}")
        # Перезапускаем через systemd
        sys.exit(1)

