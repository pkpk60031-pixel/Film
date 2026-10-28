import sqlite3
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.exceptions import Unauthorized

from config import BOT_TOKEN, ADMIN_IDS, CHANNEL_ID

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

DB_NAME = "bot.db"


def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            text TEXT,
            image_id TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    set_setting("welcome_message", "Привет! Напиши код фильма или сериала, например: 5464")
    set_setting("channel_id", CHANNEL_ID or "")
    set_setting("require_subscription", "0")

    for user_id in ADMIN_IDS:
        ensure_admin(user_id, "", "")


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute("""
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return row["value"]


def ensure_user(user: types.User):
    if not user:
        return
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO users(user_id, username, first_name, created_at)
        VALUES (?, ?, ?, ?)
    """, (user.id, user.username or "", user.full_name or "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def ensure_admin(user_id: int, username: str = "", first_name: str = ""):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO admin_users(user_id, username, first_name, created_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, username or "", first_name or "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def remove_admin(user_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM admin_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True

    conn = get_conn()
    row = conn.execute("SELECT 1 FROM admin_users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def get_content_by_code(code: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM content WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row


def get_user_count():
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    conn.close()
    return row["c"] if row else 0


def get_content_count():
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM content").fetchone()
    conn.close()
    return row["c"] if row else 0


def get_today_users():
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) AS c FROM users
        WHERE date(created_at) = date('now')
    """).fetchone()
    conn.close()
    return row["c"] if row else 0


def get_channel_chat_id(channel_value: str):
    if not channel_value:
        return None

    value = channel_value.strip()

    # Уже ID: -1001234567890
    if value.isdigit() or value.startswith("-100"):
        return value

    # username: @channelname
    if value.startswith("@"):
        value = value[1:]

    for candidate in [value, f"@{value}"]:
        try:
            chat = bot.get_chat(candidate)
            return str(chat.id)
        except Exception:
            pass

    return None


def bot_in_channel(channel_value: str) -> bool:
    chat_id = get_channel_chat_id(channel_value)
    if not chat_id:
        return False

    try:
        member = bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
        return member.status in ("administrator", "creator", "member")
    except Exception:
        return False


def check_subscription(user_id: int) -> bool:
    channel_value = get_setting("channel_id", "").strip()
    if not channel_value:
        return True

    chat_id = get_channel_chat_id(channel_value)
    if not chat_id:
        return False

    try:
        member = bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def build_message(item):
    text = f"<b>{item['text']}</b>"
    return text


def main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        KeyboardButton("🔎 Поиск по коду"),
        KeyboardButton("➕ Добавить")
    )
    return kb


def admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        KeyboardButton("➕ Добавить"),
        KeyboardButton("🗑 Удалить")
    )
    kb.row(
        KeyboardButton("📋 Список"),
        KeyboardButton("📣 Рассылка")
    )
    kb.row(
        KeyboardButton("💬 Приветствие"),
        KeyboardButton("📢 Канал")
    )
    kb.row(
        KeyboardButton("🔒 Подписка"),
        KeyboardButton("📊 Статистика")
    )
    kb.row(
        KeyboardButton("👤 Админы"),
        KeyboardButton("⚙️ Настройки")
    )
    kb.row(
        KeyboardButton("🔙 Назад")
    )
    return kb


class AddContentForm(StatesGroup):
    code = State()
    text = State()
    photo = State()


class DeleteContentForm(StatesGroup):
    code = State()


class SetChannelForm(StatesGroup):
    channel_id = State()


class SendBroadcastForm(StatesGroup):
    text = State()


class EditWelcomeForm(StatesGroup):
    text = State()


class AddAdminForm(StatesGroup):
    user_id = State()


@dp.message_handler(commands=["start", "menu"])
async def start_command(message: types.Message):
    ensure_user(message.from_user)

    if is_admin(message.from_user.id):
        await message.answer("Админ-панель готова.", reply_markup=admin_keyboard())
        return

    require_subscription = get_setting("require_subscription", "0") == "1"
    if require_subscription and not check_subscription(message.from_user.id):
        channel_value = get_setting("channel_id", "").strip()
        if not channel_value:
            await message.answer("Подписка включена, но канал не указан. Обратитесь к администратору.")
            return

        chat_id = get_channel_chat_id(channel_value)
        if not chat_id:
            await message.answer("Канал не найден. Проверьте ID канала или username.", reply_markup=admin_keyboard())
            return

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("Канал", url=f"https://t.me/{channel_value.replace('@','')}"))
        kb.add(InlineKeyboardButton("Проверить подписку ✅", callback_data="check_sub"))
        await message.answer("Сначала подпишитесь на канал.", reply_markup=kb)
        return

    welcome = get_setting("welcome_message", "Привет! Напиши код фильма или сериала, например: 5464")
    await message.answer(welcome, reply_markup=main_keyboard())


@dp.callback_query_handler(lambda c: c.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    if check_subscription(callback.from_user.id):
        await callback.answer("Подписка подтверждена ✅")
        await callback.message.answer("Теперь можно пользоваться ботом.", reply_markup=main_keyboard())
    else:
        await callback.answer("Подписка ещё не подтверждена ❌")


@dp.message_handler(lambda m: m.text == "🔙 Назад")
async def back_to_main(message: types.Message):
    await start_command(message)


@dp.message_handler(lambda m: m.text == "🔎 Поиск по коду")
async def search_by_code(message: types.Message):
    await message.answer("Напиши код фильма или сериала, например: 5464")


@dp.message_handler(lambda m: m.text == "➕ Добавить")
async def add_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await AddContentForm.code.set()
    await message.answer("Введите код:")


@dp.message_handler(state=AddContentForm.code)
async def add_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    if not code:
        await message.answer("Код не может быть пустым.")
        return

    async with state.proxy() as data:
        data["code"] = code

    await AddContentForm.next()
    await message.answer("Введите текст, который будет показываться человеку:")


@dp.message_handler(state=AddContentForm.text)
async def add_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("Текст не может быть пустым.")
        return

    async with state.proxy() as data:
        data["text"] = text

    await AddContentForm.next()
    await message.answer("Пришлите фото или напишите: пропустить")


@dp.message_handler(state=AddContentForm.photo, content_types=["photo"])
async def add_photo(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    data = await state.get_data()
    data["image_id"] = file_id
    save_item(data)
    await message.answer(f"✅ Добавлено: {data['code']}", reply_markup=admin_keyboard())
    await state.finish()


@dp.message_handler(state=AddContentForm.photo, content_types=["text"])
async def add_skip_photo(message: types.Message, state: FSMContext):
    if message.text.strip().lower() in ("пропустить", "-", "skip"):
        data = await state.get_data()
        data["image_id"] = ""
        save_item(data)
        await message.answer(f"✅ Добавлено: {data['code']}", reply_markup=admin_keyboard())
        await state.finish()
        return

    await message.answer("Пришлите фото или напишите: пропустить")


def save_item(data):
    conn = get_conn()
    conn.execute("""
        INSERT INTO content(code, text, image_id, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        data["code"],
        data["text"],
        data.get("image_id", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()


@dp.message_handler(lambda m: m.text == "🗑 Удалить")
async def delete_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await DeleteContentForm.code.set()
    await message.answer("Введите код, который нужно удалить:")


@dp.message_handler(state=DeleteContentForm.code)
async def delete_finish(message: types.Message, state: FSMContext):
    code = message.text.strip()
    conn = get_conn()
    row = conn.execute("SELECT code FROM content WHERE code = ?", (code,)).fetchone()
    if row:
        conn.execute("DELETE FROM content WHERE code = ?", (code,))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Удалено: {code}", reply_markup=admin_keyboard())
    else:
        conn.close()
        await message.answer("Такого кода нет.", reply_markup=admin_keyboard())
    await state.finish()


@dp.message_handler(lambda m: m.text == "📋 Список")
async def list_content(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    conn = get_conn()
    rows = conn.execute("SELECT code, text FROM content ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()

    if not rows:
        await message.answer("Пока ничего не добавлено.")
        return

    text = "<b>Список:</b>\n"
    for row in rows:
        text += f"• {row['code']} — {row['text'][:60]}\n"

    await message.answer(text, reply_markup=admin_keyboard())


@dp.message_handler(lambda m: m.text == "📢 Канал")
async def set_channel_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await SetChannelForm.channel_id.set()
    await message.answer("Введите ID канала или username:", reply_markup=admin_keyboard())


@dp.message_handler(state=SetChannelForm.channel_id)
async def set_channel_finish(message: types.Message, state: FSMContext):
    value = message.text.strip()
    if not value:
        await message.answer("Нужно указать ID канала или username.")
        return

    channel_id = get_channel_chat_id(value)
    if not channel_id:
        await message.answer(
            f"⚠️ Канал {value} не найден. Проверь:\n"
            f"1) бот добавлен в канал\n"
            f"2) бот — администратор\n"
            f"3) правильный ID/username\n"
            f"4) канал публичный или доступный API",
            reply_markup=admin_keyboard()
        )
        await state.finish()
        return

    set_setting("channel_id", channel_id)

    await message.answer(f"✅ Канал сохранён: {channel_id}", reply_markup=admin_keyboard())
    await state.finish()


@dp.message_handler(lambda m: m.text == "🔒 Подписка")
async def toggle_subscription(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    current = get_setting("require_subscription", "0")
    new_value = "1" if current != "1" else "0"
    set_setting("require_subscription", new_value)

    channel = get_setting("channel_id", "").strip()

    if new_value == "1":
        if not channel:
            await message.answer("⚠️ Подписка включена, но канал не указан.", reply_markup=admin_keyboard())
            return

        if not bot_in_channel(channel):
            await message.answer(
                f"⚠️ Канал {channel} не найден. Проверь ID/username и права бота.",
                reply_markup=admin_keyboard()
            )
            return

        await message.answer("✅ Подписка обязательна.", reply_markup=admin_keyboard())
    else:
        await message.answer("✅ Подписка выключена.", reply_markup=admin_keyboard())


@dp.message_handler(lambda m: m.text == "📣 Рассылка")
async def broadcast_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await SendBroadcastForm.text.set()
    await message.answer("Введите текст для рассылки:", reply_markup=admin_keyboard())


@dp.message_handler(state=SendBroadcastForm.text)
async def broadcast_finish(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("Текст рассылки не может быть пустым.")
        return

    conn = get_conn()
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()

    sent = 0
    for row in users:
        try:
            await bot.send_message(row["user_id"], text)
            sent += 1
        except Unauthorized:
            pass
        except Exception:
            pass

    await message.answer(f"✅ Рассылка отправлена: {sent} пользователям", reply_markup=admin_keyboard())
    await state.finish()


@dp.message_handler(lambda m: m.text == "💬 Приветствие")
async def change_welcome_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await EditWelcomeForm.text.set()
    await message.answer("Введите новое приветствие:", reply_markup=admin_keyboard())


@dp.message_handler(state=EditWelcomeForm.text)
async def change_welcome_finish(message: types.Message, state: FSMContext):
    text = message.text.strip()
    set_setting("welcome_message", text)
    await message.answer("✅ Приветствие обновлено.", reply_markup=admin_keyboard())
    await state.finish()


@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def settings(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    channel = get_setting("channel_id", "не указан")
    require = get_setting("require_subscription", "0")
    welcome = get_setting("welcome_message", "Не задано")

    text = (
        "<b>Настройки:</b>\n\n"
        f"Канал: {channel}\n"
        f"Подписка обязательна: {'Да' if require == '1' else 'Нет'}\n"
        f"Приветствие: {welcome}"
    )
    await message.answer(text, reply_markup=admin_keyboard())


@dp.message_handler(lambda m: m.text == "📊 Статистика")
async def stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    channel = get_setting("channel_id", "").strip()
    total_users = get_user_count()
    total_content = get_content_count()
    today_users = get_today_users()
    channel_members = get_channel_members_count(channel)

    text = (
        "<b>Статистика:</b>\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Сегодня зашли: {today_users}\n"
        f"Всего записей: {total_content}\n"
        f"Подписчиков в канале: {channel_members}\n"
    )
    await message.answer(text, reply_markup=admin_keyboard())


@dp.message_handler(lambda m: m.text == "👤 Админы")
async def admin_list(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    conn = get_conn()
    rows = conn.execute("SELECT user_id, username, first_name FROM admin_users ORDER BY created_at").fetchall()
    conn.close()

    if not rows:
        await message.answer("Админов пока нет.", reply_markup=admin_keyboard())
        return

    text = "<b>Администраторы:</b>\n"
    for row in rows:
        text += f"• {row['user_id']} — {row['username'] or row['first_name'] or 'без имени'}\n"

    await message.answer(text, reply_markup=admin_keyboard())


@dp.message_handler(commands=["add_admin"])
async def add_admin_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await AddAdminForm.user_id.set()
    await message.answer("Введите Telegram ID пользователя, которого нужно сделать админом:", reply_markup=admin_keyboard())


@dp.message_handler(state=AddAdminForm.user_id)
async def add_admin_finish(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно ввести только цифры — Telegram ID.")
        return

    if user_id == message.from_user.id:
        await message.answer("Ты уже админ.")
        await state.finish()
        return

    ensure_admin(user_id, message.from_user.username or "", message.from_user.first_name or "")
    await message.answer(f"✅ Пользователь {user_id} добавлен в администраторы.", reply_markup=admin_keyboard())
    await state.finish()


@dp.message_handler(commands=["remove_admin"])
async def remove_admin_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.get_args().strip()
    if not args:
        await message.answer("Использование: /remove_admin 123456789")
        return

    try:
        user_id = int(args)
    except ValueError:
        await message.answer("Нужно ввести только цифры.")
        return

    remove_admin(user_id)
    await message.answer(f"✅ Пользователь {user_id} удалён из админов.", reply_markup=admin_keyboard())


@dp.message_handler(content_types=["text"])
async def handle_text(message: types.Message):
    if message.text.startswith("/"):
        return

    if message.text in (
        "🔎 Поиск по коду",
        "➕ Добавить",
        "🗑 Удалить",
        "📋 Список",
        "📣 Рассылка",
        "💬 Приветствие",
        "📢 Канал",
        "🔒 Подписка",
        "⚙️ Настройки",
        "📊 Статистика",
        "👤 Админы",
        "🔙 Назад"
    ):
        return

    require_subscription = get_setting("require_subscription", "0") == "1"
    if require_subscription and not check_subscription(message.from_user.id):
        await message.answer("Сначала подпишитесь на канал.")
        return

    code = message.text.strip()
    if not code or not code.isdigit():
        await message.answer("Напиши код цифрами, например: 5464")
        return

    item = get_content_by_code(code)
    if not item:
        await message.answer("Такого кода нет в базе.")
        return

    text = build_message(item)
    if item["image_id"]:
        try:
            await bot.send_photo(message.chat.id, item["image_id"], caption=text, parse_mode="HTML")
        except Exception:
            await message.answer(text)
    else:
        await message.answer(text)


if __name__ == "__main__":
    init_db()
    executor.start_polling(dp, skip_updates=True)