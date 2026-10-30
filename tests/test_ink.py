import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QCoreApplication, QEvent, QEventLoop, QPointF, QTimer, Qt)
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glyph_desktop.reader import Document, Reader
from glyph_desktop.annotations import INK_MIN_LENGTH
from test_links import write_link_pdf


class InkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "links.pdf"
        write_link_pdf(self.path)
        self.document = Document()
        self.reader = Reader(self.document)
        self.reader.resize(900, 700)
        self.reader.show()
        self.assertIsNone(self.document.open(self.path))
        self.app.processEvents()
        self.view = self.reader.page_view
        self.markup = self.reader.annotations

    def tearDown(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def ink(self):
        return tuple(item for item in self.markup.items if item.kind == "ink")

    def at(self, page, x, y):
        return self.view.pdf_to_viewport(page, QPointF(x, y)).toPoint()

    def press(self, position):
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=position)

    def move(self, position):
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(position), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def release(self, position):
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=position)

    def send_escape(self, event_type=QEvent.Type.KeyPress, auto_repeat=False):
        event = QKeyEvent(event_type, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier,
                          "", auto_repeat, 1)
        QApplication.sendEvent(self.view.viewport(), event)

    def stroke(self, page, points):
        positions = [self.at(page, x, y) for x, y in points]
        self.press(positions[0])
        for position in positions[1:-1]:
            self.move(position)
        self.release(positions[-1])
        return positions

    def make_markup(self, kind, page=0):
        self.reader.selection.select_all(page)
        self.assertTrue(self.reader.selection.valid())
        return self.markup.create(kind, self.reader.selection)

    def test_ink_stores_plain_pdf_coordinate_values(self):
        self.view.set_ink_mode(True)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.CrossCursor)
        positions = self.stroke(0, ((100, 120), (150, 180), (200, 260)))
        items = self.ink()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.kind, "ink")
        self.assertEqual(item.page, 0)
        self.assertEqual(item.generation, self.document.generation)
        self.assertEqual(len(item.points), 3)
        self.assertTrue(all(isinstance(value, float) for point in item.points for value in point))
        for stored, position in zip(item.points, positions):
            expected = self.view.viewport_to_pdf(QPointF(position), 0, clamp=True)[1]
            self.assertAlmostEqual(stored[0], expected.x(), places=6)
            self.assertAlmostEqual(stored[1], expected.y(), places=6)
        self.stroke(0, ((300, 120), (360, 180)))
        second = self.ink()[1]
        self.assertNotEqual(second.id, item.id)
        self.assertEqual(len({entry.id for entry in self.ink()}), 2)

    def test_short_strokes_below_threshold_create_nothing(self):
        self.view.set_ink_mode(True)
        position = self.at(0, 100, 100)
        self.press(position)
        self.release(position)
        self.assertEqual(self.ink(), ())
        self.stroke(0, ((100, 100), (100 + INK_MIN_LENGTH / 2, 100)))
        self.assertEqual(self.ink(), ())
        self.stroke(0, ((100, 100), (100 + INK_MIN_LENGTH * 3, 100)))
        self.assertEqual(len(self.ink()), 1)

    def test_drag_with_many_moves_creates_one_annotation(self):
        self.view.set_ink_mode(True)
        self.press(self.at(0, 80, 100))
        for step in range(1, 12):
            self.move(self.at(0, 80 + step * 10, 100 + step * 6))
        self.assertEqual(self.ink(), ())
        self.release(self.at(0, 200, 170))
        items = self.ink()
        self.assertEqual(len(items), 1)
        self.assertGreater(len(items[0].points), 2)
        self.assertEqual(len({value for value in items[0].points}), len(items[0].points))

    def test_point_cap_release_finalizes_one_bounded_ink_record(self):
        self.document.set_zoom("Manual", 1.0)
        self.app.processEvents()
        self.view.set_ink_mode(True)
        pdf_points = []
        for row in range(4):
            xs = range(10, 601) if row % 2 == 0 else range(600, 9, -1)
            pdf_points.extend((x, 100 + row * 2) for x in xs)
        positions = [self.at(0, x, y) for x, y in pdf_points]
        self.press(positions[0])
        for position in positions[1:]:
            self.move(position)
        self.assertEqual(len(self.view._ink_points), self.view.inkMaxPoints)
        self.release(positions[-1])
        self.assertFalse(self.view.ink_drawing)
        self.assertEqual(len(self.ink()), 1)
        self.assertLessEqual(len(self.ink()[0].points), self.view.inkMaxPoints)
        self.release(positions[-1])
        self.assertEqual(len(self.ink()), 1)

    def test_press_outside_page_draws_nothing_and_drag_stays_on_page(self):
        self.view.set_ink_mode(True)
        outside = self.at(0, -40, -40)
        self.press(outside)
        self.move(self.at(0, 120, 120))
        self.release(self.at(0, 160, 160))
        self.assertEqual(self.ink(), ())
        width, height = self.document.page_sizes[0]
        self.press(self.at(0, 100, 100))
        for x, y in ((-200, 400), (width + 300, height + 300), (width / 2, height / 2)):
            self.move(self.at(0, x, y))
        self.release(self.at(0, width + 500, 40))
        items = self.ink()
        self.assertEqual(len(items), 1)
        self.assertTrue(all(0.0 <= x <= width and 0.0 <= y <= height for x, y in items[0].points))

    def test_ink_geometry_survives_zoom_resize_and_page_recreation(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 120), (180, 200), (240, 150)))
        item = self.ink()[0]
        second = self.stroke(1, ((60, 80), (140, 160)))
        original = item.points
        for mode, zoom in (("Manual", 2.0), ("Fit Width", None), ("Fit Page", None)):
            self.document.set_zoom(mode, zoom)
            self.reader.resize(760, 600)
            self.app.processEvents()
            point = QPointF(*original[0])
            hit = self.view.viewport_to_pdf(self.view.pdf_to_viewport(0, point), 0, clamp=True)
            self.assertAlmostEqual(hit[1].x(), point.x(), places=5)
            self.assertAlmostEqual(hit[1].y(), point.y(), places=5)
            self.assertEqual(item.points, original)
        self.view.set_pages(self.document.page_sizes)
        self.assertEqual(self.ink(), (item, self.ink()[1]))
        self.assertEqual(self.ink()[1].page, 1)

    def test_multi_page_ink_stays_on_its_own_page(self):
        self.view.set_ink_mode(True)
        for page in (0, 2):
            self.stroke(page, ((100, 120), (200, 220)))
        items = self.ink()
        self.assertEqual([item.page for item in items], [0, 2])
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        point = QPointF(*items[1].points[0])
        hit = self.view.viewport_to_pdf(self.view.pdf_to_viewport(2, point), 2, clamp=True)
        self.assertEqual(hit[0], 2)
        self.assertAlmostEqual(hit[1].x(), point.x(), places=5)
        self.assertAlmostEqual(hit[1].y(), point.y(), places=5)

    def test_ink_mode_blocks_links_text_selection_and_markup(self):
        self.make_markup("highlight")
        self.view.set_ink_mode(True)
        with patch.object(self.reader.links, "activate") as activate:
            self.stroke(0, ((100, 100), (150, 140), (200, 120)))
            activate.assert_not_called()
        self.assertEqual(len(self.ink()), 1)
        self.assertFalse(self.reader.selection.valid())
        self.assertIsNone(self.markup.selected_id)

    def test_disabling_ink_restores_text_selection_and_link_activation(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 100), (200, 160)))
        self.assertEqual(len(self.ink()), 1)
        self.view.set_ink_mode(False)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.press(self.at(0, 80, 90))
        self.move(self.at(0, 150, 90))
        self.release(self.at(0, 150, 90))
        self.assertTrue(self.reader.selection.valid())
        self.assertEqual(len(self.ink()), 1)
        self.reader.copy_action.trigger()
        self.assertEqual(self.app.clipboard().text(), self.reader.selection.result.text())
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 100, 100))
        self.assertEqual(self.document.page, 1)

    def test_escape_cancels_in_progress_stroke_without_committing(self):
        self.view.set_ink_mode(True)
        self.press(self.at(0, 100, 100))
        self.move(self.at(0, 160, 160))
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.ink_drawing)
        self.assertTrue(self.view.ink_mode)
        self.release(self.at(0, 220, 220))
        self.assertEqual(self.ink(), ())

    def test_escape_auto_repeat_cancels_only_until_key_release(self):
        self.view.set_ink_mode(True)
        self.press(self.at(0, 100, 100))
        self.move(self.at(0, 160, 160))

        self.send_escape()
        self.assertFalse(self.view.ink_drawing)
        self.assertTrue(self.view.ink_mode)
        self.send_escape(QEvent.Type.KeyRelease, auto_repeat=True)
        self.assertTrue(self.view.ink_mode)
        self.send_escape(auto_repeat=True)
        self.assertTrue(self.view.ink_mode)
        self.assertTrue(self.reader.ink_action.isChecked())

        self.send_escape(QEvent.Type.KeyRelease)
        self.send_escape()
        self.assertFalse(self.view.ink_mode)
        self.assertFalse(self.reader.ink_action.isChecked())
        self.send_escape(QEvent.Type.KeyRelease)
        self.release(self.at(0, 220, 220))
        self.assertEqual(self.ink(), ())

    def test_escape_syncs_ink_action_and_next_trigger_reenables_mode(self):
        self.reader.ink_action.trigger()
        self.assertTrue(self.view.ink_mode)
        self.assertTrue(self.reader.ink_action.isChecked())

        self.send_escape()
        self.send_escape(QEvent.Type.KeyRelease)
        self.assertFalse(self.view.ink_mode)
        self.assertFalse(self.reader.ink_action.isChecked())

        self.reader.ink_action.trigger()
        self.assertTrue(self.view.ink_mode)
        self.assertTrue(self.reader.ink_action.isChecked())

    def test_escape_exits_ink_mode_without_clearing_ink(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 100), (200, 160)))
        before = self.ink()
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.ink_mode)
        self.assertEqual(self.ink(), before)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_escape_exits_ink_mode_then_clears_markup_selection(self):
        markup = self.make_markup("highlight")
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 100), (200, 160)))
        ink = self.ink()[0]
        self.markup.select(markup.id, self.document.generation)
        self.assertEqual(self.markup.selected_id, markup.id)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.ink_mode)
        self.assertEqual(self.markup.selected_id, markup.id)
        self.assertEqual(self.ink(), (ink,))
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertIsNone(self.markup.selected_id)
        self.assertEqual(self.ink(), (ink,))
        self.assertEqual(len(self.markup.items), 2)

    def test_ink_follows_document_replacement_and_close_lifecycle(self):
        self.reader.ink_action.trigger()
        self.stroke(0, ((100, 100), (200, 160)))
        item = self.ink()[0]
        self.assertIsNotNone(self.document.open(self.path.parent / "missing.pdf"))
        self.assertEqual(self.ink(), (item,))
        self.assertEqual(item.generation, self.document.generation)
        self.assertTrue(self.view.ink_mode)
        self.assertTrue(self.reader.ink_action.isChecked())

        self.assertIsNone(self.document.open(self.path))
        self.assertEqual(self.ink(), ())
        self.assertFalse(self.view.ink_mode)
        self.assertFalse(self.reader.ink_action.isChecked())

        self.reader.ink_action.trigger()
        self.assertTrue(self.view.ink_mode)
        self.document.close()
        self.assertEqual(self.ink(), ())
        self.assertFalse(self.view.ink_mode)
        self.assertFalse(self.reader.ink_action.isChecked())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_stale_in_progress_stroke_cannot_mutate_replacement_document(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 100), (200, 160)))
        self.assertEqual(len(self.ink()), 1)
        self.press(self.at(0, 300, 300))
        self.move(self.at(0, 360, 360))
        self.assertIsNone(self.document.open(self.path))
        self.assertFalse(self.view.ink_drawing)
        self.assertEqual(self.ink(), ())
        self.release(self.at(0, 420, 420))
        self.assertEqual(self.ink(), ())

    def test_ink_can_be_selected_and_deleted(self):
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        self.view.set_ink_mode(True)
        self.stroke(2, ((100, 120), (200, 220), (260, 140)))
        item = self.ink()[0]
        self.view.set_ink_mode(False)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(2, 200, 220))
        self.assertEqual(self.markup.selected_id, item.id)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Delete)
        self.assertEqual(self.ink(), ())
        self.assertIsNone(self.markup.selected_id)

    def test_ink_and_text_markup_coexist_without_mutating_each_other(self):
        markup = tuple(self.make_markup(kind, 2) for kind in ("highlight", "underline", "strikeout"))
        self.assertEqual([item.kind for item in markup], ["highlight", "underline", "strikeout"])
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        self.view.set_ink_mode(True)
        self.stroke(2, ((100, 120), (200, 220)))
        ink = self.ink()[0]
        self.assertEqual(tuple(item for item in self.markup.items if item.kind != "ink"), markup)
        self.markup.select(None, self.document.generation)
        self.assertEqual(self.ink(), (ink,))
        self.markup.select(markup[0].id, self.document.generation)
        self.markup.delete_selected()
        self.assertEqual(self.ink(), (ink,))
        self.assertEqual(len(self.markup.items), 3)

    def test_copy_search_and_links_remain_functional_alongside_ink(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 100), (200, 160)))
        ink = self.ink()[0]
        self.view.set_ink_mode(False)
        self.reader.selection.select_all(0)
        self.reader.copy_action.trigger()
        self.assertTrue(self.app.clipboard().text())
        self.reader.search.set_query("Link")
        loop = QEventLoop()

        def done():
            if not self.reader.search.searching:
                loop.quit()

        self.reader.search.changed.connect(done)
        QTimer.singleShot(8000, loop.quit)
        loop.exec()
        self.reader.search.changed.disconnect(done)
        results = tuple(self.reader.search.results_for_page(0))
        self.assertTrue(results)
        self.assertEqual(self.ink(), (ink,))
        self.reader.search.clear()
        self.assertEqual(self.ink(), (ink,))
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 100, 100))
        self.assertEqual(self.document.page, 1)


if __name__ == "__main__":
    unittest.main()
