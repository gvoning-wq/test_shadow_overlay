# main.py
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import logging
from utils.logging_setup import setup_logging
from app import ScreenOverlayApp


def main():
    try:
        setup_logging()
        logging.info("Запуск приложения на PyQt6")

        app = ScreenOverlayApp()
        app.run()

    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        raise


if __name__ == "__main__":
    main()