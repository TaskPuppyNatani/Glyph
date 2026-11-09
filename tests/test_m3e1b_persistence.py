import ctypes
import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions, QPdfLinkModel
from PySide6.QtWidgets import QApplication
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link, Text
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, RectangleObject, TextStringObject)

from glyph_desktop.annotations import Ink, Markup, Note
from glyph_desktop.pdf_annotations import convert_annotation
from glyph_desktop.pdf_geometry import GeometryError, PageGeometry
from glyph_desktop.pdf_writer import (_create_private_stage, write_annotated_pdf)
from test_links import write_link_pdf


def _qt_page_sizes(path):
    document = QPdfDocument()
    try:
        error = document.load(str(path))
        if error != QPdfDocument.Error.None_:
            raise AssertionError(f"QtPdf load failed: {error}")
        return tuple((document.pagePointSize(index).width(),
                      document.pagePointSize(index).height())
                     for index in range(document.pageCount()))
    finally:
        document.close()


def _geometry_pdf(path, rotation=0, user_unit=1):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    page.mediabox = RectangleObject([10, 20, 610, 820])
    page.cropbox = RectangleObject([50, 100, 550, 700])
    page.rotation = rotation
    if user_unit != 1:
        page[NameObject("/UserUnit")] = FloatObject(user_unit)
    writer.add_annotation(0, Link(rect=(150, 200, 250, 300), url="https://example.com"))
    writer.write(path)
    return path


def _core_fixture(path):
    raw = Path(path).with_name("raw-links.pdf")
    write_link_pdf(raw)
    writer = PdfWriter(clone_from=PdfReader(raw))
    writer.add_metadata({"/Title": "Persistence fixture", "/Author": "Glyph tests"})
    writer.add_outline_item("Page two", 1)
    writer.add_named_destination("fixture-destination", 1)
    writer.add_attachment("fixture.txt", b"fixture attachment")
    writer.add_annotation(0, Text(rect=(260, 650, 275, 665), text="Existing note"))
    widget = DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                               NameObject("/Subtype"): NameObject("/Widget"),
                               NameObject("/FT"): NameObject("/Tx"),
                               NameObject("/T"): TextStringObject("fixture_field"),
                               NameObject("/V"): TextStringObject("fixture value"),
                               NameObject("/Rect"): RectangleObject([300, 650, 450, 675]),
                               NameObject("/F"): NumberObject(4)})
    writer.add_annotation(0, widget)
    widget_ref = writer.pages[0]["/Annots"][-1]
    writer.root_object[NameObject("/AcroForm")] = writer._add_object(
        DictionaryObject({NameObject("/Fields"): ArrayObject([widget_ref])}))
    writer.write(path)
    raw.unlink()
    return Path(path)


def _items(geometry, page=0):
    pdf_cycle = ((100, 500), (150, 500), (150, 480), (100, 480), (100, 500))
    region = tuple(geometry.pdf_to_qt(point) for point in pdf_cycle)
    note = Note("note-id", "note", page, 7,
                geometry.pdf_to_qt((200, 300)), "Saved note")
    markups = tuple(Markup(f"{kind}-id", kind, page, 7, (region,), "Selected words")
                    for kind in ("highlight", "underline", "strikeout"))
    points = tuple(geometry.pdf_to_qt(point) for point in ((200, 350), (220, 370), (240, 360)))
    ink = Ink("ink-id", "ink", page, 7, points, 2.5)
    return (note, *markups, ink)


def _rect(annotation):
    return tuple(float(value) for value in annotation["/Rect"])


def _render_digest(path, annotations=False):
    document = QPdfDocument()
    try:
        error = document.load(str(path))
        if error != QPdfDocument.Error.None_:
            raise AssertionError(f"QtPdf load failed: {error}")
        options = QPdfDocumentRenderOptions()
        if annotations:
            options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        image = document.render(0, QSize(612, 792), options)
        if image.isNull():
            raise AssertionError("QtPdf returned a null image")
        return hashlib.sha256(bytes(image.constBits())).hexdigest()
    finally:
        document.close()


def _render_difference_bounds(base_path, marked_path):
    documents = [QPdfDocument(), QPdfDocument()]
    try:
        for document, path in zip(documents, (base_path, marked_path)):
            error = document.load(str(path))
            if error != QPdfDocument.Error.None_:
                raise AssertionError(f"QtPdf load failed: {error}")
        size = documents[1].pagePointSize(0)
        render_size = QSize(round(size.width()), round(size.height()))
        base = documents[0].render(0, render_size)
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        marked = documents[1].render(0, render_size, options)
        changed = [(x, y) for y in range(base.height()) for x in range(base.width())
                   if any(abs(left - right) > 12 for left, right in zip(
                       base.pixelColor(x, y).getRgb()[:3],
                       marked.pixelColor(x, y).getRgb()[:3]))]
        if not changed:
            return None
        xs, ys = [point[0] for point in changed], [point[1] for point in changed]
        return min(xs), min(ys), max(xs) + 1, max(ys) + 1
    finally:
        for document in documents:
            document.close()


def _render_difference_at(base_path, marked_path, positions):
    documents = [QPdfDocument(), QPdfDocument()]
    try:
        for document, path in zip(documents, (base_path, marked_path)):
            error = document.load(str(path))
            if error != QPdfDocument.Error.None_:
                raise AssertionError(f"QtPdf load failed: {error}")
        size = documents[1].pagePointSize(0)
        render_size = QSize(round(size.width()), round(size.height()))
        base = documents[0].render(0, render_size)
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        marked = documents[1].render(0, render_size, options)
        result = []
        for x, y in positions:
            left = base.pixelColor(x, y).getRgb()[:3]
            right = marked.pixelColor(x, y).getRgb()[:3]
            result.append(any(abs(a - b) > 12 for a, b in zip(left, right)))
        return tuple(result)
    finally:
        for document in documents:
            document.close()


class PersistenceFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_geometry_matches_qtpdf_crop_rotation_and_link_coordinates(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                path = _geometry_pdf(self.root / f"geometry-{rotation}.pdf", rotation)
                reader = PdfReader(path, strict=True)
                page = reader.pages[0]
                document = QPdfDocument()
                try:
                    self.assertEqual(document.load(str(path)), QPdfDocument.Error.None_)
                    size = document.pagePointSize(0)
                    geometry = PageGeometry.from_page(page, (size.width(), size.height()))
                    self.assertEqual((size.width(), size.height()),
                                     (600, 500) if rotation in (90, 270) else (500, 600))
                    model = QPdfLinkModel(document=document)
                    model.setPage(0)
                    link_rect = model.data(model.index(0, 0), QPdfLinkModel.Role.Rectangle.value)
                    converted = geometry.qt_rect_to_pdf((link_rect.x(), link_rect.y(),
                                                         link_rect.width(), link_rect.height()))
                    for actual, expected in zip(converted, (150, 200, 250, 300)):
                        self.assertAlmostEqual(actual, expected, places=5)
                    for point in ((0, 0), (size.width(), size.height()), (123.5, 254.25)):
                        round_trip = geometry.pdf_to_qt(geometry.qt_to_pdf(point))
                        self.assertAlmostEqual(round_trip[0], point[0], places=6)
                        self.assertAlmostEqual(round_trip[1], point[1], places=6)
                finally:
                    document.close()

    def test_geometry_rejects_unverified_rotation_userunit_and_size(self):
        path = _geometry_pdf(self.root / "unsupported.pdf")
        reader = PdfReader(path)
        page = reader.pages[0]
        page[NameObject("/Rotate")] = NumberObject(45)
        with self.assertRaisesRegex(GeometryError, "rotation"):
            PageGeometry.from_page(page, (500, 600))

        page[NameObject("/Rotate")] = FloatObject(90.5)
        with self.assertRaisesRegex(GeometryError, "rotation"):
            PageGeometry.from_page(page, (500, 600))

        page[NameObject("/Rotate")] = NumberObject(0)
        page[NameObject("/UserUnit")] = FloatObject(2)
        with self.assertRaisesRegex(GeometryError, "UserUnit"):
            PageGeometry.from_page(page, (500, 600))

        del page[NameObject("/UserUnit")]
        with self.assertRaisesRegex(GeometryError, "page size"):
            PageGeometry.from_page(page, (600, 500))

    def test_all_five_native_converters_preserve_geometry_text_and_style(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                path = _geometry_pdf(self.root / f"convert-{rotation}.pdf", rotation)
                reader = PdfReader(path)
                size = _qt_page_sizes(path)[0]
                geometry = PageGeometry.from_page(reader.pages[0], size)
                records = _items(geometry)
                converted = [convert_annotation(item, geometry) for item in records]
                self.assertEqual([str(item["/Subtype"]) for item in converted],
                                 ["/Text", "/Highlight", "/Underline", "/StrikeOut", "/Ink"])
                note, highlight, underline, strikeout, ink = converted
                self.assertEqual(note["/Contents"], "Saved note")
                self.assertEqual(note["/NM"], "note-id")
                self.assertEqual(tuple(float(value) for value in note["/C"]),
                                 tuple(value / 255 for value in (151, 121, 40)))
                note_rect = _rect(note)
                self.assertAlmostEqual((note_rect[0] + note_rect[2]) / 2, 200, places=5)
                self.assertAlmostEqual((note_rect[1] + note_rect[3]) / 2, 300, places=5)
                self.assertEqual(highlight["/Contents"], "Selected words")
                self.assertEqual(tuple(float(value) for value in highlight["/C"]),
                                 (1.0, 220 / 255, 40 / 255))
                self.assertAlmostEqual(float(highlight["/CA"]), 105 / 255)
                expected_quad = (100, 480, 150, 480, 150, 500, 100, 500)
                for annotation in (highlight, underline, strikeout):
                    self.assertEqual(tuple(float(v) for v in annotation["/QuadPoints"]), expected_quad)
                    self.assertEqual(_rect(annotation), (100, 480, 150, 500))
                for annotation in (underline, strikeout):
                    self.assertEqual(tuple(float(value) for value in annotation["/C"]),
                                     tuple(value / 255 for value in (205, 91, 32)))
                    self.assertAlmostEqual(float(annotation["/CA"]), 220 / 255)
                    self.assertAlmostEqual(float(annotation["/BS"]["/W"]), 1.5)
                mapped_points = tuple(float(v) for v in ink["/InkList"][0])
                self.assertEqual(mapped_points, (200, 350, 220, 370, 240, 360))
                self.assertEqual(float(ink["/BS"]["/W"]), 2.5)
                self.assertEqual(tuple(float(value) for value in ink["/C"]),
                                 tuple(value / 255 for value in (24, 32, 44)))
                self.assertEqual(_rect(ink), (198.75, 348.75, 241.25, 371.25))

    def test_quadpoints_appearance_renders_on_expected_crop_and_rotations(self):
        expected_regions = {
            0: (50, 200, 100, 220),
            90: (380, 50, 400, 100),
            180: (400, 380, 450, 400),
            270: (200, 400, 220, 450),
        }
        underline_lines = {0: "h", 90: "v", 180: "h", 270: "v"}
        underline_coordinates = {0: 219.5, 90: 380.5, 180: 380.5, 270: 219.5}
        strike_coordinates = {0: 210, 90: 390, 180: 390, 270: 210}

        for rotation in (0, 90, 180, 270):
            source = _geometry_pdf(self.root / f"render-source-{rotation}.pdf", rotation)
            source_reader = PdfReader(source, strict=True)
            size = _qt_page_sizes(source)[0]
            geometry = PageGeometry.from_page(source_reader.pages[0], size)
            region = tuple(geometry.pdf_to_qt(point) for point in
                           ((100, 500), (150, 500), (150, 480), (100, 480), (100, 500)))
            left, top, right, bottom = expected_regions[rotation]

            for kind in ("highlight", "underline", "strikeout"):
                with self.subTest(rotation=rotation, kind=kind):
                    destination = self.root / f"render-{rotation}-{kind}.pdf"
                    record = Markup(f"render-{rotation}-{kind}", kind, 0, 1,
                                    (region,), "target")
                    result = write_annotated_pdf(source, destination, (record,))
                    self.assertTrue(result.success, result.error)
                    annotation = next(ref.get_object() for ref in
                                      PdfReader(destination).pages[0]["/Annots"]
                                      if ref.get_object().get("/NM") == record.id)
                    # The serialized CCW points are independent of the page's
                    # visible rotation and crop-space origin.
                    self.assertEqual(tuple(float(value) for value in annotation["/QuadPoints"]),
                                     (100, 480, 150, 480, 150, 500, 100, 500))
                    bounds = _render_difference_bounds(source, destination)
                    self.assertIsNotNone(bounds)
                    x0, y0, x1, y1 = bounds
                    if kind == "highlight":
                        self.assertLessEqual(x0, left + 1)
                        self.assertGreaterEqual(x1, right - 1)
                        self.assertLessEqual(y0, top + 1)
                        self.assertGreaterEqual(y1, bottom - 1)
                        self.assertGreaterEqual(x0, left - 2)
                        self.assertLessEqual(x1, right + 2)
                        self.assertGreaterEqual(y0, top - 2)
                        self.assertLessEqual(y1, bottom + 2)
                    else:
                        horizontal = underline_lines[rotation] == "h"
                        if kind == "strikeout":
                            coordinate = strike_coordinates[rotation]
                        else:
                            coordinate = underline_coordinates[rotation]
                        if horizontal:
                            self.assertLessEqual(x0, left + 1)
                            self.assertGreaterEqual(x1, right - 1)
                            self.assertLessEqual(y0, coordinate + 2)
                            self.assertGreaterEqual(y1, coordinate - 2)
                            self.assertLessEqual(y1 - y0, 4)
                        else:
                            self.assertLessEqual(y0, top + 1)
                            self.assertGreaterEqual(y1, bottom - 1)
                            self.assertLessEqual(x0, coordinate + 2)
                            self.assertGreaterEqual(x1, coordinate - 2)
                            self.assertLessEqual(x1 - x0, 4)

    def test_serialized_annotation_geometry_is_stable_for_crop_and_rotation(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                source = _geometry_pdf(self.root / f"roundtrip-{rotation}.pdf", rotation)
                before = source.read_bytes()
                source_page = PdfReader(source, strict=True).pages[0]
                geometry = PageGeometry.from_page(source_page, _qt_page_sizes(source)[0])
                destination = self.root / f"roundtrip-output-{rotation}.pdf"

                result = write_annotated_pdf(source, destination, _items(geometry))

                self.assertTrue(result.success, result.error)
                self.assertEqual(source.read_bytes(), before)
                output = PdfReader(destination, strict=True)
                page = output.pages[0]
                saved = {str(reference.get_object().get("/NM")): reference.get_object()
                         for reference in page["/Annots"] if reference.get_object().get("/NM")}
                self.assertEqual(tuple(float(value) for value in saved["note-id"]["/Rect"]),
                                 (191, 291, 209, 309))
                self.assertEqual(str(saved["note-id"]["/Contents"]), "Saved note")
                for kind in ("highlight", "underline", "strikeout"):
                    annotation = saved[f"{kind}-id"]
                    self.assertEqual(tuple(float(value) for value in annotation["/Rect"]),
                                     (100, 480, 150, 500))
                    self.assertEqual(tuple(float(value) for value in annotation["/QuadPoints"]),
                                     (100, 480, 150, 480, 150, 500, 100, 500))
                    self.assertEqual(str(annotation["/Contents"]), "Selected words")
                self.assertEqual(tuple(float(value) for value in saved["ink-id"]["/InkList"][0]),
                                 (200, 350, 220, 370, 240, 360))
                self.assertEqual(tuple(float(value) for value in saved["ink-id"]["/Rect"]),
                                 (198.75, 348.75, 241.25, 371.25))
                self.assertEqual(float(saved["ink-id"]["/BS"]["/W"]), 2.5)

    def test_multiregion_markup_survives_serialization_without_merging(self):
        source = _geometry_pdf(self.root / "multi-region-source.pdf", rotation=90)
        page = PdfReader(source, strict=True).pages[0]
        size = _qt_page_sizes(source)[0]
        geometry = PageGeometry.from_page(page, size)
        pdf_rectangles = ((100, 480, 150, 500), (220, 430, 280, 450))
        regions = tuple(tuple(geometry.pdf_to_qt(point) for point in
                              ((left, top), (right, top), (right, bottom), (left, bottom),
                               (left, top)))
                        for left, bottom, right, top in pdf_rectangles)
        item = Markup("two-regions", "highlight", 0, 2, regions, "two lines")
        destination = self.root / "multi-region-output.pdf"
        result = write_annotated_pdf(source, destination, (item,))
        self.assertTrue(result.success, result.error)

        output = PdfReader(destination, strict=True)
        saved = next(ref.get_object() for ref in output.pages[0]["/Annots"]
                     if ref.get_object().get("/NM") == item.id)
        self.assertEqual(tuple(float(value) for value in saved["/QuadPoints"]),
                         (100, 480, 150, 480, 150, 500, 100, 500,
                          220, 430, 280, 430, 280, 450, 220, 450))
        self.assertEqual(tuple(float(value) for value in saved["/Rect"]),
                         (100, 430, 280, 500))
        appearance = saved["/AP"]["/N"].get_object().get_data().decode("ascii")
        self.assertEqual(appearance.count(" m\n"), 2)
        self.assertEqual(len(saved["/QuadPoints"]) // 8, 2)
        # PDF coordinates map independently to two disjoint visible areas at
        # this crop and rotation; the empty gap between them stays untouched.
        self.assertEqual(_render_difference_at(
            source, destination, ((390, 75), (340, 200), (365, 135))),
            (True, True, False))

    def test_converter_rejects_non_quad_markup_instead_of_guessing(self):
        path = _geometry_pdf(self.root / "bad-quad.pdf")
        reader = PdfReader(path)
        geometry = PageGeometry.from_page(reader.pages[0], _qt_page_sizes(path)[0])
        item = Markup("bad", "highlight", 0, 1,
                      (((10, 10), (30, 12), (28, 30)),), "text")
        with self.assertRaisesRegex(GeometryError, "four-corner"):
            convert_annotation(item, geometry)

    def test_writer_preserves_fixture_structures_and_reopens_with_qtpdf(self):
        source = _core_fixture(self.root / "source.pdf")
        before = source.read_bytes()
        source_reader = PdfReader(source, strict=True)
        geometry = PageGeometry.from_page(source_reader.pages[0], _qt_page_sizes(source)[0])
        second_geometry = PageGeometry.from_page(source_reader.pages[1], _qt_page_sizes(source)[1])
        records = (*_items(geometry),
                   Note("page-two-id", "note", 1, 7,
                        second_geometry.pdf_to_qt((180, 260)), "Second page note"))
        destination = self.root / "annotated.pdf"

        result = write_annotated_pdf(source, destination, records)

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.output_path, destination)
        self.assertTrue(result.published)
        if os.name != "nt":
            self.assertTrue(result.crash_durable)
        else:
            self.assertFalse(result.crash_durable)
            self.assertIn("Windows", result.warning)
        self.assertEqual(source.read_bytes(), before)
        output = PdfReader(destination, strict=True)
        self.assertEqual(len(output.pages), len(source_reader.pages))
        for original_page, written_page in zip(source_reader.pages, output.pages):
            self.assertEqual(tuple(original_page.mediabox), tuple(written_page.mediabox))
            self.assertEqual(tuple(original_page.cropbox), tuple(written_page.cropbox))
            self.assertEqual(original_page.extract_text(), written_page.extract_text())
        self.assertEqual(output.metadata.title, "Persistence fixture")
        self.assertEqual(output.metadata.author, "Glyph tests")
        self.assertEqual(str(output.outline[0].title), "Page two")
        self.assertEqual(output.get_destination_page_number(
            output.named_destinations["fixture-destination"]), 1)
        self.assertEqual(output.attachments["fixture.txt"], [b"fixture attachment"])
        self.assertEqual(output.get_fields()["fixture_field"]["/V"], "fixture value")
        original_links = [ref.get_object() for ref in source_reader.pages[0]["/Annots"]
                          if ref.get_object().get("/Subtype") == "/Link"]
        written_links = [ref.get_object() for ref in output.pages[0]["/Annots"]
                         if ref.get_object().get("/Subtype") == "/Link"]
        self.assertEqual(len(written_links), len(original_links))
        def link_target(pdf, link):
            destination = link.get("/Dest")
            if destination:
                return ("destination", pdf.get_page_number(destination[0].get_object()),
                        tuple(str(value) for value in destination[1:]))
            return ("uri", str(link["/A"]["/URI"]))
        self.assertEqual([link_target(output, link) for link in written_links],
                         [link_target(source_reader, link) for link in original_links])
        self.assertEqual(len(output.pages[0]["/Annots"]), len(source_reader.pages[0]["/Annots"]) + 5)
        saved = {str(reference.get_object().get("/NM")): reference.get_object()
                 for reference in output.pages[0]["/Annots"]
                 if reference.get_object().get("/NM")}
        self.assertEqual(str(saved["note-id"]["/Subtype"]), "/Text")
        self.assertEqual(tuple(float(value) for value in saved["note-id"]["/Rect"]),
                         (191, 291, 209, 309))
        self.assertEqual(str(saved["note-id"]["/Contents"]), "Saved note")
        expected_quad = (100, 480, 150, 480, 150, 500, 100, 500)
        for kind in ("highlight", "underline", "strikeout"):
            annotation = saved[f"{kind}-id"]
            self.assertEqual(str(annotation["/Subtype"]), f"/{kind.title() if kind != 'strikeout' else 'StrikeOut'}")
            self.assertEqual(tuple(float(value) for value in annotation["/Rect"]),
                             (100, 480, 150, 500))
            self.assertEqual(tuple(float(value) for value in annotation["/QuadPoints"]),
                             expected_quad)
            self.assertEqual(str(annotation["/Contents"]), "Selected words")
            self.assertEqual(tuple(float(value) for value in
                                   annotation["/AP"]["/N"].get_object()["/BBox"]),
                             (0, 0, 50, 20))
        self.assertEqual(str(saved["ink-id"]["/Subtype"]), "/Ink")
        self.assertEqual(tuple(float(value) for value in saved["ink-id"]["/Rect"]),
                         (198.75, 348.75, 241.25, 371.25))
        self.assertEqual(tuple(float(value) for value in saved["ink-id"]["/InkList"][0]),
                         (200, 350, 220, 370, 240, 360))
        self.assertEqual(float(saved["ink-id"]["/BS"]["/W"]), 2.5)
        second_page_notes = [reference.get_object() for reference in output.pages[1]["/Annots"]
                             if reference.get_object().get("/NM") == "page-two-id"]
        self.assertEqual(len(second_page_notes), 1)
        self.assertEqual(second_page_notes[0]["/Contents"], "Second page note")
        with self.subTest(renderer="QtPdf"):
            document = QPdfDocument()
            try:
                self.assertEqual(document.load(str(destination)), QPdfDocument.Error.None_)
                self.assertEqual(document.pageCount(), 3)
                self.assertEqual(document.status(), QPdfDocument.Status.Ready)
            finally:
                document.close()

    def test_windows_absolute_rename_arguments_and_unicode(self):
        from glyph_desktop import pdf_writer

        path = "C:\\Glyph\\résumé-🐾.pdf"
        observed = {}

        class Kernel:
            def SetFileInformationByHandle(self, handle, info_class, pointer, size):
                info = ctypes.cast(pointer, ctypes.POINTER(pdf_writer._WindowsRenameInfo)).contents
                observed.update(handle=handle.value, info_class=info_class, size=size,
                                root=info.RootDirectory, replace=info.ReplaceIfExists,
                                length=info.FileNameLength,
                                name=ctypes.string_at(pointer.value + pdf_writer._WindowsRenameInfo.FileName.offset,
                                                      info.FileNameLength))
                return True

        with patch.object(pdf_writer, "_windows_api", return_value=Kernel()):
            pdf_writer._windows_publish_by_handle(42, path)
        self.assertEqual(observed["handle"], 42)
        self.assertEqual(observed["info_class"], 3)
        self.assertIsNone(observed["root"])
        self.assertEqual(observed["replace"], 0)
        self.assertEqual(observed["length"], len(path.encode("utf-16-le")))
        self.assertEqual(observed["name"].decode("utf-16-le"), path)
        self.assertEqual(observed["size"], max(ctypes.sizeof(pdf_writer._WindowsRenameInfo),
                                              pdf_writer._WindowsRenameInfo.FileName.offset + observed["length"]))
        self.assertEqual(pdf_writer._WindowsRenameInfo.RootDirectory.offset,
                         ctypes.sizeof(ctypes.c_void_p))
        with self.assertRaisesRegex(ValueError, "absolute"):
            pdf_writer._windows_rename_buffer("relative.pdf")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            pdf_writer._windows_rename_buffer("\\\\server\\share\\file.pdf")
        class ExistingTargetKernel:
            def SetFileInformationByHandle(self, handle, info_class, pointer, size):
                return False

        with (patch.object(pdf_writer, "_windows_api", return_value=ExistingTargetKernel()),
              patch.object(ctypes, "get_last_error", return_value=183, create=True)):
            with self.assertRaises(FileExistsError):
                pdf_writer._windows_publish_by_handle(42, path)

    def test_same_path_and_existing_destination_are_never_overwritten(self):
        source = _core_fixture(self.root / "source.pdf")
        before = source.read_bytes()
        same = write_annotated_pdf(source, source.parent / "." / source.name, ())
        self.assertFalse(same.success)
        self.assertIn("different", same.error)
        self.assertEqual(source.read_bytes(), before)

        destination = self.root / "existing.pdf"
        destination.write_bytes(b"keep existing file")
        result = write_annotated_pdf(source, destination, ())
        self.assertFalse(result.success)
        self.assertIn("already exists", result.error)
        self.assertEqual(destination.read_bytes(), b"keep existing file")

    def test_destination_created_during_write_is_never_overwritten(self):
        source = _core_fixture(self.root / "source.pdf")
        destination = self.root / "race.pdf"
        from glyph_desktop import pdf_writer
        if os.name == "nt":
            original_publish = pdf_writer._windows_publish_by_handle

            def create_conflict(file_handle, destination_name):
                destination.write_bytes(b"concurrent file")
                return original_publish(file_handle, destination_name)

            patcher = patch("glyph_desktop.pdf_writer._windows_publish_by_handle",
                            side_effect=create_conflict)
        else:
            original_link = pdf_writer._link_fd_noclobber

            def create_conflict(fd, stage_name, stage_fd, destination_name, destination_fd):
                destination.write_bytes(b"concurrent file")
                return original_link(fd, stage_name, stage_fd, destination_name, destination_fd)

            patcher = patch("glyph_desktop.pdf_writer._link_fd_noclobber",
                            side_effect=create_conflict)
        with patcher:
            result = write_annotated_pdf(source, destination, ())
        self.assertFalse(result.success)
        self.assertEqual(destination.read_bytes(), b"concurrent file")
        self.assertEqual(list(self.root.glob(f".{destination.name}.glyph-stage-*")), [])

    def test_qtpdf_descriptor_device_preserves_stream_ownership_and_position(self):
        from glyph_desktop.pdf_writer import _qt_stream_page_sizes

        source = _geometry_pdf(self.root / "device-source.pdf")
        with source.open("rb", buffering=0) as stream:
            stream.seek(17)
            self.assertEqual(_qt_stream_page_sizes(stream), ((500, 600),))
            self.assertFalse(stream.closed)
            self.assertEqual(stream.tell(), 17)
            self.assertGreater(os.fstat(stream.fileno()).st_size, 0)
            stream.seek(0)
            self.assertEqual(stream.read(5), b"%PDF-")
        with tempfile.TemporaryFile("w+b", buffering=0) as stream:
            stream.write(b"not a PDF")
            stream.seek(2)
            with self.assertRaisesRegex(ValueError, "QtPdf could not open"):
                _qt_stream_page_sizes(stream)
            self.assertFalse(stream.closed)
            self.assertEqual(stream.tell(), 2)

    def test_validation_and_publication_share_retained_file_identity(self):
        from glyph_desktop import pdf_writer

        source = _core_fixture(self.root / "handle-source.pdf")
        before = source.read_bytes()
        destination = self.root / "handle-résumé-🐾.pdf"
        original_validate = pdf_writer._validate_output
        original_qt_stream = pdf_writer._qt_stream_page_sizes
        original_qt_path = pdf_writer._qt_page_sizes
        seen = {}

        def validate(reader, stream, expected):
            self.assertFalse(stream.closed)
            seen["stream"] = stream
            seen["fd"] = stream.fileno()
            seen["identity"] = (os.fstat(stream.fileno()).st_dev,
                                os.fstat(stream.fileno()).st_ino)
            if os.name == "nt":
                import msvcrt
                seen["handle"] = msvcrt.get_osfhandle(stream.fileno())
            original_validate(reader, stream, expected)
            self.assertFalse(stream.closed)
            os.fstat(stream.fileno())

        if os.name == "nt":
            original_publish = pdf_writer._windows_publish_by_handle

            def publish(handle, name):
                self.assertEqual(handle, seen["handle"])
                self.assertEqual(name, str(destination.resolve()))
                os.fstat(seen["fd"])
                return original_publish(handle, name)

            publisher = patch.object(pdf_writer, "_windows_publish_by_handle", side_effect=publish)
        else:
            original_publish = pdf_writer._link_fd_noclobber

            def publish(fd, stage_name, stage_fd, name, directory_fd):
                self.assertEqual(fd, seen["fd"])
                self.assertEqual((os.fstat(fd).st_dev, os.fstat(fd).st_ino), seen["identity"])
                return original_publish(fd, stage_name, stage_fd, name, directory_fd)

            publisher = patch.object(pdf_writer, "_link_fd_noclobber", side_effect=publish)

        with (patch.object(pdf_writer, "_validate_output", side_effect=validate),
              patch.object(pdf_writer, "PdfReader", wraps=PdfReader) as pdf_reader,
              patch.object(pdf_writer, "_qt_stream_page_sizes", wraps=original_qt_stream) as qt_stream,
              patch.object(pdf_writer, "_qt_page_sizes", wraps=original_qt_path) as qt_path,
              publisher):
            result = write_annotated_pdf(source, destination, ())

        self.assertTrue(result.success, result.error)
        self.assertTrue(result.published)
        with self.assertRaises(OSError):
            os.fstat(seen["fd"])
        qt_stream.assert_called_once()
        qt_path.assert_called_once_with(source.resolve())
        self.assertIs(pdf_reader.call_args_list[1].args[0], seen["stream"])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(len(PdfReader(destination, strict=True).pages), 3)
        self.assertEqual(list(self.root.glob(f".{destination.name}.glyph-stage-*")), [])

    def test_invalid_staged_stream_fails_without_publication(self):
        from glyph_desktop import pdf_writer

        source = _core_fixture(self.root / "invalid-stage-source.pdf")
        before = source.read_bytes()
        destination = self.root / "invalid-stage-output.pdf"
        original_validate = pdf_writer._validate_output

        def corrupt_then_validate(reader, stream, expected):
            stream.seek(0)
            stream.truncate()
            stream.write(b"not a PDF")
            stream.flush()
            original_validate(reader, stream, expected)

        with patch.object(pdf_writer, "_validate_output", side_effect=corrupt_then_validate):
            result = write_annotated_pdf(source, destination, ())

        self.assertFalse(result.success)
        self.assertFalse(result.published)
        self.assertFalse(result.crash_durable)
        self.assertIsNone(result.output_path)
        self.assertTrue(result.error)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(f".{destination.name}.glyph-stage-*")), [])

    def test_stage_is_private_and_path_substitution_cannot_change_published_file(self):
        stage = _create_private_stage(self.root, ".private-")
        try:
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o700)
            self.assertEqual(stage.parent, self.root)
        finally:
            stage.rmdir()

        source = _core_fixture(self.root / "source.pdf")
        before = source.read_bytes()
        rogue = _geometry_pdf(self.root / "rogue.pdf")
        from glyph_desktop.pdf_writer import _validate_output as real_validate
        substitution_denied = []

        destination = self.root / "substitution.pdf"
        with patch("glyph_desktop.pdf_writer._validate_output") as validate:
            def validate_then_substitute(reader, stream, expected):
                real_validate(reader, stream, expected)
                stage = next(self.root.glob(f".{destination.name}.glyph-stage-*"))
                temporary = stage / "validated-output.tmp"
                try:
                    os.replace(rogue, temporary)
                except OSError:
                    if os.name != "nt":
                        raise
                    substitution_denied.append(True)

            validate.side_effect = validate_then_substitute
            result = write_annotated_pdf(source, destination, ())

        if os.name == "nt":
            # The open Windows handle denies delete sharing, so substitution is
            # blocked and the validated file is published by that same handle.
            self.assertTrue(result.success, result.error)
            self.assertTrue(result.published)
            self.assertEqual(substitution_denied, [True])
            published = PdfReader(destination, strict=True)
            self.assertEqual(len(published.pages), len(PdfReader(source).pages))
        else:
            # Replacing the last staging name unlinks the validated inode;
            # publication must fail closed rather than link the rogue PDF.
            self.assertFalse(result.success)
            self.assertFalse(result.published)
            self.assertIsNone(result.output_path)
            self.assertIn("Validated staging file", result.error)
            self.assertFalse(destination.exists())
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(self.root.glob(f".{destination.name}.glyph-stage-*")), [])

    def test_directory_sync_occurs_after_publish_and_sync_failure_is_reported(self):
        if os.name == "nt":
            self.skipTest("Windows publication reports directory-entry crash durability as unavailable")
        source = _core_fixture(self.root / "source.pdf")
        destination = self.root / "durable.pdf"
        original_fsync = os.fsync
        sync_states = []

        def record_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                sync_states.append(destination.exists())
            return original_fsync(fd)

        with patch("glyph_desktop.pdf_writer.os.fsync", side_effect=record_sync):
            result = write_annotated_pdf(source, destination, ())
        self.assertTrue(result.success, result.error)
        self.assertEqual(sync_states, [True])
        self.assertTrue(result.crash_durable)

        failed_destination = self.root / "directory-sync-failed.pdf"

        def fail_directory_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected directory fsync failure")
            return original_fsync(fd)

        with patch("glyph_desktop.pdf_writer.os.fsync", side_effect=fail_directory_sync):
            failed = write_annotated_pdf(source, failed_destination, ())
        self.assertFalse(failed.success)
        self.assertTrue(failed.published)
        self.assertFalse(failed.crash_durable)
        self.assertEqual(failed.output_path, failed_destination)
        self.assertIn("directory fsync failure", failed.error)
        self.assertTrue(failed_destination.is_file())

    def test_cleanup_failure_does_not_escape_result_or_claim_success(self):
        source = _core_fixture(self.root / "source.pdf")
        destination = self.root / "cleanup-failed.pdf"
        with patch("glyph_desktop.pdf_writer._cleanup_staged_file",
                   side_effect=OSError("injected cleanup failure")):
            result = write_annotated_pdf(source, destination, ())
        self.assertFalse(result.success)
        self.assertTrue(result.published)
        self.assertEqual(result.crash_durable, os.name != "nt")
        self.assertEqual(result.output_path, destination)
        self.assertIn("Cleanup failed", result.error)
        self.assertIn("injected cleanup failure", result.error)
        self.assertTrue(destination.is_file())

        failed_destination = self.root / "validation-and-cleanup-failed.pdf"
        with (patch("glyph_desktop.pdf_writer._validate_output",
                    side_effect=ValueError("primary validation failure")),
              patch("glyph_desktop.pdf_writer._cleanup_staged_file",
                    side_effect=OSError("secondary cleanup failure"))):
            failed = write_annotated_pdf(source, failed_destination, ())
        self.assertFalse(failed.success)
        self.assertFalse(failed.published)
        self.assertIsNone(failed.output_path)
        self.assertIn("primary validation failure", failed.error)
        self.assertIn("secondary cleanup failure", failed.error)
        self.assertFalse(failed_destination.exists())

    def test_failed_output_validation_cleans_temporary_file(self):
        source = _core_fixture(self.root / "source.pdf")
        destination = self.root / "failed.pdf"
        with patch("glyph_desktop.pdf_writer._validate_output", side_effect=ValueError("injected")):
            result = write_annotated_pdf(source, destination, ())
        self.assertFalse(result.success)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(f".{destination.name}.glyph-stage-*")), [])

    def test_malformed_encrypted_and_signed_inputs_fail_safely(self):
        malformed = self.root / "malformed.pdf"
        malformed.write_bytes(b"not a PDF")
        malformed_result = write_annotated_pdf(malformed, self.root / "malformed-out.pdf", ())
        self.assertFalse(malformed_result.success)
        self.assertFalse((self.root / "malformed-out.pdf").exists())

        source = _core_fixture(self.root / "source.pdf")
        encrypted_writer = PdfWriter(clone_from=PdfReader(source))
        encrypted_writer.encrypt("user", "owner", algorithm="RC4-128")
        encrypted = self.root / "encrypted.pdf"
        encrypted_writer.write(encrypted)
        encrypted_result = write_annotated_pdf(encrypted, self.root / "encrypted-out.pdf", ())
        self.assertFalse(encrypted_result.success)
        self.assertIn("Encrypted PDFs", encrypted_result.error)
        self.assertFalse((self.root / "encrypted-out.pdf").exists())

        signed_writer = PdfWriter(clone_from=PdfReader(source))
        signature = signed_writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Sig"),
            NameObject("/ByteRange"): ArrayObject([NumberObject(0), NumberObject(10),
                                                     NumberObject(20), NumberObject(30)]),
        }))
        signed_writer.root_object[NameObject("/Perms")] = signed_writer._add_object(
            DictionaryObject({NameObject("/DocMDP"): signature}))
        signed = self.root / "signed-marker.pdf"
        signed_writer.write(signed)
        signed_result = write_annotated_pdf(signed, self.root / "signed-out.pdf", ())
        self.assertFalse(signed_result.success)
        self.assertIn("Signed PDFs", signed_result.error)
        self.assertFalse((self.root / "signed-out.pdf").exists())

    def test_signature_field_and_doc_timestamp_guards_reject_without_changes(self):
        source = _core_fixture(self.root / "source.pdf")
        for marker in ("/FT-Sig", "/Type-DocTimeStamp"):
            with self.subTest(marker=marker):
                writer = PdfWriter(clone_from=PdfReader(source))
                if marker == "/FT-Sig":
                    field = DictionaryObject({
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Widget"),
                        NameObject("/FT"): NameObject("/Sig"),
                        NameObject("/T"): TextStringObject("signature_field"),
                        NameObject("/Rect"): RectangleObject([20, 20, 40, 40]),
                    })
                    writer.add_annotation(0, field)
                    field_ref = writer.pages[0]["/Annots"][-1]
                    writer.root_object[NameObject("/AcroForm")] = writer._add_object(
                        DictionaryObject({NameObject("/Fields"): ArrayObject([field_ref])}))
                else:
                    timestamp = writer._add_object(DictionaryObject({
                        NameObject("/Type"): NameObject("/DocTimeStamp"),
                        NameObject("/ByteRange"): ArrayObject([NumberObject(0), NumberObject(10),
                                                                 NumberObject(20), NumberObject(30)]),
                    }))
                    writer.root_object[NameObject("/Perms")] = writer._add_object(
                        DictionaryObject({NameObject("/DocTimeStamp"): timestamp}))
                signed = self.root / f"signature-{marker[1:]}.pdf"
                output = self.root / f"signature-output-{marker[1:]}.pdf"
                writer.write(signed)
                before = signed.read_bytes()

                result = write_annotated_pdf(signed, output, ())

                self.assertFalse(result.success)
                self.assertIn("Signed PDFs", result.error)
                self.assertEqual(signed.read_bytes(), before)
                self.assertFalse(output.exists())

    def test_qtpdf_native_annotation_flag_policy_and_repeat_structure(self):
        source = _core_fixture(self.root / "source.pdf")
        geometry = PageGeometry.from_page(PdfReader(source).pages[0], _qt_page_sizes(source)[0])
        records = _items(geometry)
        first = self.root / "first.pdf"
        second = self.root / "second.pdf"
        result_a = write_annotated_pdf(source, first, records)
        result_b = write_annotated_pdf(source, second, records)
        self.assertTrue(result_a.success, result_a.error)
        self.assertTrue(result_b.success, result_b.error)

        self.assertEqual(_render_digest(source), _render_digest(first))
        self.assertNotEqual(_render_digest(source, annotations=True),
                            _render_digest(first, annotations=True))
        reader_a, reader_b = PdfReader(first), PdfReader(second)
        annotations_a = [ref.get_object() for ref in reader_a.pages[0]["/Annots"]
                         if ref.get_object().get("/NM")]
        annotations_b = [ref.get_object() for ref in reader_b.pages[0]["/Annots"]
                         if ref.get_object().get("/NM")]
        normalize = lambda items: [(str(item["/NM"]), str(item["/Subtype"]),
                                    tuple(float(value) for value in item["/Rect"]),
                                    str(item.get("/Contents", ""))) for item in items]
        self.assertEqual(normalize(annotations_a), normalize(annotations_b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
