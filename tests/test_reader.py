import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QCoreApplication, QEvent, QEventLoop, QPoint, QPointF,
                            QSettings, QSize, QTimer, Qt)
from PySide6.QtGui import QImage, QKeyEvent, QPainter, QPdfWriter, QWheelEvent
from PySide6.QtWidgets import QApplication

from glyph_desktop.reader import (Document, PageCanvas, Reader,
                                  current_page_for_viewport)
from glyph_desktop.rendering import BoundedImageCache, make_render_job
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


def make_pdf(path, pages):
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    for page in range(pages):
        if page:
            writer.newPage()
        painter.drawText(100, 200, f"Page {page + 1}")
    painter.end()


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.one = Path(self.temp.name) / "one.pdf"
        self.many = Path(self.temp.name) / "many.pdf"
        make_pdf(self.one, 1)
        make_pdf(self.many, 4)
        self.doc = Document()
        self.reader = Reader(self.doc)
        self.reader.resize(760, 540)
        self.addCleanup(self.cleanup_document)

    def cleanup_document(self):
        self.doc.close()
        self.reader.close()
        self.reader.deleteLater()
        self.doc.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def cleanup_window(self, window):
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wheel(self, delta, *, pixel=False, ctrl=False, target=None,
              phase=Qt.ScrollPhase.ScrollUpdate):
        event = QWheelEvent(
            QPointF(20, 20), QPointF(20, 20),
            QPoint(0, delta if pixel else 0), QPoint(0, 0 if pixel else delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
            phase, False)
        QApplication.sendEvent(target or self.reader.page_view.viewport(), event)

    def open_reader(self, path=None):
        self.reader.show()
        self.assertIsNone(self.doc.open(path or self.many))
        self.app.processEvents()

    def test_continuous_layout_orders_centers_and_preserves_page_ratio(self):
        self.open_reader()
        view = self.reader.page_view
        self.assertEqual(len(view.page_rects), 4)
        for index, rect in enumerate(view.page_rects):
            source_width, source_height = self.doc.page_sizes[index]
            self.assertAlmostEqual(rect.width() / rect.height(), source_width / source_height, places=3)
            self.assertAlmostEqual(rect.center().x(), view.virtual_width / 2, places=2)
            if index:
                self.assertAlmostEqual(rect.top() - view.page_rects[index - 1].bottom(),
                                       view.pageGap, places=1)
        self.assertGreater(view.total_height, view.viewport().height())

    def test_current_page_calculation_uses_visible_area_and_stable_ties(self):
        tops = [0, 120, 240]
        heights = [100, 100, 100]
        self.assertEqual(current_page_for_viewport(tops, heights, 0, 100), 0)
        self.assertEqual(current_page_for_viewport(tops, heights, 60, 100), 0)
        self.assertEqual(current_page_for_viewport(tops, heights, 70, 100), 1)
        self.assertEqual(current_page_for_viewport(tops, heights, 240, 80), 2)

    def test_previous_next_direct_entry_home_and_end(self):
        self.open_reader()
        self.reader.next.click()
        self.assertEqual(self.doc.page, 1)
        self.reader.previous.click()
        self.assertEqual(self.doc.page, 0)
        self.reader.page.setText("4")
        self.reader.enter_page()
        self.assertEqual(self.doc.page, 3)
        self.reader.navigate_to_page(0)
        self.assertEqual(self.doc.page, 0)
        self.reader.navigate_to_page(99)
        self.assertEqual(self.doc.page, 0)

    def test_main_window_home_and_end_keys_navigate_continuous_document(self):
        settings = Settings(QSettings(str(Path(self.temp.name) / "settings.ini"),
                                      QSettings.Format.IniFormat))
        window = MainWindow(ThemeManager(self.app, settings), "0.1.0")
        window.reader.resize(760, 540)
        window.show()
        window.document.open(self.many)
        self.app.processEvents()
        for key, expected in ((Qt.Key.Key_PageDown, 1), (Qt.Key.Key_End, 3),
                              (Qt.Key.Key_PageUp, 2), (Qt.Key.Key_Home, 0)):
            event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
            with patch.object(window, "isActiveWindow", return_value=True), \
                    patch("glyph_desktop.window.QApplication") as application:
                application.activePopupWidget.return_value = None
                application.focusWidget.return_value = None
                self.assertTrue(window.eventFilter(window.reader, event))
            self.assertEqual(window.document.page, expected)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_current_page_tracks_scroll_and_thumbnail_selection(self):
        self.open_reader()
        self.reader.document.set_zoom("Manual", 1.0)
        self.reader.page_view.verticalScrollBar().setValue(
            self.reader.page_view.page_tops[2] + 10)
        self.reader.page_view._emit_current_page()
        self.assertEqual(self.doc.page, 2)
        self.assertEqual(self.reader.thumbnail_list.currentIndex().row(), 2)
        self.reader.thumbnail_list.clicked.emit(self.reader.thumbnail_model.index(0, 0))
        self.assertEqual(self.doc.page, 0)
        self.assertEqual(self.reader.thumbnail_list.currentIndex().row(), 0)

    def test_sustained_scroll_keeps_page_controls_and_navigation_current(self):
        large = Path(self.temp.name) / "sustained-scroll.pdf"
        make_pdf(large, 40)
        self.open_reader(large)
        self.doc.set_zoom("Manual", 1.0)
        self.reader._render_timer.stop()
        self.reader.controller.set_jobs([])

        view = self.reader.page_view
        view.current_timer.stop()
        scroll_state = {"page": 1, "ticks": 0}
        scroll_timer = QTimer()
        scroll_timer.setInterval(20)
        loop = QEventLoop()
        observed = {}

        def scroll_to_next_page():
            page = scroll_state["page"]
            if page >= self.doc.count:
                scroll_timer.stop()
                return
            scroll_state["ticks"] += 1
            view.verticalScrollBar().setValue(view.page_tops[page] + 10)
            scroll_state["page"] += 1

        def on_current_page(page):
            if (observed or not scroll_timer.isActive() or scroll_state["ticks"] < 4
                    or page < 2):
                return
            observed.update(
                page=page,
                ticks=scroll_state["ticks"],
                page_field=self.reader.page.text(),
                thumbnail=self.reader.thumbnail_list.currentIndex().row(),
                scrolling=scroll_timer.isActive(),
            )
            self.reader.next.click()
            observed["after_next"] = self.doc.page
            self.reader.previous.click()
            observed["after_previous"] = self.doc.page
            scroll_timer.stop()
            loop.quit()

        scroll_timer.timeout.connect(scroll_to_next_page)
        view.currentPageChanged.connect(on_current_page)
        scroll_timer.start()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(3000)
        loop.exec()
        scroll_timer.stop()
        timeout.stop()
        view.currentPageChanged.disconnect(on_current_page)

        self.assertTrue(observed, "current page did not update during sustained scrolling")
        self.assertTrue(observed["scrolling"])
        self.assertGreaterEqual(observed["ticks"], 4)
        self.assertEqual(observed["page_field"], str(observed["page"] + 1))
        self.assertEqual(observed["thumbnail"], observed["page"])
        self.assertEqual(observed["after_next"], observed["page"] + 1)
        self.assertEqual(observed["after_previous"], observed["page"])

    def test_resize_requests_newly_visible_pages_without_rendering_document(self):
        large = Path(self.temp.name) / "resize-neighborhood.pdf"
        make_pdf(large, 30)
        settings = Settings(QSettings(str(Path(self.temp.name) / "settings.ini"),
                                      QSettings.Format.IniFormat))
        window = MainWindow(ThemeManager(self.app, settings), "0.1.0")
        self.addCleanup(self.cleanup_window, window)
        window.resize(920, 520)
        window.show()
        window.document.open(large)
        self.app.processEvents()
        window.document.set_zoom("Manual", 0.5)
        self.app.processEvents()

        reader = window.reader
        reader._render_timer.stop()
        view = reader.page_view
        view.verticalScrollBar().setValue(0)
        reader._request_visible_pages()
        initial_jobs = set(reader._full_jobs)
        initial_visible = set(view.visible_pages(prefetch=0))
        initial_scale = view.zoom_scale

        with patch.object(reader, "schedule_render", wraps=reader.schedule_render) as schedule:
            window.resize(920, 1100)
            self.app.processEvents()
            self.assertTrue(schedule.called)

        self.assertEqual(view.zoom_scale, initial_scale)
        newly_visible = set(view.visible_pages(prefetch=0)) - initial_visible
        newly_requested = newly_visible - initial_jobs
        self.assertTrue(newly_requested, "resize did not expose a new page")
        target = min(newly_requested)

        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if target in reader._full_jobs else None)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        poll.start()
        timeout.start(2000)
        loop.exec()
        poll.stop()
        timeout.stop()

        requested = set(reader._full_jobs)
        self.assertIn(target, requested)
        self.assertTrue(requested.issubset(set(view.visible_pages(prefetch=1))))
        self.assertLess(len(requested), window.document.count)

    def test_normal_wheel_scrolls_continuous_view_without_page_snap(self):
        self.open_reader()
        self.doc.set_zoom("Manual", 1.0)
        view = self.reader.page_view
        bar = view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.wheel(-120)
        self.assertGreater(bar.value(), bar.minimum())
        self.assertLess(bar.value(), bar.maximum())
        self.assertEqual(self.doc.page, 0)
        self.assertEqual(len(view.page_rects), self.doc.count)
        bar.setValue(0)
        self.wheel(-12, pixel=True)
        self.assertGreater(bar.value(), 0)

    def test_thumbnail_sidebar_wheel_scroll_is_independent_from_document(self):
        self.open_reader()
        main_bar = self.reader.page_view.verticalScrollBar()
        thumbnail_bar = self.reader.thumbnail_list.verticalScrollBar()
        self.assertGreater(thumbnail_bar.maximum(), 0)
        main_before = main_bar.value()
        self.wheel(-120, target=self.reader.thumbnail_list.viewport())
        self.assertGreater(thumbnail_bar.value(), 0)
        self.assertEqual(main_bar.value(), main_before)

    def test_ctrl_wheel_uses_zoom_bounds_and_leaves_fit_modes(self):
        self.open_reader()
        self.doc.set_zoom("Fit Page")
        before = self.reader.effective_zoom()
        self.wheel(120, ctrl=True)
        self.assertEqual(self.doc.mode, "Manual")
        self.assertGreater(self.doc.zoom, before)

    def test_fit_zoom_never_reverses_requested_direction(self):
        self.doc.open(self.one)
        self.doc.set_zoom("Fit Page")
        with patch.object(self.reader, "effective_zoom", return_value=29.5):
            self.reader.inside.click()
        self.assertEqual(self.doc.mode, "Fit Page")
        with patch.object(self.reader, "effective_zoom", return_value=29.5):
            self.wheel(120, ctrl=True)
        self.assertEqual(self.doc.mode, "Fit Page")

        self.doc.set_zoom("Fit Width")
        with patch.object(self.reader, "effective_zoom", return_value=0.1):
            self.reader.out.click()
        self.assertEqual(self.doc.mode, "Fit Width")
        with patch.object(self.reader, "effective_zoom", return_value=0.1):
            self.wheel(-120, ctrl=True)
        self.assertEqual(self.doc.mode, "Fit Width")
        self.doc.set_zoom("Manual", 5.0)
        self.wheel(120, ctrl=True)
        self.assertEqual(self.doc.zoom, 5.0)
        self.doc.set_zoom("Manual", 0.25)
        self.wheel(-120, ctrl=True)
        self.assertEqual(self.doc.zoom, 0.25)
        self.doc.set_zoom("Fit Width")
        before = self.reader.effective_zoom()
        for _ in range(4):
            self.wheel(10, pixel=True, ctrl=True)
        self.assertEqual(self.doc.mode, "Manual")
        self.assertGreater(self.doc.zoom, before)

    def test_fit_width_uses_widest_page_and_mixed_sizes_keep_ratio(self):
        self.open_reader()
        self.reader.page_view.set_pages([(300, 600), (600, 300)])
        self.doc.set_zoom("Fit Width")
        self.reader.page_view.relayout(preserve_anchor=False)
        first, second = self.reader.page_view.page_rects
        self.assertAlmostEqual(second.width(),
                               self.reader.page_view.viewport().width() - 48, delta=2)
        self.assertAlmostEqual(first.width() / first.height(), 0.5, places=3)
        self.assertAlmostEqual(second.width() / second.height(), 2.0, places=3)

    def test_zoom_preserves_relative_reading_location(self):
        self.open_reader()
        self.doc.set_zoom("Manual", 1.0)
        self.reader.navigate_to_page(2)
        self.app.processEvents()
        view = self.reader.page_view
        page = self.doc.page
        before = view.page_rects[page]
        relative = (view.verticalScrollBar().value() + view.viewport().height() / 2
                    - before.top()) / before.height()
        self.doc.set_zoom("Manual", 2.0)
        after = view.page_rects[page]
        new_relative = (view.verticalScrollBar().value() + view.viewport().height() / 2
                        - after.top()) / after.height()
        self.assertAlmostEqual(new_relative, relative, places=2)

    def test_sidebar_toggle_and_thumbnail_state_reset_on_replace_and_close(self):
        self.open_reader()
        self.assertEqual(self.reader.thumbnail_model.rowCount(), 4)
        self.reader.set_thumbnail_visible(False)
        self.assertTrue(self.reader.sidebar.isHidden())
        self.reader.set_thumbnail_visible(True)
        self.assertFalse(self.reader.sidebar.isHidden())
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        self.reader.full_cache.put("page", image)
        self.reader.thumbnail_cache.put("thumb", image)
        self.assertIsNone(self.doc.open(self.one))
        self.assertEqual(self.reader.thumbnail_model.rowCount(), 1)
        self.assertEqual(len(self.reader.page_view.page_rects), 1)
        self.assertEqual(len(self.reader.full_cache), 0)
        self.assertEqual(len(self.reader.thumbnail_cache), 0)
        self.doc.close()
        self.assertEqual(self.reader.thumbnail_model.rowCount(), 0)
        self.assertEqual(self.reader.page_view.page_rects, [])
        self.assertIsNone(self.doc.pdf)

    def test_lazy_render_requests_only_viewport_neighborhood_and_visible_thumbnails(self):
        large = Path(self.temp.name) / "many-pages.pdf"
        make_pdf(large, 30)
        self.open_reader(large)
        self.reader._request_visible_pages()
        self.assertGreater(len(self.reader.controller._desired), 0)
        self.assertLess(len(self.reader.controller._desired), self.doc.count)
        self.assertLessEqual(len(self.reader.controller._desired), 12)
        self.reader.page_view.verticalScrollBar().setValue(
            self.reader.page_view.page_tops[20])
        self.reader._request_visible_pages()
        self.assertGreaterEqual(min(self.reader._full_jobs), 19)
        self.assertLessEqual(max(self.reader._full_jobs), 22)
        self.assertLessEqual(len(self.reader.controller._desired), 12)

    def test_bounded_cache_evicts_lru_items_by_count_and_bytes(self):
        cache = BoundedImageCache(max_items=2, max_bytes=128)
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        self.assertTrue(cache.put("one", image))
        self.assertTrue(cache.put("two", image))
        self.assertIsNotNone(cache.get("one"))
        self.assertTrue(cache.put("three", image))
        self.assertIsNone(cache.get("two"))
        self.assertEqual(len(cache), 2)
        self.assertLessEqual(cache.bytes_used, cache.max_bytes)

    def test_document_rejects_invalid_replacement_and_navigation_boundaries(self):
        self.open_reader()
        invalid = Path(self.temp.name) / "invalid.pdf"
        invalid.write_bytes(b"not a PDF")
        self.assertIsNotNone(self.doc.open(invalid))
        self.assertEqual(self.doc.path, self.many)
        self.reader.navigate_to_page(-1)
        self.assertEqual(self.doc.page, 0)
        self.reader.navigate_to_page(4)
        self.assertEqual(self.doc.page, 0)
        self.reader.navigate_to_page(3)
        self.assertEqual(self.doc.page, 3)

    def test_zoom_bounds_remain_enforced(self):
        self.doc.open(self.one)
        self.doc.set_zoom("Manual", 0.01)
        self.assertEqual(self.doc.zoom, 0.25)
        self.doc.set_zoom("Manual", 9)
        self.assertEqual(self.doc.zoom, 5.0)
        self.doc.set_zoom("Fit Width")
        self.reader.step_zoom(1)
        self.assertEqual(self.doc.mode, "Manual")
        self.assertGreaterEqual(self.doc.zoom, 0.25)
        self.assertLessEqual(self.doc.zoom, 5.0)

    def test_async_rendering_and_superseded_result_rejection(self):
        self.open_reader()
        loop = QEventLoop()
        results = []
        self.reader.controller.rendered.connect(
            lambda job, image, generation: (results.append((job, image, generation)), loop.quit()))
        old_job = make_render_job(0, QSize(180, 240), 1.0, "page", self.reader._zoom_revision)
        latest_job = make_render_job(2, QSize(180, 240), 1.0, "page", self.reader._zoom_revision)
        self.reader.controller.set_jobs([old_job])
        self.reader.controller.set_jobs([latest_job])
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(results)
        self.assertEqual([item[0] for item in results], [latest_job])
        self.assertFalse(results[0][1].isNull())
        self.assertEqual(results[0][2], self.doc.generation)
        self.doc.close()

    def test_render_cache_rejects_stale_zoom_revision(self):
        self.open_reader()
        job = make_render_job(0, QSize(120, 150), 1.0, "page", self.reader._zoom_revision - 1)
        image = QImage(120, 150, QImage.Format.Format_ARGB32)
        before = len(self.reader.full_cache)
        self.reader._on_rendered(job, image, self.doc.generation)
        self.assertEqual(len(self.reader.full_cache), before)

    def test_transparent_page_is_composited_on_white_paper(self):
        image = QImage(10, 10, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        image.setPixelColor(0, 0, Qt.GlobalColor.red)
        canvas = PageCanvas()
        canvas.show_image(image, QSize(10, 10))
        result = canvas.pixmap().toImage()
        self.assertEqual(result.pixelColor(5, 5), Qt.GlobalColor.white)
        self.assertEqual(result.pixelColor(0, 0), Qt.GlobalColor.red)

    def test_navigation_key_does_not_steal_popup_input(self):
        settings = Settings(QSettings(str(Path(self.temp.name) / "settings.ini"),
                                      QSettings.Format.IniFormat))
        window = MainWindow(ThemeManager(self.app, settings), "0.1.0")
        window.document.open(self.many)
        window.document.set_page(2)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home,
                          Qt.KeyboardModifier.NoModifier)
        with patch.object(window, "isActiveWindow", return_value=True), \
                patch("glyph_desktop.window.QApplication") as application:
            application.activePopupWidget.return_value = object()
            application.focusWidget.return_value = None
            self.assertFalse(window.eventFilter(window.reader.zoom, event))
        self.assertEqual(window.document.page, 2)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
