import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, QPointF, QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QImage, QKeyEvent, QPainter, QPdfWriter, QWheelEvent
from PySide6.QtWidgets import QApplication

from glyph_desktop.reader import Document, PageCanvas, Reader
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
        make_pdf(self.many, 3)
        self.doc = Document()
        self.reader = Reader(self.doc)
        self.addCleanup(self.cleanup_document)

    def cleanup_document(self):
        self.doc.close()
        self.reader.close()
        self.reader.deleteLater()
        self.doc.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wheel(self, delta, *, pixel=False, ctrl=False, target=None, phase=Qt.ScrollPhase.ScrollUpdate):
        event = QWheelEvent(QPointF(20, 20), QPointF(20, 20),
                            QPoint(0, delta if pixel else 0), QPoint(0, 0 if pixel else delta),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
                            phase, False)
        QApplication.sendEvent(target or self.reader.scroll.viewport(), event)

    def prepare_wheel_reader(self):
        self.reader.resize(500, 400)
        self.reader.show()
        self.doc.open(self.many)
        self.reader.canvas.setFixedSize(150, 150)
        self.app.processEvents()
        self.assertEqual(self.reader.scroll.verticalScrollBar().maximum(), 0)

    def test_wheel_navigation_boundaries_and_burst(self):
        self.prepare_wheel_reader()
        self.wheel(120, target=self.reader.surface)
        self.assertEqual(self.doc.page, 0)
        self.doc.request = (42, 0, QSize(10, 10), QSize(10, 10))
        results = []
        self.doc.rendered.connect(lambda *_: results.append(True))
        for _ in range(4):
            self.wheel(-30, target=self.reader.canvas)
        self.assertEqual(self.doc.page, 1)
        self.doc._result(self.doc.generation, 0, QSize(10, 10),
                         QImage(10, 10, QImage.Format.Format_ARGB32), 42)
        self.assertEqual(results, [])
        for _ in range(10):
            self.wheel(-120)
        self.assertEqual(self.doc.page, 1)
        self.wheel(120)
        self.assertEqual(self.doc.page, 0)
        self.doc.set_page(2)
        self.wheel(-120)
        self.assertEqual(self.doc.page, 2)
        self.doc.set_page(0)
        for _ in range(4):
            self.wheel(-10, pixel=True)
        self.assertEqual(self.doc.page, 1)
        self.reader.wheel_guard.stop()
        self.doc.set_page(0)
        self.wheel(-360)
        self.assertEqual(self.doc.page, 1)

    def test_wheel_scrolls_page_before_changing_page(self):
        self.prepare_wheel_reader()
        self.doc.set_zoom("Manual", 2.0)
        self.reader.canvas.setFixedSize(250, 1000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.app.processEvents()
        bar = self.reader.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum() // 2)
        before = bar.value()
        self.wheel(-120, target=self.reader.canvas)
        self.assertEqual(self.doc.page, 0)
        self.assertGreater(bar.value(), before)
        bar.setValue(bar.maximum())
        self.wheel(-120)
        self.assertEqual(self.doc.page, 1)
        self.reader.render()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.minimum())
        bar.setValue(bar.minimum())
        self.wheel(120)
        self.assertEqual(self.doc.page, 0)
        self.reader.render()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.app.processEvents()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), bar.maximum())

    def test_ordinary_navigation_starts_new_page_at_top(self):
        self.prepare_wheel_reader()
        self.doc.set_zoom("Manual", 2.0)
        self.reader.canvas.setFixedSize(250, 1000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.app.processEvents()
        bar = self.reader.scroll.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        self.assertGreater(bar.value(), bar.minimum())

        self.reader.next.click()
        self.assertEqual(self.doc.page, 1)
        self.reader.render()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.minimum())

    def test_trackpad_gesture_cannot_skip_pages_after_guard_expires(self):
        self.prepare_wheel_reader()
        self.wheel(0, phase=Qt.ScrollPhase.ScrollBegin)
        for _ in range(4):
            self.wheel(-10, pixel=True)
        self.assertEqual(self.doc.page, 1)
        self.reader.wheel_guard.stop()
        self.wheel(-120)
        self.assertEqual(self.doc.page, 1)
        self.wheel(0, phase=Qt.ScrollPhase.ScrollEnd)
        self.wheel(-120)
        self.assertEqual(self.doc.page, 2)

    def test_ctrl_wheel_uses_zoom_and_ignores_page_field(self):
        self.prepare_wheel_reader()
        self.wheel(-120, target=self.reader.page)
        self.wheel(-120, target=self.reader.zoom)
        self.assertEqual(self.doc.page, 0)
        self.doc.set_zoom("Fit Page")
        self.wheel(120, ctrl=True)
        self.assertEqual(self.doc.mode, "Manual")
        self.doc.set_zoom("Manual", 5.0)
        self.wheel(120, ctrl=True)
        self.assertEqual(self.doc.zoom, 5.0)
        self.doc.set_zoom("Manual", 0.25)
        self.wheel(-120, ctrl=True)
        self.assertEqual(self.doc.zoom, 0.25)
        self.doc.set_zoom("Fit Width")
        for _ in range(4):
            self.wheel(10, pixel=True, ctrl=True)
        self.assertEqual(self.doc.mode, "Manual")

    def test_navigation_and_replacement(self):
        self.assertIsNone(self.doc.open(self.many))
        self.assertEqual((self.doc.count, self.doc.page), (3, 0))
        self.assertFalse(self.reader.previous.isEnabled())
        self.reader.next.click()
        self.assertEqual(self.doc.page, 1)
        self.reader.previous.click()
        self.assertEqual(self.doc.page, 0)
        self.doc.set_page(-1)
        self.assertEqual(self.doc.page, 0)
        self.reader.page.setText("3")
        self.reader.enter_page()
        self.assertEqual(self.doc.page, 2)
        self.assertFalse(self.reader.next.isEnabled())
        self.reader.page.setText("9")
        self.reader.enter_page()
        self.assertEqual(self.doc.page, 2)
        self.doc.set_zoom("Manual", 2.0)
        self.assertIsNone(self.doc.open(self.one))
        self.assertEqual((self.doc.count, self.doc.page, self.doc.mode), (1, 0, "Fit Page"))
        self.doc.close()
        self.assertEqual(self.doc.count, 0)

    def test_zoom_bounds_and_fit_transition(self):
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

    def test_invalid_document_and_stale_result(self):
        self.doc.open(self.one)
        old_generation = self.doc.generation
        invalid = Path(self.temp.name) / "invalid.pdf"
        invalid.write_bytes(b"not a PDF")
        self.assertIsNotNone(self.doc.open(invalid))
        self.assertEqual(self.doc.path, self.one)
        self.doc.open(self.many)
        self.doc.request = (42, 0, QSize(10, 10), QSize(10, 10))
        results = []
        self.doc.rendered.connect(lambda *_: results.append(True))
        image = QImage(10, 10, QImage.Format.Format_ARGB32)
        self.doc._result(old_generation, 0, QSize(10, 10), image, 42)
        self.doc._result(self.doc.generation, 1, QSize(10, 10), image, 42)
        self.assertEqual(results, [])
        self.doc._result(self.doc.generation, 0, QSize(10, 10), image, 42)
        self.assertEqual(results, [True])

    def test_async_render_and_close(self):
        self.doc.open(self.many)
        loop = QEventLoop()
        images = []
        self.doc.rendered.connect(lambda image, _size: (images.append(image), loop.quit()))
        self.doc.render(QSize(200, 280), 1.0)
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(images)
        self.assertFalse(images[0].isNull())
        self.doc.render(QSize(1500, 2100), 1.0)
        self.doc.close()

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
        settings = Settings(QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat))
        window = MainWindow(ThemeManager(self.app, settings), "0.1.0")
        window.document.open(self.many)
        window.document.set_page(2)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier)
        with patch.object(window, "isActiveWindow", return_value=True), \
             patch("glyph_desktop.window.QApplication") as application:
            application.activePopupWidget.return_value = object()
            self.assertFalse(window.eventFilter(window.reader.zoom, event))
        self.assertEqual(window.document.page, 2)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
