# utils/logging_setup.py
import os
import logging
import traceback
from datetime import datetime


def setup_logging():
    log_dir = os.path.join(os.path.expanduser("~"), "Documents", "ScreenOverlayToolLogs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"overlay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    log_format = '%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format))

    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        handlers=[file_handler]
    )

    logging.info(f"Логирование запущено: {log_file}")
    return log_file


def log_exceptions(func):
    """Декоратор для логирования исключений"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Ошибка в '{func.__name__}': {str(e)}")
            logging.error(traceback.format_exc())
            raise
    return wrapper