"""
main.py
-------
Entry point for the OSM Route Finder desktop application.
Run with:  python main.py
"""

import sys

from PyQt6.QtWidgets import QApplication

from ui_main import MainWindow, DARK_STYLESHEET


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
