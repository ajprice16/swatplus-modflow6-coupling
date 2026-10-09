"""Small reusable widgets (no QGIS-specific enums, so identical on Qt5 and Qt6)."""
from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QToolButton, QWidget


class PathEdit(QWidget):
    """Line edit with a browse button. ``mode`` is 'file', 'save' or 'folder'."""
    textChanged = pyqtSignal(str)

    def __init__(self, mode="file", file_filter="All files (*)", title="Select", parent=None):
        super().__init__(parent)
        self.mode, self.file_filter, self.title = mode, file_filter, title
        self.edit = QLineEdit(self)
        self.button = QToolButton(self)
        self.button.setText("...")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        self.button.clicked.connect(self._browse)
        self.edit.textChanged.connect(self.textChanged)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value or "")

    def _browse(self):
        start = self.text()
        if self.mode == "folder":
            path = QFileDialog.getExistingDirectory(self, self.title, start)
        elif self.mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, self.title, start, self.file_filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.title, start, self.file_filter)
        if path:
            self.setText(path)
