import os
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, TextStringObject)
from PySide6.QtWidgets import QApplication

from glyph_desktop.pdf_geometry import PageGeometry
from glyph_desktop import pdf_writer
from glyph_desktop.pdf_writer import stage_annotated_pdf, write_annotated_pdf
from test_m3e1b_persistence import _items, _qt_page_sizes, _core_fixture


class InheritedValidationTests(unittest.TestCase):
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
        writer.add_blank_page(width=600, height=800)
        writer.write(blank)
        page = PdfReader(blank).pages[0]
        geometry = PageGeometry.from_page(page, _qt_page_sizes(blank)[0])
        self.items = _items(geometry)
        self.source = self.root / "source.pdf"
        result = write_annotated_pdf(blank, self.source, self.items)
        self.assertTrue(result.success, result.error)
        self.original = self.source.read_bytes()

    def test_existing_five_types_and_new_annotation(self):
        extra = replace(self.items[0], id="additional-note")
        with stage_annotated_pdf(self.source, (extra,), validate_inherited=True) as staged:
            self.assertTrue(staged.read_bytes().startswith(b"%PDF"))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_source_bound_staging_uses_original_snapshot_and_digest(self):
        snapshot = pdf_writer.capture_source_snapshot(self.source)
        with stage_annotated_pdf(self.source, (), validate_inherited=True,
                                 source_snapshot=snapshot) as staged:
            self.assertEqual(staged.source_snapshot, snapshot)
            self.assertEqual(staged.validated_digest, hashlib.sha256(staged.read_bytes()).digest())
        with self.source.open("ab") as source:
            source.write(b"% modified\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            stage_annotated_pdf(self.source, (), validate_inherited=True, source_snapshot=snapshot)
        with self.assertRaisesRegex(ValueError, "requires inherited"):
            stage_annotated_pdf(self.source, (), source_snapshot=snapshot)

    def test_source_changed_during_staging_fails_closed(self):
        snapshot = pdf_writer.capture_source_snapshot(self.source)
        original_validate = pdf_writer._validate_inherited_annotations
        def change_source(*args):
            original_validate(*args)
            with self.source.open("ab") as stream:
                stream.write(b"% external edit\n")
        with patch.object(pdf_writer, "_validate_inherited_annotations", side_effect=change_source):
            with self.assertRaisesRegex(ValueError, "changed"):
                stage_annotated_pdf(self.source, (), validate_inherited=True, source_snapshot=snapshot)
        self.assertEqual(list(self.root.glob(".source.pdf.glyph-stage-*")), [])

    def test_detects_lost_changed_duplicate_and_appearance(self):
        write = PdfWriter.write
        for mutation in ("missing", "geometry", "contents", "flags", "appearance", "duplicate", "relocated"):
            with self.subTest(mutation=mutation):
                def altered(writer, output):
                    annotations = writer.pages[0]["/Annots"]
                    note = annotations[0].get_object()
                    if mutation == "missing":
                        del annotations[0]
                    elif mutation == "geometry":
                        note[NameObject("/Rect")] = ArrayObject([FloatObject(0)] * 4)
                    elif mutation == "contents":
                        note[NameObject("/Contents")] = TextStringObject("changed")
                    elif mutation == "flags":
                        note[NameObject("/F")] = NumberObject(42)
                    elif mutation == "duplicate":
                        annotations.append(annotations[0])
                    elif mutation == "relocated":
                        writer.pages[1][NameObject("/Annots")] = ArrayObject([annotations.pop(0)])
                    else:
                        markup = annotations[1].get_object()
                        appearance = markup["/AP"]["/N"].get_object()
                        appearance.set_data(b"changed appearance")
                    return write(writer, output)

                with patch.object(PdfWriter, "write", altered):
                    with self.assertRaises(ValueError):
                        stage_annotated_pdf(self.source, (), validate_inherited=True)
                self.assertEqual(self.source.read_bytes(), self.original)
                self.assertEqual(list(self.root.glob(".source.pdf.glyph-stage-*")), [])

    def test_unsupported_annotation_fails_closed(self):
        source = _core_fixture(self.root / "unsupported.pdf")
        before = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "Unsupported inherited annotation"):
            stage_annotated_pdf(source, (), validate_inherited=True)
        self.assertEqual(source.read_bytes(), before)

    def test_cyclic_annotation_data_fails_closed(self):
        writer = PdfWriter(clone_from=PdfReader(self.source))
        cycle = DictionaryObject()
        cycle_ref = writer._add_object(cycle)
        cycle[NameObject("/Self")] = cycle_ref
        writer.pages[0]["/Annots"][0].get_object()[NameObject("/Custom")] = cycle_ref
        source = self.root / "cycle.pdf"
        writer.write(source)
        before = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "Cyclic annotation"):
            stage_annotated_pdf(source, (), validate_inherited=True)
        self.assertEqual(source.read_bytes(), before)
