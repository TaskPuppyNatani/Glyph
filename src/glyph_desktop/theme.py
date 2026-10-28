"""Application-wide appearance selection and palette definitions."""

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from .settings import Appearance, Settings


# Palette roles are centralized so menus, dialogs and controls share the same colors.
_COLORS = {
    Appearance.DARK: {
        "window": "#25282d", "text": "#ecedef", "base": "#1e2125",
        "alternate": "#30343a", "button": "#34383e", "border": "#50565e",
        "muted": "#a8afb7", "highlight": "#637fa0", "highlighted": "#ffffff",
    },
    Appearance.LIGHT: {
        "window": "#eeede9", "text": "#24292e", "base": "#f7f6f2",
        "alternate": "#e6e5e0", "button": "#e4e3de", "border": "#b8bab8",
        "muted": "#697178", "highlight": "#526f8b", "highlighted": "#ffffff",
    },
}


def make_palette(mode: Appearance) -> QPalette:
    colors = _COLORS[mode]
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "window", QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "base", QPalette.ColorRole.AlternateBase: "alternate",
        QPalette.ColorRole.Text: "text", QPalette.ColorRole.Button: "button",
        QPalette.ColorRole.ButtonText: "text", QPalette.ColorRole.BrightText: "highlighted",
        QPalette.ColorRole.ToolTipBase: "base", QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.Highlight: "highlight", QPalette.ColorRole.HighlightedText: "highlighted",
        QPalette.ColorRole.Link: "highlight", QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Dark: "border", QPalette.ColorRole.PlaceholderText: "muted",
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(colors[color]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["muted"]))
    return palette


class ThemeManager(QObject):
    def __init__(self, app: QApplication, settings: Settings) -> None:
        super().__init__(app)
        self.app = app
        self.settings = settings
        self.native_style = app.style().objectName()
        self.mode = settings.appearance()
        # Qt emits this when the platform exposes a dynamic appearance change.
        app.styleHints().colorSchemeChanged.connect(self._system_changed)
        self.apply()

    def select(self, mode: Appearance) -> None:
        self.mode = mode
        self.settings.set_appearance(mode)
        self.apply()

    def apply(self) -> None:
        if self.mode is Appearance.SYSTEM:
            style = QStyleFactory.create(self.native_style)
            if style is not None:
                self.app.setStyle(style)
            self.app.setPalette(QPalette())
        else:
            self.app.setStyle("Fusion")
            self.app.setPalette(make_palette(self.mode))

    def _system_changed(self, _scheme: object) -> None:
        if self.mode is Appearance.SYSTEM:
            self.apply()
