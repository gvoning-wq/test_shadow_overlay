# app.py
from PyQt6.QtWidgets import QApplication
from ui.overlay_window import OverlayAppUI
import sys


class ScreenOverlayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.ui = OverlayAppUI()

    def run(self):
        self.ui.show()
        sys.exit(self.app.exec())