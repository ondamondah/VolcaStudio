import sys

from app.core.device_worker import run_device_worker


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--device-worker":
        run_device_worker(*sys.argv[2:])
        return 0

    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VolcaStudio")
    app.setOrganizationName("VolcaStudio")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
