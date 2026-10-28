"""Access to Glyph's separate logo and application icon."""

from importlib.resources import as_file, files

from PySide6.QtGui import QIcon, QPixmap


def logo_path():
    return files("glyph_desktop").joinpath("assets", "logo.png")


def logo_pixmap() -> QPixmap:
    resource = logo_path()
    pixmap = QPixmap()
    if resource.is_file():
        pixmap.loadFromData(resource.read_bytes())
    return pixmap


def icon_path():
    return files("glyph_desktop").joinpath("assets", "icon.ico")


def application_icon() -> QIcon:
    # Load each size while the resource path is available, including when the
    # package is accessed through a non-filesystem resource loader.
    result = QIcon()
    with as_file(icon_path()) as path:
        source = QIcon(str(path))
        for size in source.availableSizes():
            result.addPixmap(source.pixmap(size))
    return result
