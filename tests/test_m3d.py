import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glyph_desktop.reader import Document, Reader
from test_links import write_link_pdf


class AnnotationMovementTests(unittest.TestCase):
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
        self.annotations = self.reader.annotations
        self.history = self.annotations.history

    def tearDown(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def at(self, page, x, y):
        return self.view.pdf_to_viewport(page, QPointF(x, y)).toPoint()

    def note(self, page=0, anchor=(300.0, 300.0), text="move me"):
        item = self.annotations.add_note(page, anchor, text)
        self.assertIsNotNone(item)
        return item

    def ink(self, page=0, points=((100.0, 100.0), (150.0, 125.0), (190.0, 160.0)), width=2.5):
        item = self.annotations.add_ink(page, points, width)
        self.assertIsNotNone(item)
        return item

    def clear_history(self):
        self.history.clear()

    def press(self, position):
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=position)

    def mouse_event(self, event_type, position, button, buttons, modifiers):
        point = position if isinstance(position, QPoint) else position.toPoint()
        global_position = QPointF(self.view.viewport().mapToGlobal(point))
        return QMouseEvent(event_type, QPointF(position), global_position,
                           button, buttons, modifiers)

    def move(self, position):
        QApplication.sendEvent(self.view.viewport(), self.mouse_event(
            QEvent.Type.MouseMove, position, Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def release(self, position):
        QApplication.sendEvent(self.view.viewport(), self.mouse_event(
            QEvent.Type.MouseButtonRelease, position, Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def drag(self, start, end):
        self.press(start)
        self.app.processEvents()
        self.move(end)
        self.app.processEvents()
        self.release(end)
        self.app.processEvents()

    def test_note_drag_commits_one_pdf_update_and_preserves_identity(self):
        original = self.note()
        self.clear_history()
        start = self.at(0, *original.anchor)
        end = self.at(0, original.anchor[0] + 42, original.anchor[1] + 27)
        start_pdf = self.view.viewport_to_pdf(QPointF(start), 0, clamp=True)[1]
        end_pdf = self.view.viewport_to_pdf(QPointF(end), 0, clamp=True)[1]
        self.drag(start, end)

        moved = self.annotations.get(original.id)
        self.assertIsNot(moved, original)
        self.assertEqual((moved.id, moved.kind, moved.page, moved.generation, moved.text),
                         (original.id, original.kind, original.page,
                          original.generation, original.text))
        self.assertAlmostEqual(moved.anchor[0], original.anchor[0] + end_pdf.x() - start_pdf.x(), places=6)
        self.assertAlmostEqual(moved.anchor[1], original.anchor[1] + end_pdf.y() - start_pdf.y(), places=6)
        self.assertEqual(len(self.history), 1)
        operation = self.history._undo[-1]
        self.assertEqual(operation.action, "update")
        self.assertEqual(operation.annotation, moved)
        self.assertEqual(operation.previous, original)
        self.assertEqual(self.annotations.selected_id, original.id)

    def test_click_without_drag_selects_note_without_moving_or_recording(self):
        original = self.note()
        self.clear_history()
        position = self.at(0, *original.anchor)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(self.annotations.selected_id, original.id)
        self.assertEqual(len(self.history), 0)

    def test_mouse_moves_keep_canonical_note_unchanged_until_one_release_update(self):
        original = self.note()
        self.clear_history()
        start = self.at(0, *original.anchor)
        middle = self.at(0, original.anchor[0] + 25, original.anchor[1] + 15)
        end = self.at(0, original.anchor[0] + 55, original.anchor[1] + 35)
        self.press(start)
        self.move(middle)
        self.move(end)
        self.assertTrue(self.view.annotation_moving)
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 0)
        self.release(end)
        self.assertIsNot(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 1)

    def test_note_anchor_clamps_to_page_and_cannot_move_to_another_page(self):
        original = self.note(anchor=(12, 16))
        self.clear_history()
        self.drag(self.at(0, *original.anchor), self.at(0, -200, -200))
        moved = self.annotations.get(original.id)
        self.assertGreaterEqual(moved.anchor[0], 0.0)
        self.assertGreaterEqual(moved.anchor[1], 0.0)
        self.assertLess(moved.anchor[0], 1.0)
        self.assertLess(moved.anchor[1], 1.0)
        self.assertEqual(moved.page, 0)
        self.assertEqual(len(self.history), 1)

        self.clear_history()
        self.drag(self.at(0, *moved.anchor), self.at(1, 250, 300))
        moved_again = self.annotations.get(original.id)
        self.assertEqual(moved_again.page, 0)
        self.assertLessEqual(moved_again.anchor[0], self.document.page_sizes[0][0])
        self.assertLessEqual(moved_again.anchor[1], self.document.page_sizes[0][1])

    def test_note_anchor_clamps_to_right_and_bottom_edges(self):
        width, height = self.document.page_sizes[0]
        original = self.note(anchor=(width - 12, height - 16))
        self.clear_history()

        self.drag(self.at(0, *original.anchor), self.at(0, width + 200, height + 200))

        moved = self.annotations.get(original.id)
        self.assertEqual(moved.page, original.page)
        self.assertLessEqual(moved.anchor[0], width)
        self.assertLessEqual(moved.anchor[1], height)
        self.assertLess(width - moved.anchor[0], 1.0)
        self.assertLess(height - moved.anchor[1], 1.0)
        self.assertEqual(len(self.history), 1)

    def test_ink_move_is_one_rigid_translation_and_preserves_metadata(self):
        original = self.ink()
        self.clear_history()
        start = original.points[1]
        start_position = self.at(0, *start)
        end_position = self.at(0, start[0] + 38.0, start[1] + 24.0)
        start_pdf = self.view.viewport_to_pdf(QPointF(start_position), 0, clamp=True)[1]
        end_pdf = self.view.viewport_to_pdf(QPointF(end_position), 0, clamp=True)[1]
        dx, dy = end_pdf.x() - start_pdf.x(), end_pdf.y() - start_pdf.y()
        self.drag(start_position, end_position)

        moved = self.annotations.get(original.id)
        self.assertEqual((moved.id, moved.kind, moved.page, moved.generation, moved.width),
                         (original.id, original.kind, original.page,
                          original.generation, original.width))
        self.assertEqual(len(moved.points), len(original.points))
        for before, after in zip(original.points, moved.points):
            self.assertAlmostEqual(after[0] - before[0], dx, places=5)
            self.assertAlmostEqual(after[1] - before[1], dy, places=5)
        self.assertEqual(len(self.history), 1)
        self.assertEqual(self.history._undo[-1].previous, original)
        self.assertEqual(self.history._undo[-1].annotation, moved)

    def test_ink_bounds_clamp_one_delta_without_deforming_the_stroke(self):
        original = self.ink(points=((10, 12), (30, 40), (60, 25)))
        self.clear_history()
        self.drag(self.at(0, 30, 40), self.at(0, -250, -250))
        moved = self.annotations.get(original.id)
        dx, dy = moved.points[0][0] - original.points[0][0], moved.points[0][1] - original.points[0][1]
        self.assertAlmostEqual(dx, original.width / 2 - 10, places=5)
        self.assertAlmostEqual(dy, original.width / 2 - 12, places=5)
        for before, after in zip(original.points, moved.points):
            self.assertAlmostEqual(after[0] - before[0], dx, places=7)
            self.assertAlmostEqual(after[1] - before[1], dy, places=7)
            self.assertGreaterEqual(after[0] - original.width / 2, 0)
            self.assertGreaterEqual(after[1] - original.width / 2, 0)
        self.assertEqual(len(self.history), 1)

    def test_ink_bounds_limit_positive_translation_at_right_and_bottom_edges(self):
        width, height = self.document.page_sizes[0]
        original = self.ink(points=((width - 35, height - 40),
                                   (width - 20, height - 25),
                                   (width - 10, height - 12)))
        self.clear_history()
        self.drag(self.at(0, width - 20, height - 25),
                  self.at(0, width + 200, height + 200))
        moved = self.annotations.get(original.id)
        pad = original.width / 2
        dx = moved.points[0][0] - original.points[0][0]
        dy = moved.points[0][1] - original.points[0][1]
        self.assertAlmostEqual(dx, width - pad - original.points[-1][0], places=6)
        self.assertAlmostEqual(dy, height - pad - original.points[-1][1], places=6)
        for before, after in zip(original.points, moved.points):
            self.assertAlmostEqual(after[0] - before[0], dx, places=7)
            self.assertAlmostEqual(after[1] - before[1], dy, places=7)
            self.assertLessEqual(after[0] + pad, width)
            self.assertLessEqual(after[1] + pad, height)
        self.assertEqual(len(self.history), 1)

    def test_ink_bounds_clamp_remaining_corner_combinations(self):
        width, height = self.document.page_sizes[0]
        cases = (
            (((width - 45, 15), (width - 30, 30), (width - 15, 45)),
             (width + 200, -200)),
            (((15, height - 45), (30, height - 30), (45, height - 15)),
             (-200, height + 200)),
        )

        for points, end in cases:
            with self.subTest(start=points[1], end=end):
                original = self.ink(points=points)
                self.clear_history()
                self.drag(self.at(0, *points[1]), self.at(0, *end))

                moved = self.annotations.get(original.id)
                dx = moved.points[0][0] - original.points[0][0]
                dy = moved.points[0][1] - original.points[0][1]
                pad = original.width / 2
                self.assertEqual((moved.page, moved.width), (original.page, original.width))
                for before, after in zip(original.points, moved.points):
                    self.assertAlmostEqual(after[0] - before[0], dx, places=7)
                    self.assertAlmostEqual(after[1] - before[1], dy, places=7)
                    self.assertGreaterEqual(after[0] - pad, 0)
                    self.assertLessEqual(after[0] + pad, width)
                    self.assertGreaterEqual(after[1] - pad, 0)
                    self.assertLessEqual(after[1] + pad, height)
                self.assertEqual(len(self.history), 1)

    def test_zero_clamped_translation_adds_no_history(self):
        original = self.note(anchor=(0, 0))
        self.clear_history()
        self.drag(self.at(0, *original.anchor), self.at(0, -150, -150))
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.annotations.can_undo)

    def test_escape_cancels_preview_and_keeps_selection_without_history(self):
        original = self.note()
        self.clear_history()
        self.press(self.at(0, *original.anchor))
        self.move(self.at(0, original.anchor[0] + 60, original.anchor[1] + 30))
        self.app.processEvents()
        self.assertTrue(self.view.annotation_moving)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.annotation_moving)
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(self.annotations.selected_id, original.id)
        self.assertEqual(len(self.history), 0)
        self.assertNotEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        self.release(self.at(0, original.anchor[0] + 60, original.anchor[1] + 30))
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 0)

    def test_escape_cancels_ink_preview_without_history(self):
        original = self.ink()
        self.clear_history()
        start = self.at(0, *original.points[1])
        end = self.at(0, original.points[1][0] + 65, original.points[1][1] + 35)
        self.press(start)
        self.move(end)
        self.app.processEvents()
        self.assertTrue(self.view.annotation_moving)
        self.assertEqual(self.annotations.selected_id, original.id)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.annotation_moving)
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(self.annotations.selected_id, original.id)
        self.assertEqual(len(self.history), 0)
        self.assertNotEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        self.release(end)
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 0)

    def test_ink_click_without_drag_does_not_move(self):
        original = self.ink()
        self.clear_history()
        position = self.at(0, *original.points[1])
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertIs(self.annotations.get(original.id), original)
        self.assertEqual(self.annotations.selected_id, original.id)
        self.assertEqual(len(self.history), 0)

    def test_undo_redo_restores_exact_ink_points(self):
        original = self.ink()
        self.clear_history()
        start = original.points[1]
        self.drag(self.at(0, *start), self.at(0, start[0] + 35, start[1] + 28))
        moved = self.annotations.get(original.id)
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), original)
        self.assertEqual(self.annotations.selected_id, original.id)
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.get(original.id), moved)
        self.assertEqual(self.annotations.get(original.id).points, moved.points)

    def test_sequential_moves_undo_and_redo_in_gesture_order(self):
        original = self.note(anchor=(250, 300))
        self.clear_history()
        self.drag(self.at(0, 250, 300), self.at(0, 290, 330))
        first = self.annotations.get(original.id)
        self.drag(self.at(0, *first.anchor), self.at(0, first.anchor[0] + 55, first.anchor[1] + 15))
        second = self.annotations.get(original.id)
        self.assertEqual(len(self.history), 2)
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), first)
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), original)
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.get(original.id), first)
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.get(original.id), second)

    def test_movement_interleaves_with_note_edit_and_delete(self):
        original = self.note(anchor=(250, 300), text="before")
        self.clear_history()
        self.drag(self.at(0, 250, 300), self.at(0, 290, 320))
        first_move = self.annotations.get(original.id)
        edited = self.annotations.update_note_text(original.id, "after")
        self.drag(self.at(0, *edited.anchor), self.at(0, edited.anchor[0] + 20, edited.anchor[1] + 10))
        second_move = self.annotations.get(original.id)
        self.assertEqual(len(self.history), 3)

        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), edited)
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), first_move)
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), original)
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.get(original.id), first_move)
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.get(original.id), edited)
        self.assertTrue(self.annotations.redo())
        self.assertEqual(self.annotations.get(original.id), second_move)

        self.annotations.select(original.id, self.document.generation)
        self.assertIsNotNone(self.annotations.delete_selected())
        self.assertIsNone(self.annotations.get(original.id))
        self.assertTrue(self.annotations.undo())
        self.assertEqual(self.annotations.get(original.id), second_move)

    def test_new_creation_after_undoing_movement_discards_redo(self):
        original = self.note()
        self.clear_history()
        self.drag(self.at(0, *original.anchor), self.at(0, original.anchor[0] + 35,
                                                       original.anchor[1] + 20))
        self.assertTrue(self.annotations.undo())
        self.assertTrue(self.annotations.can_redo)
        created = self.annotations.add_note(0, (420, 420), "new")
        self.assertIsNotNone(created)
        self.assertFalse(self.annotations.can_redo)
        self.assertEqual(self.annotations.selected_id, None)

    def test_creation_modes_keep_precedence_over_existing_movable_items(self):
        original_note = self.note(anchor=(300, 300))
        original_ink = self.ink(points=((100, 100), (160, 140)))
        self.clear_history()
        self.view.set_ink_mode(True)
        self.drag(self.at(0, *original_ink.points[0]), self.at(0, 165, 150))
        self.assertEqual(self.annotations.get(original_ink.id), original_ink)
        self.assertEqual(len([item for item in self.annotations.items if item.kind == "ink"]), 2)
        self.view.set_ink_mode(False)
        self.clear_history()

        with patch.object(self.reader, "place_note") as place_note:
            self.view.set_note_mode(True)
            QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=self.at(0, *original_note.anchor))
            place_note.assert_called_once()
        self.assertEqual(self.annotations.get(original_note.id), original_note)
        self.assertEqual(len(self.history), 0)

    def test_text_selection_and_text_markup_are_not_moved(self):
        start = self.at(0, 80, 89)
        end = self.at(0, 190, 89)
        self.drag(start, end)
        self.assertTrue(self.reader.selection.valid())

        for kind in ("highlight", "underline", "strikeout"):
            with self.subTest(kind=kind):
                self.annotations.clear()
                self.reader.selection.select_all(0)
                markup = self.annotations.create(kind, self.reader.selection)
                self.assertIsNotNone(markup)
                original_regions = markup.regions
                self.clear_history()

                region = markup.regions[0]
                start_pdf = QPointF(sum(point[0] for point in region) / len(region),
                                    sum(point[1] for point in region) / len(region))
                start = self.at(0, start_pdf.x(), start_pdf.y())
                end = self.at(0, start_pdf.x() + 70, start_pdf.y() + 45)
                hit = self.view.viewport_to_pdf(QPointF(start), 0, clamp=True)
                self.assertIs(self.annotations.at(*hit, note_radius=self.view._note_radius()), markup)
                QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.AltModifier, pos=start)
                self.move(end)
                self.release(end)
                self.assertFalse(self.view.annotation_moving)
                self.assertEqual(self.annotations.get(markup.id).regions, original_regions)
                self.assertEqual(len(self.history), 0)

    def test_note_drag_over_pdf_link_does_not_activate_underlying_link(self):
        original = self.note(anchor=(100, 100))
        self.clear_history()
        self.assertIsNotNone(self.view._link_at(self.at(0, 100, 100)))
        with patch.object(self.reader.links, "activate") as activate:
            self.drag(self.at(0, 100, 100), self.at(0, 145, 130))
            activate.assert_not_called()
        self.assertEqual(self.document.page, 0)
        self.assertNotEqual(self.annotations.get(original.id).anchor, original.anchor)
        self.assertEqual(self.annotations.get(original.id).page, 0)

    def test_note_over_pdf_link_keeps_link_click_when_no_drag_starts(self):
        original = self.note(anchor=(100, 100))
        self.clear_history()
        with patch.object(self.reader.links, "activate", wraps=self.reader.links.activate) as activate:
            QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=self.at(0, 100, 100))
            self.assertEqual(activate.call_count, 1)
        self.assertEqual(self.annotations.get(original.id), original)
        self.assertEqual(self.document.page, 1)
        self.assertEqual(len(self.history), 0)

    def test_focus_loss_cancels_pending_note_move_and_link_press(self):
        original = self.note(anchor=(100, 89))
        self.clear_history()
        point = self.at(0, 100, 89)
        self.assertIsNotNone(self.view._link_at(point))
        hit = self.view.viewport_to_pdf(QPointF(point))
        self.assertTrue(self.reader.selection.text_hit(*hit))

        with patch.object(self.reader.links, "activate", wraps=self.reader.links.activate) as activate:
            self.press(point)
            self.assertIsNotNone(self.view._move_gesture)
            self.assertIsNotNone(self.view._link_press)
            self.assertIsNotNone(self.view._drag_page)
            QApplication.sendEvent(
                self.view,
                QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason),
            )
            self.assertIsNone(self.view._move_gesture)
            self.assertIsNone(self.view._link_press)
            self.assertIsNone(self.view._link_press_position)
            self.assertIsNone(self.view._drag_page)
            self.assertFalse(self.view._drag_timer.isActive())
            self.release(point)
            activate.assert_not_called()

        self.assertEqual(self.document.page, 0)
        self.assertFalse(self.reader.selection.valid())
        self.assertEqual(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 0)

    def test_escape_cancels_pending_note_move_and_link_press(self):
        original = self.note(anchor=(100, 89))
        self.clear_history()
        point = self.at(0, 100, 89)
        self.assertIsNotNone(self.view._link_at(point))
        hit = self.view.viewport_to_pdf(QPointF(point))
        self.assertTrue(self.reader.selection.text_hit(*hit))

        with patch.object(self.reader.links, "activate", wraps=self.reader.links.activate) as activate:
            self.press(point)
            self.assertIsNotNone(self.view._move_gesture)
            self.assertIsNotNone(self.view._link_press)
            self.assertIsNotNone(self.view._drag_page)
            QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
            self.assertIsNone(self.view._move_gesture)
            self.assertIsNone(self.view._link_press)
            self.assertIsNone(self.view._link_press_position)
            self.assertIsNone(self.view._drag_page)
            self.assertFalse(self.view._drag_timer.isActive())
            self.release(point)
            activate.assert_not_called()

        self.assertEqual(self.document.page, 0)
        self.assertFalse(self.reader.selection.valid())
        self.assertEqual(self.annotations.get(original.id), original)
        self.assertEqual(len(self.history), 0)

    def test_link_click_without_movable_annotation_still_activates(self):
        with patch.object(self.reader.links, "activate", wraps=self.reader.links.activate) as activate:
            QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=self.at(0, 100, 100))
            self.assertEqual(activate.call_count, 1)
        self.assertEqual(self.document.page, 1)

    def test_move_uses_pdf_coordinates_across_zoom_fit_resize_and_relayout(self):
        for mode, zoom in (("Manual", 1.6), ("Fit Width", None), ("Fit Page", None)):
            with self.subTest(mode=mode):
                self.document.set_zoom(mode, zoom)
                self.reader.resize(840, 650)
                self.app.processEvents()
                original = self.note(anchor=(250, 300))
                self.clear_history()
                start = self.at(0, 250, 300)
                end = self.at(0, 290, 325)
                start_pdf = self.view.viewport_to_pdf(QPointF(start), 0, clamp=True)[1]
                end_pdf = self.view.viewport_to_pdf(QPointF(end), 0, clamp=True)[1]
                self.drag(start, end)
                moved = self.annotations.get(original.id)
                self.assertAlmostEqual(moved.anchor[0], original.anchor[0] + end_pdf.x() - start_pdf.x(), places=6)
                self.assertAlmostEqual(moved.anchor[1], original.anchor[1] + end_pdf.y() - start_pdf.y(), places=6)
                self.assertEqual(moved.page, 0)
                self.annotations.remove(original.id)
                self.history.clear()

    def test_active_drag_survives_page_recreation_and_uses_current_transform(self):
        original = self.note(page=1, anchor=(240, 300))
        self.clear_history()
        start = self.at(1, 240, 300)
        start_pdf = self.view.viewport_to_pdf(QPointF(start), 1, clamp=True)[1]
        self.press(start)
        self.move(self.at(1, 290, 330))
        self.app.processEvents()
        self.assertTrue(self.view.annotation_moving)
        self.view.set_pages(self.document.page_sizes)
        self.app.processEvents()
        end = self.at(1, 290, 330)
        end_pdf = self.view.viewport_to_pdf(QPointF(end), 1, clamp=True)[1]
        self.release(end)
        moved = self.annotations.get(original.id)
        self.assertAlmostEqual(moved.anchor[0], original.anchor[0] + end_pdf.x() - start_pdf.x(), places=6)
        self.assertAlmostEqual(moved.anchor[1], original.anchor[1] + end_pdf.y() - start_pdf.y(), places=6)
        self.assertEqual(len(self.history), 1)

    def test_active_drag_uses_current_pdf_transform_after_scroll(self):
        original = self.note(page=1, anchor=(240, 300))
        self.clear_history()
        self.view.scroll_to_page(1)
        self.app.processEvents()
        start = self.at(1, *original.anchor)
        start_pdf = self.view.viewport_to_pdf(QPointF(start), 1, clamp=True)[1]
        self.press(start)
        self.move(self.at(1, 290, 330))
        self.app.processEvents()
        self.assertTrue(self.view.annotation_moving)
        bar = self.view.verticalScrollBar()
        bar.setValue(min(bar.maximum(), bar.value() + 60))
        self.app.processEvents()
        end = self.at(1, 290, 330)
        end_pdf = self.view.viewport_to_pdf(QPointF(end), 1, clamp=True)[1]
        self.move(end)
        self.release(end)
        moved = self.annotations.get(original.id)
        self.assertAlmostEqual(moved.anchor[0], original.anchor[0] + end_pdf.x() - start_pdf.x(), places=6)
        self.assertAlmostEqual(moved.anchor[1], original.anchor[1] + end_pdf.y() - start_pdf.y(), places=6)
        self.assertEqual(moved.page, 1)
        self.assertEqual(len(self.history), 1)

    def test_close_cancels_movement_and_resets_cursor(self):
        original = self.note()
        self.clear_history()
        self.press(self.at(0, *original.anchor))
        self.move(self.at(0, original.anchor[0] + 60, original.anchor[1] + 30))
        self.app.processEvents()
        stale_release = self.at(0, original.anchor[0] + 60, original.anchor[1] + 30)
        self.document.close()
        self.assertFalse(self.view.annotation_moving)
        self.assertEqual(self.annotations.items, ())
        self.assertEqual(len(self.history), 0)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.release(stale_release)
        self.assertEqual(self.annotations.items, ())

    def test_successful_replacement_rejects_stale_release(self):
        original = self.note()
        self.clear_history()
        self.press(self.at(0, *original.anchor))
        self.move(self.at(0, original.anchor[0] + 60, original.anchor[1] + 30))
        self.app.processEvents()
        old_generation = self.document.generation
        self.assertIsNone(self.document.open(self.path))
        self.assertGreater(self.document.generation, old_generation)
        current = self.note(anchor=(350, 350), text="new document")
        self.clear_history()
        self.release(self.at(0, original.anchor[0] + 60, original.anchor[1] + 30))
        self.assertEqual(self.annotations.items, (current,))
        self.assertEqual(self.annotations.get(current.id).anchor, current.anchor)
        self.assertEqual(len(self.history), 0)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_failed_replacement_keeps_document_and_movement_coherent(self):
        original = self.note()
        self.clear_history()
        start = self.at(0, *original.anchor)
        end = self.at(0, original.anchor[0] + 45, original.anchor[1] + 25)
        start_pdf = self.view.viewport_to_pdf(QPointF(start), 0, clamp=True)[1]
        end_pdf = self.view.viewport_to_pdf(QPointF(end), 0, clamp=True)[1]
        self.press(start)
        self.move(end)
        self.app.processEvents()
        generation = self.document.generation
        self.assertIsNotNone(self.document.open(Path(self.temp.name) / "missing.pdf"))
        self.assertEqual(self.document.generation, generation)
        self.assertTrue(self.view.annotation_moving)
        self.release(end)
        moved = self.annotations.get(original.id)
        self.assertAlmostEqual(moved.anchor[0], original.anchor[0] + end_pdf.x() - start_pdf.x(), places=6)
        self.assertAlmostEqual(moved.anchor[1], original.anchor[1] + end_pdf.y() - start_pdf.y(), places=6)
        self.assertEqual(len(self.history), 1)

    def test_hover_active_release_and_escape_restore_drag_cursors(self):
        note = self.note()
        self.clear_history()
        position = self.at(0, *note.anchor)
        QApplication.sendEvent(self.view.viewport(), self.mouse_event(
            QEvent.Type.MouseMove, position, Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.OpenHandCursor)
        self.press(position)
        end = self.at(0, note.anchor[0] + 60, note.anchor[1] + 30)
        self.move(end)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        self.release(end)
        self.assertFalse(self.view.annotation_moving)
        self.assertNotEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ClosedHandCursor)


if __name__ == "__main__":
    unittest.main()
