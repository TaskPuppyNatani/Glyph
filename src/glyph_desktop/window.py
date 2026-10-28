"""The M0 application window."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from .about import AboutDialog
from .branding import application_icon, logo_pixmap
from .settings import Appearance
from .theme import ThemeManager


class MainWindow(QMainWindow):
    def __init__(self, theme: ThemeManager, version: str) -> None:
        super().__init__()
        self.theme = theme
        self.version = version
        self.setWindowTitle("Glyph")
        self.setWindowIcon(application_icon())
        self.resize(920, 640)
        self.setMinimumSize(420, 320)
        self._build_menu()
        self._build_welcome()

    def _build_menu(self) -> None:
        app_menu = self.menuBar().addMenu("&File")
        about = QAction("&About Glyph", self)
        about.triggered.connect(self.show_about)
        app_menu.addAction(about)
        app_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        app_menu.addAction(quit_action)

        appearance = self.menuBar().addMenu("&Appearance")
        self.appearance_actions = QActionGroup(self)
        self.appearance_actions.setExclusive(True)
        for mode in Appearance:
            action = QAction("&" + mode.value.title(), self, checkable=True)
            action.setChecked(mode is self.theme.mode)
            action.triggered.connect(lambda _checked=False, selected=mode: self.theme.select(selected))
            self.appearance_actions.addAction(action)
            appearance.addAction(action)

    def _build_welcome(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addStretch()
        logo = logo_pixmap()
        if not logo.isNull():
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setPixmap(logo.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(image)
        title = QLabel("Glyph")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = title.font()
        font.setPointSize(font.pointSize() + 10)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        subtitle = QLabel("A quieter space for your documents.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addStretch()
        self.setCentralWidget(container)

    def show_about(self) -> None:
        AboutDialog(self.version, self).exec()
