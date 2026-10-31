"""Bounded, reversible annotation history for annotation mutations only (M3B1)."""

from dataclasses import dataclass

CREATE = "create"
DELETE = "delete"

# Fixed ceiling on remembered mutations; the oldest entries fall off first.
MAX_ANNOTATION_HISTORY = 100


@dataclass(frozen=True)
class AnnotationOperation:
    """One reversible mutation: plain values plus an immutable annotation record.

    The record is a frozen Markup or Ink value owned by the annotation store. No
    document, widget, viewport, selection, or model-index reference is retained.
    """

    action: str
    generation: int
    index: int
    annotation: object


class AnnotationHistory:
    """Glyph-owned undo/redo stacks over mutations of one annotation store."""

    def __init__(self, store, limit=MAX_ANNOTATION_HISTORY):
        self.store = store
        self.limit = max(1, int(limit))
        self._undo: list[AnnotationOperation] = []
        self._redo: list[AnnotationOperation] = []

    def __len__(self) -> int:
        return len(self._undo)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def record(self, action: str, annotation, index: int) -> None:
        """Remember one completed mutation; a new mutation abandons the redo branch."""
        self._undo.append(AnnotationOperation(action, annotation.generation, index, annotation))
        if len(self._undo) > self.limit:
            del self._undo[:len(self._undo) - self.limit]
        self._redo.clear()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def undo(self) -> bool:
        return self._apply(self._undo, self._redo)

    def redo(self) -> bool:
        return self._apply(self._redo, self._undo)

    def _reject(self) -> bool:
        """Defensive recovery: drop the stale stacks and notify so action state re-syncs."""
        self.clear()
        self.store.changed.emit()
        return False

    def _apply(self, source: list[AnnotationOperation],
               destination: list[AnnotationOperation]) -> bool:
        if not source:
            return False
        operation = source[-1]
        if operation.generation != self.store.document.generation:
            # Defense in depth: stale history must never mutate a newer document.
            return self._reject()
        annotation = operation.annotation
        if operation.action == CREATE:
            if self.store.remove(annotation.id) is None:
                return self._reject()
            destination.append(AnnotationOperation(DELETE, operation.generation,
                                                   operation.index, annotation))
        else:
            if self.store.get(annotation.id) is not None:
                return self._reject()
            if not self.store.restore(operation.index, annotation):
                return self._reject()
            destination.append(AnnotationOperation(CREATE, operation.generation,
                                                   operation.index, annotation))
        source.pop()
        self.store.changed.emit()
        return True
