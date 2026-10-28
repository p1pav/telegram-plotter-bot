import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

# Импортируем функцию записи в Google Sheets
from sheets import add_order_to_sheet

# Загружаем переменные окружения из .env
load_dotenv()

# ================= НАСТРОЙКИ =================
TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID_STR = os.getenv("MODERATOR_GROUP_ID")

if not TOKEN or not GROUP_ID_STR:
    logging.critical("Ошибка: BOT_TOKEN или MODERATOR_GROUP_ID не заданы в .env")
    sys.exit(1)

MODERATOR_GROUP_ID = int(GROUP_ID_STR)
# ============================================

router = Router()

# Хранилище для связи сообщений в группе с пользователями
user_links = {}

# --- Машина состояний (FSM) ---
class OrderForm(StatesGroup):
    choosing_type = State()
    waiting_pages = State()
    waiting_handwriting = State()
    waiting_topic = State()
    waiting_text = State()

# --- 1. Старт и выбор типа работы ---
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

# --- 2. Обработка выбора типа ---
@router.callback_query(F.data.startswith("type_"), OrderForm.choosing_type)
async def process_type(callback: CallbackQuery, state: FSMContext):
    work_type = callback.data.split("_")[1]
    await state.update_data(work_type=work_type)
    
    await callback.message.edit_text(
        f"Вы выбрали: <b>{work_type.capitalize()}</b>.\n\n"
        "Сколько страниц вам нужно? (Напишите число, например: 10)",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(OrderForm.waiting_pages)
    await callback.answer()

# --- 3. Количество страниц ---
@router.message(OrderForm.waiting_pages)
async def process_pages(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Пожалуйста, отправьте количество страниц числом (например, 5).")
        return
    
    await state.update_data(pages=message.text)
    await message.answer(
        "Отлично! Теперь отправьте <b>фотографию вашего почерка</b>, "
        "чтобы мы могли подобрать исполнителя.",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(OrderForm.waiting_handwriting)

# --- 4. Фото почерка ---
@router.message(OrderForm.waiting_handwriting, F.photo)
async def process_handwriting(message: Message, state: FSMContext):
    await state.update_data(handwriting_photo=message.photo[-1].file_id)
    
    await message.answer(
        "Принято! Теперь напишите <b>тему</b> вашей работы.",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(OrderForm.waiting_topic)

@router.message(OrderForm.waiting_handwriting)
async def process_handwriting_error(message: Message):
    await message.answer("Пожалуйста, отправьте именно <b>фотографию</b> почерка.", parse_mode=ParseMode.HTML)

# --- 5. Тема ---
@router.message(OrderForm.waiting_topic)
async def process_topic(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, напишите тему текстом.")
        return
    
    await state.update_data(topic=message.text)
    await message.answer(
        "И последний шаг: отправьте <b>текст</b> вашей работы.\n\n"
        "Вы можете отправить его обычным сообщением или <b>фотографией</b> (если текст написан от руки/на фото).",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(OrderForm.waiting_text)

# --- 6. Прием текста работы, запись в Sheets и отправка в группу ---
@router.message(OrderForm.waiting_text, F.text | F.photo | F.document)
async def process_final_text(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    
    # Определяем тип контента для сводки
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

    # 1. ЗАПИСЬ В GOOGLE SHEETS (в отдельном потоке)
    sheets_success = await asyncio.to_thread(
        add_order_to_sheet,
        user_id=message.from_user.id,
        full_name=message.from_user.full_name,
        username=message.from_user.username,
        work_type=data.get('work_type'),
        pages=data.get('pages'),
        topic=data.get('topic'),
        text_preview=text_for_sheet
    )
    
    if not sheets_success:
        await bot.send_message(
            MODERATOR_GROUP_ID,
            f"⚠️ Не удалось записать заявку в Google Sheets для пользователя {message.from_user.id}."
        )

    # 2. ОТПРАВКА В ГРУППУ
    summary = (
        f"🆕 <b>Новая заявка!</b>\n\n"
        f"👤 <b>Пользователь:</b> {message.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"🔗 <b>Юзернейм:</b> @{message.from_user.username if message.from_user.username else 'скрыт'}\n\n"
        f"📂 <b>Тип работы:</b> {data.get('work_type').capitalize()}\n"
        f"📄 <b>Страниц:</b> {data.get('pages')}\n"
        f"💡 <b>Тема:</b> {data.get('topic')}\n"
        f"✍️ <b>Текст работы:</b> {content_type}\n"
        f"<blockquote>{content_preview}</blockquote>"
    )

    try:
        summary_msg = await bot.send_message(
            chat_id=MODERATOR_GROUP_ID,
            text=summary,
            parse_mode=ParseMode.HTML
        )
        
        if data.get('handwriting_photo'):
            await bot.send_photo(
                chat_id=MODERATOR_GROUP_ID,
                photo=data['handwriting_photo'],
                caption="✍️ Почерк пользователя",
                reply_to_message_id=summary_msg.message_id
            )

        if message.photo:
            await message.send_copy(chat_id=MODERATOR_GROUP_ID, reply_to_message_id=summary_msg.message_id)
        elif message.document:
            await message.send_copy(chat_id=MODERATOR_GROUP_ID, reply_to_message_id=summary_msg.message_id)

        # Связываем сообщение в группе с пользователем
        user_links[summary_msg.message_id] = message.from_user.id
        
        await message.answer(
            "🙏 <b>Спасибо за обращение!</b>\n\n"
            "Ваша заявка принята и записана в базу. Модератор свяжется с вами в ближайшее время.",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logging.error(f"Ошибка при отправке в группу: {e}")
        await message.answer("⚠️ Произошла ошибка при отправке заявки. Попробуйте позже.")
    
    await state.clear()

# --- 7. Ответ модератора ---
@router.message(F.chat.id == MODERATOR_GROUP_ID, F.reply_to_message)
async def moderator_reply_handler(message: Message, bot: Bot):
    replied_msg_id = message.reply_to_message.message_id
    user_id = user_links.get(replied_msg_id)
    
    if not user_id:
        await message.reply("⚠️ Не удалось определить пользователя. Отвечайте реплаем на <b>первое</b> сообщение со сводкой заявки.", parse_mode=ParseMode.HTML)
        return

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"📬 <b>Ответ от модератора:</b>\n\n{message.text or 'Модератор отправил вложение.'}",
            parse_mode=ParseMode.HTML
        )
        await message.reply("✅ Ответ успешно отправлен пользователю.")
    except Exception as e:
        logging.error(f"Ошибка отправки пользователю {user_id}: {e}")
        await message.reply(f"❌ Не удалось отправить сообщение пользователю. Возможно, он заблокировал бота.\nОшибка: {e}")

# Консоль для отладки
async def console_listener(bot: Bot, dp: Dispatcher):
    """Слушает ввод в консоли и управляет ботом."""
    print("\n💻 Консоль управления запущена.")
    print("Доступные команды: 'restart' (перезапуск), 'stop' (остановка), 'help'")
    
    while True:
        # Используем to_thread, чтобы input() не блокировал работу бота
        try:
            command = await asyncio.to_thread(input, "> ")
        except EOFError:
            break
            
        command = command.strip().lower()
        
        if command == "restart":
            print("🔄 Перезапуск бота...")
            # Останавливаем polling
            await dp.stop_polling()
            # Небольшая пауза для корректного закрытия сессий
            await asyncio.sleep(1)
            print("✅ Бот перезапущен. Ожидаю сообщения...")
            # Запускаем polling заново в фоновой задаче
            asyncio.create_task(dp.start_polling(bot))
            
        elif command == "stop":
            print("🛑 Остановка бота...")
            await dp.stop_polling()
            # Выходим из программы
            raise SystemExit(0)
            
        elif command == "help":
            print("Доступные команды: 'restart' (перезапуск), 'stop' (остановка)")
        elif command == "":
            continue
        else:
            print(f"❌ Неизвестная команда: '{command}'. Напишите 'help'.")

# Запуск
async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    
    # Убираем delete_webhook, так как он иногда вызывает ошибки при нестабильной сети
    # await bot.delete_webhook(drop_pending_updates=True)
    
    logging.info("Бот запущен и готов к работе.")
    
    # Запускаем polling в фоновой задаче
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    # Запускаем консольный слушатель
    try:
        await console_listener(bot, dp)
    except SystemExit:
        print("Бот завершил работу.")
    finally:
        # При выходе корректно останавливаем polling, если он еще работает
        await dp.stop_polling()
        polling_task.cancel()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    # Цикл автоперезапуска при сетевых сбоях
    import time
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\nВыход по Ctrl+C.")
            break
        except Exception as e:
            logging.error(f"Критическая ошибка: {e}. Перезапуск через 5 секунд...")
            time.sleep(5)
