import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glyph_desktop.document_state import DocumentState
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


def write_pdf(path, pages):
    writer = QPdfWriter(str(path))
    writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    painter = QPainter(writer)
    painter.setFont(QFont("DejaVu Sans", 18))
    try:
        for page_index, (page_size, lines) in enumerate(pages):
            if page_index:
                if page_size is not None:
                    writer.setPageSize(QPageSize(page_size))
                writer.newPage()
            if lines is None:
                painter.fillRect(72, 72, 400, 400, Qt.GlobalColor.black)
                continue
            for line_index, line in enumerate(lines):
                painter.drawText(72, 100 + line_index * 40, line)
    finally:
        painter.end()


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.pdf = root / "search.pdf"
        write_pdf(self.pdf, [
            (None, ["cAt café Straße", "cat cat", "alpha first", "MIXED word", "hold this"]),
            (QPageSize.PageSizeId.A5, ["cat alpha second", "beta second"]),
            (QPageSize.PageSizeId.A5, None),
            (QPageSize.PageSizeId.A4, ["alpha final fox", "beta final"]),
        ])
        self.image_pdf = root / "image-only.pdf"
        write_pdf(self.image_pdf, [(None, None)])

        settings = Settings(QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat))
        self.theme = ThemeManager(self.app, settings)
        self.window = MainWindow(self.theme, "0.1", DocumentState(root / "state.json"))
        self.window.show()
        self.assertTrue(self.window.open_document(self.pdf))
        self.app.processEvents()
        self.reader = self.window.reader
        self.search = self.reader.search
        self.view = self.reader.page_view
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_for_search(self, timeout_ms=8000):
        if not self.search.searching:
            return
        loop = QEventLoop()
        timeout = QTimer(loop)
        timeout.setSingleShot(True)
        timeout.setInterval(timeout_ms)

        def finish_when_done():
            if not self.search.searching:
                loop.quit()

        self.search.changed.connect(finish_when_done)
        timeout.timeout.connect(loop.quit)
        timeout.start()
        loop.exec()
        timeout.stop()
        self.search.changed.disconnect(finish_when_done)
        self.assertFalse(self.search.searching, "document search did not finish")

    def search_for(self, query):
        self.search.set_query(query)
        self.wait_for_search()

    def test_document_order_repeated_matches_wrap_and_page_tracking(self):
        self.search_for("cat")
        results = self.search.results
        self.assertEqual([(result.page, result.order) for result in results],
                         [(0, 0), (0, 1), (0, 2), (1, 3)])
        self.assertEqual([result.text for result in results], ["cAt", "cat", "cat", "cat"])
        self.assertEqual(len(self.search.results_for_page(0)), 3)
        self.assertEqual(self.search.active_result, results[0])
        self.assertEqual(self.search.current_index, 0)

        self.search.next()
        self.assertEqual(self.search.current_index, 1)
        self.search.next()
        self.search.next()
        self.assertEqual(self.search.current_index, 3)
        self.assertEqual(self.window.document.page, 1)
        self.assertEqual(self.reader.page.text(), "2")
        self.assertEqual(self.reader.thumbnail_list.currentIndex().row(), 1)
        active_point = self.view.pdf_to_viewport(
            self.search.active_result.page, self.search.active_result.bounding_rect.center())
        self.assertGreaterEqual(active_point.y(), 0)
        self.assertLessEqual(active_point.y(), self.view.viewport().height())

        self.search.next()
        self.assertEqual(self.search.current_index, 0)
        self.search.previous()
        self.assertEqual(self.search.current_index, 3)
        self.assertEqual(self.window.document.page, 1)

    def test_case_insensitive_unicode_and_interior_space(self):
        self.search_for("CAFÉ")
        self.assertEqual(self.search.result_count, 1)
        self.assertEqual(self.search.active_result.text, "café")

        self.search_for("STRASSE")
        self.assertEqual(self.search.result_count, 1)
        self.assertEqual(self.search.active_result.text, "Straße")

        self.search_for(" cat cat ")
        self.assertEqual(self.search.result_count, 1)
        self.assertEqual(self.search.active_result.text, "cat cat")

    def test_empty_no_result_and_image_only_queries(self):
        self.search_for("not present in this document")
        self.assertEqual(self.search.result_count, 0)
        self.assertEqual(self.reader.find_bar.result_label.text(), "0 / 0")
        self.assertFalse(self.reader.find_bar.previous_button.isEnabled())
        self.assertFalse(self.reader.find_bar.next_button.isEnabled())

        self.search.set_query("   ")
        self.assertFalse(self.search.searching)
        self.assertEqual(self.search.results, ())
        self.assertEqual(self.reader.find_bar.result_label.text(), "0 / 0")

        self.assertTrue(self.window.open_document(self.image_pdf))
        self.search_for("scannedword")
        self.assertEqual(self.search.result_count, 0)
        self.assertEqual(self.search.results_for_page(0), ())

    def test_find_shortcut_enter_shift_enter_and_query_select_all(self):
        self.window.activateWindow()
        self.app.processEvents()
        QTest.keyClick(self.window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        find_bar = self.reader.find_bar
        self.assertTrue(find_bar.isVisible())
        self.assertIs(self.app.focusWidget(), find_bar.query_edit)

        find_bar.query_edit.setText("cat")
        self.wait_for_search()
        self.assertEqual(self.search.current_index, 0)
        QTest.keyClick(find_bar.query_edit, Qt.Key.Key_Return)
        self.assertEqual(self.search.current_index, 1)
        QTest.keyClick(find_bar.query_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.search.current_index, 0)

        self.view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(self.app.focusWidget(), find_bar.query_edit)
        self.assertEqual(find_bar.query_edit.selectedText(), "cat")
        QTest.keyClick(find_bar.query_edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(find_bar.query_edit.selectedText(), "cat")
        self.assertFalse(self.reader.selection.valid())

        QTest.keyClick(find_bar.query_edit, Qt.Key.Key_Escape)
        self.assertFalse(find_bar.isVisible())
        self.assertEqual(self.search.results, ())

    def test_search_and_manual_selection_remain_independent(self):
        all_text = self.window.document.pdf.getAllText(0)
        start = all_text.text().index("cAt")
        manual = self.window.document.pdf.getSelectionAtIndex(0, start, len("cAt"))
        self.reader.selection.set_result(0, manual)
        self.search_for("alpha")
        self.assertTrue(self.reader.selection.valid())
        self.assertEqual(self.search.active_result.text, "alpha")

        self.window.activateWindow()
        self.view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), "cAt")
        self.assertEqual(self.search.active_result.text, "alpha")
        self.assertTrue(self.reader.selection.valid())

        self.reader.show_find_bar()
        self.reader.find_bar.query_edit.setText("alpha")
        self.search_for("alpha")
        QTest.keyClick(self.reader.find_bar.query_edit, Qt.Key.Key_Escape)
        self.assertFalse(self.reader.find_bar.isVisible())
        self.assertTrue(self.reader.selection.valid())
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.reader.selection.valid())

    def test_geometry_reprojects_for_zoom_resize_scroll_and_sidebar(self):
        self.search_for("cat")
        result = self.search.active_result
        original_bounds = tuple(tuple(polygon) for polygon in result.bounds)
        self.assertTrue(original_bounds)
        self.assertEqual(len(self.search.results_for_page(0)), 3)

        for mode, zoom in (("Manual", 1.0), ("Manual", 2.0),
                           ("Fit Width", None), ("Fit Page", None)):
            self.window.document.set_zoom(mode, zoom)
            self.app.processEvents()
            view_point = self.view.pdf_to_viewport(result.page, result.bounding_rect.center())
            page, pdf_point = self.view.viewport_to_pdf(view_point, result.page, clamp=True)
            self.assertEqual(page, result.page)
            self.assertAlmostEqual(pdf_point.x(), result.bounding_rect.center().x(), places=4)
            self.assertAlmostEqual(pdf_point.y(), result.bounding_rect.center().y(), places=4)
            self.assertEqual(tuple(tuple(poly) for poly in result.bounds), original_bounds)
            self.assertEqual(self.search.active_result, result)

        self.window.resize(1080, 760)
        self.reader.set_thumbnail_visible(False)
        self.app.processEvents()
        point = self.view.pdf_to_viewport(result.page, result.bounding_rect.center())
        _, inverse = self.view.viewport_to_pdf(point, result.page, clamp=True)
        self.assertAlmostEqual(inverse.x(), result.bounding_rect.center().x(), places=4)
        self.assertAlmostEqual(inverse.y(), result.bounding_rect.center().y(), places=4)

        self.view.scroll_to_page(3)
        self.app.processEvents()
        self.assertEqual(self.search.active_result, result)
        self.assertEqual(self.search.results_for_page(0)[0], result)
        self.view.scroll_to_page(result.page)
        self.app.processEvents()

    def test_query_changes_clear_old_highlights_without_rendering(self):
        self.search_for("cat")
        self.assertGreater(self.search.result_count, 0)
        with patch.object(self.reader, "schedule_render") as schedule_render:
            self.search.set_query("no such match")
            self.assertEqual(self.search.results, ())
            self.assertIsNone(self.search.active_result)
            self.wait_for_search()
            schedule_render.assert_not_called()
        self.assertEqual(self.reader.find_bar.result_label.text(), "0 / 0")

    def test_large_search_and_stale_query_replacement(self):
        large_pdf = Path(self.temp.name) / "large.pdf"
        write_pdf(large_pdf, [(None, [f"alpha beta common page {page}"])
                              for page in range(80)])
        self.assertTrue(self.window.open_document(large_pdf))
        switched = []

        def replace_query_after_first_match():
            if self.search.query == "alpha" and self.search.results and not switched:
                switched.append(True)
                self.search.set_query("beta")

        self.search.changed.connect(replace_query_after_first_match)
        self.search.set_query("alpha")
        self.assertTrue(self.search.searching)
        self.wait_for_search()
        self.search.changed.disconnect(replace_query_after_first_match)
        self.assertEqual(switched, [True])
        self.assertEqual(self.search.result_count, 80)
        self.assertEqual([result.page for result in self.search.results], list(range(80)))
        self.assertTrue(all(result.text == "beta" for result in self.search.results))

    def test_document_replacement_and_close_cancel_search(self):
        large_pdf = Path(self.temp.name) / "large.pdf"
        write_pdf(large_pdf, [(None, [f"alpha page {page}"])
                              for page in range(80)])
        self.assertTrue(self.window.open_document(large_pdf))
        replaced = []

        def replace_document_during_search():
            if self.search.query == "alpha" and self.search.results and not replaced:
                replaced.append(True)
                self.assertTrue(self.window.open_document(self.image_pdf))

        self.search.changed.connect(replace_document_during_search)
        self.search.set_query("alpha")
        self.wait_for_search()
        self.search.changed.disconnect(replace_document_during_search)
        self.app.processEvents()
        self.assertEqual(replaced, [True])
        self.assertEqual(self.window.document.path, self.image_pdf)
        self.assertEqual(self.search.query, "")
        self.assertEqual(self.search.results, ())
        self.assertFalse(self.search.searching)
        self.assertEqual(self.reader.find_bar.query_edit.text(), "")

        self.assertTrue(self.window.open_document(large_pdf))
        self.search.set_query("alpha")
        self.assertTrue(self.search.searching)
        self.window.close_document()
        self.app.processEvents()
        self.assertEqual(self.search.query, "")
        self.assertEqual(self.search.results, ())
        self.assertFalse(self.search.searching)


if __name__ == "__main__":
    unittest.main()
