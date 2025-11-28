import os
import time
import os
import time
import subprocess
import psutil
import tempfile
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    Application, CommandHandler, ContextTypes, InlineQueryHandler, CallbackQueryHandler
)
from telegram.error import TimedOut, NetworkError

# Конфигурация
BOT_TOKEN = "ТУТ_BOT_TOKEN"
OWNER_ID = # ваш айди
USER_IDS = {}
USERBOT_DIR = os.path.expanduser("~/Heroku-dev") # поменяйте на свою директорию
VENV_PYTHON = "/home/alina/.venv/bin/python" # путь до питона
USERBOT_CMD = f"{VENV_PYTHON} -m heroku --no-web" # как будет запускатся
PROXYCHAINS_PATH = "/usr/bin/proxychains" # прокси если есть
PROXY_CMD = f"{PROXYCHAINS_PATH} {VENV_PYTHON} -m heroku --no-web" # проксе
LOG_FILE = os.path.join(USERBOT_DIR, "heroku.log") # логи


DEBUG_CHATS = set() # чаты в которых будет делатся дебаг
monitor_task = None


def is_owner(user_id):
    return user_id == OWNER_ID

def is_user(user_id):
    return user_id in USER_IDS or is_owner(user_id)


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

    message_text = f"🤖 **Главное меню**\n\n📊 Статус юзербота: {status_text}\n\nВыберите действие:"

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления"""
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

    await update.callback_query.edit_message_text(
        "🔧 **Меню управления**\n\nДополнительные функции управления юзерботом:",
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
    help_text = """
🤖 **Бот мониторинга системы и юзербота**

**Основные команды:**
/menu - Главное меню
/start_userbot - Запустить юзербота
/stop_userbot - Остановить юзербота
/status - Статус юзербота
/info - Информация о системе

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

Используйте кнопки меню для удобного управления!
"""

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_user(user_id):
        await query.edit_message_text("❌ Доступ запрещен")
        return

    data = query.data

    if data == "main_menu":
        await show_main_menu(update, context)

    elif data == "status":
        is_running, start_time = get_userbot_status()
        status_text = "✅ Запущен" if is_running else "❌ Остановлен"
        if is_running:
            uptime = time.time() - start_time
            status_text += f"\n⏱ Uptime: {int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"

        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="status"), InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
        await query.edit_message_text(f"📊 **Статус юзербота:**\n\n{status_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "system_info":
        info = get_system_info()
        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="system_info"), InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
        await query.edit_message_text(f"🖥 **Информация о системе:**\n\n{info}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    # Управление юзерботом, вазилиновое дрисло
    elif data == "start_userbot":
        await start_userbot_callback(update, context)

    elif data == "start_proxy":
        await start_userbot_proxy_callback(update, context)

    elif data == "stop_userbot":
        await stop_userbot_callback(update, context)

    elif data == "management":
        await show_management_menu(update, context)

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

    # на стройки
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

    # помощь (не поможет)
    elif data == "help":
        await show_help(update, context)

# функции-обработчики для кнопок
async def start_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск юзербота через кнопку"""
    query = update.callback_query
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

async def stop_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка юзербота через кнопку"""
    query = update.callback_query
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

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
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

async def ping_host_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, host: str):
    """Ping хоста через кнопку"""
    query = update.callback_query
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

    if user_id == OWNER_ID:
        await query.edit_message_text("❌ Вы уже являетесь владельцем")
        return

    USER_IDS.add(user_id)
    await query.edit_message_text("✅ Вы добавлены как пользователь")
    await asyncio.sleep(2)
    await show_users_menu(update, context)

async def list_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей через кнопку"""
    query = update.callback_query

    users_list = ["👥 Список пользователей:"]
    users_list.append(f"👑 Владелец: {OWNER_ID}")

    for user_id in USER_IDS:
        if user_id != OWNER_ID:
            users_list.append(f"👤 Пользователь: {user_id}")

    users_list.append(f"\nВсего: {len(USER_IDS)} пользователей")

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="users_menu")]]
    await query.edit_message_text("\n".join(users_list), reply_markup=InlineKeyboardMarkup(keyboard))

# Оригинальные функции команд (для обработки текстовых команд)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        return
    await show_main_menu(update, context)

async def install_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить зависимости юзербота (оригинальная команда)"""
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
    USER_IDS.add(update.effective_user.id)
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
                        await send_debug_message(line, bot)

            is_running, _ = get_userbot_status()
            if not is_running:
                await send_debug_message("🔴 Юзербот завершил работу", bot)
                break

            await asyncio.sleep(2)

        except Exception as e:
            print(f"Ошибка чтения логов: {e}")
            await asyncio.sleep(5)

# Инлайн-режим
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.inline_query.from_user.id):
        return

    query = update.inline_query.query.lower().strip()
    results = []

    if query.startswith("ping"):
        host = query[4:].strip() or "open.spotify.com"
        try:
            process = await asyncio.create_subprocess_shell(
                f"ping -c 1 {host}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                status_text = f"✅ {host} доступен"
            else:
                status_text = f"❌ {host} недоступен"
        except:
            status_text = f"❌ Ошибка ping для {host}"

        results.append(InlineQueryResultArticle(
            id="ping",
            title=f"Ping {host}",
            input_message_content=InputTextMessageContent(status_text),
            description=f"Результат ping: {host}"
        ))

    elif query == "info" or "info" in query:
        info_text = get_system_info()
        results.append(InlineQueryResultArticle(
            id="info",
            title="System Info",
            input_message_content=InputTextMessageContent(info_text),
            description="Информация о системе"
        ))

    elif query == "uptime" or "uptime" in query:
        system_uptime = time.time() - psutil.boot_time()
        uptime_text = f"System: {int(system_uptime // 3600)}h {int((system_uptime % 3600) // 60)}m"
        results.append(InlineQueryResultArticle(
            id="uptime",
            title="System Uptime",
            input_message_content=InputTextMessageContent(uptime_text),
            description="Аптайм системы"
        ))

    elif query == "ram" or "ram" in query:
        ram = psutil.virtual_memory()
        ram_text = f"RAM: {ram.percent}%\nUsed: {ram.used // (1024**3)} GB\nTotal: {ram.total // (1024**3)} GB"
        results.append(InlineQueryResultArticle(
            id="ram",
            title="RAM Info",
            input_message_content=InputTextMessageContent(ram_text),
            description="Информация о памяти"
        ))

    elif query == "cpu" or "cpu" in query:
        cpu = psutil.cpu_percent(interval=1)
        cpu_text = f"CPU: {cpu}%"
        results.append(InlineQueryResultArticle(
            id="cpu",
            title="CPU Info",
            input_message_content=InputTextMessageContent(cpu_text),
            description="Загрузка процессора"
        ))

    elif (query == "start-userbot" or "start-userbot" in query) and is_owner(update.inline_query.from_user.id):
        results.append(InlineQueryResultArticle(
            id="start-userbot",
            title="Start Userbot",
            input_message_content=InputTextMessageContent("/start_userbot"),
            description="Запустить юзербота"
        ))

    elif not query:
        results.extend([
            InlineQueryResultArticle(
                id="info",
                title="System Info",
                input_message_content=InputTextMessageContent(get_system_info()),
                description="Информация о системе"
            ),
            InlineQueryResultArticle(
                id="ping",
                title="Ping open.spotify.com",
                input_message_content=InputTextMessageContent("Проверка доступности..."),
                description="Проверить доступность"
            ),
            InlineQueryResultArticle(
                id="uptime",
                title="System Uptime",
                input_message_content=InputTextMessageContent(f"System: {int((time.time() - psutil.boot_time()) // 3600)}h {int(((time.time() - psutil.boot_time()) % 3600) // 60)}m"),
                description="Аптайм системы"
            )
        ])

    await update.inline_query.answer(results, cache_time=1)

# Функция для отправки уведомлений при запуске
async def send_startup_notification(application):
    """Отправляет уведомление о запуске бота всем авторизованным пользователям"""
    try:
        bot_info = await application.bot.get_me()
        message = f"🤖 Бот {bot_info.first_name} запущен и готов к работе!\n\n" \
                 f"Используйте /start для просмотра команд"

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
    application = Application.builder()\
        .token(BOT_TOKEN)\
        .connect_timeout(30.0)\
        .read_timeout(30.0)\
        .write_timeout(30.0)\
        .pool_timeout(30.0)\
        .build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("start_userbot", start_userbot))
    application.add_handler(CommandHandler("stop_userbot", stop_userbot))
    application.add_handler(CommandHandler("install_requirements", install_requirements))
    application.add_handler(CommandHandler("update_heroku", update_heroku))
    application.add_handler(CommandHandler("info", system_info))
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

    # Обработчик кнопок
    application.add_handler(CallbackQueryHandler(button_handler))

    # Инлайн-режим
    application.add_handler(InlineQueryHandler(inline_query))

    print("Бот запускается...")

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            poll_interval=1.0,
            timeout=10.0,
            drop_pending_updates=True
        )

        await send_startup_notification(application)
        print("Бот успешно запущен!")

        # Бесконечный цикл ожидания
        while True:
            await asyncio.sleep(3600)  # Спим 1 час

    except (TimedOut, NetworkError) as e:
        print(f"Ошибка подключения: {e}")
    except Exception as e:
        print(f"Критическая ошибка: {e}")
    finally:
        if application.running:
            await application.stop()
        if application.updater.running:
            await application.updater.stop()

if __name__ == "__main__":
    asyncio.run(main())
