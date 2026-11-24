import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from pypdf import PdfWriter

from glyph_desktop.annotations import Rectangle, RECTANGLE_COLOR, RECTANGLE_WIDTH
from glyph_desktop.document_state import DocumentState
from glyph_desktop.reader import Document, Reader
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow
from test_links import write_link_pdf


def write_blank_pdf(path, pages=3):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as output:
        writer.write(output)


class RectangleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "blank.pdf"
        write_blank_pdf(self.path)
        self.document = Document()
        self.reader = Reader(self.document)
        self.reader.resize(900, 700)
        self.reader.show()
        self.assertIsNone(self.document.open(self.path))
        self.document.set_zoom("Manual", 0.5)
        self.app.processEvents()
        self.view = self.reader.page_view
        self.annotations = self.reader.annotations

    def tearDown(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def viewport_point(self, page, x, y):
        return self.view.pdf_to_viewport(page, QPointF(x, y)).toPoint()

    def drag(self, page, start, end):
        start_pos = self.viewport_point(page, *start)
        end_pos = self.viewport_point(page, *end)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start_pos)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end_pos),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.view.viewport(), event)
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=end_pos)
        self.app.processEvents()

    def activate_rectangle(self):
        if not self.view.rectangle_mode:
            self.reader.rectangle_action.trigger()
        self.assertTrue(self.view.rectangle_mode)

    def test_model_normalizes_and_rejects_invalid_geometry(self):
        item = self.annotations.add_rectangle(0, (200, 180, 100, 100))
        self.assertIsInstance(item, Rectangle)
        self.assertEqual(item.kind, "rectangle")
        self.assertEqual(item.page, 0)
        self.assertEqual(item.generation, self.document.generation)
        self.assertEqual(item.rect, (100.0, 100.0, 200.0, 180.0))
        self.assertEqual(len({item.id, self.annotations.add_rectangle(0, (1, 1, 2, 2)).id}), 2)
        self.assertEqual(RECTANGLE_COLOR, (200, 52, 66))
        self.assertEqual(RECTANGLE_WIDTH, 2.5)
        with self.assertRaises(FrozenInstanceError):
            item.rect = (1, 1, 2, 2)

        invalid = (
            (1, 1, 1, 2), (1, 1, 2, 1),
            (float("nan"), 1, 2, 2), (1, float("inf"), 2, 2),
            (1, 1, float("-inf"), 2), (1, 1, 10**10000, 2),
            ("1", 1, 2, 2), (True, 1, 2, 2),
            (1, 1, 900, 2), (-1, 1, 2, 2),
        )
        for rect in invalid:
            with self.subTest(rect=rect):
                self.assertIsNone(self.annotations.add_rectangle(0, rect))
        self.assertIsNone(self.annotations.add_rectangle(True, (1, 1, 2, 2)))
        self.assertIsNone(self.annotations.add_rectangle(10, (1, 1, 2, 2)))

    def test_drag_direction_normalization_and_click_does_not_create(self):
        self.activate_rectangle()
        for start, end in (
            ((100, 100), (180, 160)),
            ((180, 100), (100, 160)),
            ((100, 160), (180, 100)),
            ((180, 160), (100, 100)),
        ):
            with self.subTest(start=start, end=end):
                self.drag(0, start, end)
                self.assertEqual(self.annotations.items[-1].rect,
                                 (100.0, 100.0, 180.0, 160.0))

        click = self.viewport_point(0, 250, 250)
        count = len(self.annotations.items)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=click)
        self.assertEqual(len(self.annotations.items), count)

    def test_preview_is_painted_on_blank_page_and_escape_cancels(self):
        self.activate_rectangle()
        start = self.viewport_point(0, 110, 120)
        end = self.viewport_point(0, 250, 220)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.app.processEvents()
        self.assertTrue(self.view.rectangle_drawing)
        self.assertEqual(self.annotations.items, ())

        image = self.view.viewport().grab().toImage()
        left, right = sorted((start.x(), end.x()))
        top, bottom = sorted((start.y(), end.y()))
        preview_pixels = 0
        for y in range(max(0, top - 5), min(image.height(), bottom + 6)):
            for x in range(max(0, left - 5), min(image.width(), right + 6)):
                color = image.pixelColor(x, y)
                if color.red() > 160 and color.red() > color.green() * 1.3:
                    preview_pixels += 1
        self.assertGreater(preview_pixels, 0)

        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.rectangle_drawing)
        self.assertTrue(self.view.rectangle_mode)
        self.assertEqual(self.annotations.items, ())

    def test_rectangle_escape_latch_consumes_repeats_until_release(self):
        self.activate_rectangle()
        start = self.viewport_point(0, 110, 120)
        end = self.viewport_point(0, 250, 220)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(self.view.rectangle_drawing)

        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                          Qt.KeyboardModifier.NoModifier)
        repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                           Qt.KeyboardModifier.NoModifier, "", True, 2)
        self.assertTrue(self.view.handle_rectangle_escape(press))
        self.assertTrue(self.view.handle_rectangle_escape(repeat))
        self.assertFalse(self.view.rectangle_drawing)
        self.assertTrue(self.view.rectangle_mode)

        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Escape,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.view, release)
        self.assertFalse(self.view._escape_latch)

    def test_focus_loss_and_document_change_cancel_preview(self):
        self.activate_rectangle()
        start = self.viewport_point(0, 110, 120)
        end = self.viewport_point(0, 250, 220)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(self.view, QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(self.view.rectangle_drawing)

        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.annotations.items, ())

        self.activate_rectangle()
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(self.view.rectangle_drawing)
        self.assertIsNone(self.document.open(self.path))
        self.app.processEvents()
        self.assertFalse(self.view.rectangle_drawing)
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.annotations.items, ())

    def test_page_edges_are_clamped_to_origin_page(self):
        self.activate_rectangle()
        self.drag(0, (20, 30), (-60, -50))
        self.assertEqual(self.annotations.items[-1].rect, (0.0, 0.0, 20.0, 30.0))

        self.drag(0, (590, 760), (700, 880))
        self.assertEqual(self.annotations.items[-1].rect, (590.0, 760.0, 612.0, 792.0))

    def test_rectangle_selection_outline_delete_history_and_movement(self):
        item = self.annotations.add_rectangle(0, (100, 100, 200, 180))
        self.assertIs(self.annotations.at(0, QPointF(150, 100), rectangle_tolerance=0), item)
        self.assertIsNone(self.annotations.at(0, QPointF(150, 140), rectangle_tolerance=0))

        outline = self.viewport_point(0, 150, 100)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=outline)
        self.assertEqual(self.annotations.selected_id, item.id)

        moved_to = self.viewport_point(0, 190, 130)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=outline)
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(moved_to), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=moved_to)
        moved = self.annotations.get(item.id)
        self.assertEqual(moved.rect, (140.0, 130.0, 240.0, 210.0))
        self.assertTrue(self.reader.undo_annotation())
        self.assertEqual(self.annotations.get(item.id).rect, item.rect)
        self.assertTrue(self.reader.redo_annotation())
        self.assertEqual(self.annotations.get(item.id).rect, moved.rect)

        self.annotations.delete_selected()
        self.assertEqual(self.annotations.items, ())
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.items, (moved,))
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.items, ())

    def test_rectangle_geometry_survives_zoom_and_scroll_during_drag(self):
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        self.document.set_zoom("Manual", 0.75)
        self.app.processEvents()
        self.activate_rectangle()
        start = self.viewport_point(2, 100, 120)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)

        scroll = self.view.verticalScrollBar()
        scroll.setValue(min(scroll.maximum(), scroll.value() + 35))
        self.document.set_zoom("Manual", 1.0)
        self.app.processEvents()
        end = self.viewport_point(2, 230, 210)
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()

        item = self.annotations.items[-1]
        self.assertEqual(item.page, 2)
        for actual, expected in zip(item.rect, (100.0, 120.0, 230.0, 210.0)):
            self.assertAlmostEqual(actual, expected, delta=1.0)

    def test_snapshot_rebinds_rectangles_and_history_to_reopened_generation(self):
        item = self.annotations.add_rectangle(1, (80, 90, 160, 170))
        snapshot = self.annotations.capture_session()
        old_generation = self.document.generation

        self.assertIsNone(self.document.open(self.path))
        self.assertGreater(self.document.generation, old_generation)
        self.annotations.restore_session(snapshot)

        restored = self.annotations.items[0]
        self.assertEqual(restored.rect, item.rect)
        self.assertEqual(restored.id, item.id)
        self.assertEqual(restored.generation, self.document.generation)
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.items, ())
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.items[0].rect, item.rect)

    def test_rectangle_mode_is_exclusive_and_clears_text_selection(self):
        links_path = Path(self.temp.name) / "links.pdf"
        write_link_pdf(links_path)
        self.assertIsNone(self.document.open(links_path))
        self.app.processEvents()
        self.reader.selection.select_all(0)
        self.assertTrue(self.reader.selection.valid())

        self.reader.ink_action.trigger()
        self.assertTrue(self.view.ink_mode)
        self.reader.rectangle_action.trigger()

        self.assertTrue(self.view.rectangle_mode)
        self.assertFalse(self.view.ink_mode)
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.ink_action.isChecked())
        self.assertFalse(self.reader.note_action.isChecked())
        self.assertTrue(self.reader.rectangle_action.isChecked())
        self.assertFalse(self.reader.selection.valid())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.CrossCursor)

    def test_window_escape_cancels_rectangle_before_visible_find(self):
        settings = Settings(QSettings(str(Path(self.temp.name) / "settings.ini"),
                                      QSettings.Format.IniFormat))
        window = MainWindow(ThemeManager(self.app, settings), "0.1.0",
                            DocumentState(Path(self.temp.name) / "state.json"))
        try:
            window.resize(920, 700)
            window.show()
            self.assertTrue(window.open_document(self.path))
            self.app.processEvents()
            window.reader.show_find_bar()
            window.reader.rectangle_action.trigger()
            view = window.reader.page_view
            self.assertTrue(window.reader.find_bar.isVisible())
            self.assertTrue(view.rectangle_mode)

            start = view.pdf_to_viewport(0, QPointF(110, 120)).toPoint()
            end = view.pdf_to_viewport(0, QPointF(250, 220)).toPoint()
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QApplication.sendEvent(view.viewport(), QMouseEvent(
                QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            self.assertTrue(view.rectangle_drawing)

            QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)
            self.assertFalse(view.rectangle_drawing)
            self.assertTrue(window.reader.find_bar.isVisible())
            self.assertEqual(window.reader.annotations.items, ())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
