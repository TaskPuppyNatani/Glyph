"""Disposable-file proof of the proposed two-pass Save writer sequence."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from PySide6.QtWidgets import QApplication

from glyph_desktop.pdf_geometry import PageGeometry
from glyph_desktop.pdf_writer import prepare_replacement_target, write_annotated_pdf
from test_m3e1b_persistence import _core_fixture, _geometry_pdf, _items, _qt_page_sizes


def annotation_records(path):
    reader = PdfReader(path, strict=True)
    return [(index, str(obj.get("/Subtype")), str(obj.get("/NM", "")),
             str(obj.get("/Contents", "")))
            for index, page in enumerate(reader.pages)
            for ref in page.get("/Annots", [])
            for obj in (ref.get_object(),)]


class TwoPassProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_pass_preserves_annotations_and_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = _core_fixture(root / "original.pdf")
            before = original.read_bytes()
            existing = annotation_records(original)
            source_page = PdfReader(original, strict=True).pages[0]
            geometry = PageGeometry.from_page(source_page, _qt_page_sizes(original)[0])
            items = _items(geometry)
            stage = root / "staged.pdf"

            same = write_annotated_pdf(original, original, items)
            self.assertFalse(same.success)
            self.assertFalse(same.published)
            self.assertEqual(original.read_bytes(), before)

            first = write_annotated_pdf(original, stage, items)
            self.assertTrue(first.success, first.error)
            self.assertEqual(original.read_bytes(), before)
            staged_records = annotation_records(stage)
            for record in existing:
                self.assertIn(record, staged_records)
            for item in items:
                self.assertEqual(sum(record[2] == item.id for record in staged_records), 1)
            self.assertEqual(len(staged_records), len(existing) + len(items))

            prepared = prepare_replacement_target(stage, original)
            with patch("glyph_desktop.pdf_writer._validate_output", side_effect=ValueError("injected")):
                failed = write_annotated_pdf(stage, original, (), replacement_target=prepared)
            self.assertFalse(failed.success)
            self.assertFalse(failed.published)
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual(annotation_records(stage), staged_records)

            prepared = prepare_replacement_target(stage, original)
            result = write_annotated_pdf(stage, original, (), replacement_target=prepared)
            self.assertTrue(result.success, result.error)
            self.assertTrue(result.published)
            self.assertEqual(result.output_path, original)
            self.assertEqual(annotation_records(original), staged_records)
            self.assertEqual(annotation_records(stage), staged_records)

    def test_second_pass_validator_does_not_detect_lost_existing_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = _core_fixture(root / "original.pdf")
            source_page = PdfReader(original, strict=True).pages[0]
            geometry = PageGeometry.from_page(source_page, _qt_page_sizes(original)[0])
            stage = root / "staged.pdf"
            first = write_annotated_pdf(original, stage, _items(geometry))
            self.assertTrue(first.success, first.error)
            expected = annotation_records(stage)
            prepared = prepare_replacement_target(stage, original)
            write = PdfWriter.write

            def drop_annotations(writer, output):
                del writer.pages[0]["/Annots"]
                return write(writer, output)

            with patch.object(PdfWriter, "write", drop_annotations):
                result = write_annotated_pdf(stage, original, (), replacement_target=prepared)

            self.assertTrue(result.success, result.error)
            self.assertTrue(result.published)
            self.assertNotEqual(annotation_records(original), expected)
            self.assertEqual(annotation_records(stage), expected)

    def test_preparation_after_external_replacement_accepts_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = _core_fixture(root / "original.pdf")
            stage = root / "staged.pdf"
            first = write_annotated_pdf(original, stage, ())
            self.assertTrue(first.success, first.error)
            replacement = _geometry_pdf(root / "external.pdf")
            replacement.replace(original)
            changed_bytes = original.read_bytes()

            prepared = prepare_replacement_target(stage, original)
            result = write_annotated_pdf(stage, original, (), replacement_target=prepared)
            self.assertTrue(result.success, result.error)
            self.assertTrue(result.published)
            self.assertNotEqual(original.read_bytes(), changed_bytes)

if __name__ == "__main__":
    unittest.main()
