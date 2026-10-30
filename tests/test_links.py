import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QCoreApplication, QEvent, QEventLoop, QModelIndex, QPointF,
                            QTimer, Qt)
from PySide6.QtGui import QMouseEvent
from PySide6.QtPdf import QPdfLinkModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glyph_desktop.reader import Document, Reader


def write_link_pdf(path):
    content = b"BT /F1 14 Tf 72 700 Td (Link text for selecting) Tj ET"
    stream = f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 4 0 R /Annots [10 0 R 11 0 R 12 0 R 13 0 R 14 0 R 15 0 R 17 0 R] >>",
        stream,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] /Resources << /Font << /F1 9 0 R >> >> /Contents 6 0 R /Annots [16 0 R] >>",
        stream,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 8 0 R >>",
        stream,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Annot /Subtype /Link /Rect [72 680 250 710] /Dest [5 0 R /XYZ 60 500 null] >>",
        b"<< /Type /Annot /Subtype /Link /Rect [72 630 250 660] /Dest [7 0 R /XYZ 100 500 2] >>",
        b"<< /Type /Annot /Subtype /Link /Rect [72 580 250 610] /A << /S /URI /URI (https://example.com) >> >>",
        b"<< /Type /Annot /Subtype /Link /Rect [72 530 250 560] /A << /S /URI /URI (http://example.com) >> >>",
        b"<< /Type /Annot /Subtype /Link /Rect [72 480 250 510] /A << /S /URI /URI (file:///tmp/test) >> >>",
        b"<< /Type /Annot /Subtype /Link /Rect [72 430 250 460] /A << /S /URI /URI (javascript:alert) >> >>",
        b"<< /Type /Annot /Subtype /Link /Rect [40 400 200 430] /Dest [3 0 R /XYZ 50 700 null] >>",
        b"<< /Type /Annot /Subtype /Link /Rect [300 680 450 710] /Dest [5 0 R /XYZ 60 500 1] >>",
    ]
    data = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)


class LinkTests(unittest.TestCase):
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

    def tearDown(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def hit(self, x, y, page=0):
        return self.reader.links.hit(page, QPointF(x, y))

    def test_internal_and_external_targets(self):
        first = self.hit(100, 100)
        self.assertIsNotNone(first)
        self.assertEqual(first.page, 1)
        self.assertEqual(first.location, (60.0, 100.0))
        self.assertIsNone(self.hit(480, 100))
        self.assertTrue(self.reader.links.activate(first))
        self.assertEqual(self.document.page, 1)
        self.assertAlmostEqual(self.reader.page_view.pdf_to_viewport(1, QPointF(60, 100)).y(),
                               64, delta=2)
        second = self.hit(100, 150)
        self.assertTrue(self.reader.links.activate(second))
        self.assertEqual(self.document.page, 2)
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 2.0))
        back = self.hit(60, 185, page=1)
        self.assertTrue(self.reader.links.activate(back))
        self.assertEqual(self.document.page, 0)
        self.document.set_zoom("Fit Width")
        self.assertTrue(self.reader.links.activate(replace(first, zoom=0.0)))
        self.assertEqual(self.document.mode, "Fit Width")
        self.assertFalse(self.reader.links.activate(replace(first, page=99)))
        with patch("glyph_desktop.links.QDesktopServices.openUrl", return_value=True) as open_url:
            for y in (200, 250):
                self.assertTrue(self.reader.links.activate(self.hit(100, y)))
            self.assertEqual(open_url.call_count, 2)
            for y in (300, 350):
                self.assertFalse(self.reader.links.activate(self.hit(100, y)))
            external = self.hit(100, 200)
            for url in (b"", b"https://", b"file:///tmp/test", b"ftp://example.com",
                        b"javascript:alert", b"data:text/plain,test",
                        b"mailto:a@example.com", b"custom://example.com", b"https://%"):
                self.assertFalse(self.reader.links.activate(replace(external, url=url)))
            self.assertTrue(self.reader.links.activate(
                replace(external, url=b"HTTPS://EXAMPLE.COM/path")))
            self.assertEqual(open_url.call_count, 3)
        with patch("glyph_desktop.links.QDesktopServices.openUrl", return_value=False):
            self.assertFalse(self.reader.links.activate(external))

    def test_click_drag_cursor_and_stale_target(self):
        view = self.reader.page_view
        point = view.pdf_to_viewport(0, QPointF(100, 100)).toPoint()
        QTest.mouseMove(view.viewport(), point)
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
        target = self.hit(100, 100)
        with patch.object(self.reader.links, "activate", wraps=self.reader.links.activate) as activate:
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.assertEqual(activate.call_count, 1)
        self.reader.navigate_to_page(0)
        point = view.pdf_to_viewport(0, QPointF(80, 89)).toPoint()
        end = view.pdf_to_viewport(0, QPointF(150, 89)).toPoint()
        with patch.object(self.reader.links, "activate", wraps=self.reader.links.activate) as activate:
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(view.viewport(), move)
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
            over_other_link = view.pdf_to_viewport(0, QPointF(100, 150)).toPoint()
            move = QMouseEvent(QEvent.Type.MouseMove, QPointF(over_other_link),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(view.viewport(), move)
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.assertEqual(activate.call_count, 0)
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
            self.assertTrue(self.reader.selection.valid())
            self.reader.copy_selection()
            self.assertEqual(self.app.clipboard().text(), self.reader.selection.result.text())
        external = self.hit(100, 200)
        self.assertIsNone(self.document.open(self.path))
        self.assertFalse(self.reader.links.activate(target))
        with patch("glyph_desktop.links.QDesktopServices.openUrl") as open_url:
            self.assertFalse(self.reader.links.activate(external))
            open_url.assert_not_called()
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_mapping_and_press_release_rules(self):
        view = self.reader.page_view
        for mode, value in (("Manual", 1.5), ("Fit Width", None), ("Fit Page", None)):
            self.document.set_zoom(mode, value)
            self.reader.resize(810, 620)
            self.reader.set_sidebar_visible(False)
            self.app.processEvents()
            self.reader.navigate_to_page(0)
            point = view.pdf_to_viewport(0, QPointF(100, 100))
            self.assertEqual(view._link_at(point).page, 1)
            self.assertIsNone(view._link_at(view.pdf_to_viewport(0, QPointF(480, 100))))
            self.reader.set_sidebar_visible(True)
        inside = view.pdf_to_viewport(0, QPointF(100, 100)).toPoint()
        outside = view.pdf_to_viewport(0, QPointF(480, 100)).toPoint()
        with patch.object(self.reader.links, "activate") as activate:
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=inside)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=outside)
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=outside)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=inside)
            activate.assert_not_called()
        QTest.mouseMove(view.viewport(), outside)
        self.assertNotEqual(view.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
        target = self.hit(100, 100)
        self.document.close()
        self.assertFalse(self.reader.links.activate(target))
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_stale_external_press_and_search_coexistence(self):
        view = self.reader.page_view
        self.reader.search.set_query("Link")
        if self.reader.search.searching:
            loop = QEventLoop()
            self.reader.search.changed.connect(lambda: loop.quit() if not self.reader.search.searching else None)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
        self.assertFalse(self.reader.search.searching)
        results = self.reader.search.results
        self.assertGreater(len(results), 0)
        self.reader.set_sidebar_mode("outline")
        self.assertTrue(self.reader.links.activate(self.hit(100, 100)))
        self.assertEqual(self.reader.search.results, results)
        self.reader.navigate_to_page(0)
        point = view.pdf_to_viewport(0, QPointF(100, 200)).toPoint()
        QTest.mouseMove(view.viewport(), point)
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        active_model = self.reader.links.model
        self.assertIsNotNone(self.document.open(Path(self.temp.name) / "missing.pdf"))
        self.assertIs(self.reader.links.model, active_model)
        self.assertIs(self.reader.links.model.document(), self.document.pdf)
        self.assertIsNotNone(view._link_press)
        self.assertIsNone(self.document.open(self.path))
        with patch("glyph_desktop.links.QDesktopServices.openUrl") as open_url:
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            open_url.assert_not_called()
        self.reader.search.set_query("Link")
        self.assertIsNotNone(self.reader.outline_panel.model)

    def test_qt_exposes_same_zoom_for_null_and_explicit_one(self):
        # QtPdf loses the distinction between an unspecified XYZ zoom and 1.0.
        self.assertEqual(self.hit(100, 100).zoom, 1.0)
        self.assertEqual(self.hit(350, 100).zoom, 1.0)

    def test_ambiguous_zoom_preserves_mode_and_reaches_destination(self):
        for mode, zoom in (("Fit Width", None), ("Fit Page", None), ("Manual", 1.5)):
            with self.subTest(mode=mode):
                self.reader.navigate_to_page(0)
                self.document.set_zoom(mode, zoom)
                previous = (self.document.mode, self.document.zoom)
                target = self.hit(100, 100)
                self.assertEqual(target.zoom, 1.0)
                self.assertTrue(self.reader.links.activate(target))
                self.assertEqual((self.document.mode, self.document.zoom), previous)
                self.assertEqual(self.document.page, 1)
                self.assertAlmostEqual(
                    self.reader.page_view.pdf_to_viewport(1, QPointF(60, 100)).y(),
                    64, delta=2)

        # The indistinguishable explicit-100% destination follows the same policy.
        self.document.set_zoom("Manual", 1.5)
        self.assertTrue(self.reader.links.activate(self.hit(350, 100)))
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 1.5))
        self.assertTrue(self.reader.links.activate(self.hit(60, 185, page=1)))
        self.assertEqual(self.document.page, 0)
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 1.5))

        target = self.hit(100, 150)
        self.assertTrue(self.reader.links.activate(target))
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 2.0))
        self.assertEqual(self.document.page, 2)
        self.assertTrue(self.reader.links.activate(replace(target, zoom=100.0)))
        self.assertEqual(self.document.zoom, 5.0)

    def test_link_model_detaches_and_old_document_is_released(self):
        destroyed = []
        destroyed_models = []
        target = self.hit(100, 100)
        for sequence in range(4):
            previous_pdf = self.document.pdf
            previous_model = self.reader.links.model
            previous_pdf.destroyed.connect(lambda *_, marker=sequence: destroyed.append(marker))
            previous_model.destroyed.connect(
                lambda *_, marker=sequence: destroyed_models.append(marker))
            self.assertIs(previous_model.document(), previous_pdf)
            self.assertIsNone(self.document.open(self.path))
            self.assertIsNot(self.reader.links.model, previous_model)
            self.assertIs(self.reader.links.model.document(), self.document.pdf)
            self.assertFalse(self.reader.links.activate(target))
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertEqual(destroyed, list(range(sequence + 1)))
            self.assertEqual(destroyed_models, list(range(sequence + 1)))
            self.assertEqual(len(self.reader.findChildren(QPdfLinkModel)), 1)
        active_model = self.reader.links.model
        active_pdf = self.document.pdf
        active_model.destroyed.connect(lambda *_: destroyed_models.append("close"))
        active_pdf.destroyed.connect(lambda *_: destroyed.append("close"))
        self.document.close()
        self.assertIsNone(self.reader.links.model)
        self.assertFalse(self.reader.links.activate(target))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(destroyed[-1], "close")
        self.assertEqual(destroyed_models[-1], "close")
        self.assertEqual(len(self.reader.findChildren(QPdfLinkModel)), 0)
        self.assertIsNone(self.document.open(self.path))
        self.assertIsNotNone(self.reader.links.model)
        self.assertGreater(self.reader.links.model.rowCount(QModelIndex()), 0)
        self.assertTrue(self.reader.links.hit(0, QPointF(100, 100)))
