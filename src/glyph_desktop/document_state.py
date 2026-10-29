"""Local, atomic document history and reading state."""

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths


def document_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


class DocumentState:
    MAX_RECENTS = 10

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.recents = []
        self.last_document = None
        self.documents = {}
        self._executor = ThreadPoolExecutor(max_workers=1) if self.path else None
        if self.path:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("version", 1) == 1:
                    recents = data.get("recents")
                    for item in recents if isinstance(recents, list) else []:
                        if isinstance(item, str) and item and document_key(item) not in self.recents:
                            self.recents.append(document_key(item))
                            if len(self.recents) == self.MAX_RECENTS:
                                break
                    last = data.get("last_document")
                    if isinstance(last, str) and last:
                        self.last_document = document_key(last)
                    docs = data.get("documents")
                    if isinstance(docs, dict):
                        self.documents = {document_key(k): v for k, v in docs.items()
                                          if isinstance(k, str) and k and isinstance(v, dict)}
            except (OSError, ValueError, TypeError, RuntimeError):
                pass

    @classmethod
    def default(cls):
        return cls(Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation)) / "document-state.json")

    def opened(self, path):
        key = document_key(path)
        self.recents = [key] + [item for item in self.recents if item != key]
        self.recents = self.recents[:self.MAX_RECENTS]
        self.last_document = key

    def remove_recent(self, path):
        key = document_key(path)
        self.recents = [item for item in self.recents if item != key]
        if self.last_document == key:
            self.last_document = None

    def clear_recents(self):
        # Reading positions and the last session survive clearing the visible list.
        self.recents = []

    def reading(self, path):
        value = self.documents.get(document_key(path))
        if not isinstance(value, dict):
            return None
        page, offset, mode, zoom = (value.get("page"), value.get("offset"),
                                    value.get("mode"), value.get("zoom"))
        if (type(page) is not int or page < 0 or type(offset) not in (int, float)
                or not math.isfinite(offset) or not 0 <= offset <= 1
                or mode not in ("Manual", "Fit Page", "Fit Width")
                or type(zoom) not in (int, float) or not math.isfinite(zoom)
                or not 0.25 <= zoom <= 5.0):
            return None
        return {"page": page, "offset": float(offset), "mode": mode, "zoom": float(zoom)}

    def set_reading(self, path, state):
        self.documents[document_key(path)] = state

    def save(self):
        if not self._executor:
            return
        payload = json.dumps({"version": 1, "recents": self.recents,
                              "last_document": self.last_document,
                              "documents": self.documents}, ensure_ascii=False).encode("utf-8")
        self._executor.submit(self._write, payload)

    def _write(self, payload):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            file = QSaveFile(str(self.path))
            if file.open(QIODevice.OpenModeFlag.WriteOnly):
                if file.write(payload) == len(payload):
                    file.commit()
                else:
                    file.cancelWriting()
        except OSError:
            pass

    def close(self):
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
