import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QCoreApplication, QEvent, QEventLoop, QSettings, QPointF,
                            QTimer, Qt)
from PySide6.QtGui import QPalette
from PySide6.QtPdf import QPdfBookmarkModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glyph_desktop.document_state import DocumentState
from glyph_desktop.outline import OutlineDestination
from glyph_desktop.reader import Document, Reader
from glyph_desktop.settings import Appearance, Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


def _page_object(content_number: int) -> bytes:
    return (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 9 0 R >> >> "
        + f"/Contents {content_number} 0 R >>".encode()
    )


def _content_object(content: bytes) -> bytes:
    return (f"<< /Length {len(content)} >>\nstream\n".encode()
            + content + b"\nendstream")


def write_pdf(path: Path, *, with_outline: bool = True,
              single_top_level: bool = False) -> None:
    page_text = (b"needle first page", b"needle second page", b"needle third page")
    content_streams = [
        b"BT /F1 14 Tf 72 700 Td (" + text + b") Tj ET" for text in page_text
    ]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R" + (b" /Outlines 10 0 R" if with_outline else b"") + b" >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>",
        _page_object(4),
        _content_object(content_streams[0]),
        _page_object(6),
        _content_object(content_streams[1]),
        _page_object(8),
        _content_object(content_streams[2]),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    if with_outline:
        if single_top_level:
            objects.extend([
                b"<< /Type /Outlines /First 11 0 R /Last 11 0 R /Count 3 >>",
                b"<< /Title (Chapter 1) /Parent 10 0 R /First 12 0 R /Last 14 0 R "
                b"/Count 3 /Dest [3 0 R /XYZ 40 700 null] >>",
                b"<< /Title (Section 1.1) /Parent 11 0 R /First 13 0 R /Last 13 0 R "
                b"/Next 14 0 R /Count 1 /Dest [5 0 R /XYZ 80 500 null] >>",
                b"<< /Title (Topic 1.1.1) /Parent 12 0 R /Dest [7 0 R /XYZ 110 400 2] >>",
                b"<< /Title (Section 1.2) /Parent 11 0 R /Prev 12 0 R "
                b"/Dest [5 0 R /XYZ 30 600 null] >>",
            ])
        else:
            objects.extend([
                b"<< /Type /Outlines /First 11 0 R /Last 16 0 R /Count 7 >>",
                b"<< /Title (Chapter 1) /Parent 10 0 R /First 12 0 R /Last 14 0 R "
                b"/Next 15 0 R /Count 3 /Dest [3 0 R /XYZ 40 700 null] >>",
                b"<< /Title (Section 1.1) /Parent 11 0 R /First 13 0 R /Last 13 0 R "
                b"/Next 14 0 R /Count 1 /Dest [5 0 R /XYZ 80 500 null] >>",
                b"<< /Title (Topic 1.1.1) /Parent 12 0 R /Dest [7 0 R /XYZ 110 400 2] >>",
                b"<< /Title (Section 1.2) /Parent 11 0 R /Prev 12 0 R "
                b"/Dest [5 0 R /XYZ 30 600 null] >>",
                b"<< /Title (Chapter 2) /Parent 10 0 R /Prev 11 0 R "
                b"/Next 16 0 R /Dest [7 0 R /Fit] >>",
                b"<< /Title (Appendices) /Parent 10 0 R /Prev 15 0 R /First 17 0 R "
                b"/Last 17 0 R /Count 1 >>",
                b"<< /Title (Appendix A) /Parent 16 0 R /Dest [3 0 R /XYZ 50 600 null] >>",
            ])

    data = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode())
        data.extend(body)
        data.extend(b"\nendobj\n")
    xref_offset = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    data.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode())
    path.write_bytes(data)


class OutlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.outline_pdf = root / "outline.pdf"
        self.single_outline_pdf = root / "single-outline.pdf"
        self.no_outline_pdf = root / "no-outline.pdf"
        self.corrupt_pdf = root / "corrupt.pdf"
        write_pdf(self.outline_pdf)
        write_pdf(self.single_outline_pdf, single_top_level=True)
        write_pdf(self.no_outline_pdf, with_outline=False)
        self.corrupt_pdf.write_bytes(b"not a PDF")

        self.document = Document()
        self.reader = Reader(self.document)
        self.reader.resize(760, 540)
        self.reader.show()
        self.addCleanup(self._cleanup_reader)

    def _cleanup_reader(self):
        self.document.close()
        self.reader.close()
        self.reader.deleteLater()
        self.document.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def open_outline(self):
        self.assertIsNone(self.document.open(self.outline_pdf))
        self.app.processEvents()
        self.assertEqual(self.reader.outline_panel._document, self.document.pdf)

    def destination(self, page, location=None, zoom=None, generation=None):
        if isinstance(location, QPointF):
            location = (location.x(), location.y())
        return OutlineDestination(
            self.document.generation if generation is None else generation,
            page, location, zoom)

    def wait_for_search(self):
        search = self.reader.search
        if not search.searching:
            return
        loop = QEventLoop()
        timer = QTimer(loop)
        timer.setSingleShot(True)
        timer.setInterval(5000)

        def finish_when_done():
            if not search.searching:
                loop.quit()

        search.changed.connect(finish_when_done)
        timer.timeout.connect(loop.quit)
        timer.start()
        loop.exec()
        timer.stop()
        search.changed.disconnect(finish_when_done)
        self.assertFalse(search.searching, "document search did not finish")

    def test_bookmark_model_exposes_native_hierarchy_and_destination_roles(self):
        self.open_outline()
        model = self.reader.outline_panel.model
        chapter = model.index(0, 0)
        section = model.index(0, 0, chapter)
        topic = model.index(0, 0, section)
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(chapter.data(int(QPdfBookmarkModel.Role.Title)), "Chapter 1")
        self.assertEqual(chapter.data(Qt.ItemDataRole.DisplayRole), "Chapter 1")
        self.assertEqual(chapter.data(int(QPdfBookmarkModel.Role.Level)), 0)
        self.assertEqual(section.data(int(QPdfBookmarkModel.Role.Title)), "Section 1.1")
        self.assertEqual(section.data(int(QPdfBookmarkModel.Role.Level)), 1)
        self.assertEqual(topic.data(int(QPdfBookmarkModel.Role.Title)), "Topic 1.1.1")
        self.assertEqual(topic.data(int(QPdfBookmarkModel.Role.Level)), 2)
        self.assertEqual(chapter.data(int(QPdfBookmarkModel.Role.Page)), 0)
        self.assertEqual(section.data(int(QPdfBookmarkModel.Role.Page)), 1)
        self.assertEqual(topic.data(int(QPdfBookmarkModel.Role.Page)), 2)
        self.assertEqual(chapter.data(int(QPdfBookmarkModel.Role.Location)), QPointF(40, 92))
        self.assertEqual(section.data(int(QPdfBookmarkModel.Role.Location)), QPointF(80, 292))
        self.assertEqual(topic.data(int(QPdfBookmarkModel.Role.Location)), QPointF(110, 392))
        self.assertEqual(section.data(int(QPdfBookmarkModel.Role.Zoom)), 0.0)
        self.assertEqual(topic.data(int(QPdfBookmarkModel.Role.Zoom)), 2.0)
        self.assertEqual(model.rowCount(chapter), 2)
        self.assertIs(self.reader.outline_panel._stack.currentWidget(), self.reader.outline_panel.tree)

    def test_single_top_level_outline_item_preserves_its_nested_children(self):
        self.assertIsNone(self.document.open(self.single_outline_pdf))
        self.app.processEvents()
        model = self.reader.outline_panel.model
        self.assertEqual(model.rowCount(), 1)
        chapter = model.index(0, 0)
        self.assertEqual(chapter.data(int(QPdfBookmarkModel.Role.Title)), "Chapter 1")
        self.assertEqual(model.rowCount(chapter), 2)
        self.assertEqual(model.index(0, 0, chapter).data(int(QPdfBookmarkModel.Role.Title)),
                         "Section 1.1")

    def test_no_outline_displays_empty_state_and_failed_open_preserves_current_model(self):
        self.open_outline()
        model = self.reader.outline_panel.model
        self.assertIsNotNone(self.document.open(self.corrupt_pdf))
        self.assertIs(model, self.reader.outline_panel.model)
        self.assertEqual(model.rowCount(), 3)
        self.assertIsNone(self.document.open(self.no_outline_pdf))
        self.app.processEvents()
        self.assertEqual(model.rowCount(), 0)
        self.assertIs(self.reader.outline_panel._stack.currentWidget(),
                      self.reader.outline_panel.empty_label)
        self.assertEqual(self.reader.outline_panel.empty_label.text(), "No document outline")

    def test_outline_activation_navigates_to_page_zero_and_later_pages(self):
        self.open_outline()
        model = self.reader.outline_panel.model
        chapter = model.index(0, 0)
        self.reader.outline_panel._activate_index(chapter, self.document.generation)
        self.assertEqual(self.document.page, 0)
        self.assertEqual(self.reader.page.text(), "1")
        self.assertEqual(self.reader.thumbnail_list.currentIndex().row(), 0)
        self.assertEqual(self.document.mode, "Fit Page")
        self.assertEqual(self.document.zoom, 1.0)
        chapter_rect = self.reader.page_view.page_rects[0]
        chapter_location_y = chapter_rect.top() + 92 * chapter_rect.height() / 792
        self.assertAlmostEqual(chapter_location_y - self.reader.page_view.verticalScrollBar().value(),
                               64, delta=2)
        section = model.index(0, 0, chapter)
        self.reader.outline_panel._activate_index(section, self.document.generation)
        self.assertEqual(self.document.page, 1)
        self.assertEqual(self.document.mode, "Fit Page")
        self.assertEqual(self.document.zoom, 1.0)
        topic = model.index(0, 0, section)
        self.reader.outline_panel._activate_index(topic, self.document.generation)
        self.assertEqual(self.document.page, 2)
        self.assertEqual(self.reader.page.text(), "3")
        self.assertEqual(self.reader.thumbnail_list.currentIndex().row(), 2)
        self.assertEqual(self.document.zoom, 2.0)
        self.assertGreater(self.reader.page_view.verticalScrollBar().value(),
                           self.reader.page_view.page_tops[2])

    def test_tree_mouse_and_enter_activate_outline_destinations(self):
        self.open_outline()
        self.reader.set_sidebar_mode("outline")
        tree = self.reader.outline_panel.tree
        chapter = self.reader.outline_panel.model.index(0, 0)
        tree.setCurrentIndex(chapter)
        QTest.keyClick(tree, Qt.Key.Key_Right)
        self.assertTrue(tree.isExpanded(chapter))
        QTest.keyClick(tree, Qt.Key.Key_Left)
        self.assertFalse(tree.isExpanded(chapter))
        tree.expand(chapter)
        self.app.processEvents()
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=tree.visualRect(chapter).center())
        self.assertEqual(self.document.page, 0)

        section = self.reader.outline_panel.model.index(0, 0, chapter)
        tree.setCurrentIndex(section)
        tree.setFocus()
        QTest.keyClick(tree, Qt.Key.Key_Enter)
        self.assertEqual(self.document.page, 1)

        topic = self.reader.outline_panel.model.index(0, 0, section)
        tree.expand(section)
        tree.scrollTo(topic)
        self.app.processEvents()
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=tree.visualRect(topic).center())
        self.assertEqual(self.document.page, 2)
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 2.0))

    def test_zero_or_invalid_zoom_preserves_fit_mode_and_finite_zoom_is_clamped(self):
        self.open_outline()
        self.reader.navigate_to_destination(self.destination(0, QPointF(40, 92), 0.0))
        self.assertEqual(self.document.mode, "Fit Page")
        self.document.set_zoom("Fit Width")
        self.reader.navigate_to_destination(self.destination(2, QPointF(70, 142), 0.0))
        self.assertEqual(self.document.mode, "Fit Width")
        fit_width_zoom = self.document.zoom
        self.reader.navigate_to_destination(
            self.destination(1, QPointF(30, 192), float("nan")))
        self.assertEqual(self.document.mode, "Fit Width")
        self.assertEqual(self.document.zoom, fit_width_zoom)
        self.reader.navigate_to_destination(self.destination(1, None, float("inf")))
        self.assertEqual((self.document.mode, self.document.zoom), ("Fit Width", fit_width_zoom))
        self.document.set_zoom("Manual", 1.0)
        self.reader.navigate_to_destination(self.destination(1))
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 1.0))
        self.document.set_zoom("Manual", 1.25)
        self.reader.navigate_to_destination(self.destination(1))
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 1.25))
        self.reader.navigate_to_destination(self.destination(0, None, 100.0))
        self.assertEqual(self.document.mode, "Manual")
        self.assertEqual(self.document.zoom, 5.0)
        self.reader.navigate_to_destination(self.destination(1, None, 0.01))
        self.assertEqual(self.document.zoom, 0.25)

    def test_zoom_destination_is_applied_before_page_location_is_positioned(self):
        self.open_outline()
        self.assertTrue(self.reader.navigate_to_destination(self.destination(2, zoom=1.5)))
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 1.5))
        page_top = self.reader.page_view.page_rects[2].top()
        page_bottom = self.reader.page_view.page_rects[2].bottom()
        view_top = self.reader.page_view.verticalScrollBar().value()
        view_bottom = view_top + self.reader.page_view.viewport().height()
        self.assertGreater(min(page_bottom, view_bottom), max(page_top, view_top))

        self.assertTrue(self.reader.navigate_to_destination(
            self.destination(2, QPointF(580, 142), 1.5)))
        self.assertEqual((self.document.mode, self.document.zoom), ("Manual", 1.5))
        self.assertAlmostEqual(self.reader.page_view.zoom_scale, 1.5)
        rect = self.reader.page_view.page_rects[2]
        _, page_height = self.document.page_sizes[2]
        destination_y = rect.top() + 142 * rect.height() / page_height
        screen_y = destination_y - self.reader.page_view.verticalScrollBar().value()
        self.assertAlmostEqual(screen_y, 64, delta=2)
        self.assertGreater(self.reader.page_view.horizontalScrollBar().value(), 0)
        page_width = self.document.page_sizes[2][0]
        destination_x = rect.left() + 580 * rect.width() / page_width
        screen_x = destination_x - self.reader.page_view.horizontalScrollBar().value()
        self.assertGreaterEqual(screen_x, 0)
        self.assertLess(screen_x, self.reader.page_view.viewport().width())

    def test_invalid_page_and_stale_generation_are_ignored(self):
        self.open_outline()
        self.document.set_page(1)
        previous_zoom = (self.document.mode, self.document.zoom)
        self.assertFalse(self.reader.navigate_to_destination(
            self.destination(3, QPointF(20, 20), 2.0)))
        self.assertFalse(self.reader.navigate_to_destination(self.destination(
            0, QPointF(20, 20), 2.0, self.document.generation - 1)))
        self.assertEqual(self.document.page, 1)
        self.assertEqual((self.document.mode, self.document.zoom), previous_zoom)

    def test_structural_parent_without_destination_does_not_navigate_to_page_zero(self):
        self.open_outline()
        self.reader.navigate_to_page(2)
        previous_zoom = (self.document.mode, self.document.zoom)
        parent = self.reader.outline_panel.model.index(2, 0)
        self.assertEqual(parent.data(int(QPdfBookmarkModel.Role.Title)), "Appendices")
        self.reader.set_sidebar_mode("outline")
        tree = self.reader.outline_panel.tree
        tree.setCurrentIndex(parent)
        QTest.keyClick(tree, Qt.Key.Key_Right)
        self.assertTrue(tree.isExpanded(parent))
        QTest.keyClick(tree, Qt.Key.Key_Left)
        self.assertFalse(tree.isExpanded(parent))
        self.reader.outline_panel._activate_index(parent, self.document.generation)
        self.assertEqual(self.document.page, 2)
        self.assertEqual((self.document.mode, self.document.zoom), previous_zoom)

    def test_outline_sidebar_mode_visibility_and_search_selection_state_coexist(self):
        self.open_outline()
        self.reader.search.set_query("needle")
        self.wait_for_search()
        self.assertEqual(self.reader.search.result_count, 3)
        search_results = self.reader.search.results
        search_index = self.reader.search.current_index
        self.reader.selection.select_all(0)
        self.assertTrue(self.reader.selection.valid())
        selected_text = self.reader.selection.result.text()
        self.assertIn("needle first page", selected_text)

        self.reader.set_sidebar_mode("outline")
        self.assertEqual(self.reader.sidebar_tabs.tabText(self.reader.sidebar_tabs.currentIndex()),
                         "Outline")
        self.assertEqual(self.reader.search.query, "needle")
        self.assertEqual(self.reader.search.results, search_results)
        self.assertTrue(self.reader.selection.valid())
        self.reader.outline_panel._activate_index(
            self.reader.outline_panel.model.index(1, 0), self.document.generation)
        self.assertEqual(self.document.page, 2)
        self.assertEqual(self.reader.search.results, search_results)
        self.assertEqual(self.reader.search.current_index, search_index)
        self.assertTrue(self.reader.selection.valid())
        self.assertEqual(self.reader.selection.page, 0)
        self.reader.page_view.setFocus()
        QTest.keyClick(self.reader.page_view.viewport(), Qt.Key.Key_C,
                       Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), selected_text)
        self.reader.set_sidebar_visible(False)
        self.assertTrue(self.reader.sidebar.isHidden())
        self.reader.set_sidebar_visible(True)
        self.assertFalse(self.reader.sidebar.isHidden())
        self.assertEqual(self.reader._sidebar_mode, "outline")
        self.reader.set_sidebar_mode("thumbnails")
        self.assertEqual(self.reader.sidebar_tabs.currentIndex(), 0)

    def test_main_window_outline_action_switches_tab_and_keeps_visibility_state_consistent(self):
        root = Path(self.temp.name)
        settings = Settings(QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat))
        window = MainWindow(ThemeManager(self.app, settings), "0.1",
                            DocumentState(root / "state.json"))
        window.show()
        self.assertIsNone(window.document.open(self.outline_pdf))
        self.app.processEvents()
        self.assertTrue(window.outline_action.isEnabled())
        window.outline_action.trigger()
        window.activateWindow()
        self.app.processEvents()
        QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(window.reader.find_bar.isVisible())
        self.assertIs(self.app.focusWidget(), window.reader.find_bar.query_edit)
        window.theme.select(Appearance.DARK)
        self.app.processEvents()
        self.assertEqual(window.reader.outline_panel.tree.palette().color(QPalette.ColorRole.Text).name(),
                         "#ecedef")
        window.theme.select(Appearance.LIGHT)
        self.app.processEvents()
        self.assertEqual(window.reader.outline_panel.tree.palette().color(QPalette.ColorRole.Text).name(),
                         "#24292e")
        window.theme.select(Appearance.SYSTEM)
        self.app.processEvents()
        self.assertEqual(window.reader.outline_panel.tree.palette().color(QPalette.ColorRole.Text),
                         self.app.palette().color(QPalette.ColorRole.Text))
        window.theme.select(Appearance.LIGHT)
        self.app.processEvents()
        self.assertTrue(window.outline_action.isChecked())
        self.assertFalse(window.thumbnails_action.isChecked())
        self.assertEqual(window.reader.sidebar_tabs.currentIndex(), 1)
        self.assertTrue(window.sidebar_visibility_action.isChecked())
        window.sidebar_visibility_action.trigger()
        self.assertFalse(window.sidebar_visibility_action.isChecked())
        self.assertFalse(window.reader.sidebar.isVisible())
        self.assertTrue(window.outline_action.isChecked())
        window.sidebar_visibility_action.trigger()
        self.assertTrue(window.reader.sidebar.isVisible())
        window.close_document()
        self.assertFalse(window.document.pdf)
        self.assertEqual(window.reader.outline_panel.model.rowCount(), 0)
        self.assertIs(window.stack.currentWidget(), window.stack.widget(0))
        self.assertFalse(window.outline_action.isEnabled())
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_late_outline_destination_from_replaced_document_is_ignored(self):
        self.open_outline()
        panel = self.reader.outline_panel
        destinations = []
        panel.destinationActivated.connect(destinations.append)
        panel._activate_index(panel.model.index(0, 0), self.document.generation)
        self.assertEqual(len(destinations), 1)
        destination = destinations[0]
        self.assertIsInstance(destination, OutlineDestination)
        self.assertEqual(destination.location, (40.0, 92.0))
        self.assertEqual(destination.generation, self.document.generation)

        self.assertIsNone(self.document.open(self.single_outline_pdf))
        self.app.processEvents()
        self.assertEqual(self.document.path, self.single_outline_pdf)
        self.reader.navigate_to_page(2)
        self.assertFalse(self.reader.navigate_to_destination(destination))
        self.assertEqual(self.document.page, 2)

    def test_late_outline_destination_after_close_is_ignored(self):
        self.open_outline()
        panel = self.reader.outline_panel
        destinations = []
        panel.destinationActivated.connect(destinations.append)
        panel._activate_index(panel.model.index(1, 0), self.document.generation)
        self.assertEqual(len(destinations), 1)
        destination = destinations[0]

        self.document.close()
        self.app.processEvents()
        self.assertIsNone(panel._document)
        self.assertEqual(panel.model.rowCount(), 0)
        self.assertFalse(self.document.pdf)
        self.assertFalse(self.reader.navigate_to_destination(destination))
        self.assertEqual(self.reader.page.text(), "")

    def test_repeated_document_replacement_releases_bookmark_models(self):
        panel = self.reader.outline_panel
        destroyed_models = []
        destroyed_documents = []
        paths = (self.outline_pdf, self.single_outline_pdf,
                 self.outline_pdf, self.single_outline_pdf, self.outline_pdf)

        for sequence, path in enumerate(paths):
            previous_model = panel.model if self.document.pdf else None
            previous_pdf = self.document.pdf
            if previous_model is not None:
                previous_model.destroyed.connect(
                    lambda *_args, marker=sequence, output=destroyed_models:
                    output.append(marker))
                previous_pdf.destroyed.connect(
                    lambda *_args, marker=sequence, output=destroyed_documents:
                    output.append(marker))

            self.assertIsNone(self.document.open(path))
            self.app.processEvents()
            if previous_model is not None:
                self.assertIsNot(panel.model, previous_model)
                self.assertIs(panel.model.document(), self.document.pdf)
                self.assertIsNone(previous_model.document())
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

            if previous_model is not None:
                self.assertIn(sequence, destroyed_models)
                self.assertIn(sequence, destroyed_documents)
            self.assertFalse(hasattr(panel, "_retired_models"))
            self.assertLessEqual(len(panel.findChildren(QPdfBookmarkModel)), 2)

        requests = []
        panel.destinationActivated.connect(requests.append)
        panel.tree.clicked.emit(panel.model.index(0, 0))
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.document.path, self.outline_pdf)

        sequence = len(paths)
        current_model = panel.model
        current_pdf = self.document.pdf
        current_model.destroyed.connect(
            lambda *_args, marker=sequence, output=destroyed_models:
            output.append(marker))
        current_pdf.destroyed.connect(
            lambda *_args, marker=sequence, output=destroyed_documents:
            output.append(marker))
        self.document.close()
        self.app.processEvents()
        self.assertIsNone(current_model.document())
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertIn(sequence, destroyed_models)
        self.assertIn(sequence, destroyed_documents)
        self.assertEqual(len(panel.findChildren(QPdfBookmarkModel)), 1)


if __name__ == "__main__":
    unittest.main()
