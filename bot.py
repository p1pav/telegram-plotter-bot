import asyncio
import logging
import sys
import os
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

from sheets import add_order_to_sheet, get_next_order_id, get_user_id_by_order_text

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID_STR = os.getenv("MODERATOR_GROUP_ID")

if not TOKEN or not GROUP_ID_STR:
    logging.critical("Ошибка: BOT_TOKEN или MODERATOR_GROUP_ID не заданы в .env")
    sys.exit(1)

MODERATOR_GROUP_ID = int(GROUP_ID_STR)

router = Router()

class OrderForm(StatesGroup):
    choosing_type = State()
    waiting_pages = State()
    waiting_topic = State()
    waiting_handwriting = State()
    waiting_text = State()

# --- 1. Старт ---
@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Конспект", callback_data="type_конспект")],
            [InlineKeyboardButton(text="📄 Реферат", callback_data="type_реферат")],
            [InlineKeyboardButton(text="📚 Доклад", callback_data="type_доклад")],
        ]
    )
    await message.answer(
        f"Здравствуйте, <b>{message.from_user.full_name}</b>! 👋\n\n"
        "Я помогу вам оформить заказ. Что вам нужно подготовить?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await state.set_state(OrderForm.choosing_type)

# --- 2. Тип ---
@router.callback_query(F.data.startswith("type_"), OrderForm.choosing_type)
async def process_type(callback: CallbackQuery, state: FSMContext):
    work_type = callback.data.split("_")[1]
    await state.update_data(work_type=work_type)
    await callback.message.edit_text(f"Вы выбрали: <b>{work_type.capitalize()}</b>.\n\nСколько страниц вам нужно?", parse_mode=ParseMode.HTML)
    await state.set_state(OrderForm.waiting_pages)
    await callback.answer()

# --- 3. Страницы ---
@router.message(OrderForm.waiting_pages)
async def process_pages(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Пожалуйста, отправьте количество страниц числом.")
        return
    await state.update_data(pages=message.text)
    await message.answer("Принято! Теперь напишите <b>тему</b> вашей работы.", parse_mode=ParseMode.HTML)
    await state.set_state(OrderForm.waiting_topic)

# --- 4. Тема ---
@router.message(OrderForm.waiting_topic)
async def process_topic(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, напишите тему текстом.")
        return
    await state.update_data(topic=message.text)
    await message.answer("Отлично! Теперь отправьте <b>фотографию вашего почерка</b>.", parse_mode=ParseMode.HTML)
    await state.set_state(OrderForm.waiting_handwriting)

# --- 5. Почерк ---
@router.message(OrderForm.waiting_handwriting, F.photo)
async def process_handwriting(message: Message, state: FSMContext):
    await state.update_data(handwriting_photo=message.photo[-1].file_id)
    await message.answer("И последний шаг: отправьте <b>текст</b> работы (текстом или фото).", parse_mode=ParseMode.HTML)
    await state.set_state(OrderForm.waiting_text)

@router.message(OrderForm.waiting_handwriting)
async def process_handwriting_error(message: Message):
    await message.answer("Пожалуйста, отправьте именно <b>фотографию</b> почерка.", parse_mode=ParseMode.HTML)

# --- 6. Финальный шаг ---
@router.message(OrderForm.waiting_text, F.text | F.photo | F.document)
async def process_final_text(message: Message, state: FSMContext, bot: Bot):
    print(">>> [LOG] Начало обработки финального текста")
    data = await state.get_data()
    
    if message.text:
        content_type = "📝 Текстом"
        content_preview = message.text[:100] + "..." if len(message.text) > 100 else message.text
        text_for_sheet = message.text
    elif message.photo:
        content_type = "🖼 Фотографией"
        content_preview = "(фото текста прикреплено ниже)"
        text_for_sheet = "[Фото текста]"
    elif message.document:
        content_type = "📎 Файлом"
        content_preview = f"(файл: {message.document.file_name})"
        text_for_sheet = f"[Файл: {message.document.file_name}]"
    else:
        await message.answer("Пожалуйста, отправьте текст работы текстом или фотографией.")
        return

    print(">>> [LOG] Запрос ID у Google Sheets...")
    # Оборачиваем в to_thread, чтобы не блокировать бота
    order_id = await asyncio.to_thread(get_next_order_id)
    print(f">>> [LOG] Получен ID: {order_id}")

    print(">>> [LOG] Отправка данных в Google Sheets...")
    sheets_success = await asyncio.to_thread(
        add_order_to_sheet,
        order_id=order_id,
        user_id=message.from_user.id,
        full_name=message.from_user.full_name,
        username=message.from_user.username,
        work_type=data.get('work_type'),
        pages=data.get('pages'),
        topic=data.get('topic'),
        text_preview=text_for_sheet
    )
    print(f">>> [LOG] Результат записи в Sheets: {sheets_success}")
    
    if not sheets_success:
        await bot.send_message(MODERATOR_GROUP_ID, f"⚠️ Ошибка записи в Sheets для заявки {order_id}")

    summary = (
        f"🆕 <b>Заявка {order_id}</b>\n\n"
        f"👤 <b>Пользователь:</b> {message.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"🔗 <b>Юзернейм:</b> @{message.from_user.username if message.from_user.username else 'скрыт'}\n\n"
        f"📂 <b>Тип:</b> {data.get('work_type').capitalize()}\n"
        f"📄 <b>Страниц:</b> {data.get('pages')}\n"
        f"💡 <b>Тема:</b> {data.get('topic')}\n"
        f"✍️ <b>Текст работы:</b> {content_type}\n"
        f"<blockquote>{content_preview}</blockquote>"
    )

    print(">>> [LOG] Отправка сводки в группу...")
    try:
        summary_msg = await bot.send_message(
            chat_id=MODERATOR_GROUP_ID,
            text=summary,
            parse_mode=ParseMode.HTML
        )
        
        if data.get('handwriting_photo'):
            print(">>> [LOG] Отправка фото почерка в группу...")
            await bot.send_photo(
                chat_id=MODERATOR_GROUP_ID,
                photo=data['handwriting_photo'],
                caption=f"✍️ Почерк (Заявка {order_id})",
                reply_to_message_id=summary_msg.message_id
            )

        if message.photo:
            await message.send_copy(chat_id=MODERATOR_GROUP_ID, reply_to_message_id=summary_msg.message_id)
        elif message.document:
            await message.send_copy(chat_id=MODERATOR_GROUP_ID, reply_to_message_id=summary_msg.message_id)

        print(">>> [LOG] Отправка подтверждения пользователю...")
        await message.answer(
            f"🙏 <b>Спасибо за обращение!</b>\n\n"
            f"Ваша заявка <b>{order_id}</b> принята. Модератор свяжется с вами в ближайшее время.",
            parse_mode=ParseMode.HTML
        )
        print(">>> [LOG] Заявка успешно обработана!")
        
    except Exception as e:
        logging.error(f"Ошибка при отправке в группу: {e}")
        await message.answer("⚠️ Произошла ошибка при отправке. Попробуйте позже.")
    finally:
        # Обязательно очищаем состояние, чтобы бот не завис в ожидании
        await state.clear()
        print(">>> [LOG] Состояние очищено.")

# =====================================================================
# ЛОГИКА ОТВЕТОВ
# =====================================================================

# --- 7. Модератор отвечает пользователю (из группы) ---
@router.message(F.chat.id == MODERATOR_GROUP_ID, F.reply_to_message)
async def moderator_reply_handler(message: Message, bot: Bot):
    print(">>> [LOG] Модератор сделал реплай.")
    replied_msg = message.reply_to_message
    replied_text = replied_msg.text or replied_msg.caption or ""
    
    if "Заявка #" not in replied_text:
        print(">>> [LOG] Это не ответ на заявку. Игнорируем.")
        return

    print(">>> [LOG] Ищем пользователя по тексту заявки...")
    user_id = await asyncio.to_thread(get_user_id_by_order_text, replied_text)
    
    if not user_id:
        await message.reply("⚠️ Не удалось найти пользователя для этой заявки в таблице.")
        return

    match = re.search(r'Заявка (#\d+)', replied_text)
    order_id = match.group(1) if match else ""

    text_to_user = (
        f"📬 <b>Ответ от модератора по заявке {order_id}:</b>\n\n"
        f"{message.text or 'Модератор отправил вложение.'}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✉️ <i>Чтобы ответить, сделайте <b>реплай</b> на это сообщение.</i>"
    )

    try:
        sent_msg = await bot.send_message(
            chat_id=user_id,
            text=text_to_user,
            parse_mode=ParseMode.HTML
        )
        if message.photo or message.document:
            await message.send_copy(chat_id=user_id, reply_to_message_id=sent_msg.message_id)

        await message.reply(f"✅ Ответ отправлен пользователю (ID: {user_id}).")
    except Exception as e:
        logging.error(f"Ошибка отправки пользователю {user_id}: {e}")
        await message.reply(f"❌ Не удалось отправить сообщение. Пользователь мог заблокировать бота.")

# --- 8. Пользователь отвечает модератору (из лички) ---
@router.message(F.chat.type == "private", F.reply_to_message)
async def user_reply_handler(message: Message, bot: Bot):
    print(">>> [LOG] Пользователь сделал реплай.")
    replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    
    if "заявке #" not in replied_text:
        print(">>> [LOG] Это не ответ на сообщение модератора. Игнорируем.")
        return

    summary = (
        f"💬 <b>Ответ пользователя по заявке</b>\n\n"
        f"👤 <b>От:</b> {message.from_user.full_name} (ID: <code>{message.from_user.id}</code>)\n"
        f"Текст сообщения:\n"
        f"<blockquote>{message.text or '(вложение)'}</blockquote>"
    )

    try:
        sent_group_msg = await bot.send_message(
            chat_id=MODERATOR_GROUP_ID,
            text=summary,
            parse_mode=ParseMode.HTML
        )
        if message.photo or message.document:
            await message.send_copy(chat_id=MODERATOR_GROUP_ID, reply_to_message_id=sent_group_msg.message_id)
            
        await message.answer("✅ Ваше сообщение доставлено модератору.")
    except Exception as e:
        logging.error(f"Ошибка отправки в группу: {e}")
        await message.answer("⚠️ Не удалось отправить сообщение. Попробуйте позже.")

# --- Запуск ---
async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("Бот запущен и готов к работе.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            logging.error(f"Сетевая ошибка: {e}. Перезапуск через 5 секунд...")
            import time
            time.sleep(5)
