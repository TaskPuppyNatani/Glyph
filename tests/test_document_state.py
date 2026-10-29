import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication

from glyph_desktop.document_state import DocumentState, document_key
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


def make_pdf(path, pages):
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    for page in range(pages):
        if page:
            writer.newPage()
        painter.drawText(100, 200, f"Page {page + 1}")
    painter.end()


class DocumentMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = self.root / "a.pdf", self.root / "b.pdf"
        make_pdf(self.a, 8)
        make_pdf(self.b, 3)
        self.path = self.root / "state.json"
        self.theme = ThemeManager(self.app, Settings(QSettings(str(self.root / "settings.ini"),
                                                               QSettings.Format.IniFormat)))
        self.windows = []
        self.addCleanup(self.close_windows)

    def window(self):
        window = MainWindow(self.theme, "0.1.0", DocumentState(self.path))
        self.windows.append(window)
        window.resize(920, 640)
        window.show()
        self.app.processEvents()
        return window

    def close_windows(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def open(self, window, path):
        self.assertTrue(window.open_document(path))
        self.app.processEvents()

    def test_recent_order_bound_clear_and_persistence(self):
        state = DocumentState(self.path)
        for i in range(12):
            state.opened(self.root / f"{i}.pdf")
        state.opened(self.root / "3.pdf")
        state.opened(self.root / "." / "3.pdf")
        self.assertEqual(len(state.recents), 10)
        self.assertEqual(state.recents[0], document_key(self.root / "3.pdf"))
        self.assertEqual(len(set(state.recents)), 10)
        state.set_reading(self.a, {"page": 1, "offset": 0.5, "mode": "Manual", "zoom": 1.0})
        state.clear_recents()
        state.save(); state.close()
        loaded = DocumentState(self.path)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.recents, [])
        self.assertEqual(loaded.reading(self.a)["page"], 1)

    def test_missing_recent_and_session_fallback(self):
        state = DocumentState(self.path)
        state.opened(self.root / "missing.pdf")
        state.save(); state.close()
        window = self.window()
        window.startup()
        self.assertIsNone(window.document.pdf)
        self.assertEqual(window.state.recents, [])
        window.state.opened(self.root / "missing.pdf")
        with patch("glyph_desktop.window.QMessageBox.warning") as warning:
            self.assertFalse(window.open_document(self.root / "missing.pdf", recent=True))
        warning.assert_called_once()
        self.assertEqual(window.state.recents, [])

    def test_invalid_explicit_path_does_not_restore_last_session(self):
        state = DocumentState(self.path)
        state.opened(self.a)
        state.save(); state.close()
        window = self.window()
        with patch("glyph_desktop.window.QMessageBox.warning") as warning:
            window.startup(str(self.root / "missing.pdf"))
        warning.assert_called_once()
        self.assertIsNone(window.document.pdf)

    def test_recent_button_opens_and_moves_document_to_front(self):
        window = self.window()
        self.open(window, self.a)
        self.open(window, self.b)
        window.close_document()
        window.recent_layout.itemAt(2).widget().click()
        self.app.processEvents()
        self.assertEqual(window.document.path, self.a.resolve())
        self.assertEqual(window.state.recents[:2], [document_key(self.a), document_key(self.b)])

    def test_reading_memory_replacement_and_session_precedence(self):
        window = self.window()
        self.open(window, self.a)
        view = window.reader.page_view
        window.document.set_zoom("Manual", 1.5)
        view.restore_position(4, 0.65)
        expected = view.reading_position()
        self.open(window, self.b)
        self.assertEqual(window.document.page, 0)
        window.document.set_zoom("Fit Width")
        self.open(window, self.a)
        self.assertEqual(window.document.mode, "Manual")
        self.assertEqual(window.document.zoom, 1.5)
        self.assertEqual(window.document.page, expected[0])
        self.assertAlmostEqual(view.reading_position()[1], expected[1], delta=0.03)
        self.assertEqual(window.reader.thumbnail_list.currentIndex().row(), window.document.page)
        self.assertEqual(window.state.recents[:2], [document_key(self.a), document_key(self.b)])
        self.open(window, self.b)
        self.assertEqual(window.document.mode, "Fit Width")
        window.document.set_zoom("Fit Page")
        window.close()
        restored = self.window()
        restored.startup(); self.app.processEvents()
        self.assertEqual(restored.document.path, self.b.resolve())
        self.assertEqual(restored.document.mode, "Fit Page")
        restored.close()
        explicit = self.window()
        explicit.startup(str(self.a)); self.app.processEvents()
        self.assertEqual(explicit.document.path, self.a.resolve())
        self.assertEqual(explicit.document.mode, "Manual")

    def test_latest_scroll_is_saved_on_immediate_shutdown(self):
        window = self.window()
        self.open(window, self.a)
        window.document.set_zoom("Manual", 1.5)
        view = window.reader.page_view
        for page in (1, 2, 4, 5):
            view.verticalScrollBar().setValue(view.page_tops[page] + 90)
        expected = view.reading_position()
        window.close()
        restored = self.window()
        restored.startup(); self.app.processEvents()
        self.assertEqual(restored.document.page, expected[0])
        self.assertAlmostEqual(restored.reader.page_view.reading_position()[1], expected[1], delta=0.03)
        self.assertEqual((restored.document.mode, restored.document.zoom), ("Manual", 1.5))

    def test_invalid_and_out_of_range_state(self):
        self.path.write_text("{broken", encoding="utf-8")
        window = self.window()
        self.assertEqual(window.state.recents, [])
        window.state.set_reading(self.b, {"page": 99, "offset": 0.5, "mode": "Fit Page", "zoom": 1.0})
        self.open(window, self.b)
        self.assertEqual(window.document.page, 2)
        window.close_document()
        window.state.set_reading(self.a, {"page": "bad", "offset": 2, "mode": "Manual", "zoom": 9})
        self.open(window, self.a)
        self.assertEqual(window.document.page, 0)
        self.assertEqual(window.document.mode, "Fit Page")

    def test_drop_on_welcome_and_reader_restores_and_rejects_non_pdf(self):
        window = self.window()
        window.state.set_reading(self.a, {"page": 3, "offset": 0.5, "mode": "Manual", "zoom": 1.0})
        def drop(target, path):
            mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))])
            enter = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(target, enter)
            move = QDragMoveEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(target, move)
            event = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(target, event)
            self.app.processEvents()
            if enter.isAccepted():
                self.assertTrue(move.isAccepted())
            return enter.isAccepted(), event.isAccepted()
        self.assertEqual(drop(window.stack.widget(0), self.a), (True, True))
        self.assertEqual(window.document.page, 3)
        self.assertEqual(window.state.recents[0], document_key(self.a))
        self.assertEqual(drop(window.reader.page_view.viewport(), self.b), (True, True))
        self.assertEqual(window.document.path, self.b.resolve())
        self.assertEqual(drop(window.reader.page_view.viewport(), self.root / "bad.txt"), (False, False))
        self.assertEqual(window.document.path, self.b.resolve())

    def test_malformed_persisted_recents_are_bounded(self):
        self.path.write_text(json.dumps({"version": 1, "recents": [str(self.root / str(i)) for i in range(50)]}), encoding="utf-8")
        state = DocumentState(self.path)
        self.addCleanup(state.close)
        self.assertEqual(len(state.recents), 10)
