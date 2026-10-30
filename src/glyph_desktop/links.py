"""Page-local PDF hyperlink hit testing and detached destinations."""

from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtPdf import QPdfLinkModel

from .outline import OutlineDestination


@dataclass(frozen=True)
class PdfLinkTarget:
    generation: int
    source_page: int
    rectangles: tuple
    url: bytes
    page: int
    location: tuple[float, float] | None
    zoom: float | None


class PdfLinks(QObject):
    """Reuse one page model and never retain model-backed link objects."""

    def __init__(self, document, navigate, parent=None):
        super().__init__(parent)
        self.document = document
        self.navigate = navigate
        self.model: QPdfLinkModel | None = None

    def attach(self, pdf):
        if pdf is None:
            self._retire_model()
            return
        if self.model is not None and self.model.document() is pdf:
            return
        self._retire_model()
        self.model = QPdfLinkModel(self, document=pdf)

    def _retire_model(self):
        model, self.model = self.model, None
        if model is not None:
            model.deleteLater()

    def hit(self, page, point):
        if (not self.document.pdf or self.model is None
                or self.model.document() is not self.document.pdf
                or not 0 <= page < self.document.count):
            return None
        if self.model.page() != page:
            self.model.setPage(page)
        link = self.model.linkAt(point)
        # QtPdf reports URI links as invalid because their destination page is
        # -1, even though linkAt supplies their URL and annotation geometry.
        if not link.isValid() and link.url().isEmpty():
            return None
        location = link.location()
        destination_page = link.page()
        y = location.y()
        if not link.url().isEmpty():
            destination = None
        elif 0 <= destination_page < self.document.count:
            # QtPdf converts the destination Y using the source page height.
            # Convert it to the destination page's coordinate system.
            y += (self.document.page_sizes[destination_page][1]
                  - self.document.page_sizes[page][1])
            destination = (location.x(), y)
        else:
            destination = None
        rects = tuple((r.x(), r.y(), r.width(), r.height()) for r in link.rectangles())
        return PdfLinkTarget(self.document.generation, page, rects,
                             bytes(link.url().toEncoded()), destination_page,
                             destination, link.zoom())

    def activate(self, target):
        if (not isinstance(target, PdfLinkTarget) or not self.document.pdf
                or target.generation != self.document.generation):
            return False
        if target.url:
            url = QUrl.fromEncoded(target.url, QUrl.ParsingMode.StrictMode)
            if (not url.isValid() or url.isEmpty() or url.isLocalFile()
                    or url.scheme().lower() not in ("http", "https") or not url.host()):
                return False
            return QDesktopServices.openUrl(url)
        # QtPdf conflates unspecified zoom with an explicit 100% destination.
        zoom = None if target.zoom == 1.0 else target.zoom
        return self.navigate(OutlineDestination(target.generation, target.page,
                                                target.location, zoom))
