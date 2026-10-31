"""Transient sticky-note editor for M3C note annotations."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout


class NoteEditor(QDialog):
    """Small multiline editor. It owns no annotation state and is not retained after close.

    Canonical note text lives only in the immutable Note record; this dialog is the
    temporary place where the user types it, and its own Undo/Redo stay local to it.
    """

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Note")
        self.setModal(True)
        self.setMinimumSize(280, 160)
        layout = QVBoxLayout(self)
        self.body = QTextEdit(self)
        self.body.setAcceptRichText(False)
        self.body.setPlaceholderText("Note text")
        self.body.setPlainText(text)
        layout.addWidget(self.body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.body.setFocus(Qt.FocusReason.OtherFocusReason)

    def text(self) -> str:
        return self.body.toPlainText()
