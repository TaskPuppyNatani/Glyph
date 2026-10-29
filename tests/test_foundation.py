import os
import hashlib
import tempfile
import unittest
from importlib.metadata import entry_points, version
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QRect, QSize
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from glyph_desktop.about import AboutDialog
from glyph_desktop.branding import application_icon, icon_path, logo_pixmap
from glyph_desktop.settings import Appearance, Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


class FoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "settings.ini")
        self.storage = QSettings(self.path, QSettings.Format.IniFormat)
        self.settings = Settings(self.storage)
        self.theme = ThemeManager(self.app, self.settings)

    def tearDown(self):
        self.theme.select(Appearance.SYSTEM)
        self.theme.deleteLater()
        self.app.processEvents()

    def test_entry_point_and_metadata(self):
        script = next(ep for ep in entry_points(group="console_scripts") if ep.name == "glyph")
        self.assertEqual(script.value, "glyph_desktop.app:main")
        self.assertEqual(version("glyph-desktop"), "0.1.0")

    def test_modes_apply_and_persist_without_resolving_system(self):
        for mode in (Appearance.DARK, Appearance.LIGHT, Appearance.SYSTEM):
            with self.subTest(mode=mode):
                self.theme.select(mode)
                reread = Settings(QSettings(self.path, QSettings.Format.IniFormat))
                self.assertEqual(reread.appearance(), mode)
                if mode is not Appearance.SYSTEM:
                    expected = "#25282d" if mode is Appearance.DARK else "#eeede9"
                    self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window).name(), expected)
        self.theme._system_changed(Qt.ColorScheme.Dark)
        self.assertEqual(self.settings.appearance(), Appearance.SYSTEM)

    def test_invalid_saved_mode_falls_back_to_system(self):
        self.storage.setValue("appearance", "invalid")
        self.assertEqual(self.settings.appearance(), Appearance.SYSTEM)

    def test_menu_changes_mode_and_welcome_and_about_metadata(self):
        window = MainWindow(self.theme, "0.1.0")
        self.addCleanup(window.close)
        actions = window.appearance_actions.actions()
        self.assertEqual([a.text() for a in actions], ["&System", "&Dark", "&Light"])
        actions[1].trigger()
        self.assertEqual(self.settings.appearance(), Appearance.DARK)
        self.assertTrue(actions[1].isChecked())
        self.assertIn("Glyph", [label.text() for label in window.findChildren(QLabel)])
        about = AboutDialog("0.1.0", window)
        self.assertIn("Version 0.1.0", " ".join(label.text() for label in about.findChildren(QLabel)))
        self.assertEqual(about.windowTitle(), "About Glyph")
        self.assertFalse(window.windowIcon().isNull())
        self.assertFalse(about.windowIcon().isNull())

    def test_logo_resource_handles_missing_and_loads_supplied_image(self):
        path = Path(self.tmp.name) / "logo.png"
        with patch("glyph_desktop.branding.logo_path", return_value=path):
            self.assertTrue(logo_pixmap().isNull())
            image = QPixmap(8, 8)
            image.fill(QColor("#637fa0"))
            self.assertTrue(image.save(str(path)))
            self.assertFalse(logo_pixmap().isNull())

    def test_square_logo_canvas_preserves_canonical_source_pixels(self):
        from glyph_desktop.branding import logo_path

        original = Path(__file__).resolve().parents[1] / "assets" / "taskpuppykreations-logo.png"
        canonical = QPixmap(str(original)).toImage()
        packaged = QPixmap(str(logo_path())).toImage()
        self.assertEqual(packaged.size(), QSize(2048, 2048))
        x = (packaged.width() - canonical.width()) // 2
        y = (packaged.height() - canonical.height()) // 2
        source_area = packaged.copy(QRect(x, y, canonical.width(), canonical.height()))
        pixel_format = QImage.Format.Format_RGBA8888
        self.assertEqual(bytes(source_area.convertToFormat(pixel_format).constBits()),
                         bytes(canonical.convertToFormat(pixel_format).constBits()))
        for x, y in ((0, 0), (2047, 0), (0, 2047), (2047, 2047)):
            self.assertEqual(packaged.pixelColor(x, y).alpha(), 0)
        self.assertFalse(logo_pixmap().isNull())

    def test_canonical_icon_integrity_and_sizes(self):
        original = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
        expected_sha256 = "1cbaab7aba93aa50d589d834ee79e494bda24602bcdc7f0dcd118820bc8ac893"
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), expected_sha256)
        self.assertEqual(hashlib.sha256(icon_path().read_bytes()).hexdigest(), expected_sha256)
        icon = application_icon()
        self.assertFalse(icon.isNull())
        self.assertEqual([(size.width(), size.height()) for size in icon.availableSizes()],
                         [(n, n) for n in (16, 24, 32, 48, 64, 72, 96, 128, 256)])
        self.assertFalse(icon.pixmap(64, 64).isNull())

    def test_startup_sets_application_icon(self):
        from glyph_desktop.app import main

        app = MagicMock()
        app.exec.return_value = 0
        app.arguments.return_value = ["glyph", "requested.pdf"]
        with patch("glyph_desktop.app.sys.argv", ["glyph", "requested.pdf"]), \
             patch("glyph_desktop.app.QApplication", return_value=app), \
             patch("glyph_desktop.app.ThemeManager"), \
             patch("glyph_desktop.app.DocumentState"), \
             patch("glyph_desktop.app.MainWindow") as window:
            self.assertEqual(main(), 0)
            window.return_value.startup.assert_called_once_with("requested.pdf")
        icon = app.setWindowIcon.call_args.args[0]
        self.assertFalse(icon.isNull())
        self.assertEqual(len(icon.availableSizes()), 9)

    def test_startup_uses_qt_filtered_arguments(self):
        from glyph_desktop.app import main

        document = str(Path(self.tmp.name) / "計画 draft.pdf")
        cases = (
            (["glyph", "-platform", "offscreen"], ["glyph"], None),
            (["glyph", "-platform", "offscreen", document],
             ["glyph", document], document),
        )
        for raw_arguments, qt_arguments, expected in cases:
            with self.subTest(raw_arguments=raw_arguments):
                app = MagicMock()
                app.exec.return_value = 0
                app.arguments.return_value = qt_arguments
                with patch("glyph_desktop.app.sys.argv", raw_arguments), \
                     patch("glyph_desktop.app.QApplication", return_value=app), \
                     patch("glyph_desktop.app.ThemeManager"), \
                     patch("glyph_desktop.app.DocumentState"), \
                     patch("glyph_desktop.app.MainWindow") as window:
                    self.assertEqual(main(), 0)
                    window.return_value.startup.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
