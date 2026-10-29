import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QMouseEvent, QPageSize, QPainter, QPdfWriter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from glyph_desktop.document_state import DocumentState
from glyph_desktop.settings import Settings
from glyph_desktop.theme import ThemeManager
from glyph_desktop.window import MainWindow


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.pdf = root / "text.pdf"
        writer = QPdfWriter(str(self.pdf))
        writer.setResolution(72)
        painter = QPainter(writer)
        painter.setFont(QFont("DejaVu Sans", 18))
        painter.drawText(72, 100, "Hello café world")
        painter.drawText(72, 140, "Second line")
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A5))
        writer.newPage()
        painter.drawText(72, 100, "Other page")
        writer.newPage()
        painter.fillRect(72, 72, 100, 100, Qt.GlobalColor.black)
        painter.end()
        self.blank = root / "image.pdf"
        image_writer = QPdfWriter(str(self.blank))
        image_painter = QPainter(image_writer)
        image_painter.fillRect(100, 100, 500, 500, Qt.GlobalColor.black)
        image_painter.end()
        settings = Settings(QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat))
        self.theme = ThemeManager(self.app, settings)
        self.window = MainWindow(self.theme, "0.1", DocumentState(root / "state.json"))
        self.window.show()
        self.assertTrue(self.window.open_document(self.pdf))
        self.app.processEvents()
        self.view = self.window.reader.page_view
        self.view.verticalScrollBar().setValue(0)
        self.app.processEvents()
        self.selection = self.window.reader.selection
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def drag(self, start, end, page=0):
        target = self.view.viewport()
        first = self.view.pdf_to_viewport(page, start).toPoint()
        last = self.view.pdf_to_viewport(page, end).toPoint()
        QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=first)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(last), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, move)
        QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=last)
        self.app.processEvents()

    def context_menu_actions(self, position, choose_copy=False):
        from PySide6.QtGui import QContextMenuEvent

        seen = []

        def inspect_menu():
            menu = QApplication.activePopupWidget()
            if isinstance(menu, QMenu):
                seen.extend(menu.actions())
                if choose_copy and self.window.reader.copy_action in menu.actions():
                    self.window.reader.copy_action.trigger()
                menu.close()

        QTimer.singleShot(0, inspect_menu)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                  self.view.viewport().mapToGlobal(position))
        QApplication.sendEvent(self.view.viewport(), event)
        self.app.processEvents()
        return seen

    def test_drag_multiline_copy_escape_and_empty_area(self):
        glyphs = self.window.document.pdf.getAllText(0).bounds()
        with patch.object(self.window.reader, "schedule_render") as render:
            self.drag(glyphs[0].boundingRect().center(), glyphs[3].boundingRect().center())
            self.assertTrue(self.selection.result.text().startswith("Hel"))
            self.drag(glyphs[0].boundingRect().center(), glyphs[-1].boundingRect().center())
            render.assert_not_called()
        self.assertTrue(self.selection.valid())
        self.assertIn("café", self.selection.result.text())
        self.assertIn("Second", self.selection.result.text())
        self.assertTrue(self.selection.result.bounds())
        self.assertTrue(self.window.reader.copy_action.isEnabled())
        selected_text = self.selection.result.text()
        self.window.activateWindow()
        self.view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), selected_text)
        self.assertTrue(self.selection.valid())
        selected_polygon = self.selection.result.bounds()[0]
        selected_point = self.view.pdf_to_viewport(
            self.selection.page, selected_polygon.boundingRect().center()).toPoint()
        seen = self.context_menu_actions(selected_point, choose_copy=True)
        self.assertIn(self.window.reader.copy_action, seen)
        self.assertEqual(self.app.clipboard().text(), selected_text)
        self.assertTrue(self.selection.valid())

        outside = self.view.viewport().rect().center()
        outside_hit = self.view.viewport_to_pdf(outside)
        self.assertIsNotNone(outside_hit)
        self.assertEqual(outside_hit[0], self.selection.page)
        self.assertFalse(any(polygon.containsPoint(outside_hit[1], Qt.FillRule.OddEvenFill)
                             for polygon in self.selection.result.bounds()))
        self.assertEqual(self.context_menu_actions(outside), [])
        self.assertTrue(self.selection.valid())
        self.assertEqual(self.selection.result.text(), selected_text)

        self.window.activateWindow()
        self.view.setFocus()
        self.app.processEvents()
        self.app.clipboard().clear()
        self.window.reader.copy_action.trigger()
        self.assertEqual(self.app.clipboard().text(), selected_text)
        self.app.clipboard().clear()
        self.view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), selected_text)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_Escape)
        self.assertFalse(self.selection.valid())
        self.assertFalse(self.window.reader.copy_action.isEnabled())
        self.drag(QPointF(300, 300), QPointF(350, 350))
        self.assertFalse(self.selection.valid())

    def test_select_all_focus_layout_virtualization_and_lifecycle(self):
        self.window.activateWindow()
        self.view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selection.page, 0)
        self.assertIn("café", self.selection.result.text())
        self.assertNotIn("Other page", self.selection.result.text())
        self.window.reader.navigate_to_page(1)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selection.page, 1)
        self.assertIn("Other page", self.selection.result.text())
        self.window.reader.navigate_to_page(0)
        QTest.keyClick(self.view.viewport(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selection.page, 0)
        text = self.selection.result.text()
        self.window.reader.page.setFocus()
        self.window.reader.page.setText("123")
        QTest.keyClick(self.window.reader.page, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.window.reader.page.selectedText(), "123")
        QTest.keyClick(self.window.reader.page, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), "123")
        self.assertEqual(self.selection.result.text(), text)
        wheel = QWheelEvent(QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(self.view.viewport(), wheel)
        self.assertEqual(self.window.document.mode, "Manual")
        self.assertEqual(self.selection.result.text(), text)
        point = self.selection.result.bounds()[0].boundingRect().center()
        self.assertNotEqual(self.window.document.page_sizes[0], self.window.document.page_sizes[1])
        for mode, zoom in (("Manual", 1.0), ("Manual", 2.0), ("Fit Width", None), ("Fit Page", None)):
            self.window.document.set_zoom(mode, zoom)
            mapped = self.view.pdf_to_viewport(0, point)
            page, inverse = self.view.viewport_to_pdf(mapped, 0, clamp=True)
            self.assertEqual(page, 0)
            self.assertAlmostEqual(inverse.x(), point.x(), places=4)
            self.assertAlmostEqual(inverse.y(), point.y(), places=4)
            mixed_point = QPointF(72, 100)
            _, mixed_inverse = self.view.viewport_to_pdf(self.view.pdf_to_viewport(1, mixed_point), 1, clamp=True)
            self.assertAlmostEqual(mixed_inverse.x(), mixed_point.x(), places=4)
            self.assertAlmostEqual(mixed_inverse.y(), mixed_point.y(), places=4)
            self.assertEqual(self.selection.result.text(), text)
        self.window.resize(1050, 750)
        self.window.reader.set_thumbnail_visible(False)
        self.app.processEvents()
        _, inverse = self.view.viewport_to_pdf(self.view.pdf_to_viewport(0, point), 0, clamp=True)
        self.assertAlmostEqual(inverse.x(), point.x(), places=4)
        self.assertAlmostEqual(inverse.y(), point.y(), places=4)
        self.view.scroll_to_page(2)
        self.app.processEvents()
        self.assertTrue(self.selection.valid())
        self.view.scroll_to_page(0)
        self.assertEqual(self.selection.page, 0)
        self.assertTrue(self.window.open_document(self.blank))
        self.assertFalse(self.selection.valid())
        self.app.processEvents()
        self.assertTrue(self.window.open_document(self.pdf))
        self.app.processEvents()
        self.window.reader.select_current_page()
        self.window.close_document()
        self.assertFalse(self.selection.valid())

    def test_page_boundary_and_image_only_page(self):
        self.view.verticalScrollBar().setValue(0)
        self.app.processEvents()
        self.assertIsNone(self.view.viewport_to_pdf(QPointF(0, 0)))
        glyphs = self.window.document.pdf.getAllText(0).bounds()
        start = glyphs[0].boundingRect().center()
        QTest.mouseMove(self.view.viewport(), self.view.pdf_to_viewport(0, start).toPoint())
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
        QTest.mouseMove(self.view.viewport(), QPoint(0, 0))
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
        outside = QPointF(start.x(), self.window.document.page_sizes[0][1] + 100)
        self.drag(start, outside)
        if self.selection.valid():
            self.assertEqual(self.selection.page, 0)
            self.assertNotIn("Other page", self.selection.result.text())
        self.view.scroll_to_page(2)
        self.app.processEvents()
        self.drag(QPointF(100, 100), QPointF(150, 150), page=2)
        self.assertFalse(self.selection.valid())
        self.selection.select_all(2)
        self.assertFalse(self.selection.valid())


if __name__ == "__main__":
    unittest.main()
