import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pypdf import PdfReader, PdfWriter
from PySide6.QtWidgets import QApplication

from glyph_desktop import pdf_writer
from glyph_desktop.pdf_geometry import PageGeometry
from test_m3e1b_persistence import _items, _qt_page_sizes


class WindowsPublicationFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        blank = self.root / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        writer.write(blank)
        geometry = PageGeometry.from_page(PdfReader(blank).pages[0], _qt_page_sizes(blank)[0])
        self.items = _items(geometry)
        self.source = self.root / "source.pdf"
        result = pdf_writer.write_annotated_pdf(blank, self.source, self.items)
        self.assertTrue(result.success, result.error)
        self.before = self.source.read_bytes()
        self.snapshot = pdf_writer.capture_source_snapshot(self.source)
        self.extra = replace(self.items[0], id="new-note")
        self.prepared = pdf_writer.stage_annotated_pdf(
            self.source, (self.extra,), validate_inherited=True, source_snapshot=self.snapshot)
        self.addCleanup(self.prepared.discard)


@unittest.skipIf(os.name == "nt", "portable mocks exercise control flow on Linux")
class WindowsPublicationControlFlowTests(WindowsPublicationFixture):
    def call_mocked_windows(self, *, replace_error=None, verify_error=None, source_error=None):
        stage_id = (1, b"s" * 16)
        target_id = (1, b"t" * 16)
        kernel = Mock()
        kernel.CloseHandle.return_value = 1
        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.get_osfhandle = lambda _: 111
        def identity(handle):
            return stage_id if handle == 111 else target_id
        def replace_stage(*_args):
            if replace_error:
                raise replace_error
            os.unlink(str(self.prepared.path))
        with (patch.object(pdf_writer.os, "name", "nt"),
              patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
              patch.object(pdf_writer, "_windows_replace_buffer"),
              patch.object(pdf_writer, "_windows_api", return_value=kernel),
              patch.object(pdf_writer, "_windows_file_identity", side_effect=identity),
              patch.object(pdf_writer, "_open_windows_replacement_target", return_value=(222, target_id)),
              patch.object(pdf_writer, "_windows_path_identity", return_value=(target_id, 0)),
              patch.object(pdf_writer, "verify_source_snapshot", side_effect=source_error) as verify_source,
              patch.object(pdf_writer, "_windows_replace_by_handle", side_effect=replace_stage) as publish,
              patch.object(pdf_writer, "_windows_verify_published_file", side_effect=verify_error)):
            result = pdf_writer.publish_prepared_pdf_windows(self.prepared)
        return result, publish, verify_source, kernel

    def test_control_flow_and_one_use(self):
        result, publish, verify, kernel = self.call_mocked_windows()
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.published)
        self.assertFalse(result.crash_durable)
        publish.assert_called_once_with(111, str(self.source))
        verify.assert_called_once_with(self.snapshot)
        kernel.CloseHandle.assert_called_once_with(222)
        self.assertFalse(pdf_writer.publish_prepared_pdf_windows(self.prepared).success)

    def test_prepublication_failure(self):
        result, _, _, kernel = self.call_mocked_windows(replace_error=OSError("injected"))
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertEqual(self.source.read_bytes(), self.before)
        kernel.CloseHandle.assert_called_once_with(222)

    def test_postpublication_verification_failure(self):
        result, _, _, kernel = self.call_mocked_windows(verify_error=ValueError("injected"))
        self.assertFalse(result.success)
        self.assertTrue(result.published)
        self.assertIsNone(result.output_path)
        self.assertIn("unknown", result.error)
        kernel.CloseHandle.assert_called_once_with(222)

    def test_snapshot_mismatch_closes_pinned_handle(self):
        result, publish, _, kernel = self.call_mocked_windows(source_error=ValueError("changed"))
        self.assertFalse(result.published)
        self.assertIn("changed", result.error)
        publish.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(222)
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_changed_stage_never_opens_target(self):
        self.prepared._stream.seek(0, os.SEEK_END)
        self.prepared._stream.write(b"tampered")
        result, publish, verify, kernel = self.call_mocked_windows()
        self.assertFalse(result.published)
        self.assertIn("staged output changed", result.error)
        publish.assert_not_called()
        verify.assert_not_called()
        kernel.CloseHandle.assert_not_called()


@unittest.skipUnless(os.name == "nt", "requires native Windows publication")
class NativeWindowsPublicationTests(WindowsPublicationFixture):
    def test_success_preserves_annotations_and_one_use(self):
        staged = self.prepared.read_bytes()
        directory = self.prepared.directory
        result = pdf_writer.publish_prepared_pdf(self.prepared)
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.published)
        self.assertFalse(result.crash_durable)
        self.assertEqual(self.source.read_bytes(), staged)
        ids = [str(ref.get_object().get("/NM", "")) for ref in PdfReader(self.source).pages[0]["/Annots"]]
        for item in (*self.items, self.extra):
            self.assertEqual(ids.count(item.id), 1)
        self.assertEqual(len(ids), 6)
        self.assertFalse(directory.exists())
        self.assertFalse(pdf_writer.publish_prepared_pdf(self.prepared).success)

    def test_source_change_or_replacement_rejected(self):
        with self.source.open("ab") as stream:
            stream.write(b"changed")
        changed = self.source.read_bytes()
        result = pdf_writer.publish_prepared_pdf(self.prepared)
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertEqual(self.source.read_bytes(), changed)

    def test_external_replacement_rejected(self):
        replacement = self.root / "replacement.pdf"
        replacement.write_bytes(self.before)
        replacement.replace(self.source)
        result = pdf_writer.publish_prepared_pdf(self.prepared)
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_changed_staged_handle_rejected(self):
        self.prepared._stream.seek(0, os.SEEK_END)
        self.prepared._stream.write(b"changed")
        result = pdf_writer.publish_prepared_pdf(self.prepared)
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_prepublication_failure_preserves_source(self):
        directory = self.prepared.directory
        with patch.object(pdf_writer, "_windows_replace_by_handle", side_effect=OSError("injected")):
            result = pdf_writer.publish_prepared_pdf(self.prepared)
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertFalse(directory.exists())

    def test_postpublication_failure_reports_unknown_location(self):
        staged = self.prepared.read_bytes()
        directory = self.prepared.directory
        with patch.object(pdf_writer, "_windows_verify_published_file", side_effect=ValueError("injected")):
            result = pdf_writer.publish_prepared_pdf(self.prepared)
        self.assertFalse(result.success)
        self.assertTrue(result.published)
        self.assertIsNone(result.output_path)
        self.assertIn("unknown", result.error)
        self.assertEqual(self.source.read_bytes(), staged)
        self.assertFalse(directory.exists())
