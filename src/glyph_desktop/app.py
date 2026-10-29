"""Single application startup path for both entry points."""

import sys
from importlib.metadata import version

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from .branding import application_icon
from .settings import Settings
from .theme import ThemeManager
from .window import MainWindow
from .document_state import DocumentState


def main() -> int:
    QCoreApplication.setOrganizationName("Glyph")
    QCoreApplication.setApplicationName("Glyph")
    app = QApplication(sys.argv)
    arguments = app.arguments()
    explicit = arguments[1] if len(arguments) > 1 else None
    app.setApplicationDisplayName("Glyph")
    app.setApplicationVersion(version("glyph-desktop"))
    app.setWindowIcon(application_icon())
    theme = ThemeManager(app, Settings())
    window = MainWindow(theme, app.applicationVersion(), DocumentState.default())
    window.show()
    window.startup(explicit)
    return app.exec()
