"""Convert immutable Glyph annotation records to native pypdf annotations."""

import math

from pypdf.annotations import Highlight, Text
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, RectangleObject,
                           TextStringObject)

from .annotations import Ink, Markup, Note
from .pdf_geometry import GeometryError, PageGeometry


NOTE_RECT_SIZE = 18.0
MARKUP_STROKE_WIDTH = 1.5


def _numbers(values):
    numbers = [float(value) for value in values]
    if not all(math.isfinite(value) for value in numbers):
        raise GeometryError("Annotation geometry must be finite")
    return ArrayObject([FloatObject(value) for value in numbers])


def _color(rgb):
    return _numbers(channel / 255 for channel in rgb)


def _set_common(annotation, item_id, opacity=1.0):
    if not isinstance(item_id, str) or not item_id:
        raise GeometryError("Annotation id must be a non-empty string")
    annotation[NameObject("/NM")] = TextStringObject(item_id)
    annotation[NameObject("/F")] = NumberObject(4)  # visible and printable
    annotation[NameObject("/CA")] = FloatObject(opacity)
    return annotation


def _border(width):
    return DictionaryObject({NameObject("/Type"): NameObject("/Border"),
                             NameObject("/W"): FloatObject(width),
                             NameObject("/S"): NameObject("/S")})


def _pdf_number(value):
    return format(float(value), ".8g")


def _markup_appearance(kind, quads, rect, color, width):
    """Build a constrained vector appearance for supported axis-aligned quads."""
    left, bottom, right, top = rect
    content = ["q", " ".join(_pdf_number(channel / 255) for channel in color)
               + (" rg" if kind == "highlight" else " RG")]
    if kind != "highlight":
        content.append(f"{_pdf_number(width)} w")
    for quad in quads:
        local = tuple((x - left, y - bottom) for x, y in quad)
        if kind == "highlight":
            content.append(f"{_pdf_number(local[0][0])} {_pdf_number(local[0][1])} m")
            content.extend(f"{_pdf_number(x)} {_pdf_number(y)} l" for x, y in local[1:])
            content.append("h f")
        elif kind == "underline":
            content.append(f"{_pdf_number(local[0][0])} {_pdf_number(local[0][1])} m")
            content.append(f"{_pdf_number(local[1][0])} {_pdf_number(local[1][1])} l S")
        else:
            center_y = (local[0][1] + local[2][1]) / 2
            content.append(f"{_pdf_number(local[0][0])} {_pdf_number(center_y)} m")
            content.append(f"{_pdf_number(local[1][0])} {_pdf_number(center_y)} l S")
    content.append("Q")

    appearance = DecodedStreamObject()
    appearance.set_data(("\n".join(content) + "\n").encode("ascii"))
    appearance[NameObject("/Type")] = NameObject("/XObject")
    appearance[NameObject("/Subtype")] = NameObject("/Form")
    appearance[NameObject("/FormType")] = NumberObject(1)
    appearance[NameObject("/BBox")] = RectangleObject((0, 0, right - left, top - bottom))
    appearance[NameObject("/Resources")] = DictionaryObject()
    return DictionaryObject({NameObject("/N"): appearance})


def convert_annotation(item, geometry: PageGeometry):
    """Return one native PDF annotation dictionary for a Glyph record."""
    if isinstance(item, Note) and item.kind == "note":
        x, y = geometry.qt_to_pdf(item.anchor)
        half = NOTE_RECT_SIZE / 2
        annotation = Text(rect=(x - half, y - half, x + half, y + half),
                          text=item.text, flags=4)
        annotation[NameObject("/Name")] = NameObject("/Comment")
        annotation[NameObject("/C")] = _color((151, 121, 40))
        return _set_common(annotation, item.id, 235 / 255)

    if isinstance(item, Markup) and item.kind in ("highlight", "underline", "strikeout"):
        if not item.regions:
            raise GeometryError("Markup annotations must contain at least one region")
        quads = [geometry.qt_quad_to_pdf(region) for region in item.regions]
        flat_quads = [value for quad in quads for point in quad for value in point]
        all_points = [point for quad in quads for point in quad]
        xs, ys = [point[0] for point in all_points], [point[1] for point in all_points]
        rect = (min(xs), min(ys), max(xs), max(ys))
        if item.kind == "highlight":
            annotation = Highlight(rect=rect, quad_points=_numbers(flat_quads),
                                   highlight_color="ffdc28", printing=True)
            opacity = 105 / 255
        else:
            subtype = "/Underline" if item.kind == "underline" else "/StrikeOut"
            annotation = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject(subtype),
                NameObject("/Rect"): _numbers(rect),
                NameObject("/QuadPoints"): _numbers(flat_quads),
                NameObject("/C"): _color((205, 91, 32)),
                NameObject("/BS"): _border(MARKUP_STROKE_WIDTH),
                NameObject("/Contents"): TextStringObject(item.text),
            })
            opacity = 220 / 255
        annotation[NameObject("/Contents")] = TextStringObject(item.text)
        color = (255, 220, 40) if item.kind == "highlight" else (205, 91, 32)
        annotation[NameObject("/AP")] = _markup_appearance(
            item.kind, quads, rect, color,
            0.0 if item.kind == "highlight" else MARKUP_STROKE_WIDTH)
        return _set_common(annotation, item.id, opacity)

    if isinstance(item, Ink) and item.kind == "ink":
        if len(item.points) < 2 or not math.isfinite(item.width) or item.width <= 0:
            raise GeometryError("Ink requires two points and a positive finite width")
        points = [geometry.qt_to_pdf(point) for point in item.points]
        width = item.width
        half = width / 2
        xs, ys = [point[0] for point in points], [point[1] for point in points]
        rect = (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
        annotation = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Ink"),
            NameObject("/Rect"): _numbers(rect),
            NameObject("/InkList"): ArrayObject([_numbers(value for point in points for value in point)]),
            NameObject("/BS"): _border(width),
            NameObject("/C"): _color((24, 32, 44)),
        })
        return _set_common(annotation, item.id)

    raise GeometryError(f"Unsupported Glyph annotation record: {type(item).__name__}/{getattr(item, 'kind', None)}")
