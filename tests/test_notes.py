import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QCoreApplication, QEvent, QSettings, QPointF, QTimer, Qt)
from PySide6.QtGui import QContextMenuEvent, QImage, QMouseEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from glyph_desktop.annotations import Note
from glyph_desktop.note_editor import NoteEditor
from glyph_desktop.reader import Document, Reader
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow
from test_links import write_link_pdf


class NoteTests(unittest.TestCase):
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

    def at(self, page, x, y):
        return self.view.pdf_to_viewport(page, QPointF(x, y)).toPoint()

    def notes(self):
        return tuple(item for item in self.markup.items if item.kind == "note")

    def drive(self, text=None, accept=True, before=None):
        """Arm the transient editor driver before the modal dialog opens."""
        def act():
            dialog = QApplication.activeModalWidget()
            self.assertIsInstance(dialog, NoteEditor)
            if before is not None:
                before(dialog)
            if text is not None:
                dialog.body.setPlainText(text)
            dialog.accept() if accept else dialog.reject()
        QTimer.singleShot(0, act)

    def place(self, page, x, y, text="note text", accept=True, before=None):
        self.drive(text=text, accept=accept, before=before)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(page, x, y))
        self.app.processEvents()

    def edit(self, item, text, accept=True):
        self.drive(text=text, accept=accept)
        self.reader.edit_note(item)
        self.app.processEvents()

    def note_mode(self, enabled=True):
        if self.view.note_mode != enabled:
            self.reader.note_action.trigger()

    def move(self, position):
        QApplication.sendEvent(self.view.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, QPointF(position), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def test_note_stores_plain_immutable_pdf_values(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first line\nsecond line")
        items = self.notes()
        self.assertEqual(len(items), 1)
        note = items[0]
        self.assertIsInstance(note, Note)
        self.assertEqual(note.kind, "note")
        self.assertEqual(note.page, 0)
        self.assertEqual(note.generation, self.document.generation)
        self.assertIsInstance(note.anchor, tuple)
        self.assertTrue(all(isinstance(value, float) for value in note.anchor))
        self.assertAlmostEqual(note.anchor[0], 400.0, delta=1.0)
        self.assertAlmostEqual(note.anchor[1], 500.0, delta=1.0)
        self.assertEqual(note.text, "first line\nsecond line")
        self.assertEqual(len({item.id for item in items}), 1)
        self.assertFalse(note.id.isdigit())
        with self.assertRaises(FrozenInstanceError):
            note.text = "mutated"
        operation = self.history._undo[-1]
        self.assertIs(operation.annotation, note)
        self.assertEqual(operation.generation, note.generation)

    def test_note_ids_are_unique_across_placements(self):
        self.note_mode(True)
        self.place(0, 300, 300, text="one")
        self.place(0, 320, 320, text="two")
        ids = {note.id for note in self.notes()}
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(self.history), 2)

    def test_cancelled_and_blank_editors_create_nothing(self):
        self.note_mode(True)
        self.place(0, 300, 300, text="discarded", accept=False)
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.can_undo)
        self.place(0, 320, 320, text="   \n  ")
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)
        self.place(0, 340, 340, text="")
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)

    def test_click_outside_a_page_creates_no_note(self):
        self.note_mode(True)
        opened = []

        def check():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, NoteEditor):
                opened.append(dialog)
                dialog.reject()
        QTimer.singleShot(0, check)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, -400, -400))
        self.app.processEvents()
        self.assertEqual(opened, [])
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)

    def test_note_mode_blocks_links_text_selection_and_annotation_selection(self):
        markup = self.reader.markup_actions["highlight"]
        self.reader.selection.select_all(0)
        markup.trigger()
        highlighted = self.markup.items[0]
        self.note_mode(True)
        with patch.object(self.reader.links, "activate") as activate:
            self.place(0, 80, 90, text="on a link")
            activate.assert_not_called()
        self.assertEqual(len(self.notes()), 1)
        self.assertFalse(self.reader.selection.valid())
        self.assertIsNone(self.markup.selected_id)
        self.assertEqual(self.markup.items[0], highlighted)
        self.assertEqual(len(self.history), 2)

    def test_disabling_note_mode_restores_links_and_text_selection(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="placed")
        self.note_mode(False)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 80, 90))
        self.assertEqual(self.document.page, 1)
        self.assertEqual(len(self.notes()), 1)
        self.reader.navigate_to_page(0)
        self.app.processEvents()
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 120, 90))
        self.move(self.at(0, 200, 90))
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton,
                           pos=self.at(0, 200, 90))
        self.assertTrue(self.reader.selection.valid())
        self.reader.copy_action.trigger()
        self.assertEqual(self.app.clipboard().text(), self.reader.selection.result.text())
        self.assertEqual(len(self.notes()), 1)

    def test_note_anchor_stays_aligned_through_zoom_resize_relayout_and_recreation(self):
        self.note_mode(True)
        self.place(0, 300, 420, text="anchored")
        note = self.notes()[0]
        original = note.anchor
        for mode, zoom in (("Manual", 2.0), ("Fit Width", None), ("Fit Page", None)):
            self.document.set_zoom(mode, zoom)
            self.reader.resize(760, 600)
            self.app.processEvents()
            point = QPointF(*original)
            mapped = self.view.pdf_to_viewport(0, point)
            hit = self.view.viewport_to_pdf(mapped, 0, clamp=True)
            self.assertAlmostEqual(hit[1].x(), point.x(), places=5)
            self.assertAlmostEqual(hit[1].y(), point.y(), places=5)
            self.assertEqual(note.anchor, original)
            self.assertIs(self.markup.at(0, hit[1], note_radius=self.view._note_radius()), note)
            self.assertIs(self.markup.at(0, QPointF(original[0] + 200, original[1]),
                                         note_radius=self.view._note_radius()), None)
        self.view.verticalScrollBar().setValue(self.view.page_tops[1])
        self.app.processEvents()
        self.view.set_pages(self.document.page_sizes)
        self.app.processEvents()
        self.assertEqual(self.notes(), (note,))
        self.assertEqual(self.notes()[0].anchor, original)

    def test_marker_screen_size_stays_fixed_while_anchor_follows_zoom(self):
        self.note_mode(True)
        self.place(0, 300, 420, text="fixed marker")
        note = self.notes()[0]
        widths = {}
        for zoom in (0.5, 2.0):
            self.document.set_zoom("Manual", zoom)
            self.app.processEvents()
            radius_pdf = self.view._note_radius()
            near = self.view.pdf_to_viewport(0, QPointF(*note.anchor))
            far = self.view.pdf_to_viewport(0, QPointF(note.anchor[0] + radius_pdf,
                                                       note.anchor[1]))
            widths[zoom] = far.x() - near.x()
            self.assertAlmostEqual(radius_pdf * zoom, 9.0, places=4)
        self.assertAlmostEqual(widths[0.5], widths[2.0], places=4)
        self.assertAlmostEqual(widths[0.5], 9.0, places=1)

    def test_multi_page_notes_stay_on_their_own_pages(self):
        self.note_mode(True)
        self.place(0, 200, 200, text="page zero")
        self.place(2, 300, 400, text="page two")
        self.note_mode(False)
        notes = self.notes()
        self.assertEqual([note.page for note in notes], [0, 2])
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        point = QPointF(*notes[1].anchor)
        hit = self.view.viewport_to_pdf(self.view.pdf_to_viewport(2, point), 2, clamp=True)
        self.assertEqual(hit[0], 2)
        self.assertIs(self.markup.at(2, hit[1], note_radius=self.view._note_radius()), notes[1])
        self.assertIs(self.markup.at(0, QPointF(*notes[1].anchor),
                                    note_radius=self.view._note_radius()), None)

    def test_click_selects_note_and_delete_leaves_other_annotations_intact(self):
        highlighted = self.make_markup("highlight", 0)
        self.note_mode(True)
        self.place(0, 400, 500, text="selectable")
        self.note_mode(False)
        note = self.notes()[0]
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 400, 500))
        self.assertEqual(self.markup.selected_id, note.id)
        self.assertEqual(len(self.history), 2)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Delete)
        self.assertEqual(self.notes(), ())
        self.assertEqual(self.markup.items, (highlighted,))
        self.assertIsNone(self.markup.selected_id)
        self.assertEqual(len(self.history), 3)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.notes(), (note,))
        self.assertEqual(self.markup.items, (highlighted, note))
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (highlighted,))

    def make_markup(self, kind, page=0):
        self.reader.selection.select_all(page)
        item = self.markup.create(kind, self.reader.selection)
        self.assertIsNotNone(item)
        return item

    def test_note_mode_and_ink_mode_are_mutually_exclusive(self):
        self.reader.ink_action.trigger()
        self.assertTrue(self.view.ink_mode)
        self.reader.note_action.trigger()
        self.assertTrue(self.view.note_mode)
        self.assertFalse(self.view.ink_mode)
        self.assertFalse(self.reader.ink_action.isChecked())
        self.assertTrue(self.reader.note_action.isChecked())

        self.reader.ink_action.trigger()
        self.assertTrue(self.view.ink_mode)
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.note_action.isChecked())
        self.assertTrue(self.reader.ink_action.isChecked())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.CrossCursor)

    def test_note_action_and_esc_state_stay_synchronized(self):
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.note_action.isChecked())
        self.reader.note_action.trigger()
        self.assertTrue(self.view.note_mode)
        self.assertTrue(self.reader.note_action.isChecked())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.CrossCursor)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.note_action.isChecked())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(self.notes(), ())

    def test_escape_never_deletes_a_note(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="kept")
        note = self.notes()[0]
        self.markup.select(note.id, self.document.generation)
        for _ in range(3):
            QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
            self.app.processEvents()
        self.assertEqual(self.notes(), (note,))
        self.assertEqual(self.markup.items, (note,))

    def test_context_menu_over_a_note_offers_edit_note_and_selects_it(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="menu note")
        self.note_mode(False)
        note = self.notes()[0]
        position = self.at(0, 400, 500)
        seen = []

        def inspect_menu():
            menu = QApplication.activePopupWidget()
            if isinstance(menu, QMenu):
                seen.extend(menu.actions())
                menu.close()
        QTimer.singleShot(0, inspect_menu)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                  self.view.viewport().mapToGlobal(position))
        QApplication.sendEvent(self.view.viewport(), event)
        self.app.processEvents()
        self.assertIn(self.reader.edit_note_action, seen)
        self.assertNotIn(self.reader.copy_action, seen)
        self.assertEqual(self.markup.selected_id, note.id)
        self.assertTrue(self.reader.edit_note_action.isEnabled())
        self.assertEqual(len(self.history), 1)

    def test_context_menu_over_text_selection_still_offers_only_copy(self):
        self.reader.selection.select_all(0)
        position = self.view.pdf_to_viewport(
            0, self.reader.selection.result.bounds()[0].boundingRect().center()).toPoint()
        seen = []

        def inspect_menu():
            menu = QApplication.activePopupWidget()
            if isinstance(menu, QMenu):
                seen.extend(menu.actions())
                menu.close()
        QTimer.singleShot(0, inspect_menu)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                  self.view.viewport().mapToGlobal(position))
        QApplication.sendEvent(self.view.viewport(), event)
        self.app.processEvents()
        self.assertEqual(seen, [self.reader.copy_action])
        self.assertFalse(self.reader.edit_note_action.isEnabled())

    def test_editing_a_note_preserves_identity_and_changes_only_text(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.note_mode(False)
        note = self.notes()[0]
        edited = self.edit_and_return(note, "second\nline")
        self.assertEqual(edited.id, note.id)
        self.assertEqual(edited.page, note.page)
        self.assertEqual(edited.anchor, note.anchor)
        self.assertEqual(edited.generation, note.generation)
        self.assertEqual(edited.kind, "note")
        self.assertEqual(edited.text, "second\nline")
        self.assertEqual(self.notes(), (edited,))
        self.assertEqual(len(self.history), 2)

    def edit_and_return(self, note, text, accept=True):
        self.edit(note, text, accept=accept)
        return self.markup.get(note.id)

    def test_cancelled_edit_changes_nothing_and_records_nothing(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.note_mode(False)
        note = self.notes()[0]
        before = len(self.history)
        self.edit(note, "discarded", accept=False)
        self.assertEqual(self.markup.get(note.id).text, "first")
        self.assertEqual(len(self.history), before)
        self.assertFalse(self.markup.can_redo)

    def test_edit_with_identical_or_blank_text_records_nothing(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.note_mode(False)
        note = self.notes()[0]
        self.edit(note, "first")
        self.assertEqual(len(self.history), 1)
        self.edit(note, "   ")
        self.assertEqual(len(self.history), 1)
        self.assertEqual(self.markup.get(note.id).text, "first")

    def test_note_create_edit_and_delete_undo_redo_cycles(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.note_mode(False)
        note = self.notes()[0]
        self.assertEqual(len(self.history), 1)

        self.assertTrue(self.markup.undo())
        self.assertEqual(self.notes(), ())
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.notes(), (note,))
        self.assertIs(self.notes()[0], note)

        edited = self.edit_and_return(note, "second")
        self.assertEqual(len(self.history), 2)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.get(note.id).text, "first")
        self.assertEqual(self.markup.get(note.id).id, note.id)
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.get(note.id).text, "second")
        self.assertIs(self.markup.items[0], edited)

        self.markup.select(note.id, self.document.generation)
        self.markup.delete_selected()
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 3)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.notes(), (edited,))
        self.assertEqual(self.notes()[0].text, "second")
        self.assertEqual(self.notes()[0].anchor, note.anchor)
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.notes(), ())

    def test_update_undo_redo_keeps_collection_position_and_other_annotations(self):
        highlighted = self.make_markup("highlight", 0)
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.note_mode(False)
        self.reader.ink_action.trigger()
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 100, 120))
        self.move(self.at(0, 180, 200))
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton,
                           pos=self.at(0, 240, 150))
        self.reader.ink_action.trigger()
        ink = tuple(item for item in self.markup.items if item.kind == "ink")[0]
        note = self.markup.get(self.notes()[0].id)
        self.assertEqual(self.markup.items, (highlighted, note, ink))

        edited = self.edit_and_return(note, "second")
        self.assertEqual(self.markup.items, (highlighted, edited, ink))
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.markup.items, (highlighted, note, ink))
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.markup.items, (highlighted, edited, ink))
        self.assertEqual(highlighted.regions, highlighted.regions)
        self.assertEqual(ink.points, ink.points)

    def test_typing_in_the_editor_does_not_record_annotation_history(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="seed")
        self.note_mode(False)
        note = self.notes()[0]
        before = len(self.history)

        def type_then_undo(dialog):
            dialog.body.moveCursor(QTextCursor.MoveOperation.End)
            QTest.keyClicks(dialog.body, " more")
            QTest.keyClick(dialog.body, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(dialog.body, Qt.Key.Key_Z,
                           Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.drive(text=None, accept=True, before=type_then_undo)
        self.reader.edit_note(note)
        self.app.processEvents()
        self.assertEqual(len(self.history), before + 1)
        self.assertEqual(self.markup.get(note.id).text, "seed more")
        self.assertEqual(len(self.markup.items), 1)

    def test_new_mutation_after_undo_discards_the_note_edit_redo_branch(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.note_mode(False)
        note = self.notes()[0]
        self.edit(note, "second")
        self.assertTrue(self.markup.undo())
        self.assertTrue(self.markup.can_redo)
        other = self.make_markup("underline", 1)
        self.assertFalse(self.markup.can_redo)
        self.assertFalse(self.markup.redo())
        self.assertEqual(self.markup.get(note.id).text, "first")
        self.assertEqual(self.markup.items, (note, other))

    def test_note_marker_paints_and_shows_a_selected_state(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="painted")
        self.note_mode(False)
        note = self.notes()[0]

        def paint():
            image = QImage(self.view.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            self.view.render(image, self.view.rect().topLeft(), self.view.rect())
            return image

        self.markup.select(None, self.document.generation)
        self.app.processEvents()
        unselected = paint()
        self.markup.select(note.id, self.document.generation)
        self.app.processEvents()
        selected = paint()
        self.assertNotEqual(unselected, selected)
        self.markup.delete_selected()
        self.app.processEvents()
        self.assertEqual(paint().isNull(), False)
        self.assertEqual(self.notes(), ())

    def test_escape_inside_the_editor_cancels_without_mutating(self):
        def press_escape(dialog):
            QTest.keyClick(dialog.body, Qt.Key.Key_Escape)

        self.note_mode(True)
        self.drive(accept=False, before=press_escape)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 400, 500))
        self.app.processEvents()
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)
        self.assertTrue(self.view.note_mode)

        self.place(0, 400, 500, text="note text")
        note = self.notes()[0]
        self.assertEqual(len(self.history), 1)

        self.drive(accept=False, before=press_escape)
        self.reader.edit_note(note)
        self.app.processEvents()
        self.assertEqual(self.markup.get(note.id).text, "note text")
        self.assertEqual(len(self.history), 1)
        self.assertEqual(self.notes(), (note,))

    def test_close_and_successful_replacement_clear_notes_and_history(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="first")
        self.assertTrue(self.view.note_mode)
        self.document.close()
        self.app.processEvents()
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.can_undo)
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.note_action.isChecked())
        self.assertFalse(self.reader.edit_note_action.isEnabled())

        self.assertIsNone(self.document.open(self.path))
        self.app.processEvents()
        self.note_mode(True)
        self.place(0, 400, 500, text="second")
        self.assertIsNone(self.document.open(self.path))
        self.app.processEvents()
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.note_action.isChecked())

    def test_failed_replacement_preserves_notes_and_history(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="kept")
        note = self.notes()[0]
        self.assertTrue(self.view.note_mode)
        self.assertTrue(self.reader.note_action.isChecked())
        self.assertIsNotNone(self.document.open(self.path.parent / "missing.pdf"))
        self.app.processEvents()
        self.assertEqual(self.notes(), (note,))
        self.assertTrue(self.markup.can_undo)
        self.assertTrue(self.view.note_mode)
        self.assertTrue(self.reader.note_action.isChecked())
        self.assertTrue(self.reader.note_action.isEnabled())
        self.assertEqual(self.document.path, self.path)
        self.assertTrue(self.markup.undo())
        self.assertEqual(self.notes(), ())
        self.assertTrue(self.markup.redo())
        self.assertEqual(self.notes(), (note,))

    def test_stale_editor_cannot_commit_a_note_into_a_newer_document(self):
        self.note_mode(True)

        def replace_document(dialog):
            self.assertIsNone(self.document.open(self.path))
            self.app.processEvents()
            dialog.body.setPlainText("stale")

        self.place(0, 400, 500, text=None, before=replace_document)
        self.assertEqual(self.notes(), ())
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.view.note_mode)

    def test_stale_edit_cannot_mutate_a_replacement_document(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="original")
        self.note_mode(False)
        note = self.notes()[0]
        old_generation = self.document.generation
        generations = []

        def replace_while_open(dialog):
            self.assertIsNone(self.document.open(self.path))
            self.app.processEvents()
            generations.append(self.document.generation)
            dialog.body.setPlainText("stale edit")
            dialog.accept()

        self.drive(accept=True, before=replace_while_open)
        with patch.object(self.markup, "update_note_text") as stale_update:
            self.reader.edit_note(note)
        self.app.processEvents()

        self.assertEqual(generations, [old_generation + 1])
        stale_update.assert_not_called()
        self.assertIsNotNone(self.document.pdf)
        self.assertEqual(self.document.path, self.path)
        self.assertEqual(self.document.generation, old_generation + 1)
        self.assertEqual(self.notes(), ())
        self.assertIsNone(self.markup.get(note.id))
        self.assertEqual(len(self.history), 0)
        self.assertFalse(self.markup.can_undo)
        self.assertFalse(self.markup.can_redo)

        # Control: the same production path does reach the mutation boundary while the
        # editor still belongs to the active generation, so the spy above is meaningful.
        self.note_mode(True)
        self.place(0, 400, 500, text="current")
        self.note_mode(False)
        current = self.notes()[0]
        self.drive(text="edited")
        with patch.object(self.markup, "update_note_text") as live_update:
            self.reader.edit_note(current)
        self.app.processEvents()
        live_update.assert_called_once_with(current.id, "edited")

    def test_editor_dialogs_are_not_retained_after_close(self):
        self.note_mode(True)
        self.place(0, 400, 500, text="transient")
        self.edit(self.notes()[0], "edited")
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(self.reader.findChildren(NoteEditor), [])
        self.assertEqual(self.view.findChildren(QMenu), [])


class NoteMenuTests(unittest.TestCase):
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

    def at(self, page, x, y):
        return self.view.pdf_to_viewport(page, QPointF(x, y)).toPoint()

    def drive(self, text=None, accept=True, before=None):
        def act():
            dialog = QApplication.activeModalWidget()
            self.assertIsInstance(dialog, NoteEditor)
            if before is not None:
                before(dialog)
            if text is not None:
                dialog.body.setPlainText(text)
            dialog.accept() if accept else dialog.reject()
        QTimer.singleShot(0, act)

    def notes(self):
        return tuple(item for item in self.markup.items if item.kind == "note")

    def test_annotation_menu_exposes_note_and_edit_note_actions(self):
        menu = next(m for m in self.window.findChildren(QMenu) if m.title() == "&Annotation")
        texts = [action.text() for action in menu.actions()]
        self.assertIn("&Note", texts)
        self.assertIn("Edit &Note", texts)
        self.assertIs(menu.actions()[texts.index("&Note")], self.reader.note_action)
        self.assertIs(menu.actions()[texts.index("Edit &Note")], self.reader.edit_note_action)
        self.assertTrue(self.reader.note_action.isCheckable())
        self.assertTrue(self.reader.note_action.isEnabled())
        self.assertFalse(self.reader.edit_note_action.isEnabled())
        self.assertIn("Right-click a note to edit it", texts)

    def test_note_action_from_the_menu_places_and_undoes_a_note(self):
        self.reader.note_action.trigger()
        self.assertTrue(self.view.note_mode)
        self.drive(text="menu placed")
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 400, 500))
        self.app.processEvents()
        notes = tuple(item for item in self.markup.items if item.kind == "note")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].text, "menu placed")
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.window.activateWindow()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.markup.items, ())
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Z,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.markup.items, notes)
        self.assertIs(self.markup.items[0], notes[0])

    def test_ctrl_z_inside_note_editor_stays_local_to_the_editor(self):
        self.reader.note_action.trigger()
        self.drive(text="seed")
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 400, 500))
        self.app.processEvents()
        note = self.notes()[0]
        self.reader.selection.select_all(1)
        markup = self.markup.create("underline", self.reader.selection)
        self.assertIsNotNone(markup)
        self.assertEqual(self.markup.items, (note, markup))
        self.assertEqual(len(self.markup.history), 2)
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())
        snapshots = []

        def drive_editor(dialog):
            body = dialog.body
            self.window.activateWindow()
            body.setFocus(Qt.FocusReason.OtherFocusReason)
            self.app.processEvents()
            body.moveCursor(QTextCursor.MoveOperation.End)

            def snap(label):
                snapshots.append((label, body.toPlainText(), tuple(self.markup.items),
                                  len(self.markup.history),
                                  self.reader.undo_action.isEnabled(),
                                  self.reader.redo_action.isEnabled()))

            QTest.keyClicks(body, " typed")
            snap("typed")
            QTest.keyClick(body, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            snap("ctrl+z")
            QTest.keyClick(body, Qt.Key.Key_Z,
                           Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            snap("ctrl+shift+z")
            QTest.keyClick(body, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            snap("ctrl+z again")
            QTest.keyClick(body, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
            snap("ctrl+y")
            dialog.accept()

        self.drive(accept=True, before=drive_editor)
        self.reader.edit_note(note)
        self.app.processEvents()

        self.assertEqual([step[0] for step in snapshots],
                         ["typed", "ctrl+z", "ctrl+shift+z", "ctrl+z again", "ctrl+y"])
        self.assertEqual([step[1] for step in snapshots],
                         ["seed typed", "seed", "seed typed", "seed", "seed typed"])
        for label, _text, items, depth, undo_enabled, redo_enabled in snapshots:
            with self.subTest(step=label):
                self.assertEqual(len(items), 2)
                self.assertIs(items[0], note)
                self.assertIs(items[1], markup)
                self.assertEqual(items[0].text, "seed")
                self.assertEqual(depth, 2)
                self.assertTrue(undo_enabled)
                self.assertFalse(redo_enabled)

        self.assertEqual([item.id for item in self.markup.items], [note.id, markup.id])
        self.assertEqual(self.markup.items[1], markup)
        self.assertEqual(self.markup.get(note.id).text, "seed typed")
        self.assertEqual(len(self.markup.history), 3)
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())

    def test_close_document_resets_note_mode_and_actions(self):
        self.reader.note_action.trigger()
        self.drive(text="closing")
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.at(0, 400, 500))
        self.app.processEvents()
        self.assertTrue(self.reader.undo_action.isEnabled())
        self.window.close_document()
        self.app.processEvents()
        self.assertFalse(self.view.note_mode)
        self.assertFalse(self.reader.note_action.isChecked())
        self.assertFalse(self.reader.note_action.isEnabled())
        self.assertFalse(self.reader.edit_note_action.isEnabled())
        self.assertFalse(self.reader.undo_action.isEnabled())
        self.assertFalse(self.reader.redo_action.isEnabled())
        self.assertEqual(self.markup.items, ())


if __name__ == "__main__":
    unittest.main()
