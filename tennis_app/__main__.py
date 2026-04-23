"""
Entry point for the Tennis Analytics V2 desktop application.

Run with:
    python -m tennis_app                # sync cloud, then open UI
    python -m tennis_app --no-sync      # skip cloud sync (offline mode)
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def _run_cloud_sync(splash, app):
    """Try to merge cloud data into local DB. Never raises."""
    try:
        from cloud.sync import sync_cloud_to_local
    except Exception as exc:
        logger.info("Cloud sync module unavailable: %s", exc)
        return

    def _on_progress(table, n):
        if splash is not None:
            splash.showMessage(
                f"Sincronizzazione cloud...\n{table}: {n} righe",
                alignment=0x0004 | 0x0080,  # Qt.AlignBottom | Qt.AlignHCenter
                color=0xFFFFFF,
            )
            app.processEvents()

    try:
        if splash is not None:
            splash.showMessage(
                "Connessione a Turso...",
                alignment=0x0004 | 0x0080,
                color=0xFFFFFF,
            )
            app.processEvents()
        result = sync_cloud_to_local(progress_callback=_on_progress)
        elapsed = result.pop("_elapsed_seconds", 0.0)
        total = sum(v for v in result.values() if isinstance(v, int))
        logger.info("Cloud sync OK: %d total rows in %.1fs", total, elapsed)
    except FileNotFoundError as exc:
        # First run: local DB not yet initialized — open app, user can
        # import CSVs, then re-launch to pick up cloud data.
        logger.info("Skipping cloud sync (first run): %s", exc)
    except Exception:
        logger.exception("Cloud sync failed; opening app with stale data")


def main():
    from PySide6.QtWidgets import QApplication, QSplashScreen
    from PySide6.QtGui import QPixmap, QColor
    from .ui.main_window import MainWindow
    from .ui.theme import build_stylesheet, enable_dark_title_bar, load_fonts

    no_sync = "--no-sync" in sys.argv
    if no_sync:
        sys.argv.remove("--no-sync")

    app = QApplication(sys.argv)
    app.setApplicationName("Tennis Analytics")
    app.setStyle("Fusion")
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    splash = None
    if not no_sync:
        pix = QPixmap(520, 220)
        pix.fill(QColor("#101218"))
        splash = QSplashScreen(pix)
        splash.show()
        app.processEvents()
        _run_cloud_sync(splash, app)

    window = MainWindow()
    window.show()
    if splash is not None:
        splash.finish(window)

    if sys.platform == "win32":
        enable_dark_title_bar(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
