import os
import gspread
import logging
from datetime import datetime
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Путь к файлу ключа
CREDENTIALS_FILE = 'credentials.json'

def get_sheet():
    """Подключается к Google Sheets и возвращает лист."""
    try:
        # Читаем ID таблицы и имя листа из env
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        sheet_name = os.getenv("SHEET_NAME", "Заявки")  # По умолчанию "Заявки"

        if not spreadsheet_id:
            logging.error("SPREADSHEET_ID не найден в .env")
            return None

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        return sheet
    except Exception as e:
        logging.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

def add_order_to_sheet(user_id, full_name, username, work_type, pages, topic, text_preview):
    """Добавляет новую строку с заявкой в таблицу."""
    sheet = get_sheet()
    if not sheet:
        return False

    try:
        # Получаем текущую дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ
        current_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        row = [
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
        logging.info(f"Заявка от {user_id} добавлена в Google Sheets.")
        return True
    except Exception as e:
        logging.error(f"Ошибка записи в Google Sheets: {e}")
        return False
