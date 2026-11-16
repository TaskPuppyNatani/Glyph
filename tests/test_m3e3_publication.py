import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pypdf import PdfReader, PdfWriter
from PySide6.QtWidgets import QApplication

from glyph_desktop import pdf_writer
from glyph_desktop.pdf_geometry import PageGeometry
from test_m3e1b_persistence import _items, _qt_page_sizes


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux atomic exchange")
class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        blank = self.root / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        writer.write(blank)
        geometry = PageGeometry.from_page(PdfReader(blank).pages[0], _qt_page_sizes(blank)[0])
        self.items = _items(geometry)
        self.source = self.root / "source.pdf"
        result = pdf_writer.write_annotated_pdf(blank, self.source, self.items)
        self.assertTrue(result.success, result.error)
        self.original = self.source.read_bytes()
        self.snapshot = pdf_writer.capture_source_snapshot(self.source)
        self.new = replace(self.items[0], id="new-note")
        self.prepared = pdf_writer.stage_annotated_pdf(
            self.source, (self.new,), validate_inherited=True, source_snapshot=self.snapshot)
        self.addCleanup(self.prepared.discard)

    def publish(self):
        return pdf_writer.publish_prepared_pdf_linux(self.prepared)

    def test_success_and_one_use(self):
        staged = self.prepared.read_bytes()
        stage_dir = self.prepared.directory
        result = self.publish()
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.published)
        self.assertTrue(result.crash_durable)
        self.assertEqual(result.output_path, self.source)
        self.assertEqual(self.source.read_bytes(), staged)
        annotations = PdfReader(self.source).pages[0]["/Annots"]
        ids = [str(ref.get_object().get("/NM", "")) for ref in annotations]
        self.assertEqual(len(ids), 6)
        for item in (*self.items, self.new):
            self.assertEqual(ids.count(item.id), 1)
        self.assertFalse(stage_dir.exists())
        self.assertFalse(self.publish().success)

    def test_changed_source_or_stage_fails_before_exchange(self):
        for kind in ("source", "replacement", "staged"):
            with self.subTest(kind=kind):
                if kind != "source":
                    self.setUp()
                if kind == "source":
                    with self.source.open("ab") as stream:
                        stream.write(b"changed")
                elif kind == "replacement":
                    other = self.root / "replacement.pdf"
                    other.write_bytes(self.original)
                    other.replace(self.source)
                else:
                    with self.prepared.path.open("ab") as stream:
                        stream.write(b"changed")
                before = self.source.read_bytes()
                stage_dir = self.prepared.directory
                result = self.publish()
                self.assertFalse(result.success)
                self.assertFalse(result.published)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertFalse(stage_dir.exists())
                self.assertFalse(self.publish().success)

    def test_post_exchange_failure_rolls_back(self):
        stage_dir = self.prepared.directory
        with patch.object(pdf_writer, "_verify_displaced_original", side_effect=ValueError("injected")):
            result = self.publish()
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertIn("restored", result.error)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(stage_dir.exists())

    def test_sync_failure_reports_publication_and_retains_original(self):
        stage_dir = self.prepared.directory
        with patch.object(pdf_writer.os, "fsync", side_effect=OSError("injected sync failure")):
            result = self.publish()
        self.assertFalse(result.success)
        self.assertTrue(result.published)
        self.assertFalse(result.crash_durable)
        self.assertEqual(result.output_path, self.source)
        self.assertEqual((stage_dir / "validated-output.tmp").read_bytes(), self.original)
        self.assertIn(str(stage_dir), result.error)

    def test_failed_rollback_preserves_displaced_original(self):
        stage_dir = self.prepared.directory
        rename = pdf_writer._rename_exchange
        calls = 0
        def exchange(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected rollback failure")
            return rename(*args)
        with (patch.object(pdf_writer, "_rename_exchange", side_effect=exchange),
              patch.object(pdf_writer, "_verify_displaced_original", side_effect=ValueError("injected"))):
            result = self.publish()
        self.assertFalse(result.success)
        self.assertTrue(result.published)
        self.assertIsNone(result.output_path)
        self.assertIn("ambiguous", result.error)
        self.assertEqual((stage_dir / "validated-output.tmp").read_bytes(), self.original)
        self.prepared.discard()
        self.assertTrue(stage_dir.exists())

    def test_unbound_output_cannot_publish(self):
        staged = pdf_writer.stage_annotated_pdf(self.source, (), validate_inherited=True)
        directory = staged.directory
        result = pdf_writer.publish_prepared_pdf_linux(staged)
        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(directory.exists())

    def test_cleanup_failure_after_publication_is_reported(self):
        with patch.object(pdf_writer, "_cleanup_stage_directory", side_effect=OSError("cleanup injected")):
            result = self.publish()
        self.assertFalse(result.success)
        self.assertTrue(result.published)
        self.assertTrue(result.crash_durable)
        self.assertEqual(result.output_path, self.source)
        self.assertIn("cleanup injected", result.error)

    def test_staged_change_during_validation_is_rejected(self):
        validate = pdf_writer._validate_inherited_annotations
        def change_stage(reader, stream, expected):
            validate(reader, stream, expected)
            stream.seek(0, os.SEEK_END)
            stream.write(b"changed during validation")
        with patch.object(pdf_writer, "_validate_inherited_annotations", side_effect=change_stage):
            with self.assertRaisesRegex(ValueError, "changed during validation"):
                pdf_writer.stage_annotated_pdf(self.source, (), validate_inherited=True,
                                              source_snapshot=self.snapshot)
        self.assertEqual(self.source.read_bytes(), self.original)
