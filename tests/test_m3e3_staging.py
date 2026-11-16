import os
import io
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pypdf import PdfReader
from PySide6.QtWidgets import QApplication

from glyph_desktop import pdf_writer
from glyph_desktop.pdf_geometry import PageGeometry
from glyph_desktop.pdf_writer import stage_annotated_pdf
from test_m3e1b_persistence import _core_fixture, _items, _qt_page_sizes


class StagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_staging_keeps_source_and_annotations_until_discard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _core_fixture(root / "source.pdf")
            original = source.read_bytes()
            reader = PdfReader(source, strict=True)
            existing_count = sum(len(page.get("/Annots", [])) for page in reader.pages)
            geometry = PageGeometry.from_page(reader.pages[0], _qt_page_sizes(source)[0])
            items = _items(geometry)
            with patch.object(pdf_writer, "_validate_output", wraps=pdf_writer._validate_output) as validate:
                prepared = stage_annotated_pdf(source, items)
            self.addCleanup(prepared.discard)
            validate.assert_called_once()
            staged_path, stage_directory = prepared.path, prepared.directory
            self.assertTrue(staged_path.is_file())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual({p.name for p in root.iterdir()}, {source.name, stage_directory.name})
            staged = PdfReader(io.BytesIO(prepared.read_bytes()), strict=True)
            annotations = [ref.get_object() for page in staged.pages
                           for ref in page.get("/Annots", [])]
            self.assertEqual(len(annotations), existing_count + len(items))
            for item in items:
                self.assertEqual(sum(str(annotation.get("/NM", "")) == item.id
                                     for annotation in annotations), 1)
            self.assertTrue(any(str(a.get("/Contents", "")) == "Existing note"
                                for a in annotations))
            prepared.discard()
            prepared.discard()
            with self.assertRaisesRegex(ValueError, "discarded"):
                prepared.read_bytes()
            self.assertFalse(staged_path.exists())
            self.assertFalse(stage_directory.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_validation_failure_cleans_up_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _core_fixture(root / "source.pdf")
            original = source.read_bytes()
            with patch("glyph_desktop.pdf_writer._validate_output", side_effect=ValueError("injected")):
                with self.assertRaisesRegex(ValueError, "injected"):
                    stage_annotated_pdf(source, ())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(root.iterdir()), [source])

    def test_context_manager_discards_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = _core_fixture(Path(directory) / "source.pdf")
            with stage_annotated_pdf(source, ()) as prepared:
                stage_directory = prepared.directory
                self.assertTrue(stage_directory.is_dir())
            self.assertFalse(stage_directory.exists())

    def test_file_creation_failure_removes_empty_staging_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _core_fixture(root / "source.pdf")
            original = source.read_bytes()
            target = ("glyph_desktop.pdf_writer._create_windows_stage_fd"
                      if os.name == "nt" else "glyph_desktop.pdf_writer.os.open")
            with patch(target, side_effect=OSError("injected creation failure")):
                with self.assertRaisesRegex(OSError, "injected creation failure"):
                    stage_annotated_pdf(source, ())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(root.iterdir()), [source])

    def test_source_snapshot_detects_replacement_and_in_place_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _core_fixture(root / "source.pdf")
            snapshot = pdf_writer.capture_source_snapshot(source)
            pdf_writer.verify_source_snapshot(snapshot)
            identity = source.stat().st_ino
            with source.open("r+b") as stream:
                stream.seek(10)
                byte = stream.read(1)
                stream.seek(10)
                stream.write(bytes([byte[0] ^ 1]))
            self.assertEqual(source.stat().st_ino, identity)
            with self.assertRaises(ValueError):
                pdf_writer.verify_source_snapshot(snapshot)
            current = pdf_writer.capture_source_snapshot(source)
            replacement = root / "replacement.pdf"
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
            with self.assertRaises(ValueError):
                pdf_writer.verify_source_snapshot(current)

    def test_windows_snapshot_ignores_path_handle_creation_time_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _core_fixture(root / "source.pdf")
            generated = root / "glyph-output.pdf"
            result = pdf_writer.write_annotated_pdf(source, generated, ())
            self.assertTrue(result.success, result.error)
            real_fstat = os.fstat

            def handle_stat(descriptor):
                info = real_fstat(descriptor)
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                                       st_ino=info.st_ino, st_size=info.st_size,
                                       st_mtime_ns=info.st_mtime_ns,
                                       st_ctime_ns=info.st_ctime_ns + 13136500)

            with patch.object(pdf_writer.os, "fstat", side_effect=handle_stat):
                with patch.object(pdf_writer.sys, "platform", "linux"):
                    with self.assertRaisesRegex(ValueError, "changed while opening"):
                        pdf_writer.capture_source_snapshot(generated)
                with patch.object(pdf_writer.sys, "platform", "win32"):
                    snapshot = pdf_writer.capture_source_snapshot(generated)
                    pdf_writer.verify_source_snapshot(snapshot)
                    self.assertEqual(len(snapshot.state), 4)
                    original = generated.stat()
                    with generated.open("r+b") as stream:
                        stream.seek(10)
                        byte = stream.read(1)
                        stream.seek(10)
                        stream.write(bytes([byte[0] ^ 1]))
                    os.utime(generated, ns=(original.st_atime_ns, original.st_mtime_ns))
                    with self.assertRaisesRegex(ValueError, "changed"):
                        pdf_writer.verify_source_snapshot(snapshot)
                    current = pdf_writer.capture_source_snapshot(generated)
                    replacement = root / "replacement.pdf"
                    replacement.write_bytes(generated.read_bytes())
                    replacement.replace(generated)
                    with self.assertRaisesRegex(ValueError, "changed"):
                        pdf_writer.verify_source_snapshot(current)
