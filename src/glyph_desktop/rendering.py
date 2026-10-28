"""Bounded image caches and demand-driven QtPdf page rendering."""

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument, QPdfPageRenderer


@dataclass(frozen=True)
class RenderJob:
    page: int
    logical_width: int
    logical_height: int
    pixel_width: int
    pixel_height: int
    purpose: str
    revision: int

    @property
    def logical_size(self) -> QSize:
        return QSize(self.logical_width, self.logical_height)

    @property
    def pixel_size(self) -> QSize:
        return QSize(self.pixel_width, self.pixel_height)

    @property
    def identity(self) -> tuple[int, int, int, int, int, str, int]:
        return (self.page, self.logical_width, self.logical_height,
                self.pixel_width, self.pixel_height, self.purpose, self.revision)


def make_render_job(page: int, logical_size: QSize, dpr: float,
                    purpose: str, revision: int = 0) -> RenderJob:
    width = max(1, round(logical_size.width() * dpr))
    height = max(1, round(logical_size.height() * dpr))
    factor = min(1.0, 4096 / width, 4096 / height,
                 (16_000_000 / (width * height)) ** 0.5)
    return RenderJob(page, logical_size.width(), logical_size.height(),
                     max(1, round(width * factor)), max(1, round(height * factor)),
                     purpose, revision)


class BoundedImageCache:
    """Small LRU cache with independent item-count and byte ceilings."""

    def __init__(self, max_items: int, max_bytes: int):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._images: OrderedDict[object, tuple[QImage, int]] = OrderedDict()
        self.bytes_used = 0

    def get(self, key) -> QImage | None:
        item = self._images.get(key)
        if item is None:
            return None
        self._images.move_to_end(key)
        return item[0]

    def contains(self, key) -> bool:
        return key in self._images

    def put(self, key, image: QImage) -> bool:
        if image.isNull():
            return False
        size = int(image.sizeInBytes())
        if size > self.max_bytes:
            return False
        old = self._images.pop(key, None)
        if old is not None:
            self.bytes_used -= old[1]
        self._images[key] = (image, size)
        self.bytes_used += size
        while len(self._images) > self.max_items or self.bytes_used > self.max_bytes:
            _, (_, removed_size) = self._images.popitem(last=False)
            self.bytes_used -= removed_size
        return key in self._images

    def clear(self) -> None:
        self._images.clear()
        self.bytes_used = 0

    def __len__(self) -> int:
        return len(self._images)


class PageRenderController(QObject):
    """Feeds only the latest visible-page jobs to QtPdf's serial worker."""

    rendered = Signal(object, QImage, int)

    def __init__(self, document: QPdfDocument, generation: int, parent=None):
        super().__init__(parent)
        self.generation = generation
        self.closed = False
        self._desired: set[tuple] = set()
        self._queue: list[RenderJob] = []
        self._active: RenderJob | None = None
        self._active_request = 0
        self.renderer = QPdfPageRenderer(self)
        self.renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
        self.renderer.setDocument(document)
        self.renderer.pageRendered.connect(self._finished)

    def set_jobs(self, jobs: list[RenderJob]) -> None:
        if self.closed:
            return
        unique = []
        seen = set()
        for job in jobs:
            if job.identity not in seen:
                seen.add(job.identity)
                unique.append(job)
        self._desired = seen
        active_key = self._active.identity if self._active else None
        self._queue = [job for job in unique if job.identity != active_key]
        self._pump()

    def _pump(self) -> None:
        if self.closed or self._active is not None:
            return
        while self._queue:
            job = self._queue.pop(0)
            if job.identity not in self._desired:
                continue
            request = self.renderer.requestPage(job.page, job.pixel_size)
            if not request:
                return
            self._active = job
            self._active_request = request
            return

    def _finished(self, page, size, image, _options, request) -> None:
        job = self._active
        if job is None or request != self._active_request:
            return
        self._active = None
        self._active_request = 0
        if (not self.closed and job.identity in self._desired and page == job.page
                and size == job.pixel_size and not image.isNull()):
            self.rendered.emit(job, image, self.generation)
        self._pump()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._desired.clear()
        self._queue.clear()
        self._active = None
        self._active_request = 0
        self.renderer.setDocument(None)
        self.renderer.deleteLater()
