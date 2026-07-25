"""
main.py
Entry point. Run with:  python main.py
(after `pip install -r requirements.txt` and `playwright install chromium`)
"""

import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Fouani Store Scraper")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
