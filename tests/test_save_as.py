import copy
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QPainter, QPdfWriter, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QMessageBox
from pypdf import PdfReader

from glyph_desktop.annotations import Annotations, Markup
from glyph_desktop.document_state import DocumentState
from glyph_desktop.pdf_writer import WriteResult
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


def make_pdf(path, pages=3):
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    for page in range(pages):
        if page:
            writer.newPage()
        painter.drawText(100, 200, f"Page {page + 1} has selectable text")
    painter.end()


class FakeSaveDialog:
    def __init__(self, path, accepted=True):
        self.path = Path(path)
        self.accepted = accepted
        self.options = []
        self.accept_mode = None
        self.file_mode = None
        self.name_filter = None
        self.default_suffix = None
        self.selected_file = None

    def setOption(self, option, enabled=True):
        self.options.append((option, enabled))

    def setAcceptMode(self, mode):
        self.accept_mode = mode

    def setFileMode(self, mode):
        self.file_mode = mode

    def setNameFilter(self, name_filter):
        self.name_filter = name_filter

    def setDefaultSuffix(self, suffix):
        self.default_suffix = suffix

    def selectFile(self, filename):
        self.selected_file = filename

    def exec(self):
        return (QDialog.DialogCode.Accepted if self.accepted
                else QDialog.DialogCode.Rejected)

    def selectedFiles(self):
        return [str(self.path)] if self.accepted else []


class SaveAsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.pdf"
        self.replacement = self.root / "replacement.pdf"
        make_pdf(self.source)
        make_pdf(self.replacement, 2)
        self.theme = ThemeManager(
            self.app,
            Settings(QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)))
        state = DocumentState(self.root / "document-state.json")
        self.window = MainWindow(self.theme, "0.1.0", state)
        self.window.resize(920, 640)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def open_source(self):
        self.assertTrue(self.window.open_document(self.source))
        self.app.processEvents()

    def file_dialog(self, path, *, accepted=True):
        dialog = FakeSaveDialog(path, accepted)
        factory = Mock(return_value=dialog)
        factory.Option = QFileDialog.Option
        factory.AcceptMode = QFileDialog.AcceptMode
        factory.FileMode = QFileDialog.FileMode
        return dialog, patch("glyph_desktop.window.QFileDialog", factory)

    def test_action_enablement_shortcut_and_document_lifecycle(self):
        action = self.window.save_as_action
        self.assertFalse(action.isEnabled())
        self.assertEqual(action.shortcut(), QKeySequence(QKeySequence.StandardKey.SaveAs))

        self.open_source()
        self.assertTrue(action.isEnabled())
        self.assertTrue(self.window.open_document(self.replacement))
        self.app.processEvents()
        self.assertTrue(action.isEnabled())
        with patch("glyph_desktop.window.QMessageBox.warning") as warning:
            self.assertFalse(self.window.open_document(self.root / "missing.pdf"))
        warning.assert_called_once()
        self.assertTrue(action.isEnabled())

        self.window.close_document()
        self.assertFalse(action.isEnabled())

    def test_cancel_does_not_call_writer_or_show_a_message(self):
        self.open_source()
        dialog, dialog_patch = self.file_dialog(self.root / "unused.pdf", accepted=False)
        with (dialog_patch,
              patch("glyph_desktop.window.write_annotated_pdf") as writer,
              patch("glyph_desktop.window.QMessageBox.warning") as warning,
              patch("glyph_desktop.window.QMessageBox.information") as information):
            self.window.save_as_pdf()

        writer.assert_not_called()
        warning.assert_not_called()
        information.assert_not_called()
        self.assertEqual(dialog.accept_mode, QFileDialog.AcceptMode.AcceptSave)

    def test_dialog_configuration_and_current_generation_snapshot(self):
        self.open_source()
        destination = self.root / "chosen.pdf"
        generation = self.window.document.generation
        current = Markup("current", "highlight", 0, generation,
                         (((10.0, 10.0), (30.0, 10.0),
                           (30.0, 20.0), (10.0, 20.0)),), "current")
        stale = replace(current, id="stale", generation=generation - 1)
        dialog, dialog_patch = self.file_dialog(destination)
        result = WriteResult(True, destination, None, True, True, None)

        with (dialog_patch,
              patch.object(Annotations, "items", new_callable=PropertyMock) as items,
              patch("glyph_desktop.window.write_annotated_pdf", return_value=result) as writer,
              patch("glyph_desktop.window.QMessageBox.information") as information):
            items.return_value = (stale, current)
            self.window.save_as_pdf()

        writer.assert_called_once_with(self.source.resolve(), destination, (current,))
        self.assertEqual(dialog.options, [(QFileDialog.Option.DontConfirmOverwrite, True)])
        self.assertEqual(dialog.accept_mode, QFileDialog.AcceptMode.AcceptSave)
        self.assertEqual(dialog.file_mode, QFileDialog.FileMode.AnyFile)
        self.assertEqual(dialog.name_filter, "PDF files (*.pdf)")
        self.assertEqual(dialog.default_suffix, "pdf")
        self.assertEqual(dialog.selected_file, "source-annotated.pdf")
        information.assert_not_called()
        self.assertIn(str(destination), self.window.statusBar().currentMessage())

    def test_real_save_writes_copy_and_preserves_active_document_state(self):
        self.open_source()
        reader = self.window.reader
        annotations = reader.annotations
        generation = self.window.document.generation

        reader.selection.select_all(0)
        self.assertTrue(reader.selection.valid())
        markup = annotations.create("highlight", reader.selection)
        note = annotations.add_note(0, (80.0, 100.0), "Keep this note")
        ink = annotations.add_ink(0, ((40.0, 50.0), (80.0, 70.0)))
        self.assertIsNotNone(markup)
        self.assertIsNotNone(note)
        self.assertIsNotNone(ink)
        self.assertTrue(annotations.undo())
        annotations.select(markup.id, generation)

        self.window.document.set_page(1)
        reader.page_view.restore_position(1, 0.35)
        self.app.processEvents()
        pdf = self.window.document.pdf
        source_path = self.window.document.path
        items = annotations.items
        selected_id = annotations.selected_id
        undo_stack = tuple(annotations.history._undo)
        redo_stack = tuple(annotations.history._redo)
        page = self.window.document.page
        position = reader.page_view.reading_position()
        mode, zoom = self.window.document.mode, self.window.document.zoom
        recents = tuple(self.window.state.recents)
        last_document = self.window.state.last_document
        documents = copy.deepcopy(self.window.state.documents)
        source_bytes = self.source.read_bytes()

        destination = self.root / "source-annotated.pdf"
        dialog, dialog_patch = self.file_dialog(destination)
        with (dialog_patch,
              patch("glyph_desktop.window.QMessageBox.information") as information,
              patch("glyph_desktop.window.QMessageBox.warning") as warning):
            self.window.save_as_pdf()

        self.assertTrue(destination.is_file())
        self.assertEqual(self.source.read_bytes(), source_bytes)
        saved = PdfReader(destination)
        self.assertEqual(len(saved.pages), 3)
        saved_annotations = saved.pages[0].get("/Annots", []).get_object()
        self.assertEqual(len(saved_annotations), len(items))
        information.assert_not_called()
        warning.assert_not_called()
        self.assertIn(str(destination), self.window.statusBar().currentMessage())

        self.assertIs(self.window.document.pdf, pdf)
        self.assertEqual(self.window.document.path, source_path)
        self.assertEqual(self.window.document.generation, generation)
        self.assertEqual(annotations.items, items)
        self.assertEqual(annotations.selected_id, selected_id)
        self.assertEqual(tuple(annotations.history._undo), undo_stack)
        self.assertEqual(tuple(annotations.history._redo), redo_stack)
        self.assertEqual(self.window.document.page, page)
        self.assertEqual(reader.page_view.reading_position(), position)
        self.assertEqual((self.window.document.mode, self.window.document.zoom), (mode, zoom))
        self.assertEqual(tuple(self.window.state.recents), recents)
        self.assertEqual(self.window.state.last_document, last_document)
        self.assertEqual(self.window.state.documents, documents)

    def test_open_source_is_never_an_overwrite_target(self):
        self.open_source()
        source_bytes = self.source.read_bytes()
        dialog, dialog_patch = self.file_dialog(self.source)
        with (dialog_patch,
              patch("glyph_desktop.window.QMessageBox.warning") as warning,
              patch.object(self.window, "_confirm_replace_pdf") as confirm):
            self.window.save_as_pdf()
        warning.assert_called_once()
        self.assertIn("source", warning.call_args.args[2].lower())
        confirm.assert_not_called()
        self.assertEqual(self.source.read_bytes(), source_bytes)

    def test_cancel_existing_destination_leaves_it_untouched(self):
        self.open_source()
        destination = self.root / "existing.pdf"
        old_bytes = b"leave this destination untouched"
        destination.write_bytes(old_bytes)
        dialog, dialog_patch = self.file_dialog(destination)

        with (dialog_patch,
              patch.object(self.window, "_confirm_replace_pdf", return_value=False) as confirm,
              patch("glyph_desktop.window.write_annotated_pdf") as writer,
              patch("glyph_desktop.window.QMessageBox.warning") as warning,
              patch("glyph_desktop.window.QMessageBox.information") as information):
            self.window.save_as_pdf()

        confirm.assert_called_once_with(destination)
        writer.assert_not_called()
        warning.assert_not_called()
        information.assert_not_called()
        self.assertEqual(destination.read_bytes(), old_bytes)

    def test_confirmed_existing_destination_is_replaced_after_validation(self):
        self.open_source()
        destination = self.root / "replace-me.pdf"
        make_pdf(destination, pages=1)
        old_bytes = destination.read_bytes()
        source_bytes = self.source.read_bytes()
        note = self.window.reader.annotations.add_note(0, (50.0, 60.0), "Saved note")
        dialog, dialog_patch = self.file_dialog(destination)

        with (dialog_patch,
              patch.object(self.window, "_confirm_replace_pdf", return_value=True) as confirm,
              patch("glyph_desktop.window.QMessageBox.warning") as warning,
              patch("glyph_desktop.window.QMessageBox.information") as information):
            self.window.save_as_pdf()

        confirm.assert_called_once_with(destination)
        warning.assert_not_called()
        information.assert_not_called()
        self.assertNotEqual(destination.read_bytes(), old_bytes)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self.window.document.path, self.source.resolve())
        saved = PdfReader(destination, strict=True)
        self.assertEqual(len(saved.pages), 3)
        self.assertIn(note.id, {
            str(reference.get_object().get("/NM"))
            for reference in saved.pages[0].get("/Annots", [])
        })
        self.assertIn(str(destination), self.window.statusBar().currentMessage())

    def test_replace_confirmation_has_explicit_replace_and_cancel_buttons(self):
        real_message_box = QMessageBox

        class FakeMessageBox:
            Icon = real_message_box.Icon
            ButtonRole = real_message_box.ButtonRole
            StandardButton = real_message_box.StandardButton

            def __init__(self, parent):
                FakeMessageBox.last = self
                self.parent = parent
                self.buttons = []
                self.default = None
                self.clicked = None

            def setIcon(self, icon):
                self.icon = icon

            def setWindowTitle(self, title):
                self.title = title

            def setText(self, text):
                self.text = text

            def setInformativeText(self, text):
                self.informative_text = text

            def addButton(self, label, role=None):
                button = (label, role)
                self.buttons.append(button)
                if label == "Replace":
                    self.replace = button
                else:
                    self.cancel = button
                return button

            def setDefaultButton(self, button):
                self.default = button

            def exec(self):
                self.clicked = self.cancel

            def clickedButton(self):
                return self.clicked

        with patch("glyph_desktop.window.QMessageBox", FakeMessageBox):
            self.assertFalse(self.window._confirm_replace_pdf(self.root / "existing.pdf"))

        message = FakeMessageBox.last
        self.assertEqual(message.title, "Replace Existing PDF?")
        self.assertIn("fully written and validated", message.informative_text)
        self.assertEqual(message.buttons, [
            ("Replace", QMessageBox.ButtonRole.DestructiveRole),
            (QMessageBox.StandardButton.Cancel, None),
        ])
        self.assertIs(message.default, message.cancel)

    def test_failure_before_publication_shows_error_and_preserves_document(self):
        self.open_source()
        pdf = self.window.document.pdf
        generation = self.window.document.generation
        result = WriteResult(False, None, "ValueError: unsupported PDF feature", False, False, None)
        dialog, dialog_patch = self.file_dialog(self.root / "failed.pdf")

        with (dialog_patch,
              patch("glyph_desktop.window.write_annotated_pdf", return_value=result) as writer,
              patch("glyph_desktop.window.QMessageBox.warning") as warning):
            self.window.save_as_pdf()

        writer.assert_called_once()
        warning.assert_called_once()
        self.assertIn(result.error, warning.call_args.args[2])
        self.assertIs(self.window.document.pdf, pdf)
        self.assertEqual(self.window.document.generation, generation)
        self.assertEqual(self.window.document.path, self.source.resolve())

    def test_published_with_error_reports_known_or_unknown_location_without_retry(self):
        self.open_source()
        destination = self.root / "partially-published.pdf"
        for output_path in (destination, None):
            with self.subTest(output_path=output_path):
                result = WriteResult(
                    False, output_path, "RuntimeError: post-publication verification failed",
                    True, False, "Crash durability is not guaranteed.")
                dialog, dialog_patch = self.file_dialog(destination)
                with (dialog_patch,
                      patch("glyph_desktop.window.write_annotated_pdf", return_value=result) as writer,
                      patch("glyph_desktop.window.QMessageBox.warning") as warning):
                    self.window.save_as_pdf()

                writer.assert_called_once()
                warning.assert_called_once()
                message = warning.call_args.args[2]
                self.assertIn("published", message.lower())
                self.assertIn(result.error, message)
                self.assertIn("Crash durability is not guaranteed.", message)
                if output_path is not None:
                    self.assertIn(str(output_path), message)
                else:
                    self.assertIn("location could not be verified", message)
                self.assertIn("did not retry", message)

    def test_success_with_windows_durability_warning_uses_status_bar(self):
        self.open_source()
        destination = self.root / "windows-copy.pdf"
        result = WriteResult(
            True, destination, None, True, False,
            "The PDF was published, but crash durability of its destination name is not guaranteed.")
        dialog, dialog_patch = self.file_dialog(destination)

        with (dialog_patch,
              patch("glyph_desktop.window.write_annotated_pdf", return_value=result),
              patch("glyph_desktop.window.QMessageBox.warning") as warning,
              patch("glyph_desktop.window.QMessageBox.information") as information):
            self.window.save_as_pdf()

        warning.assert_not_called()
        information.assert_not_called()
        self.assertIn(str(destination), self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
