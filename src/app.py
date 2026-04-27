"""
Sideswipe — entry point for the PyQt6 desktop app.
Run: .venv/bin/python src/app.py
"""

import sys
import os

# Ensure src/ is importable as root package for ui.* and gesture_engine
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from ui.main_window import SideswipeWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Sideswipe')
    app.setApplicationDisplayName('Sideswipe')
    app.setOrganizationName('Sideswipe')

    logo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Sideswipe-Logo.png')
    if os.path.exists(logo):
        app.setWindowIcon(QIcon(logo))

    win = SideswipeWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
