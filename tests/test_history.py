import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, QPointF, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from glyph_desktop.history import AnnotationOperation, CREATE, DELETE, MAX_ANNOTATION_HISTORY
from glyph_desktop.reader import Document, Reader
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow
from test_links import write_link_pdf


class HistoryTests(unittest.TestCase):
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
        self.history = self.markup.history

    def tearDown(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def make(self, kind, page=0):
        self.reader.selection.select_all(page)
        self.assertTrue(self.reader.selection.valid())
        item = self.markup.create(kind, self.reader.selection)
        self.assertIsNotNone(item)
        return item

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

    def stroke(self, page, points):
        positions = [self.at(page, x, y) for x, y in points]
        self.press(positions[0])
        for position in positions[1:-1]:
            self.move(position)
        self.release(positions[-1])
        return positions

    def assert_same(self, restored, original):
        self.assertIs(restored, original)
        self.assertEqual(restored.id, original.id)
        self.assertEqual(restored.kind, original.kind)
        self.assertEqual(restored.page, original.page)
        self.assertEqual(restored.generation, original.generation)

    def test_markup_create_undo_redo_restores_the_same_annotation(self):
        for kind in ("highlight", "underline", "strikeout"):
            item = self.make(kind)
            self.assertEqual(len(self.history), 1)
            self.assertTrue(self.markup.can_undo)
            self.assertFalse(self.markup.can_redo)
            self.assertTrue(self.markup.undo())
            self.assertEqual(self.markup.items, ())
            self.assertFalse(self.markup.can_undo)
            self.assertTrue(self.markup.can_redo)
            self.assertTrue(self.markup.redo())
            self.assertEqual(len(self.markup.items), 1)
            self.assert_same(self.markup.items[0], item)
            self.assertEqual(self.markup.items[0].regions, item.regions)
            self.assertEqual(self.markup.items[0].text, item.text)
            self.assertTrue(self.markup.undo())
            self.assertEqual(self.markup.items, ())
            self.assertEqual(len(self.history), 0)

    def test_markup_action_creates_exactly_one_history_entry(self):
        action = self.reader.markup_actions["highlight"]
        self.reader.selection.select_all(1)
        action.trigger()
        self.assertEqual(len(self.markup.items), 1)
        self.assertEqual(len(self.history), 1)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)

    def test_ink_stroke_records_one_entry_and_undo_redo_restores_the_whole_stroke(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 120), (150, 180), (200, 260)))
        item = self.ink()[0]
        self.assertEqual(len(self.history), 1)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.ink(), ())
        self.assertEqual(self.markup.items, ())
        self.assertTrue(self.markup.redo())
        self.assertEqual(len(self.ink()), 1)
        self.assert_same(self.ink()[0], item)
        self.assertEqual(self.ink()[0].points, item.points)
        self.assertEqual(self.ink()[0].width, item.width)

    def test_mouse_moves_record_nothing_and_each_stroke_records_one_entry(self):
        self.view.set_ink_mode(True)
        self.press(self.at(0, 80, 100))
        for step in range(1, 12):
            self.move(self.at(0, 80 + step * 10, 100 + step * 6))
        self.assertEqual(len(self.history), 0)
        self.assertEqual(self.markup.items, ())
        self.release(self.at(0, 200, 170))
        self.assertEqual(len(self.history), 1)
        self.stroke(0, ((300, 120), (360, 180), (420, 240)))
        self.assertEqual(len(self.history), 2)
        self.assertEqual(len(self.ink()), 2)
        self.assertTrue(self.markup.undo())
        self.assertEqual(len(self.ink()), 1)
        self.assertTrue(self.markup.redo())
        self.assertEqual(len(self.ink()), 2)

    def test_delete_of_markup_and_ink_is_reversible(self):
        markup = self.make("highlight", 2)
        self.markup.select(markup.id, self.document.generation)
        self.assertEqual(self.markup.selected_id, markup.id)
        self.assertEqual(len(self.history), 1)
        self.assertIs(self.markup.delete_selected(), markup)
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 2)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (markup,))
        self.assert_same(self.markup.items[0], markup)
        self.assertIsNone(self.markup.selected_id)
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, ())
        self.assertIsNone(self.markup.selected_id)

        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 120), (200, 220), (260, 140)))
        ink = self.ink()[0]
        self.view.set_ink_mode(False)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 200, 220))
        self.assertEqual(self.markup.selected_id, ink.id)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Delete)
        self.assertEqual(self.ink(), ())
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.ink(), (ink,))
        self.assert_same(self.ink()[0], ink)
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.ink(), ())

    def test_non_mutating_interaction_records_no_history(self):
        item = self.make("highlight", 2)
        self.assertEqual(len(self.history), 1)

        self.markup.select(item.id, self.document.generation)
        self.assertEqual(self.markup.selected_id, item.id)
        self.markup.select(None, self.document.generation)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(2, 300, 300))
        self.reader.navigate_to_page(1)
        self.app.processEvents()
        self.document.set_zoom("Manual", 2.0)
        self.app.processEvents()
        self.reader.selection.select_all(1)
        self.assertTrue(self.reader.selection.valid())
        self.view.set_ink_mode(True)
        self.view.set_ink_mode(False)
        self.view.set_ink_mode(True)
        self.press(self.at(0, 100, 100))
        self.move(self.at(0, 160, 160))
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.ink_drawing)
        self.release(self.at(0, 220, 220))
        self.stroke(0, ((100, 100), (101, 100)))
        self.view.set_ink_mode(False)

        self.assertEqual(len(self.history), 1)
        self.assertEqual(self.markup.items, (item,))
        self.assertEqual(self.ink(), ())

    def test_undo_and_redo_run_in_reverse_and_forward_order(self):
        first = self.make("highlight")
        second = self.make("underline")
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 120), (200, 220), (260, 140)))
        self.view.set_ink_mode(False)
        ink = self.ink()[0]
        self.assertEqual(self.markup.items, (first, second, ink))

        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (first, second))
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (first,))
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, ())
        self.assertFalse(self.markup.undo())

        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (first,))
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (first, second))
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (first, second, ink))
        self.assertFalse(self.markup.redo())

    def test_new_mutation_after_undo_discards_the_redo_branch(self):
        first = self.make("highlight")
        second = self.make("underline")
        third = self.make("strikeout")
        self.assertTrue(self.markup.undo())
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (first,))
        self.assertTrue(self.markup.can_redo)

        fourth = self.make("highlight")
        self.assertFalse(self.markup.can_redo)
        self.assertEqual(self.markup.items, (first, fourth))
        self.assertFalse(self.markup.redo())
        self.assertEqual(self.markup.items, (first, fourth))
        self.assertNotIn(second.id, {item.id for item in self.markup.items})
        self.assertNotIn(third.id, {item.id for item in self.markup.items})
        self.assertEqual(len(self.history), 2)

    def test_undo_during_an_unfinished_stroke_discards_geometry_without_history(self):
        self.view.set_ink_mode(True)
        self.press(self.at(0, 300, 300))
        self.move(self.at(0, 360, 360))
        self.assertTrue(self.view.ink_drawing)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(self.view.ink_drawing)
        self.assertTrue(self.view.ink_mode)
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)
        self.release(self.at(0, 420, 420))
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)

        self.stroke(0, ((100, 100), (200, 160), (260, 220)))
        ink = self.ink()[0]
        self.assertEqual(len(self.history), 1)

        self.press(self.at(0, 300, 300))
        self.move(self.at(0, 360, 360))
        self.assertTrue(self.view.ink_drawing)
        self.assertFalse(self.reader.undo_annotation())
        self.assertFalse(self.view.ink_drawing)
        self.assertTrue(self.view.ink_mode)
        self.assertEqual(self.ink(), (ink,))
        self.assertEqual(len(self.history), 1)

        self.release(self.at(0, 420, 420))
        self.assertEqual(self.ink(), (ink,))
        self.assertEqual(len(self.history), 1)
        self.assertTrue(self.reader.undo_annotation())
        self.assertEqual(self.ink(), ())
        self.assertTrue(self.reader.redo_annotation())
        self.assertEqual(self.ink(), (ink,))

    def test_history_is_bounded_and_oldest_entries_fall_off(self):
        self.assertEqual(MAX_ANNOTATION_HISTORY, 100)
        self.assertEqual(self.history.limit, MAX_ANNOTATION_HISTORY)
        items = [self.make("highlight") for _ in range(MAX_ANNOTATION_HISTORY + 5)]
        self.assertEqual(len(self.markup.items), MAX_ANNOTATION_HISTORY + 5)
        self.assertEqual(len(self.history), MAX_ANNOTATION_HISTORY)

        for _ in range(MAX_ANNOTATION_HISTORY):
            self.assertTrue(self.markup.undo())
        self.assertFalse(self.markup.can_undo)
        self.assertFalse(self.markup.undo())
        self.assertEqual(self.markup.items, tuple(items[:5]))

        for _ in range(MAX_ANNOTATION_HISTORY):
            self.assertTrue(self.markup.redo())
        self.assertFalse(self.markup.can_redo)
        self.assertEqual(self.markup.items, tuple(items))

    def test_interleaved_create_and_delete_preserves_order_and_identity(self):
        first = self.make("highlight", 0)
        second = self.make("underline", 1)
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 120), (200, 220), (260, 140)))
        self.view.set_ink_mode(False)
        third = self.ink()[0]
        self.assertEqual(self.markup.items, (first, second, third))
        self.assertEqual(len(self.history), 3)

        self.markup.select(second.id, self.document.generation)
        self.assertIs(self.markup.delete_selected(), second)
        self.assertEqual(self.markup.items, (first, third))
        self.assertEqual(len(self.history), 4)

        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (first, second, third))
        self.assertIs(self.markup.items[1], second)
        self.assertEqual(self.markup.items[1].kind, "underline")
        self.assertEqual(self.markup.items[1].regions, second.regions)

        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (first, second))

        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (first, second, third))
        self.assertIs(self.markup.items[2], third)
        self.assertEqual(self.markup.items[2].points, third.points)

        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (first, third))
        self.assertIs(self.markup.items[0], first)
        self.assertIs(self.markup.items[1], third)
        self.assertNotIn(second.id, {item.id for item in self.markup.items})
        self.assertFalse(self.markup.can_redo)
        self.assertEqual(len(self.history), 4)

    def test_stale_history_recovery_disables_undo_and_redo_actions(self):
        item = self.make("highlight")
        stale_generation = item.generation
        self.assertIsNone(self.document.open(self.path))
        self.make("underline")
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, ())
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertTrue(self.reader.redo_action.isEnabled())

        self.history._undo.append(AnnotationOperation(CREATE, stale_generation, 0, item))
        self.reader._sync_history_actions()
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertTrue(self.reader.redo_action.isEnabled())

        self.assertFalse(self.markup.undo())
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.can_undo)
        self.assertFalse(self.markup.can_redo)
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        current = self.make("strikeout")
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, ())
        self.assertTrue(self.reader.redo_action.isEnabled())

        self.history._redo.append(AnnotationOperation(DELETE, stale_generation, 0, item))
        self.reader._sync_history_actions()
        self.assertTrue(self.reader.redo_action.isEnabled())

        self.assertFalse(self.markup.redo())
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.can_undo)
        self.assertFalse(self.markup.can_redo)
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())
        self.assertEqual(current.kind, "strikeout")

    def test_close_clears_history(self):
        self.make("highlight")
        self.assertTrue(self.markup.can_undo)
        self.document.close()
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.can_undo)
        self.assertFalse(self.markup.can_redo)
        self.assertFalse(self.markup.undo())
        self.assertFalse(self.markup.redo())

    def test_successful_replacement_clears_history(self):
        self.make("highlight")
        self.assertIsNone(self.document.open(self.path))
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.undo())
        self.assertFalse(self.markup.redo())
        self.make("underline")
        self.assertEqual(len(self.history), 1)

    def test_failed_replacement_preserves_annotations_and_history(self):
        item = self.make("highlight")
        self.assertIsNotNone(self.document.open(self.path.parent / "missing.pdf"))
        self.assertEqual(self.markup.items, (item,))
        self.assertTrue(self.markup.can_undo)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, ())
        self.assertTrue(self.markup.can_redo)
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (item,))

    def test_stale_history_cannot_mutate_a_replacement_document(self):
        item = self.make("highlight")
        stale_generation = item.generation
        self.assertIsNone(self.document.open(self.path))
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.history), 0)
        current = self.make("underline")

        self.history.record(CREATE, item, 0)
        self.assertFalse(self.markup.undo())
        self.assertEqual(self.markup.items, (current,))
        self.assertEqual(len(self.history), 0)

        self.history._redo.append(AnnotationOperation(DELETE, stale_generation, 0, item))
        self.assertFalse(self.markup.redo())
        self.assertEqual(self.markup.items, (current,))
        self.assertEqual(len(self.history._redo), 0)
        self.assertFalse(self.markup.can_undo)


class HistoryMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "links.pdf"
        write_link_pdf(self.path)
        settings = Settings(QSettings(str(Path(self.temp.name) / "settings.ini"),
                                      QSettings.Format.IniFormat))
        self.window = MainWindow(ThemeManager(self.app, settings), "0.1.0")
        self.window.reader.resize(900, 700)
        self.window.show()
        self.assertTrue(self.window.open_document(self.path))
        self.app.processEvents()
        self.reader = self.window.reader
        self.markup = self.reader.annotations
        self.view = self.reader.page_view

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def make(self, kind, page=0):
        self.reader.selection.select_all(page)
        item = self.markup.create(kind, self.reader.selection)
        self.assertIsNotNone(item)
        return item

    def at(self, page, x, y):
        return self.view.pdf_to_viewport(page, QPointF(x, y)).toPoint()

    def move(self, position):
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(position), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def stroke(self, page, points):
        positions = [self.at(page, x, y) for x, y in points]
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=positions[0])
        for position in positions[1:-1]:
            self.move(position)
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=positions[-1])

    def press_key(self, key, modifiers=Qt.KeyboardModifier.NoModifier):
        self.window.activateWindow()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()
        QTest.keyClick(self.view.viewport(), key, modifiers)
        self.app.processEvents()

    def test_edit_menu_exposes_undo_and_redo_with_standard_shortcuts(self):
        edit = next(menu for menu in self.window.findChildren(QMenu)
                    if menu.title() == "&Edit")
        texts = [action.text() for action in edit.actions()]
        self.assertIn("&Undo", texts)
        self.assertIn("&Redo", texts)
        self.assertLess(texts.index("&Undo"), texts.index("&Redo"))
        self.assertIs(edit.actions()[texts.index("&Undo")], self.reader.undo_action)
        self.assertIs(edit.actions()[texts.index("&Redo")], self.reader.redo_action)
        self.assertIn(QKeySequence(QKeySequence.StandardKey.Undo),
                      self.reader.undo_action.shortcuts())
        self.assertIn(QKeySequence("Ctrl+Z"), self.reader.undo_action.shortcuts())
        self.assertIn(QKeySequence("Ctrl+Shift+Z"), self.reader.redo_action.shortcuts())
        self.assertIn(QKeySequence(QKeySequence.StandardKey.Redo),
                      self.reader.redo_action.shortcuts())

    def test_action_enabled_state_tracks_history(self):
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        self.make("highlight")
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        self.reader.undo_action.trigger()
        self.assertEqual(self.markup.items, ())
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertTrue(self.reader.redo_action.isEnabled())

        self.make("underline")
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        self.reader.undo_action.trigger()
        self.reader.undo_action.trigger()
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertTrue(self.reader.redo_action.isEnabled())
        self.assertEqual(self.markup.items, ())

        self.reader.redo_action.trigger()
        self.assertEqual(self.markup.items, (self.markup.items[0],))
        self.assertEqual(self.markup.items[0].kind, "underline")
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

    def test_shortcuts_undo_and_redo_markup(self):
        item = self.make("highlight", 2)
        second = self.make("underline", 2)
        self.assertEqual(len(self.markup.history), 2)
        self.press_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.markup.items, (item,))
        self.assertEqual(len(self.markup.history), 1)
        self.press_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier
                       | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.markup.items, (item, second))
        self.assertIs(self.markup.items[1], second)

        self.press_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.markup.items, (item,))
        self.press_key(Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.markup.items, (item, second))
        self.press_key(Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.markup.items, (item, second))

    def test_shortcuts_undo_and_redo_ink(self):
        self.view.set_ink_mode(True)
        self.stroke(0, ((100, 120), (180, 200), (240, 150)))
        self.view.set_ink_mode(False)
        ink = tuple(item for item in self.markup.items if item.kind == "ink")
        self.assertEqual(len(ink), 1)
        self.assertEqual(len(self.markup.history), 1)

        self.press_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.markup.items, ())
        self.press_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier
                       | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.markup.items, ink)
        self.assertIs(self.markup.items[0], ink[0])

    def test_shortcut_during_an_unfinished_stroke_cancels_it_without_history(self):
        self.view.set_ink_mode(True)
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 300, 300))
        self.move(self.at(0, 360, 360))
        self.assertTrue(self.view.ink_drawing)
        self.press_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(self.view.ink_drawing)
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.markup.history), 0)
        self.assertFalse(self.reader.undo_action.isEnabled())
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton,
                           pos=self.at(0, 420, 420))
        self.assertEqual(self.markup.items, ())
        self.assertEqual(len(self.markup.history), 0)

    def test_page_number_field_keeps_its_own_undo_and_redo(self):
        item = self.make("highlight")
        field = self.reader.page
        field.clear()
        self.app.processEvents()
        self.window.activateWindow()
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()
        QTest.keyClicks(field, "12")
        self.assertEqual(field.text(), "12")

        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(field.text(), "")
        self.assertEqual(self.markup.items, (item,))
        self.assertIs(self.markup.items[0], item)
        self.assertEqual(len(self.markup.history), 1)
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        QTest.keyClick(field, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(field.text(), "12")
        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(field.text(), "")
        QTest.keyClick(field, Qt.Key.Key_Z,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(field.text(), "12")
        self.assertEqual(self.markup.items, (item,))
        self.assertEqual(len(self.markup.history), 1)
        self.assertTrue(self.reader.undo_action.isEnabled())

    def test_find_field_keeps_its_own_undo_and_redo(self):
        item = self.make("underline")
        self.reader.show_find_bar()
        self.app.processEvents()
        field = self.reader.find_bar.query_edit
        field.clear()
        self.app.processEvents()
        self.window.activateWindow()
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()
        QTest.keyClicks(field, "cat")
        self.assertEqual(field.text(), "cat")

        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(field.text(), "")
        self.assertEqual(self.markup.items, (item,))
        self.assertIs(self.markup.items[0], item)
        self.assertEqual(len(self.markup.history), 1)
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        QTest.keyClick(field, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(field.text(), "cat")
        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(field.text(), "")
        QTest.keyClick(field, Qt.Key.Key_Z,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(field.text(), "cat")
        self.assertEqual(self.markup.items, (item,))
        self.assertEqual(len(self.markup.history), 1)
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.reader.close_find_bar()
        self.app.processEvents()

    def test_close_and_replacement_disable_both_actions(self):
        self.make("highlight")
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertTrue(self.window.open_document(self.path))
        self.app.processEvents()
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

        self.make("underline")
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.window.close_document()
        self.app.processEvents()
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())
        self.assertEqual(self.markup.items, ())


if __name__ == "__main__":
    unittest.main()
