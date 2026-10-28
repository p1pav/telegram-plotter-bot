import os
import gspread
import logging
import re
from datetime import datetime
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

CREDENTIALS_FILE = 'credentials.json'

def get_sheet():
    try:
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        sheet_name = os.getenv("SHEET_NAME", "Заявки")
        if not spreadsheet_id:
            logging.error("SPREADSHEET_ID не найден в .env")
            return None
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        client = gspread.authorize(creds)
        return client.open_by_key(spreadsheet_id).worksheet(sheet_name)
    except Exception as e:
        logging.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

def get_next_order_id():
    """Читает таблицу и возвращает следующий свободный ID (например, #4)."""
    logging.info("Запрос следующего ID из Google Sheets...")
    sheet = get_sheet()
    if not sheet:
        return "#1"

    try:
        col_a = sheet.col_values(1)
        ids = col_a[1:]
        max_id = 0
        for val in ids:
            if val.startswith("#"):
                try:
                    num = int(val[1:])
                    if num > max_id:
                        max_id = num
                except ValueError:
                    continue
        new_id = f"#{max_id + 1}"
        logging.info(f"Сгенерирован новый ID: {new_id}")
        return new_id
    except Exception as e:
        logging.error(f"Ошибка при получении ID: {e}")
        return "#1"

def add_order_to_sheet(order_id, user_id, full_name, username, work_type, pages, topic, text_preview):
    logging.info(f"Запись заявки {order_id} в Google Sheets...")
    sheet = get_sheet()
    if not sheet:
        return False

    try:
        current_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        row = [
            order_id,
            current_date,
            str(user_id),
            full_name,
            f"@{username}" if username else "скрыт",
            work_type.capitalize(),
            pages,
            topic,
            text_preview[:200]
        ]
        sheet.append_row(row, value_input_option='USER_ENTERED')
        logging.info(f"Заявка {order_id} успешно записана в Sheets.")
        return True
    except Exception as e:
        logging.error(f"Ошибка записи в Google Sheets: {e}")
        return False

def get_user_id_by_order_text(text: str):
    """Ищет ID пользователя в таблице, извлекая номер заявки из текста (например, '#1')."""
    match = re.search(r'#(\d+)', text)
    if not match:
        logging.warning("В тексте не найден номер заявки (формат #123).")
        return None
    
    order_id = f"#{match.group(1)}"
    logging.info(f"Ищем пользователя по заявке: {order_id}")
    
    sheet = get_sheet()
    if not sheet:
        return None
    try:
        # Ищем ячейку в колонке A с номером заявки
        cell = sheet.find(order_id)
        if cell:
            # Если нашли, берем значение из 3-й колонки (ID пользователя)
            user_id_str = sheet.cell(cell.row, 3).value
            logging.info(f"Найден user_id: {user_id_str} для заявки {order_id}")
            return int(user_id_str)
        logging.warning(f"Заявка {order_id} не найдена в таблице.")
        return None
    except Exception as e:
        logging.error(f"Ошибка поиска заявки {order_id}: {e}")
        return None
