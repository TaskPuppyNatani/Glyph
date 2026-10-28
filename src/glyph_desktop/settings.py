"""Persistent preferences for the application."""

from enum import Enum

from PySide6.QtCore import QSettings


class Appearance(str, Enum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class Settings:
    def __init__(self, storage: QSettings | None = None) -> None:
        self.storage = storage if storage is not None else QSettings()

    def appearance(self) -> Appearance:
        try:
            return Appearance(self.storage.value("appearance", Appearance.SYSTEM.value))
        except (TypeError, ValueError):
            return Appearance.SYSTEM

    def set_appearance(self, mode: Appearance) -> None:
        self.storage.setValue("appearance", mode.value)
        self.storage.sync()
