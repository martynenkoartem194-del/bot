import asyncio
import uuid
import logging
import sqlite3
import os
from datetime import datetime, date
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from yookassa import Configuration, Payment

logging.basicConfig(level=logging.INFO)

# ============================================================
#  🔧 НАСТРОЙКИ
# ============================================================

BOT_TOKEN           = "8641991509:AAF_7MSbetK3mt7KHbP452oM7GxZktP34tA"
ADMIN_ID            = 5860629455               # твой Telegram ID
YOOKASSA_SHOP_ID    = "ТВОЙ_SHOP_ID"          # ← yookassa.ru → Интеграция → Ключи API
YOOKASSA_SECRET_KEY = "ТВОЙ_SECRET_KEY"       # ← там же, Secret key (не Test key!)

# ⚠️ ТЕСТОВЫЙ РЕЖИМ — включи для тестирования без реальной оплаты
TEST_MODE = False  # ← поставь False для боевого режима

# Ссылка на тебя — будет показываться в кнопке «Написать создателю»
CREATOR_USERNAME = "@Trav3L1"           # ← например @marty_knits

# 🔒 ОБЯЗАТЕЛЬНАЯ ПОДПИСКА — ID канала для проверки подписки
# Если None, проверка отключена. Если указан ID, пользователь должен быть подписан
REQUIRED_CHANNEL_ID = -1002887171607  # ← вставь ID канала, например: -1001234567890
REQUIRED_CHANNEL_LINK = "https://t.me/softotoys"  # ← ссылка на канал, например: "https://t.me/your_channel"

PAYMENT_CHECK_INTERVAL = 15
PAYMENT_EXPIRE_MINUTES = 30

# ============================================================
#  🛍️ ТОВАРЫ
# ============================================================

PRODUCTS = {
    "mk_socks": {
        "name":        "МК: Тёплые носочки",
        "price":       500,
        "description": (
            "🧶 Подробный видео-мастер-класс по вязанию уютных носков.\n"
            "Уровень: начинающий. Длительность: 2 часа.\n"
            "В курсе: пошаговое видео + PDF-схемы."
        ),
        "photo":      None,                    # ← вставь прямую ссылку на фото или оставь None
        "channel_id": -1003809479564,
    },
    "mk_sweater": {
        "name":        "МК: Уютный свитер",
        "price":       1500,
        "description": (
            "🧥 Полный курс по вязанию свитера оверсайз.\n"
            "Уровень: средний. Длительность: 8 часов.\n"
            "В курсе: 12 видео-уроков + чат поддержки."
        ),
        "photo":      None,
        "channel_id": -1003820635063,
    },
    "mk_hat": {
        "name":        "МК: Шапка бини",
        "price":       800,
        "description": (
            "🎩 Быстрый МК — шапка за один вечер.\n"
            "Уровень: начинающий. Длительность: 1.5 часа.\n"
            "В курсе: видео + текстовая инструкция."
        ),
        "photo":      None,
        "channel_id": -1003333333333,
    },
}

# ============================================================
#  🗄️ БАЗА ДАННЫХ
# ============================================================

DB_PATH = "sales.db"

def db_init():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id   TEXT    UNIQUE NOT NULL,
                user_id      INTEGER NOT NULL,
                username     TEXT,
                item_code    TEXT    NOT NULL,
                item_name    TEXT    NOT NULL,
                amount       INTEGER NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'pending',
                created_at   TEXT    NOT NULL,
                paid_at      TEXT
            )
        """)
        con.commit()

def db_add_sale(payment_id, user_id, username, item_code, item_name, amount):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR IGNORE INTO sales "
            "(payment_id, user_id, username, item_code, item_name, amount, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (payment_id, user_id, username, item_code, item_name, amount,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        con.commit()

def db_mark_paid(payment_id):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "UPDATE sales SET status='paid', paid_at=? WHERE payment_id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), payment_id)
        )
        con.commit()

def db_mark_expired(payment_id):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "UPDATE sales SET status='expired' WHERE payment_id=? AND status='pending'",
            (payment_id,)
        )
        con.commit()

def db_get_stats():
    with sqlite3.connect(DB_PATH) as con:
        today = date.today().strftime("%Y-%m-%d")
        total_count, total_sum = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM sales WHERE status='paid'"
        ).fetchone()
        today_count, today_sum = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM sales WHERE status='paid' AND paid_at LIKE ?",
            (f"{today}%",)
        ).fetchone()
        top_items = con.execute(
            "SELECT item_name, COUNT(*) as cnt FROM sales WHERE status='paid' "
            "GROUP BY item_code ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        pending_count = con.execute(
            "SELECT COUNT(*) FROM sales WHERE status='pending'"
        ).fetchone()[0]
    return today_count, today_sum, total_count, total_sum, top_items, pending_count

def db_get_recent_sales(limit=10):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT user_id, username, item_name, amount, paid_at "
            "FROM sales WHERE status='paid' ORDER BY paid_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

def db_get_user_sales(user_id):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT item_name, amount, paid_at FROM sales "
            "WHERE user_id=? AND status='paid' ORDER BY paid_at DESC",
            (user_id,)
        ).fetchall()

def db_get_all_buyers():
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT DISTINCT user_id, username, COUNT(*) as cnt, SUM(amount) as total "
            "FROM sales WHERE status='paid' GROUP BY user_id ORDER BY total DESC"
        ).fetchall()

def db_clear_all():
    """Полностью очистить базу данных"""
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM sales")
        con.commit()

def db_clear_pending():
    """Удалить только незавершенные платежи"""
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM sales WHERE status='pending'")
        con.commit()

def db_clear_expired():
    """Удалить только истекшие платежи"""
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM sales WHERE status='expired'")
        con.commit()

def db_get_count_by_status():
    """Получить количество записей по статусам"""
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT status, COUNT(*) FROM sales GROUP BY status"
        ).fetchall()

# ============================================================
#  ⚙️ ИНИЦИАЛИЗАЦИЯ
# ============================================================

if not TEST_MODE:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key  = YOOKASSA_SECRET_KEY

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

pending_payments: dict[str, dict] = {}


# ============================================================
#  🛡️ Только для админа
# ============================================================

def admin_only(func):
    async def wrapper(message: Message, **kwargs):
        if message.from_user.id != ADMIN_ID:
            return
        return await func(message)
    return wrapper


# ============================================================
#  🔒 Проверка подписки на канал
# ============================================================

async def check_subscription(user_id: int) -> bool:
    """Проверить подписку пользователя на обязательный канал"""
    if REQUIRED_CHANNEL_ID is None:
        return True  # Проверка отключена
    
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return False


def subscription_required_keyboard():
    """Клавиатура с кнопкой подписки"""
    keyboard = []
    if REQUIRED_CHANNEL_LINK:
        keyboard.append([InlineKeyboardButton(
            text="📢 Подписаться на канал",
            url=REQUIRED_CHANNEL_LINK
        )])
    keyboard.append([InlineKeyboardButton(
        text="✅ Я подписался",
        callback_data="check_sub"
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ─────────────────────────────────────────────────────────────
#  Вспомогательная функция: главное меню с кнопками
# ─────────────────────────────────────────────────────────────

def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton(
            text=f"{data['name']}  —  {data['price']} ₽",
            callback_data=f"item_{code}"
        )]
        for code, data in PRODUCTS.items()
    ]
    # Кнопка «Написать создателю» внизу списка товаров
    keyboard.append([
        InlineKeyboardButton(
            text="✍️ Написать создателю",
            url=f"https://t.me/{CREATOR_USERNAME.lstrip('@')}"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ============================================================
#  🏠 /start
# ============================================================

@dp.message(Command("start"))
async def start_command(message: Message):
    # Проверка подписки (админ освобожден от проверки)
    if message.from_user.id != ADMIN_ID:
        if not await check_subscription(message.from_user.id):
            await message.answer(
                "🔒 Для использования бота необходимо подписаться на наш канал!\n\n"
                "После подписки нажми «Я подписался»",
                reply_markup=subscription_required_keyboard()
            )
            return
    
    test_badge = " 🧪 ТЕСТ" if TEST_MODE else ""
    await message.answer(
        f"👋 Привет! Выбери мастер-класс:{test_badge}",
        reply_markup=main_menu_keyboard()
    )


# ============================================================
#  🔒 Проверка подписки по кнопке
# ============================================================

@dp.callback_query(F.data == "check_sub")
async def check_subscription_callback(callback: CallbackQuery):
    if await check_subscription(callback.from_user.id):
        test_badge = " 🧪 ТЕСТ" if TEST_MODE else ""
        await callback.message.edit_text(
            f"✅ Отлично! Теперь выбери мастер-класс:{test_badge}",
            reply_markup=main_menu_keyboard()
        )
        await callback.answer("✅ Подписка подтверждена!", show_alert=False)
    else:
        await callback.answer(
            "❌ Подписка не найдена. Подпишись на канал и попробуй снова.",
            show_alert=True
        )


# ============================================================
#  📦 Карточка товара
# ============================================================

@dp.callback_query(F.data.startswith("item_"))
async def show_product(callback: CallbackQuery):
    # Проверка подписки (админ освобожден от проверки)
    if callback.from_user.id != ADMIN_ID:
        if not await check_subscription(callback.from_user.id):
            await callback.message.edit_text(
                "🔒 Для использования бота необходимо подписаться на наш канал!\n\n"
                "После подписки нажми «Я подписался»",
                reply_markup=subscription_required_keyboard()
            )
            await callback.answer("❌ Требуется подписка на канал", show_alert=True)
            return
    
    code = callback.data[5:]
    if code not in PRODUCTS:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    item = PRODUCTS[code]
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить  {item['price']} ₽", callback_data=f"pay_{code}")],
        [InlineKeyboardButton(
            text="✍️ Написать создателю",
            url=f"https://t.me/{CREATOR_USERNAME.lstrip('@')}"
        )],
        [InlineKeyboardButton(text="← Назад", callback_data="back")],
    ])
    text = f"*{item['name']}*\n\n{item['description']}\n\n💰 Стоимость: *{item['price']} ₽*"

    if item["photo"]:
        try:
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=item["photo"], caption=text,
                parse_mode="Markdown", reply_markup=markup
            )
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "back")
async def go_back(callback: CallbackQuery):
    test_badge = " 🧪 ТЕСТ" if TEST_MODE else ""
    try:
        await callback.message.edit_text(
            f"👋 Привет! Выбери мастер-класс:{test_badge}",
            reply_markup=main_menu_keyboard()
        )
    except Exception:
        await callback.message.answer(
            f"👋 Привет! Выбери мастер-класс:{test_badge}",
            reply_markup=main_menu_keyboard()
        )
    await callback.answer()


# ============================================================
#  💳 Создание платежа
# ============================================================

@dp.callback_query(F.data.startswith("pay_"))
async def create_payment(callback: CallbackQuery):
    # Проверка подписки (админ освобожден от проверки)
    if callback.from_user.id != ADMIN_ID:
        if not await check_subscription(callback.from_user.id):
            await callback.message.edit_text(
                "🔒 Для использования бота необходимо подписаться на наш канал!\n\n"
                "После подписки нажми «Я подписался»",
                reply_markup=subscription_required_keyboard()
            )
            await callback.answer("❌ Требуется подписка на канал", show_alert=True)
            return
    
    code = callback.data[4:]
    if code not in PRODUCTS:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    item = PRODUCTS[code]
    user = callback.from_user

    # ── ТЕСТОВЫЙ РЕЖИМ ──────────────────────────────────────
    if TEST_MODE:
        payment_id = f"test_{uuid.uuid4().hex[:16]}"
        db_add_sale(payment_id, user.id, user.username or "", code, item["name"], item["price"])
        pending_payments[payment_id] = {
            "user_id":   user.id,
            "item_code": code,
            "chat_id":   callback.message.chat.id,
        }

        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Симулировать оплату", callback_data=f"testpay_{payment_id}")],
            [InlineKeyboardButton(
                text="✍️ Написать создателю",
                url=f"https://t.me/{CREATOR_USERNAME.lstrip('@')}"
            )],
        ])
        text = (
            f"🧪 *ТЕСТОВЫЙ РЕЖИМ*\n\n"
            f"*{item['name']}*\n"
            f"Сумма: *{item['price']} ₽*\n\n"
            f"Нажми кнопку ниже, чтобы симулировать успешную оплату."
        )
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)
        await callback.answer()
        return

    # ── БОЕВОЙ РЕЖИМ ────────────────────────────────────────
    try:
        payment = Payment.create({
            "amount":       {"value": f"{item['price']}.00", "currency": "RUB"},
            "confirmation": {
                "type":       "redirect",
                "return_url": f"https://t.me/{CREATOR_USERNAME.lstrip('@')}"
            },
            "capture":      True,
            "description":  f"{item['name']} (user_id={user.id})",
            "metadata":     {"user_id": user.id, "item_code": code}
        }, str(uuid.uuid4()))
    except Exception as e:
        logging.error(f"ЮKassa: не удалось создать платёж: {e}")
        await callback.answer(
            "⚠️ Не удалось создать ссылку для оплаты. "
            "Напишите создателю напрямую.",
            show_alert=True
        )
        await callback.message.edit_text(
            f"⚠️ *Оплата временно недоступна.*\n\n"
            f"Пожалуйста, свяжитесь с создателем: {CREATOR_USERNAME}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✍️ Написать создателю",
                    url=f"https://t.me/{CREATOR_USERNAME.lstrip('@')}"
                )],
                [InlineKeyboardButton(text="← Назад", callback_data="back")]
            ])
        )
        return

    payment_id = payment.id
    pay_url    = payment.confirmation.confirmation_url

    db_add_sale(payment_id, user.id, user.username or "", code, item["name"], item["price"])
    pending_payments[payment_id] = {
        "user_id":   user.id,
        "item_code": code,
        "chat_id":   callback.message.chat.id,
    }

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перейти к оплате",  url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату",  callback_data=f"check_{payment_id}")],
        [InlineKeyboardButton(
            text="✍️ Написать создателю",
            url=f"https://t.me/{CREATOR_USERNAME.lstrip('@')}"
        )],
    ])
    text = (
        f"*{item['name']}*\n"
        f"Сумма: *{item['price']} ₽*\n\n"
        f"Нажми «Перейти к оплате», оплати картой и вернись.\n"
        f"Бот проверит автоматически через {PAYMENT_CHECK_INTERVAL} сек.\n\n"
        f"Есть вопросы? Напиши создателю: {CREATOR_USERNAME}"
    )
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)

    await callback.answer()
    asyncio.create_task(auto_check_payment(payment_id))


# ============================================================
#  🧪 ТЕСТОВАЯ ОПЛАТА
# ============================================================

@dp.callback_query(F.data.startswith("testpay_"))
async def test_payment(callback: CallbackQuery):
    if not TEST_MODE:
        await callback.answer("Тестовый режим отключен.", show_alert=True)
        return

    payment_id = callback.data[8:]
    await callback.answer("✅ Оплата симулирована!", show_alert=False)
    await verify_and_issue(payment_id, test_mode=True)


# ============================================================
#  🔄 Проверка платежа
# ============================================================

@dp.callback_query(F.data.startswith("check_"))
async def manual_check(callback: CallbackQuery):
    payment_id = callback.data[6:]
    await callback.answer("Проверяю...", show_alert=False)
    success = await verify_and_issue(payment_id)
    if not success:
        await callback.answer("⏳ Оплата ещё не поступила. Попробуй через минуту.", show_alert=True)


async def auto_check_payment(payment_id: str):
    elapsed, limit = 0, PAYMENT_EXPIRE_MINUTES * 60
    while elapsed < limit:
        await asyncio.sleep(PAYMENT_CHECK_INTERVAL)
        elapsed += PAYMENT_CHECK_INTERVAL
        if payment_id not in pending_payments:
            return
        if await verify_and_issue(payment_id):
            return
    info = pending_payments.pop(payment_id, None)
    db_mark_expired(payment_id)
    if info:
        await bot.send_message(
            chat_id=info["user_id"],
            text=(
                f"⌛ Время ожидания оплаты истекло.\n"
                f"Если оплатил — напиши создателю: {CREATOR_USERNAME}"
            )
        )


async def verify_and_issue(payment_id: str, test_mode: bool = False) -> bool:
    info = pending_payments.get(payment_id)
    if not info:
        return True

    # В тестовом режиме пропускаем проверку ЮKassa
    if not test_mode:
        try:
            payment = Payment.find_one(payment_id)
        except Exception as e:
            logging.error(f"ЮKassa ошибка: {e}")
            return False

        if payment.status != "succeeded":
            return False

    pending_payments.pop(payment_id, None)
    db_mark_paid(payment_id)

    user_id   = info["user_id"]
    item_code = info["item_code"]
    item      = PRODUCTS[item_code]

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=item["channel_id"],
            member_limit=1,
            name=f"Оплата МК • user {user_id}"
        )
        test_badge = " 🧪 ТЕСТ" if test_mode else ""
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 *Оплата прошла успешно!*{test_badge}\n\n"
                f"Твой доступ к *{item['name']}* готов.\n\n"
                f"👇 Личная ссылка (работает 1 раз):\n{invite.invite_link}\n\n"
                f"Есть вопросы? Пиши: {CREATOR_USERNAME}"
            ),
            parse_mode="Markdown"
        )
        tg_user = await bot.get_chat(user_id)
        uname = f"@{tg_user.username}" if tg_user.username else f"ID {user_id}"
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"💰 *Новая оплата!*{test_badge}\n\n"
                f"Покупатель: {uname}\n"
                f"Товар: {item['name']}\n"
                f"Сумма: {item['price']} ₽\n"
                f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка invite-ссылки: {e}")
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ Оплата прошла, но возникла техническая ошибка.\n"
                f"Напиши создателю: {CREATOR_USERNAME} — выдадим доступ вручную."
            )
        )
    return True


# ============================================================
#  👑 АДМИН-ПАНЕЛЬ
# ============================================================

@dp.message(Command("admin"))
@admin_only
async def admin_menu(message: Message):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",        callback_data="adm_stats")],
        [InlineKeyboardButton(text="🧾 Последние продажи", callback_data="adm_sales")],
        [InlineKeyboardButton(text="👥 Все покупатели",    callback_data="adm_buyers")],
        [InlineKeyboardButton(text="⏳ В ожидании оплаты", callback_data="adm_pending")],
        [InlineKeyboardButton(text="🔍 Проверить каналы",  callback_data="adm_check")],
        [InlineKeyboardButton(text="📋 Получить ID канала", callback_data="adm_getid")],
        [InlineKeyboardButton(text="🔗 Тест инвайт-ссылки", callback_data="adm_testinv")],
        [InlineKeyboardButton(text="🗑️ Очистить БД",       callback_data="adm_cleardb")],
    ])
    await message.answer("👑 *Админ-панель*", parse_mode="Markdown", reply_markup=markup)


@dp.callback_query(F.data == "adm_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    t_cnt, t_sum, all_cnt, all_sum, top, pending = db_get_stats()
    top_text = "\n".join(
        f"  {i+1}. {name} — {cnt} шт." for i, (name, cnt) in enumerate(top)
    ) or "  нет данных"
    text = (
        f"📊 *Статистика продаж*\n\n"
        f"*Сегодня:*  {t_cnt} продаж  /  {t_sum} ₽\n"
        f"*Всего:*    {all_cnt} продаж  /  {all_sum} ₽\n"
        f"*Ожидают оплаты:* {pending}\n\n"
        f"*Топ товаров:*\n{top_text}"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "adm_sales")
async def admin_sales(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    rows = db_get_recent_sales(10)
    if not rows:
        text = "🧾 *Продаж пока нет.*"
    else:
        lines = []
        for user_id, username, item_name, amount, paid_at in rows:
            uname = f"@{username}" if username else f"ID {user_id}"
            dt    = paid_at[:16] if paid_at else "—"
            lines.append(f"• {dt}  |  {uname}  |  {item_name}  —  {amount} ₽")
        text = "🧾 *Последние 10 продаж:*\n\n" + "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "adm_buyers")
async def admin_buyers(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    rows = db_get_all_buyers()
    if not rows:
        text = "👥 *Покупателей пока нет.*"
    else:
        lines = []
        for user_id, username, cnt, total in rows:
            uname = f"@{username}" if username else f"ID {user_id}"
            lines.append(f"• {uname}  |  {cnt} покупок  |  {total} ₽")
        text = "👥 *Все покупатели:*\n\n" + "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "adm_pending")
async def admin_pending(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not pending_payments:
        text = "⏳ Нет платежей в ожидании."
    else:
        lines = []
        for pid, info in pending_payments.items():
            item = PRODUCTS.get(info["item_code"], {})
            lines.append(f"• ID {info['user_id']}  |  {item.get('name', '?')}  |  {pid[:8]}…")
        text = "⏳ *Платежи в ожидании:*\n\n" + "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "adm_back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",        callback_data="adm_stats")],
        [InlineKeyboardButton(text="🧾 Последние продажи", callback_data="adm_sales")],
        [InlineKeyboardButton(text="👥 Все покупатели",    callback_data="adm_buyers")],
        [InlineKeyboardButton(text="⏳ В ожидании оплаты", callback_data="adm_pending")],
        [InlineKeyboardButton(text="🔍 Проверить каналы",  callback_data="adm_check")],
        [InlineKeyboardButton(text="📋 Получить ID канала", callback_data="adm_getid")],
        [InlineKeyboardButton(text="🔗 Тест инвайт-ссылки", callback_data="adm_testinv")],
        [InlineKeyboardButton(text="🗑️ Очистить БД",       callback_data="adm_cleardb")],
    ])
    await callback.message.edit_text("👑 *Админ-панель*", parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "adm_check")
async def admin_check_channels_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    await callback.answer("Проверяю каналы...", show_alert=False)
    results = []
    
    for code, item in PRODUCTS.items():
        channel_id = item["channel_id"]
        try:
            chat = await bot.get_chat(channel_id)
            try:
                bot_member = await bot.get_chat_member(channel_id, bot.id)
                if bot_member.status in ["administrator"]:
                    if bot_member.can_invite_users:
                        status = "✅"
                        details = "OK"
                    else:
                        status = "⚠️"
                        details = "Нет права приглашать"
                else:
                    status = "❌"
                    details = f"Статус: {bot_member.status}"
            except Exception as e:
                status = "❌"
                details = f"Ошибка прав: {str(e)[:30]}"
            
            results.append(
                f"{status} *{item['name']}*\n"
                f"   Канал: {chat.title}\n"
                f"   ID: `{channel_id}`\n"
                f"   {details}"
            )
        except Exception as e:
            results.append(
                f"❌ *{item['name']}*\n"
                f"   ID: `{channel_id}`\n"
                f"   Ошибка: {str(e)[:50]}"
            )
    
    text = "🔍 *Проверка доступа к каналам:*\n\n" + "\n\n".join(results)
    
    if any("❌" in r or "⚠️" in r for r in results):
        text += (
            "\n\n📝 *Как исправить:*\n"
            "1. Открой канал → Настройки → Администраторы\n"
            "2. Добавь бота как администратора\n"
            "3. Включи право 'Приглашение пользователей'"
        )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


@dp.callback_query(F.data == "adm_getid")
async def admin_getid_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    text = (
        "📋 *Получить ID канала/группы*\n\n"
        "Используй команду:\n"
        "`/checkchannels`\n\n"
        "Она покажет все каналы из PRODUCTS с их ID и статусом."
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "adm_testinv")
async def admin_testinv_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    # Создаем кнопки для каждого товара
    buttons = []
    for code, item in PRODUCTS.items():
        buttons.append([InlineKeyboardButton(
            text=item['name'],
            callback_data=f"testinv_{code}"
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="adm_back")])
    
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "🔗 *Тест инвайт-ссылки*\n\nВыбери товар для создания тестовой ссылки:"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("testinv_"))
async def admin_create_testinv(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    code = callback.data[8:]
    if code not in PRODUCTS:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    
    item = PRODUCTS[code]
    await callback.answer("Создаю ссылку...", show_alert=False)
    
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=item["channel_id"],
            member_limit=1,
            name=f"Тест • {datetime.now().strftime('%H:%M:%S')}"
        )
        text = (
            f"✅ *Тестовая ссылка создана!*\n\n"
            f"Товар: *{item['name']}*\n"
            f"Канал ID: `{item['channel_id']}`\n"
            f"Ссылка: {invite.invite_link}\n\n"
            f"Ссылка работает для 1 человека."
        )
    except Exception as e:
        text = (
            f"❌ *Ошибка создания ссылки:*\n\n"
            f"`{str(e)}`\n\n"
            f"Проверь:\n"
            f"• Бот добавлен в канал?\n"
            f"• Бот — администратор?\n"
            f"• У бота есть право 'Приглашение пользователей'?"
        )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="adm_testinv")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


@dp.message(Command("user"))
@admin_only
async def admin_user_info(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /user 123456789")
        return
    user_id = int(parts[1])
    rows    = db_get_user_sales(user_id)
    if not rows:
        await message.answer(f"По ID {user_id} продаж не найдено.")
        return
    total = sum(r[1] for r in rows)
    lines = [f"• {(r[2] or '?')[:16]}  |  {r[0]}  —  {r[1]} ₽" for r in rows]
    text = (
        f"👤 *Покупатель ID {user_id}*\n"
        f"Покупок: {len(rows)}, итого: {total} ₽\n\n"
        + "\n".join(lines)
    )
    await message.answer(text, parse_mode="Markdown")


@dp.callback_query(F.data == "adm_cleardb")
async def admin_cleardb_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    # Получаем статистику по статусам
    status_counts = db_get_count_by_status()
    stats_text = "\n".join([f"• {status}: {count} шт." for status, count in status_counts]) if status_counts else "База пуста"
    
    text = (
        f"🗑️ *Очистка базы данных*\n\n"
        f"*Текущее состояние:*\n{stats_text}\n\n"
        f"Выбери что удалить:"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Удалить незавершенные (pending)", callback_data="clear_pending")],
        [InlineKeyboardButton(text="🗑️ Удалить истекшие (expired)", callback_data="clear_expired")],
        [InlineKeyboardButton(text="⚠️ УДАЛИТЬ ВСЁ", callback_data="clear_all_confirm")],
        [InlineKeyboardButton(text="← Назад", callback_data="adm_back")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "clear_pending")
async def clear_pending_payments(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    db_clear_pending()
    await callback.answer("✅ Незавершенные платежи удалены", show_alert=True)
    
    # Возвращаемся в меню очистки с обновленной статистикой
    await admin_cleardb_menu(callback)


@dp.callback_query(F.data == "clear_expired")
async def clear_expired_payments(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    db_clear_expired()
    await callback.answer("✅ Истекшие платежи удалены", show_alert=True)
    
    # Возвращаемся в меню очистки с обновленной статистикой
    await admin_cleardb_menu(callback)


@dp.callback_query(F.data == "clear_all_confirm")
async def clear_all_confirm(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    text = (
        "⚠️ *ВНИМАНИЕ!*\n\n"
        "Ты собираешься удалить ВСЕ данные из базы:\n"
        "• Все продажи\n"
        "• Всю статистику\n"
        "• Всю историю\n\n"
        "Это действие НЕОБРАТИМО!\n\n"
        "Уверен?"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ ДА, УДАЛИТЬ ВСЁ", callback_data="clear_all_yes")],
        [InlineKeyboardButton(text="← Отмена", callback_data="adm_cleardb")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "clear_all_yes")
async def clear_all_database(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    db_clear_all()
    await callback.answer("✅ База данных полностью очищена", show_alert=True)
    
    text = "✅ *База данных очищена*\n\nВсе данные удалены."
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад в админ-панель", callback_data="adm_back")
    ]])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


@dp.message(Command("checkchannels"))
@admin_only
async def check_channels_command(message: Message):
    """Проверить доступ бота ко всем каналам и показать их ID"""
    await message.answer("🔍 Проверяю каналы...")
    
    results = []
    
    for code, item in PRODUCTS.items():
        channel_id = item["channel_id"]
        try:
            chat = await bot.get_chat(channel_id)
            # Проверяем права бота
            try:
                bot_member = await bot.get_chat_member(channel_id, bot.id)
                if bot_member.status in ["administrator"]:
                    if bot_member.can_invite_users:
                        status = "✅"
                        details = "OK"
                    else:
                        status = "⚠️"
                        details = "Нет права приглашать"
                else:
                    status = "❌"
                    details = f"Статус: {bot_member.status}"
            except Exception as e:
                status = "❌"
                details = f"Ошибка прав: {str(e)[:30]}"
            
            results.append(
                f"{status} *{item['name']}*\n"
                f"   Канал: {chat.title}\n"
                f"   ID: `{channel_id}`\n"
                f"   {details}"
            )
        except Exception as e:
            results.append(
                f"❌ *{item['name']}*\n"
                f"   ID: `{channel_id}`\n"
                f"   Ошибка: {str(e)[:50]}"
            )
    
    text = "🔍 *Проверка доступа к каналам:*\n\n" + "\n\n".join(results)
    
    if any("❌" in r or "⚠️" in r for r in results):
        text += (
            "\n\n📝 *Как исправить:*\n"
            "1. Открой канал → Настройки → Администраторы\n"
            "2. Добавь бота как администратора\n"
            "3. Включи право 'Приглашение пользователей'\n"
            "4. Запусти /checkchannels снова"
        )
    
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("listchannels"))
@admin_only
async def list_all_channels(message: Message):
    """Показать все каналы/группы, где бот является администратором"""
    await message.answer("🔍 Ищу все каналы и группы...")
    
    # К сожалению, Telegram Bot API не предоставляет метод для получения списка всех чатов
    # Можем только показать те, что уже в PRODUCTS
    text = (
        "📋 *Каналы/группы из PRODUCTS:*\n\n"
        "Для получения ID приватного канала:\n"
        "1. Открой канал/группу\n"
        "2. Напиши там `/getid`\n"
        "3. Бот пришлет ID в личку\n\n"
        "*Текущие каналы:*\n"
    )
    
    for code, item in PRODUCTS.items():
        text += f"\n• {item['name']}: `{item['channel_id']}`"
    
    text += (
        "\n\n💡 *Совет:*\n"
        "Если бот уже добавлен в канал, просто напиши `/getid` прямо в том канале."
    )
    
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("testinvite"))
@admin_only
async def test_invite(message: Message):
    """Создать тестовую инвайт-ссылку для проверки"""
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Использование: /testinvite <код_товара>\n\n"
            f"Доступные коды: {', '.join(PRODUCTS.keys())}"
        )
        return
    
    code = parts[1]
    if code not in PRODUCTS:
        await message.answer(f"Товар '{code}' не найден.")
        return
    
    item = PRODUCTS[code]
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=item["channel_id"],
            member_limit=1,
            name=f"Тест • {datetime.now().strftime('%H:%M:%S')}"
        )
        await message.answer(
            f"✅ Тестовая ссылка создана!\n\n"
            f"Товар: *{item['name']}*\n"
            f"Канал ID: `{item['channel_id']}`\n"
            f"Ссылка: {invite.invite_link}\n\n"
            f"Ссылка работает для 1 человека.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(
            f"❌ Ошибка создания ссылки:\n\n"
            f"`{str(e)}`\n\n"
            f"Проверь:\n"
            f"1. Бот добавлен в канал?\n"
            f"2. Бот — администратор?\n"
            f"3. У бота есть право 'Приглашение пользователей'?",
            parse_mode="Markdown"
        )


# ============================================================
#  🚀 Запуск
# ============================================================

async def main():
    db_init()

    # Команды для всех пользователей
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
    ])

    # Команды только для админа
    from aiogram.types import BotCommandScopeChat
    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="admin", description="👑 Админ-панель"),
            BotCommand(command="user", description="👤 Инфо о покупателе (ID)"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )

    mode = "ТЕСТОВЫЙ" if TEST_MODE else "БОЕВОЙ"
    logging.info(f"Бот запущен в {mode} режиме.")
    
    # Для Render используем webhooks
    import os
    WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL")  # Render автоматически устанавливает эту переменную
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
    WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
    
    if WEBHOOK_HOST:
        # Режим webhooks для Render
        from aiohttp import web
        from aiogram import types as aiogram_types
        
        async def webhook_handler(request):
            """Обработчик входящих обновлений от Telegram"""
            update = await request.json()
            telegram_update = aiogram_types.Update(**update)
            await dp.feed_update(bot, telegram_update)
            return web.Response()
        
        async def on_startup(app):
            """Устанавливаем webhook при запуске"""
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(WEBHOOK_URL)
            logging.info(f"Webhook установлен: {WEBHOOK_URL}")
        
        async def on_shutdown(app):
            """Удаляем webhook при остановке"""
            await bot.delete_webhook()
            logging.info("Webhook удален")
        
        # Создаем веб-приложение
        app = web.Application()
        app.router.add_post(WEBHOOK_PATH, webhook_handler)
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
        
        # Запускаем веб-сервер
        PORT = int(os.getenv("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logging.info(f"Webhook сервер запущен на порту {PORT}")
        
        # Держим сервер запущенным
        await asyncio.Event().wait()
    else:
        # Режим polling для локальной разработки
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
