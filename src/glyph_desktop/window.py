"""The M0 application window."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QStackedWidget, QTextEdit, QPlainTextEdit, QVBoxLayout, QPushButton, QScrollArea,
                               QWidget, QMenu)

from .about import AboutDialog
from .branding import application_icon, logo_pixmap
from .settings import Appearance
from .theme import ThemeManager
from .reader import Document, Reader
from .document_state import DocumentState


class MainWindow(QMainWindow):
    def __init__(self, theme: ThemeManager, version: str, state: DocumentState | None = None) -> None:
        super().__init__()
        self.theme = theme
        self.version = version
        self.state = state if state is not None else DocumentState()
        self._restoring = False
        self._persist_timer = QTimer(self, singleShot=True)
        self._persist_timer.setInterval(750)
        self._persist_timer.timeout.connect(self.state.save)
        self.setWindowTitle("Glyph")
        self.setWindowIcon(application_icon())
        self.resize(920, 640)
        self.setMinimumSize(420, 320)
        self.document = Document(self)
        self._build_menu()
        self._build_welcome()
        self.reader = Reader(self.document, self)
        edit = QMenu("&Edit", self)
        self.menuBar().insertMenu(self.menuBar().actions()[1], edit)
        self.find_action = QAction("&Find…", self)
        self.find_action.setShortcut(QKeySequence.StandardKey.Find)
        self.find_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.find_action.triggered.connect(self.reader.show_find_bar)
        self.addAction(self.find_action)
        edit.addAction(self.find_action)
        edit.addSeparator()
        edit.addAction(self.reader.copy_action)
        edit.addAction(self.reader.select_all_action)
        annotations = self.menuBar().addMenu("&Annotation")
        annotations.addAction("Annotations are not saved").setEnabled(False)
        annotations.addAction("Alt+click markup on links to select").setEnabled(False)
        annotations.addSeparator()
        for action in self.reader.markup_actions.values():
            annotations.addAction(action)
        annotations.addAction(self.reader.ink_action)
        self.stack.addWidget(self.reader)
        self.sidebar_visibility_action.triggered.connect(self.reader.set_sidebar_visible)
        self.thumbnails_action.triggered.connect(
            lambda _checked=False: self.reader.set_sidebar_mode("thumbnails"))
        self.outline_action.triggered.connect(
            lambda _checked=False: self.reader.set_sidebar_mode("outline"))
        self.reader.sidebarStateChanged.connect(self._sync_sidebar_actions)
        self.document.changed.connect(self._document_changed)
        self.document.changed.connect(self._reading_changed)
        self.reader.page_view.verticalScrollBar().valueChanged.connect(self._reading_changed)
        for target in (self, self.stack.widget(0), self.reader.page_view.viewport()):
            target.setAcceptDrops(True)
            if target is not self:
                target.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self._document_changed()
        self._refresh_recents()

    def _build_menu(self) -> None:
        app_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open PDF…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_pdf)
        app_menu.addAction(open_action)
        self.close_action = QAction("&Close Document", self)
        self.close_action.triggered.connect(self.close_document)
        app_menu.addAction(self.close_action)
        clear = QAction("Clear Recent Documents", self)
        clear.triggered.connect(self.clear_recents)
        app_menu.addAction(clear)
        app_menu.addSeparator()
        about = QAction("&About Glyph", self)
        about.triggered.connect(self.show_about)
        app_menu.addAction(about)
        app_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        app_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        self.sidebar_visibility_action = QAction("&Navigation Sidebar", self, checkable=True)
        self.sidebar_visibility_action.setChecked(True)
        self.sidebar_visibility_action.setEnabled(False)
        view_menu.addAction(self.sidebar_visibility_action)
        view_menu.addSeparator()
        self.sidebar_mode_actions = QActionGroup(self)
        self.sidebar_mode_actions.setExclusive(True)
        self.thumbnails_action = QAction("&Thumbnails", self, checkable=True)
        self.thumbnails_action.setChecked(True)
        self.thumbnails_action.setEnabled(False)
        view_menu.addAction(self.thumbnails_action)
        self.sidebar_mode_actions.addAction(self.thumbnails_action)
        self.outline_action = QAction("&Outline", self, checkable=True)
        self.outline_action.setEnabled(False)
        view_menu.addAction(self.outline_action)
        self.sidebar_mode_actions.addAction(self.outline_action)

        appearance = self.menuBar().addMenu("&Appearance")
        self.appearance_actions = QActionGroup(self)
        self.appearance_actions.setExclusive(True)
        for mode in Appearance:
            action = QAction("&" + mode.value.title(), self, checkable=True)
            action.setChecked(mode is self.theme.mode)
            action.triggered.connect(lambda _checked=False, selected=mode: self.theme.select(selected))
            self.appearance_actions.addAction(action)
            appearance.addAction(action)

    def _build_welcome(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addStretch()
        logo = logo_pixmap()
        if not logo.isNull():
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setPixmap(logo.scaled(168, 168, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(image)
        title = QLabel("Glyph")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = title.font()
        font.setPointSize(font.pointSize() + 10)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        subtitle = QLabel("A quieter space for your documents.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        open_button = QPushButton("Open PDF…")
        open_button.clicked.connect(self.open_pdf)
        layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.recent_panel = QWidget()
        self.recent_layout = QVBoxLayout(self.recent_panel)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_scroll = QScrollArea()
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setMinimumSize(320, 150)
        self.recent_scroll.setMaximumSize(480, 160)
        self.recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_scroll.setWidget(self.recent_panel)
        layout.addWidget(self.recent_scroll, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.stack = QStackedWidget(self)
        self.stack.addWidget(container)
        self.setCentralWidget(self.stack)

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if path:
            self.open_document(path)

    def startup(self, explicit=None):
        path = explicit if explicit is not None else self.state.last_document
        if path:
            self.open_document(path, quiet=explicit is None)

    def open_document(self, path, *, quiet=False, recent=False):
        path = Path(path).expanduser().resolve(strict=False)
        if not path.is_file():
            if recent or (quiet and self.state.last_document):
                self.state.remove_recent(path)
                self.state.save()
                self._refresh_recents()
            if not quiet:
                QMessageBox.warning(self, "Unable to open PDF", "The document is no longer available.")
            return False
        self._save_current()
        saved = self.state.reading(path)
        self._restoring = True
        error = self.document.open(path)
        if error:
            self._restoring = False
            if quiet:
                self.state.remove_recent(path)
                self.state.save()
                self._refresh_recents()
            else:
                QMessageBox.warning(self, "Unable to open PDF", error)
            return False
        self.state.opened(path)
        self.state.save()
        self._refresh_recents()
        generation = self.document.generation

        def restore():
            if generation != self.document.generation or not self.document.pdf:
                return
            if saved:
                self.document.set_zoom(saved["mode"], saved["zoom"])
                self.reader.page_view.restore_position(saved["page"], saved["offset"])
            self._restoring = False
            self._reading_changed()

        QTimer.singleShot(0, restore)
        return True

    def _save_current(self):
        if self.document.pdf and self.document.path and not self._restoring:
            page, offset = self.reader.page_view.reading_position()
            self.state.set_reading(self.document.path, {"page": page, "offset": offset,
                                   "mode": self.document.mode, "zoom": self.document.zoom})

    def _reading_changed(self, *_args):
        if self.document.pdf and not self._restoring:
            self._save_current()
            self._persist_timer.start()

    def close_document(self):
        self._save_current()
        self._persist_timer.stop()
        self.state.save()
        self._restoring = False
        self.document.close()

    def clear_recents(self):
        self.state.clear_recents()
        self.state.save()
        self._refresh_recents()

    def _refresh_recents(self):
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.state.recents:
            self.recent_layout.addWidget(QLabel("Recent Documents"))
            for path in self.state.recents:
                label = f"{Path(path).name} — {Path(path).parent.name}"
                button = QPushButton()
                button.setText(button.fontMetrics().elidedText(label, Qt.TextElideMode.ElideMiddle, 285))
                button.setToolTip(path)
                button.clicked.connect(lambda _checked=False, p=path: self.open_document(p, recent=True))
                self.recent_layout.addWidget(button)
        self.recent_scroll.setVisible(bool(self.state.recents))

    @staticmethod
    def _drop_path(event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".pdf":
                    return url.toLocalFile()
        return None

    def dragEnterEvent(self, event):
        if self._drop_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = self._drop_path(event)
        if path:
            self.open_document(path)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _document_changed(self):
        active = self.document.pdf is not None
        self.close_action.setEnabled(active)
        self.sidebar_visibility_action.setEnabled(active)
        self.thumbnails_action.setEnabled(active)
        self.outline_action.setEnabled(active)
        self.find_action.setEnabled(active)
        self.stack.setCurrentWidget(self.reader if active else self.stack.widget(0))
        self.setWindowTitle(f"{self.document.path.name} — Glyph" if active else "Glyph")
        self._sync_sidebar_actions(self.reader.sidebar_visible, self.reader.sidebar_mode)

    def _sync_sidebar_actions(self, visible: bool, mode: str) -> None:
        self.sidebar_visibility_action.setChecked(visible)
        self.thumbnails_action.setChecked(mode == "thumbnails")
        self.outline_action.setChecked(mode == "outline")

    def eventFilter(self, watched, event):
        if watched in (self.stack.widget(0), self.reader.page_view.viewport()):
            if event.type() == QEvent.Type.DragEnter:
                self.dragEnterEvent(event)
                return True
            if event.type() == QEvent.Type.DragMove:
                self.dragMoveEvent(event)
                return True
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        if (event.type() == QEvent.Type.KeyPress and self.isActiveWindow()
                and QApplication.activePopupWidget() is None
                and event.key() == Qt.Key.Key_Escape
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and self.reader.find_bar.isVisible()):
            self.reader.close_find_bar()
            return True
        if (event.type() == QEvent.Type.KeyPress and self.isActiveWindow() and self.document.pdf
                and QApplication.activePopupWidget() is None):
            if (event.key() == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier
                    and self.reader.selection.valid()):
                self.reader.selection.clear()
                return True
            focus = QApplication.focusWidget()
            sidebar_focus = (focus is not None and
                             (isinstance(focus, QAbstractItemView) or
                              self.reader.thumbnail_list.isAncestorOf(focus) or
                              self.reader.thumbnail_list.viewport().isAncestorOf(focus)))
            if not sidebar_focus and not isinstance(focus, (QLineEdit, QTextEdit,
                                                            QPlainTextEdit, QComboBox)):
                keys = {Qt.Key.Key_PageUp: self.document.page - 1,
                        Qt.Key.Key_PageDown: self.document.page + 1,
                        Qt.Key.Key_Home: 0, Qt.Key.Key_End: self.document.count - 1}
                if event.key() in keys and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                    self.reader.navigate_to_page(keys[event.key()])
                    return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        self.close_document()
        self.state.close()
        super().closeEvent(event)

    def show_about(self) -> None:
        AboutDialog(self.version, self).exec()
