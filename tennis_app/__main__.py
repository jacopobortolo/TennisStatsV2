"""
Entry point for the Tennis Analytics V2 desktop application.

Run with:
    python -m tennis_app
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from .ui.main_window import MainWindow
    from .ui.theme import build_stylesheet, enable_dark_title_bar, load_fonts

    app = QApplication(sys.argv)
    app.setApplicationName("Tennis Analytics")
    app.setStyle("Fusion")
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    window = MainWindow()
    window.show()

    # Dark title bar on Windows
    if sys.platform == "win32":
        enable_dark_title_bar(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
