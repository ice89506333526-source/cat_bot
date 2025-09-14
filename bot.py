import sys
print(">>> PYTHON VERSION:", sys.version)

# bot.py — Telegram bot: публикации в группу, тарифы, история, оплата с фискализацией (ЮKassa)
# Поместите этот файл в папку проекта, активируйте venv и запустите: python bot.py

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, types
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from aiogram.types import ContentType
from collections import defaultdict
from aiohttp import web


# Для сбора медиа, чтобы объединять в один пост
media_groups = defaultdict(list)



# ------------------ Константы (вставлены ваши данные) ------------------
API_TOKEN = "8423035573:AAHo53sPuZJXbGXLhW5-EThbdXM5GrCULDQ"
BOT_USERNAME = "cat777_cash_bot"
GROUP_ID = -1002522022019
ADMIN_ID = 827299190

# ---------------- Webhook settings ----------------
# Путь webhook — содержит токен, чтобы усложнить доступ сторонним
WEBHOOK_PATH = f"/webhook/{API_TOKEN}"

# Render предоставляет публичный hostname в переменной окружения RENDER_EXTERNAL_HOSTNAME.
# Соберём публичный URL webhook
RENDER_HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME")  # на Render вроде cat-bot-4.onrender.com
PORT = int(os.environ.get("PORT", 10000))

if RENDER_HOST:
    WEBHOOK_URL = f"https://{RENDER_HOST}{WEBHOOK_PATH}"
else:
    # fallback для локальной разработки (Telegram требует HTTPS — для локали нужен ngrok)
    WEBHOOK_URL = f"http://localhost:{PORT}{WEBHOOK_PATH}"

# Provider token для Telegram Payments
PROVIDER_TOKEN = "390540012:LIVE:77400"
YOOKASSA_SHOP_ID = "1151636"
YOOKASSA_SECRET_KEY = "live_9WZWrOx1vsciG0JzhQqb8fP_JdPwvLJ3YSJBbc1acBE"

USERS_FILE = "users_data.json"


# ------------------ Тарифы ------------------
TARIFFS = {
    "free": {"price": 0, "posts_per_day": 1, "pin_limit": 0, "delay": False},
    "pro": {"price": 200, "posts_per_day": 3, "pin_limit": 2, "delay": True},
    "premium": {"price": 1000, "posts_per_day": 15, "pin_limit": 5, "delay": True},
}

# ------------------ Логирование ------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ Загрузка/сохранение пользователей ------------------
def load_users() -> Dict[str, Any]:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {str(k): v for k, v in data.items()}
        except Exception:
            logger.exception("Не удалось загрузить users_data.json — начнем с пустых данных")
            return {}
    return {}

def save_users(data: Dict[str, Any]) -> None:
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Ошибка при сохранении users_data.json")

users_data: Dict[str, Any] = load_users()

# ------------------ Инициализация пользователя ------------------
def init_user(user_id: int) -> Dict[str, Any]:
    key = str(user_id)
    if key not in users_data:
        users_data[key] = {
            "tariff": "free",
            "posts_today": 0,
            "last_post_day": None,
            "history": [],  # список последних постов (будем хранить максимум 5)
            "email": None,
            "pending_tariff": None,
            "payments": []
        }
        save_users(users_data)
    # админ — вечный премиум (перезаписать тариф, если нужно)
    if key == str(ADMIN_ID):
        users_data[key]["tariff"] = "premium"
        save_users(users_data)
    return users_data[key]

# инициализируем администратора, если не был
init_user(ADMIN_ID)

# ------------------ FSM ------------------
class States(StatesGroup):
    waiting_for_post = State()
    waiting_for_schedule_time = State()
    waiting_for_email_for_payment = State()
    waiting_for_edit_index = State()

# ------------------ Бот ------------------
bot = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ------------------ Планировщик отложенных постов (в память) ------------------
scheduled_posts: List[Dict[str, Any]] = []  # каждый элемент: {"time": datetime, "post": post_dict, "user_id": user_id}

async def scheduled_post_worker():
    while True:
        now = datetime.utcnow()
        for task in list(scheduled_posts):
            if task["time"] <= now:
                ukey = str(task["user_id"])
                init_user(task["user_id"])

                if can_post(task["user_id"]):
                    await send_post_to_group(task["post"])
                    register_post(task["user_id"])

                    users_data[ukey]["history"].append(task["post"])
                    # trim history to last 5
                    if len(users_data[ukey]["history"]) > 5:
                        users_data[ukey]["history"] = users_data[ukey]["history"][-5:]
                    save_users(users_data)
                else:
                    try:
                        await bot.send_message(task["user_id"], "⚠️ Лимит публикаций исчерпан, запланированный пост не опубликован.")
                    except:
                        pass

                scheduled_posts.remove(task)

        await asyncio.sleep(15)


# ------------------ Kлавиатуры ------------------
def main_menu_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📢 Создать пост", callback_data="create_post"),
        types.InlineKeyboardButton("💳 Сменить тариф", callback_data="change_tariff"),
        types.InlineKeyboardButton("📜 Мои посты", callback_data="my_posts"),
    )
    return kb

def publish_choice_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📌 Сразу публиковать", callback_data="publish_now"),
        types.InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_post")
    )
    return kb

def make_tariff_kb(user_tariff: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for name, info in TARIFFS.items():
        if name == "free":
            continue
        label = f"{name.capitalize()} — {info['price']}₽/мес, {info['posts_per_day']} постов/день"
        if name == user_tariff:
            label += " (текущий)"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"pay:{name}"))
    return kb

# ------------------ Помощники ------------------
async def send_post_to_group(post: Dict[str, Any]) -> None:
    try:
        if post["type"] == "text":
            await bot.send_message(GROUP_ID, post["text"])
        elif post["type"] == "photo":
            await bot.send_photo(GROUP_ID, post["file_id"], caption=post.get("caption"))
        elif post["type"] == "video":
            await bot.send_video(GROUP_ID, post["file_id"], caption=post.get("caption"))
    except Exception:
        logger.exception("Ошибка при отправке поста в группу")

# ------------------ Хендлеры ------------------

@dp.message_handler(commands=["start"])
async def on_start(message: types.Message):
    # если команда в группе — просто ссылка на личку
    if message.chat.type != types.ChatType.PRIVATE:
        await message.reply(f"Публикация постов ЗДЕСЬ — перейдите в личку: @{BOT_USERNAME}")
        return

    # в личке — инициализация и меню
    init_user(message.from_user.id)
    await message.answer("Привет! Выберите действие:", reply_markup=main_menu_kb())

# Обработчик кнопок: Создать пост
@dp.callback_query_handler(lambda c: c.data == "create_post")
async def cb_create_post(callback: types.CallbackQuery):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)
    await callback.answer()
    user = init_user(callback.from_user.id)
    today = datetime.now().day
    if user.get("last_post_day") != today:
        user["posts_today"] = 0
        user["last_post_day"] = today
        save_users(users_data)
    tariff = TARIFFS[user["tariff"]]
    if user.get("posts_today", 0) >= tariff["posts_per_day"]:
        # перенаправляем на смену тарифа
        await callback.message.answer(
            f"⚠️ Лимит публикаций исчерпан для тарифа {user['tariff'].capitalize()}. Выберите тариф:",
            reply_markup=make_tariff_kb(user["tariff"])
        )
        return
    await callback.message.answer("Отправь текст, фото или видео для публикации.", reply_markup=None)
    await States.waiting_for_post.set()

# Сменить тариф — показать варианты
@dp.callback_query_handler(lambda c: c.data == "change_tariff")
async def cb_change_tariff(callback: types.CallbackQuery):
    await callback.answer()
    user = init_user(callback.from_user.id)
    kb = make_tariff_kb(user["tariff"])
    await callback.message.answer(f"Текущий тариф: {user['tariff'].capitalize()}\nВыберите тариф для оплаты:", reply_markup=kb)

# Обработка нажатия pay:<tariff>
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("pay:"))
async def cb_pay(callback: types.CallbackQuery):
    await callback.answer()
    user = init_user(callback.from_user.id)
    _, tariff_name = callback.data.split(":", 1)
    if tariff_name not in TARIFFS or tariff_name == "free":
        await callback.message.answer("Недоступный тариф.")
        return

    # если нет email или телефона — попросим email (минимально)
    if not user.get("email"):
        users_data[str(callback.from_user.id)]["pending_tariff"] = tariff_name
        save_users(users_data)
        await callback.message.answer("Для формирования чека необходим email. Пожалуйста, пришлите ваш email.")
        await States.waiting_for_email_for_payment.set()
        return

    # если email есть — формируем provider_data и отправляем invoice
    await send_invoice_for_tariff(callback.from_user.id, tariff_name)

# Промежуточный: запрос email -> отправка invoice
@dp.message_handler(state=States.waiting_for_email_for_payment, content_types=types.ContentTypes.TEXT)
async def email_for_payment_handler(message: types.Message, state: FSMContext):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)
    email = message.text.strip()
    # простая валидация
    if "@" not in email or "." not in email:
        await message.reply("Пожалуйста, введите корректный email (пример: example@mail.ru).")
        return
    key = str(message.from_user.id)
    init_user(message.from_user.id)
    users_data[key]["email"] = email
    pending = users_data[key].get("pending_tariff")
    save_users(users_data)
    await message.reply(f"Email {email} сохранён. Формирую счёт...")
    if pending:
        await send_invoice_for_tariff(message.from_user.id, pending)
        users_data[key]["pending_tariff"] = None
        save_users(users_data)
    await state.finish()

# Формирование и отправка счета (с provider_data для ЮKassa)
async def send_invoice_for_tariff(user_id: int, tariff_name: str):
    user = init_user(user_id)
    tariff = TARIFFS[tariff_name]
    price = tariff["price"]
    # Формируем provider_data.receipt
    receipt = {
        "customer": {},
        "items": [
            {
                "description": f"Тариф {tariff_name.capitalize()}",
                "quantity": 1,
                "amount": {"value": f"{price:.2f}", "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service"
            }
        ],
        "tax_system_code": 1
    }
    # добавим email или (если понадобится) phone
    if user.get("email"):
        receipt["customer"]["email"] = user["email"]
    # provider_data должен быть строкой JSON
    provider_data = {"receipt": receipt}

    payload = f"tariff:{tariff_name}:{user_id}"  # уникальный payload
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=f"Оплата тарифа {tariff_name.capitalize()}",
            description=f"Тариф {tariff_name.capitalize()} — {tariff['posts_per_day']} постов/день",
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"{tariff_name.capitalize()} тариф", amount=int(price * 100))],
            need_email=True,
            provider_data=json.dumps(provider_data)
        )
    except Exception:
        logger.exception("Ошибка при отправке invoice")
        try:
            await bot.send_message(user_id, "Не удалось создать счёт. Попробуйте позже.")
        except Exception:
            pass

# PreCheckoutQuery — отвечаем Telegram что всё ок
@dp.pre_checkout_query_handler(lambda query: True)
async def precheckout_handler(pre_checkout_q: PreCheckoutQuery):
    try:
        await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)
    except Exception:
        logger.exception("Ошибка при answer_pre_checkout_query")

# Successful payment
@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: types.Message):
    try:
        payload = message.successful_payment.invoice_payload  # формат: tariff:<name>:<user_id>
        parts = payload.split(":")
        if len(parts) != 3 or parts[0] != "tariff":
            logger.warning("Некорректный payload в успешной оплате: %s", payload)
            await message.answer("Оплата прошла, но payload некорректен. Обратитесь к администратору.")
            return
        tariff_name = parts[1]
        user_id = int(parts[2])
        key = str(user_id)
        init_user(user_id)
        users_data[key]["tariff"] = tariff_name
        users_data[key]["posts_today"] = 0
        # сохраняем provider_payment_charge_id (номер транзакции в провайдере)
        charge_id = getattr(message.successful_payment, "provider_payment_charge_id", None)
        if charge_id:
            users_data[key].setdefault("payments", []).append({
                "tariff": tariff_name,
                "date": datetime.utcnow().isoformat(),
                "provider_payment_charge_id": charge_id
            })
        save_users(users_data)
        await message.answer(f"✅ Оплата прошла успешно — тариф {tariff_name.capitalize()} активирован!", reply_markup=main_menu_kb())
    except Exception:
        logger.exception("Ошибка при обработке успешной оплаты")
        await message.answer("Платёж прошёл, но произошла ошибка при обработке. Свяжитесь с администратором.")

# Просмотр истории (кнопка 'Мои посты')
@dp.callback_query_handler(lambda c: c.data == "my_posts")
async def cb_my_posts(callback: types.CallbackQuery):
    await callback.answer()
    user = init_user(callback.from_user.id)
    hist = user.get("history", [])
    if not hist:
        await callback.message.answer("История пока пуста.", reply_markup=main_menu_kb())
        return
    # показываем последние до 5
    for idx, p in enumerate(hist[-5:][::-1]):  # показываем в обратном порядке: от новейшего к старому
        index_real = len(hist) - 1 - idx  # индекс в оригинальном списке
        if p["type"] == "text":
            preview = p["text"][:1000]
            await callback.message.answer(preview, reply_markup=types.InlineKeyboardMarkup(row_width=2).add(
                types.InlineKeyboardButton(f"🔁 Опубликовать", callback_data=f"repost:{index_real}"),
                types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{index_real}")
            ))
        elif p["type"] == "photo":
            preview = p.get("caption", "")
            await callback.message.answer(preview, reply_markup=types.InlineKeyboardMarkup(row_width=2).add(
                types.InlineKeyboardButton(f"🔁 Опубликовать", callback_data=f"repost:{index_real}"),
                types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{index_real}")
            ))
        elif p["type"] == "video":
            preview = p.get("caption", "")
            await callback.message.answer(preview, reply_markup=types.InlineKeyboardMarkup(row_width=2).add(
                types.InlineKeyboardButton(f"🔁 Опубликовать", callback_data=f"repost:{index_real}"),
                types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{index_real}")
            ))

# Повторная публикация (repost:<idx>) — проверка лимита
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("repost:"))
async def cb_repost(callback: types.CallbackQuery):
    await callback.answer()
    _, idx_str = callback.data.split(":", 1)
    try:
        idx = int(idx_str)
    except ValueError:
        await callback.message.answer("Неверный индекс поста.")
        return

    user = init_user(callback.from_user.id)
    hist = user.get("history", [])
    if idx < 0 or idx >= len(hist):
        await callback.message.answer("Пост не найден.")
        return

    # Проверка лимитов
    if not can_post(callback.from_user.id):
        await callback.message.answer("⚠️ Лимит публикаций на сегодня исчерпан.")
        return

    post = hist[idx]
    await send_post_to_group(post)
    register_post(callback.from_user.id)

    await callback.message.answer("✅ Пост опубликован повторно!", reply_markup=main_menu_kb())



# Редактирование поста (простой поток: пользователь отправляет новый контент; заменяем запись в истории)
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("edit:"))
async def cb_edit(callback: types.CallbackQuery):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)
    await callback.answer()
    _, idx_str = callback.data.split(":", 1)
    try:
        idx = int(idx_str)
    except ValueError:
        await callback.message.answer("Неверный индекс для редактирования.")
        return
    key = str(callback.from_user.id)
    init_user(callback.from_user.id)
    hist = users_data[key].get("history", [])
    if idx < 0 or idx >= len(hist):
        await callback.message.answer("Пост не найден.")
        return
    # сохраним индекс в state
    await callback.message.answer("Отправьте новый текст/фото/видео для замены этого поста.")
    # записываем индекс в временном месте (в памяти) — сохраним в users_data, но без перманентного поля
    users_data[key]["_editing_index"] = idx
    save_users(users_data)
    await States.waiting_for_edit_index.set()

@dp.message_handler(state=States.waiting_for_edit_index, content_types=types.ContentTypes.ANY)
async def handle_edit_submission(message: types.Message, state: FSMContext):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)
    key = str(message.from_user.id)
    init_user(message.from_user.id)
    idx = users_data[key].get("_editing_index")
    if idx is None:
        await message.reply("Нет открытого редактирования.")
        await state.finish()
        return
    # формируем новый пост
    if message.content_type == "text":
        new_post = {"type": "text", "text": message.text}
    elif message.content_type == "photo":
        new_post = {"type": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption}
    elif message.content_type == "video":
        new_post = {"type": "video", "file_id": message.video.file_id, "caption": message.caption}
    else:
        await message.reply("Тип контента не поддерживается для редактирования.")
        await state.finish()
        return
    hist = users_data[key].get("history", [])
    if 0 <= idx < len(hist):
        hist[idx] = new_post
        # trim to 5 if needed
        if len(hist) > 5:
            users_data[key]["history"] = hist[-5:]
        save_users(users_data)
        await message.reply("✅ Пост обновлён.", reply_markup=main_menu_kb())
    else:
        await message.reply("Ошибка: сохранённый индекс недействителен.")
    users_data[key].pop("_editing_index", None)
    save_users(users_data)
    await state.finish()

# Получение поста после нажатия 'Создать пост'
from aiogram.types import ContentType

@dp.message_handler(state=States.waiting_for_post, content_types=ContentType.ANY)
async def handle_post(message: types.Message, state: FSMContext):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)

    uid = message.from_user.id
    init_user(uid)

    # Проверяем, не пришла ли медиа-группа
    if message.media_group_id:
        media_groups[uid].append(message)
        await asyncio.sleep(0.5)  # собираем все сообщения группы
        first = media_groups[uid][0]
        if first.photo:
            post = {"type": "photo", "file_id": first.photo[-1].file_id, "caption": first.caption or ""}
        elif first.video:
            post = {"type": "video", "file_id": first.video.file_id, "caption": first.caption or ""}
        else:
            post = {"type": "text", "text": first.caption or ""}
        media_groups[uid].clear()
    else:
        if message.photo:
            post = {"type": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption or ""}
        elif message.video:
            post = {"type": "video", "file_id": message.video.file_id, "caption": message.caption or ""}
        else:
            post = {"type": "text", "text": message.text}

    # Сохраняем пост в state
    await state.update_data(post_content=post)

    # Снимаем состояние, чтобы не ловить повторные сообщения
    await state.reset_state(with_data=False)

    # Отправляем кнопки для публикации
    await message.answer("Выберите действие для публикации:", reply_markup=publish_choice_kb())





# Обработка кнопок "Сразу публиковать" и "Отложить публикацию"
@dp.callback_query_handler(lambda c: c.data in ("publish_now", "schedule_post"), state="*")
async def cb_publish_choice(callback: types.CallbackQuery, state: FSMContext):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)
    await callback.answer()

    data = await state.get_data()
    post = data.get("post_content")
    if not post:
        await callback.message.answer("Нет содержимого для публикации. Начните заново.")
        await state.finish()
        return

    ukey = str(callback.from_user.id)
    user = init_user(callback.from_user.id)
    today = datetime.now().day
    if user.get("last_post_day") != today:
        user["posts_today"] = 0
        user["last_post_day"] = today

    tariff = TARIFFS[user["tariff"]]
    if user.get("posts_today", 0) >= tariff["posts_per_day"]:
        await callback.message.answer(
            "⚠️ Лимит публикаций на сегодня исчерпан. Смените тариф.",
            reply_markup=make_tariff_kb(user["tariff"])
        )
        await state.finish()
        return

    if callback.data == "publish_now":
        await send_post_to_group(post)
        user["posts_today"] = user.get("posts_today", 0) + 1
        user.setdefault("history", []).append(post)
        if len(user["history"]) > 5:
            user["history"] = user["history"][-5:]
        save_users(users_data)
        await callback.message.answer("✅ Пост опубликован!", reply_markup=main_menu_kb())
        await state.finish()
        return

    if callback.data == "schedule_post":
        if not tariff["delay"]:
            await callback.message.answer(
                "⏰ Отложенные публикации недоступны на вашем тарифе.",
                reply_markup=main_menu_kb()
            )
            await state.finish()
            return
        await callback.message.answer(
            "Введите время публикации в формате ГГГГ-ММ-ДД ЧЧ:ММ (например: 2025-09-05 14:30):",
            reply_markup=None
        )
        await States.waiting_for_schedule_time.set()



@dp.message_handler(state=States.waiting_for_schedule_time, content_types=ContentType.TEXT)
async def schedule_time_handler(message: types.Message, state: FSMContext):
    Bot.set_current(bot)
    Dispatcher.set_current(dp)
    text = message.text.strip()
    try:
        publish_time = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Неверный формат времени. Попробуйте снова (пример: 2025-09-05 14:30).")
        return

    data = await state.get_data()
    post = data.get("post_content")
    if not post:
        await message.answer("Нет содержимого для публикации. Начните заново.")
        await state.finish()
        return

    scheduled_posts.append({"time": publish_time, "post": post, "user_id": message.from_user.id})
    await message.answer(f"✅ Пост запланирован на {publish_time.strftime('%d-%m-%Y %H:%M')}", reply_markup=main_menu_kb())
    await state.finish()


# ------------------ Запуск / завершение ------------------
async def on_startup(dp):
    logger.info("Запуск бота...")
    # запустим воркер для отложенных постов

# ---------------- Webhook handler ----------------
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400)

    update = types.Update(**data)

    # Устанавливаем текущий бот для Aiogram
    Bot.set_current(bot)

    try:
        await dp.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        logger.exception("Ошибка при обработке update: %s", e)
        return web.Response(status=500, text=f"Internal Error: {str(e)}")





# ---------------- Main entry ----------------
async def main():
    # Убедимся, что admin присутствует
    init_user(ADMIN_ID)

    # Настраиваем webhook
    await bot.delete_webhook()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set: {WEBHOOK_URL}")

    # Создаём aiohttp-приложение
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_get("/", lambda request: web.Response(text="Bot is running!"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Server started at http://0.0.0.0:{PORT}")

    # Запустим воркер для отложенных постов
    asyncio.create_task(scheduled_post_worker())

    # Бесконечный цикл (держим alive)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())

