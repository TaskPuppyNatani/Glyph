"""Checked transforms between Glyph's Qt page space and PDF user space."""

from dataclasses import dataclass
import math


class GeometryError(ValueError):
    """The PDF page geometry cannot be mapped to Glyph page coordinates safely."""


def _finite(value, name):
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"{name} must be finite")
    return number


def _box_tuple(box, name):
    values = tuple(_finite(value, name) for value in
                   (box.left, box.bottom, box.right, box.top))
    left, bottom, right, top = values
    if right <= left or top <= bottom:
        raise GeometryError(f"{name} has no positive area")
    return values


@dataclass(frozen=True)
class PageGeometry:
    """Mapping for a pagePointSize-sized Qt page with top-left origin.

    PDF default user space is bottom-left based. QtPdf exposes the visible crop
    box in points after applying /Rotate. QtPdf 6.12 reports pagePointSize
    without scaling it by /UserUnit, so non-default units are rejected.
    """

    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float]
    rotation: int
    user_unit: float
    qt_width: float
    qt_height: float

    @classmethod
    def from_page(cls, page, qt_size):
        media = _box_tuple(page.mediabox, "MediaBox")
        crop = _box_tuple(page.cropbox, "CropBox")
        left = max(media[0], crop[0])
        bottom = max(media[1], crop[1])
        right = min(media[2], crop[2])
        top = min(media[3], crop[3])
        if right <= left or top <= bottom:
            raise GeometryError("MediaBox and CropBox do not overlap")

        rotation_value = _finite(page.get("/Rotate", 0), "Page rotation")
        if not rotation_value.is_integer():
            raise GeometryError(f"Unsupported page rotation: {rotation_value}")
        rotation = int(rotation_value) % 360
        if rotation not in (0, 90, 180, 270):
            raise GeometryError(f"Unsupported page rotation: {rotation}")
        user_unit = _finite(page.get("/UserUnit", 1), "UserUnit")
        if user_unit != 1:
            raise GeometryError(f"Non-default UserUnit is unsupported: {user_unit}")
        width, height = _finite(qt_size[0], "Qt page width"), _finite(qt_size[1], "Qt page height")
        expected_width = top - bottom if rotation in (90, 270) else right - left
        expected_height = right - left if rotation in (90, 270) else top - bottom
        tolerance = max(0.02, max(expected_width, expected_height) * 1e-6)
        if (width <= 0 or height <= 0 or abs(width - expected_width) > tolerance
                or abs(height - expected_height) > tolerance):
            raise GeometryError(
                "Qt page size does not match the effective CropBox and page rotation"
            )
        return cls(media, (left, bottom, right, top), rotation,
                   user_unit, width, height)

    def qt_to_pdf(self, point):
        """Map a Qt point (points, top-left origin) to PDF default user space."""
        x, y = (_finite(value, "Qt point coordinate") for value in point)
        left, bottom, right, top = self.crop_box
        if self.rotation == 0:
            return left + x, top - y
        if self.rotation == 90:
            return left + y, bottom + x
        if self.rotation == 180:
            return right - x, bottom + y
        return right - y, top - x

    def pdf_to_qt(self, point):
        """Map PDF default user space to Qt page points with top-left origin."""
        x, y = (_finite(value, "PDF point coordinate") for value in point)
        left, bottom, right, top = self.crop_box
        if self.rotation == 0:
            return x - left, top - y
        if self.rotation == 90:
            return y - bottom, x - left
        if self.rotation == 180:
            return right - x, y - bottom
        return top - y, right - x

    def qt_rect_to_pdf(self, rect):
        """Map an (x, y, width, height) Qt rectangle to an axis-aligned PDF Rect."""
        x, y, width, height = (_finite(value, "Qt rectangle value") for value in rect)
        points = (self.qt_to_pdf((x, y)), self.qt_to_pdf((x + width, y)),
                  self.qt_to_pdf((x, y + height)), self.qt_to_pdf((x + width, y + height)))
        xs, ys = [point[0] for point in points], [point[1] for point in points]
        return min(xs), min(ys), max(xs), max(ys)

    def qt_quad_to_pdf(self, polygon):
        """Map a Glyph markup rectangle to counterclockwise PDF QuadPoints.

        Glyph selection regions may repeat the first point as a closing vertex.
        Other polygons and non-axis-aligned text quads are rejected because the
        in-memory record does not retain enough text-direction metadata to order
        their PDF QuadPoints without guessing.
        """
        points = [tuple(map(float, point)) for point in polygon]
        if len(points) == 5 and math.dist(points[0], points[-1]) <= 0.02:
            points.pop()
        if len(points) != 4:
            raise GeometryError("Markup regions must be four-corner text quads")
        mapped = [self.qt_to_pdf(point) for point in points]
        xs, ys = [point[0] for point in mapped], [point[1] for point in mapped]
        left, right = min(xs), max(xs)
        bottom, top = min(ys), max(ys)
        tolerance = max(0.02, max(right - left, top - bottom) * 1e-6)
        if right - left <= tolerance or top - bottom <= tolerance:
            raise GeometryError("Markup region has no positive area")
        expected = {(left, bottom), (left, top), (right, bottom), (right, top)}
        matched = [min(expected, key=lambda corner: math.dist(point, corner)) for point in mapped]
        if (any(math.dist(point, corner) > tolerance for point, corner in zip(mapped, matched))
                or len(set(matched)) != 4):
            raise GeometryError("Markup region is not a rectangular text quad")
        return ((left, bottom), (right, bottom), (right, top), (left, top))
