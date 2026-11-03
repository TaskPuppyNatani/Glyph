"""Disposable pypdf feasibility checks; run from the repository root.

Outputs go to /tmp/glyph-m3e1-spike. This is not application persistence code.
"""
import hashlib
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_links import write_link_pdf
from pypdf import PdfReader, PdfWriter, __version__
from pypdf.annotations import Highlight, Text
from pypdf.constants import UserAccessPermissions
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from PySide6.QtCore import QSize
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PySide6.QtWidgets import QApplication

OUT = Path('/tmp/glyph-m3e1-spike')


def array(values):
    return ArrayObject([FloatObject(v) for v in values])


def annotation(kind):
    # These sample coordinates are already in PDF user space (origin at bottom left).
    if kind == 'Text':
        return Text(rect=(280, 600, 305, 625), text='Glyph note')
    quad = array([280, 570, 390, 570, 280, 550, 390, 550])
    if kind == 'Highlight':
        return Highlight(rect=(280, 550, 390, 570), quad_points=quad, highlight_color='ffff00')
    obj = DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
                            NameObject('/Subtype'): NameObject('/' + kind),
                            NameObject('/C'): array([1, 0, 0]),
                            NameObject('/F'): NumberObject(4)})
    if kind == 'Ink':
        obj[NameObject('/Rect')] = array([278, 478, 392, 522])
        obj[NameObject('/InkList')] = ArrayObject([array([280, 480, 330, 520, 390, 485])])
        obj[NameObject('/BS')] = DictionaryObject({NameObject('/W'): FloatObject(2.5)})
    else:
        obj[NameObject('/Rect')] = array([280, 550, 390, 570])
        obj[NameObject('/QuadPoints')] = quad
    return obj


def subtypes(reader):
    return [str(ref.get_object().get('/Subtype')) for ref in reader.pages[0].get('/Annots', [])]


def render(path, show_annotations=False, page=0):
    doc = QPdfDocument()
    error = doc.load(str(path))
    if error != QPdfDocument.Error.None_:
        raise AssertionError(f'QtPdf load: {error}')
    options = QPdfDocumentRenderOptions()
    if show_annotations:
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
    image = doc.render(page, QSize(612, 792), options)
    if image.isNull():
        raise AssertionError('QtPdf returned a null page image')
    digest = hashlib.sha256(bytes(image.constBits())).hexdigest()
    count = doc.pageCount()
    doc.close()
    return count, digest


class WriterSpike(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        OUT.mkdir(parents=True, exist_ok=True)
        raw = OUT / 'fixture-base.pdf'
        write_link_pdf(raw)
        writer = PdfWriter(clone_from=PdfReader(raw))
        writer.add_metadata({'/Title': 'Glyph spike fixture', '/Author': 'Glyph'})
        writer.add_outline_item('Page two', 1)
        writer.add_named_destination('spike-destination', 1)
        writer.add_attachment('spike.txt', b'attachment sample')
        writer.root_object[NameObject('/GlyphSpikeCustom')] = DictionaryObject(
            {NameObject('/Marker'): TextStringObject('opaque custom data')})
        writer.add_annotation(0, Text(rect=(260, 650, 275, 665), text='existing annotation'))
        widget = DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
                                   NameObject('/Subtype'): NameObject('/Widget'),
                                   NameObject('/FT'): NameObject('/Tx'),
                                   NameObject('/T'): TextStringObject('spike_field'),
                                   NameObject('/V'): TextStringObject('original value'),
                                   NameObject('/Rect'): array([300, 650, 450, 675]),
                                   NameObject('/F'): NumberObject(4)})
        writer.add_annotation(0, widget)
        ref = writer.pages[0]['/Annots'][-1]
        form = DictionaryObject({NameObject('/Fields'): ArrayObject([ref])})
        writer.root_object[NameObject('/AcroForm')] = writer._add_object(form)
        cls.source = OUT / 'source.pdf'
        writer.write(cls.source)
        cls.original = cls.source.read_bytes()
        print('PYPDF_VERSION', __version__, 'SOURCE_SHA256', hashlib.sha256(cls.original).hexdigest())

    def test_01_full_rewrite_preserves_fixture_structures(self):
        source = PdfReader(self.source)
        output = OUT / 'rewrite.pdf'
        PdfWriter(clone_from=source).write(output)
        result = PdfReader(output)
        self.assertEqual(len(result.pages), 3)
        self.assertEqual([tuple(p.mediabox) for p in source.pages], [tuple(p.mediabox) for p in result.pages])
        self.assertEqual(source.pages[0].extract_text(), result.pages[0].extract_text())
        self.assertEqual(source.metadata.title, result.metadata.title)
        self.assertEqual(result.metadata.author, 'Glyph')
        self.assertEqual(str(source.outline[0].title), str(result.outline[0].title))
        self.assertIn('spike-destination', result.named_destinations)
        self.assertEqual(result.get_destination_page_number(result.named_destinations['spike-destination']), 1)
        self.assertEqual(result.attachments['spike.txt'], [b'attachment sample'])
        self.assertEqual(result.get_fields()['spike_field']['/V'], 'original value')
        self.assertEqual(subtypes(source), subtypes(result))
        self.assertEqual(sum(x == '/Link' for x in subtypes(result)), 7)
        self.assertEqual([ref.get_object().get('/Contents') for ref in result.pages[0]['/Annots']
                          if ref.get_object().get('/Subtype') == '/Text'], ['existing annotation'])
        self.assertEqual(result.trailer['/Root']['/GlyphSpikeCustom']['/Marker'], 'opaque custom data')
        for before, after in zip(source.pages[0]['/Annots'], result.pages[0]['/Annots']):
            before, after = before.get_object(), after.get_object()
            self.assertEqual(before.get('/Rect'), after.get('/Rect'))
            if before.get('/Dest'):
                self.assertEqual(source.get_page_number(before['/Dest'][0].get_object()),
                                 result.get_page_number(after['/Dest'][0].get_object()))
                self.assertEqual(before['/Dest'][1:], after['/Dest'][1:])
            if before.get('/A'):
                self.assertEqual(before['/A'].get('/URI'), after['/A'].get('/URI'))
            if before.get('/Subtype') == '/Widget':
                self.assertEqual(after.get('/T'), 'spike_field')
                self.assertEqual(after.get('/V'), 'original value')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_02_native_annotation_roundtrip(self):
        writer = PdfWriter(clone_from=PdfReader(self.source))
        kinds = ('Text', 'Highlight', 'Underline', 'StrikeOut', 'Ink')
        for kind in kinds:
            writer.add_annotation(0, annotation(kind))
        output = OUT / 'all-native-annotations.pdf'
        writer.write(output)
        reader = PdfReader(output)
        refs = reader.pages[0]['/Annots'][-5:]
        objects = [ref.get_object() for ref in refs]
        self.assertEqual([str(o['/Subtype']) for o in objects], ['/' + k for k in kinds])
        self.assertEqual(objects[0]['/Contents'], 'Glyph note')
        for obj in objects[1:4]:
            self.assertEqual(list(obj['/QuadPoints']), [280, 570, 390, 570, 280, 550, 390, 550])
            self.assertEqual(list(obj['/Rect']), [280, 550, 390, 570])
        self.assertEqual(list(objects[4]['/InkList'][0]), [280, 480, 330, 520, 390, 485])
        self.assertEqual(float(objects[4]['/BS']['/W']), 2.5)
        self.assertEqual(list(objects[4]['/C']), [1, 0, 0])
        self.assertEqual(list(objects[4]['/Rect']), [278, 478, 392, 522])
        self.assertTrue(all(reader.get_page_number(obj['/P'].get_object()) == 0 for obj in objects))
        self.assertEqual(len(reader.pages[0]['/Annots']), len(PdfReader(self.source).pages[0]['/Annots']) + 5)
        self.assertTrue(all('/AP' not in obj for obj in objects))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_03_incremental_prefix_and_annotation(self):
        writer = PdfWriter(self.source, incremental=True)
        writer.add_annotation(0, annotation('Text'))
        path = OUT / 'incremental.pdf'
        writer.write(path)
        data = path.read_bytes()
        self.assertTrue(data.startswith(self.original))
        self.assertGreater(len(data), len(self.original))
        self.assertIn(b'/Prev', data[len(self.original):])
        result = PdfReader(path)
        original_text = PdfReader(self.source).pages[0].extract_text()
        self.assertIn('Link text for selecting', original_text)
        self.assertEqual(result.pages[0].extract_text(), original_text)
        self.assertEqual(result.pages[0]['/Annots'][-1].get_object()['/Contents'], 'Glyph note')
        self.assertEqual(self.source.read_bytes(), self.original)
        print('INCREMENTAL_BYTES', len(self.original), len(data), 'PREFIX_EQUAL', True)

    def test_04_password_and_incremental_encryption(self):
        encrypted = OUT / 'encrypted-source.pdf'
        writer = PdfWriter(clone_from=PdfReader(self.source))
        writer.encrypt('user', 'owner', algorithm='RC4-128')
        writer.write(encrypted)
        before = encrypted.read_bytes()
        locked = PdfReader(encrypted)
        self.assertEqual(int(locked.decrypt('wrong')), 0)
        self.assertNotEqual(int(locked.decrypt('user')), 0)
        reader = PdfReader(encrypted, password='user')
        self.assertTrue(reader.is_encrypted)
        writer = PdfWriter(reader, incremental=True)
        writer.add_annotation(0, annotation('Text'))
        output = OUT / 'encrypted-incremental.pdf'
        with output.open('wb') as stream:
            with self.assertRaisesRegex(NotImplementedError, 'encrypted PDF files'):
                writer.write(stream)
        output.unlink()
        writer = PdfWriter(clone_from=reader)
        writer.add_annotation(0, annotation('Text'))
        permissions = UserAccessPermissions(reader.trailer['/Encrypt'].get_object()['/P'])
        writer.encrypt('user', 'owner', algorithm='RC4-128', permissions_flag=permissions)
        output = OUT / 'encrypted-rewrite.pdf'
        writer.write(output)
        result = PdfReader(output, password='user')
        self.assertTrue(result.is_encrypted)
        self.assertEqual(result.pages[0]['/Annots'][-1].get_object()['/Contents'], 'Glyph note')
        for key in ('/V', '/R', '/Length', '/P'):
            self.assertEqual(result.trailer['/Encrypt'].get_object().get(key),
                             reader.trailer['/Encrypt'].get_object().get(key))
        self.assertEqual(encrypted.read_bytes(), before)
        print('ENCRYPTION', 'RC4-128 incremental rejected; explicit encrypted rewrite retained V/R/Length/P; AES not tested')

    def test_05_qtpdf_render_and_annotation_appearance_probe(self):
        count, baseline = render(self.source)
        _, annotated_baseline = render(self.source, show_annotations=True)
        self.assertEqual(count, 3)
        changes = {}
        for kind in ('Text', 'Highlight', 'Underline', 'StrikeOut', 'Ink'):
            writer = PdfWriter(clone_from=PdfReader(self.source))
            writer.add_annotation(0, annotation(kind))
            path = OUT / ('appearance-' + kind + '.pdf')
            writer.write(path)
            pages, digest = render(path)
            self.assertEqual(pages, 3)
            _, annotated_digest = render(path, show_annotations=True)
            changes[kind] = {'default': digest != baseline,
                             'annotation_flag': annotated_digest != annotated_baseline}
            self.assertEqual(digest, baseline, f'{kind}: default rendering changed')
            self.assertNotEqual(annotated_digest, annotated_baseline, f'{kind}: annotation not rendered')
            self.assertNotEqual(annotated_digest, digest, f'{kind}: annotation flag had no effect')
        print('QT_RENDER_IMAGE_CHANGED_BY_ANNOTATION', changes)
        for page in range(3):
            self.assertEqual(render(OUT / 'all-native-annotations.pdf', show_annotations=True, page=page)[0], 3)
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
