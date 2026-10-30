import os
import gc
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPointF, QTimer, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glyph_desktop.reader import Document, Reader
from test_links import write_link_pdf


class AnnotationTests(unittest.TestCase):
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

    def tearDown(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def make(self, kind, page=0):
        self.reader.selection.select_all(page)
        self.assertTrue(self.reader.selection.valid())
        return self.markup.create(kind, self.reader.selection)

    def test_creation_and_pdf_coordinates(self):
        self.assertIsNone(self.markup.create("highlight", self.reader.selection))
        items = [self.make(kind) for kind in ("highlight", "underline", "strikeout")]
        self.assertEqual([item.kind for item in items], ["highlight", "underline", "strikeout"])
        self.assertEqual(len({item.id for item in items}), 3)
        polygon = self.document.pdf.getAllText(0).bounds()[0]
        self.assertEqual(items[0].regions[0], tuple((p.x(), p.y()) for p in polygon))
        self.assertFalse(self.reader.selection.valid())
        self.assertTrue(all(isinstance(point[0], float) for item in items
                            for region in item.regions for point in region))

    def test_geometry_survives_zoom_resize_and_view_relayout(self):
        item = self.make("highlight")
        second = self.make("underline", 1)
        original = item.regions
        point = QPointF(*original[0][0])
        for mode, zoom in (("Manual", 2.0), ("Fit Width", None), ("Fit Page", None)):
            self.document.set_zoom(mode, zoom)
            self.reader.resize(760, 600)
            self.app.processEvents()
            mapped = self.view.pdf_to_viewport(0, point)
            hit = self.view.viewport_to_pdf(mapped, 0, clamp=True)
            self.assertAlmostEqual(hit[1].x(), point.x(), places=5)
            self.assertAlmostEqual(hit[1].y(), point.y(), places=5)
            self.assertEqual(item.regions, original)
        self.view.set_pages(self.document.page_sizes)
        self.assertEqual(self.markup.items, (item, second))

    def test_lifecycle_and_stale_generation(self):
        item = self.make("highlight")
        generation = self.document.generation
        self.assertIsNotNone(self.document.open(self.path.parent / "missing.pdf"))
        self.assertEqual(self.markup.items, (item,))
        self.assertIsNone(self.document.open(self.path))
        self.assertEqual(self.markup.items, ())
        new = self.make("underline")
        self.markup.select(item.id, generation)
        self.assertIsNone(self.markup.selected_id)
        self.assertEqual(self.markup.items, (new,))
        self.document.close()
        self.assertEqual(self.markup.items, ())

    def test_replacement_releases_annotation_records_and_documents(self):
        records = []
        documents = []
        for _ in range(3):
            item = self.make("highlight")
            records.append(weakref.ref(item))
            documents.append(weakref.ref(self.document.pdf))
            del item
            self.assertIsNone(self.document.open(self.path))
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        gc.collect()
        self.assertTrue(all(ref() is None for ref in records))
        self.assertTrue(all(ref() is None for ref in documents))
        self.assertEqual(self.markup.items, ())

    def test_click_select_delete_and_empty_clear(self):
        first = self.make("highlight", 2)
        second = self.make("underline", 2)
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        points = second.regions[0]
        center = QPointF(sum(p[0] for p in points) / len(points),
                         sum(p[1] for p in points) / len(points))
        pos = self.view.pdf_to_viewport(2, center).toPoint()
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        self.assertEqual(self.markup.selected_id, second.id)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Delete)
        self.assertEqual(self.markup.items, (first,))
        self.markup.select(first.id, self.document.generation)
        empty = self.view.pdf_to_viewport(2, QPointF(300, 300)).toPoint()
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=empty)
        self.assertIsNone(self.markup.selected_id)

    def test_escape_clears_annotation_selection_without_deleting_markup(self):
        first = self.make("highlight", 2)
        second = self.make("underline", 2)
        before = self.markup.items
        self.markup.select(first.id, self.document.generation)
        self.assertEqual(self.markup.selected_id, first.id)

        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)

        self.assertIsNone(self.markup.selected_id)
        self.assertEqual(self.markup.items, before)
        self.assertEqual(self.markup.items, (first, second))

    def test_search_clear_keeps_markup_and_markup_delete_keeps_search(self):
        item = self.make("highlight")
        self.reader.search.set_query("Link")
        loop = QEventLoop()
        def done():
            if not self.reader.search.searching:
                loop.quit()
        self.reader.search.changed.connect(done)
        QTimer.singleShot(8000, loop.quit)
        loop.exec()
        self.reader.search.changed.disconnect(done)
        self.assertFalse(self.reader.search.searching)
        results = tuple(self.reader.search.results_for_page(0))
        self.assertTrue(results)
        self.markup.select(item.id, self.document.generation)
        self.markup.delete_selected()
        self.assertEqual(tuple(self.reader.search.results_for_page(0)), results)
        item = self.make("strikeout")
        self.reader.search.clear()
        self.assertEqual(self.markup.items, (item,))

    def test_link_overlapping_markup_has_precedence(self):
        item = self.make("highlight")
        point = self.view.pdf_to_viewport(0, QPointF(80, 90)).toPoint()
        with patch.object(self.reader.links, "activate", return_value=True) as activate:
            QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            activate.assert_called_once()
        self.assertIsNone(self.markup.selected_id)
        with patch.object(self.reader.links, "activate") as activate:
            QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.AltModifier, pos=point)
            activate.assert_not_called()
        self.assertEqual(self.markup.selected_id, item.id)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Delete)
        self.assertEqual(self.markup.items, ())

    def test_drag_linked_markup_still_selects_and_restores_cursor(self):
        self.make("highlight")
        start = self.view.pdf_to_viewport(0, QPointF(80, 89)).toPoint()
        end = self.view.pdf_to_viewport(0, QPointF(150, 89)).toPoint()
        with patch.object(self.reader.links, "activate") as activate:
            QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), Qt.MouseButton.NoButton,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(self.view.viewport(), move)
            self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
            QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            activate.assert_not_called()
        self.assertTrue(self.reader.selection.valid())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.reader.copy_action.trigger()
        self.assertEqual(self.app.clipboard().text(), self.reader.selection.result.text())
        self.assertEqual(len(self.markup.items), 1)

    def test_actions_and_stale_press_after_replacement(self):
        action = self.reader.markup_actions["highlight"]
        self.assertFalse(action.isEnabled())
        self.reader.selection.select_all(2)
        self.assertTrue(action.isEnabled())
        action.trigger()
        self.assertFalse(action.isEnabled())
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        region = self.markup.items[0].regions[0]
        point = QPointF(sum(p[0] for p in region) / len(region),
                        sum(p[1] for p in region) / len(region))
        pos = self.view.pdf_to_viewport(2, point).toPoint()
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        self.assertIsNone(self.document.open(self.path))
        new = self.make("underline")
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        self.assertIsNone(self.markup.selected_id)
        self.assertEqual(self.markup.items, (new,))
