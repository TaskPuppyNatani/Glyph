"""Safe internal PDF annotation writer boundary.

QtPdf remains Glyph's base-page renderer with native annotation rendering off.
The current in-memory Glyph overlays are the only visible annotation source in
an open session. A future import/display path must either map native objects to
Glyph overlays while keeping the Qt annotation flag off, or enable native
rendering and suppress matching overlays; enabling both would draw twice.
"""

from dataclasses import dataclass
import ctypes
import errno
import math
import ntpath
import os
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Iterable

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject
from PySide6.QtCore import QFile, QFileDevice, QIODevice
from PySide6.QtPdf import QPdfDocument

from .pdf_annotations import convert_annotation
from .pdf_geometry import PageGeometry


WINDOWS_DURABILITY_WARNING = (
    "The PDF was published and its file contents were flushed, but Windows "
    "does not provide a supported parent-directory flush guarantee here; "
    "crash durability of the destination name is not guaranteed."
)


@dataclass(frozen=True)
class WriteResult:
    success: bool
    output_path: Path | None = None
    error: str | None = None
    published: bool = False
    crash_durable: bool = False
    warning: str | None = None


def _qt_page_sizes(path: Path):
    document = QPdfDocument()
    try:
        error = document.load(str(path))
        if (error != QPdfDocument.Error.None_
                or document.status() != QPdfDocument.Status.Ready
                or document.pageCount() < 1):
            raise ValueError("QtPdf could not open the PDF")
        return tuple((document.pagePointSize(index).width(),
                      document.pagePointSize(index).height())
                     for index in range(document.pageCount()))
    finally:
        document.close()


def _qt_stream_page_sizes(stream):
    """Load from the retained descriptor without taking ownership of it."""
    position = stream.tell()
    device = QFile()
    document = QPdfDocument()
    try:
        if not device.open(stream.fileno(),
                           QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered,
                           QFileDevice.FileHandleFlag.DontCloseHandle):
            raise ValueError(f"QtPdf could not access the staged PDF: {device.errorString()}")
        if not device.seek(0):
            raise ValueError("QtPdf could not seek the staged PDF")
        document.load(device)
        if (document.error() != QPdfDocument.Error.None_
                or document.status() != QPdfDocument.Status.Ready
                or document.pageCount() < 1):
            raise ValueError("QtPdf could not open the staged PDF")
        return tuple((document.pagePointSize(index).width(),
                      document.pagePointSize(index).height())
                     for index in range(document.pageCount()))
    finally:
        document.close()
        device.close()
        stream.seek(position)


def _contains_signature_marker(reader: PdfReader) -> bool:
    """Detect standard signature dictionaries/fields reachable from the catalog."""
    stack = [reader.trailer.get("/Root")]
    references = set()
    direct_objects = set()
    while stack:
        value = stack.pop()
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in references:
                continue
            references.add(key)
            value = value.get_object()
        if isinstance(value, (DictionaryObject, ArrayObject)):
            identity = id(value)
            if identity in direct_objects:
                continue
            direct_objects.add(identity)
        if isinstance(value, DictionaryObject):
            if ("/ByteRange" in value
                    or str(value.get("/Type")) in ("/Sig", "/DocTimeStamp")
                    or str(value.get("/FT")) == "/Sig"):
                return True
            stack.extend(value.values())
        elif isinstance(value, ArrayObject):
            stack.extend(value)
    return False


def _windows_api():
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.restype = wintypes.HANDLE
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    return kernel


def _create_private_stage(parent: Path, prefix: str) -> Path:
    """Create a same-volume staging directory inaccessible to other users."""
    if os.name != "nt":
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        os.chmod(path, 0o700)
        return path

    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    kernel = _windows_api()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.c_void_p()
    sid_string = wintypes.LPWSTR()
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        token_buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, token_buffer, needed,
                                          ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.cast(token_buffer, ctypes.POINTER(TokenUser)).contents
        if not advapi.ConvertSidToStringSidW(user.User.Sid, ctypes.byref(sid_string)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = (f"D:P(A;OICI;FA;;;{sid_string.value})"
                "(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        for _ in range(20):
            path = parent / f"{prefix}{secrets.token_hex(12)}"
            if kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                return path
            code = ctypes.get_last_error()
            if code != 183:  # ERROR_ALREADY_EXISTS
                raise ctypes.WinError(code)
        raise FileExistsError("Could not allocate a unique private staging directory")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_string:
            kernel.LocalFree(ctypes.cast(sid_string, wintypes.HANDLE))
        kernel.CloseHandle(token)


def _create_windows_stage_fd(path: Path) -> int:
    """Create and pin the staging file with write/delete sharing denied."""
    from ctypes import wintypes
    import msvcrt

    kernel = _windows_api()
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x40000000 | 0x00010000,
                               0x00000001, None, 1, 0x00000080, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise


class _WindowsRenameInfo(ctypes.Structure):
    _fields_ = [("ReplaceIfExists", ctypes.c_ubyte),
                ("RootDirectory", ctypes.c_void_p),
                ("FileNameLength", ctypes.c_uint32),
                ("FileName", ctypes.c_uint16 * 1)]


def _windows_rename_buffer(destination_name: str):
    drive, _ = ntpath.splitdrive(destination_name)
    if not ntpath.isabs(destination_name) or not drive:
        raise ValueError("Windows destination must be an absolute path")
    if drive.startswith("\\\\") and not (drive.startswith("\\\\?\\")
                                              and len(drive) == 6 and drive[5] == ":"):
        raise ValueError("Windows network or device destination paths are unsupported")
    encoded = destination_name.encode("utf-16-le")
    buffer_size = max(ctypes.sizeof(_WindowsRenameInfo),
                      _WindowsRenameInfo.FileName.offset + len(encoded))
    buffer = ctypes.create_string_buffer(buffer_size)
    info = ctypes.cast(buffer, ctypes.POINTER(_WindowsRenameInfo)).contents
    info.ReplaceIfExists = 0
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + _WindowsRenameInfo.FileName.offset,
                   encoded, len(encoded))
    return buffer


def _windows_publish_by_handle(file_handle, destination_name: str):
    """Atomically rename the validated handle without replacing a target."""
    from ctypes import wintypes

    kernel = _windows_api()

    buffer = _windows_rename_buffer(destination_name)
    if not kernel.SetFileInformationByHandle(
            wintypes.HANDLE(file_handle), 3, ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.sizeof(buffer)):
        code = ctypes.get_last_error()
        if code in (80, 183):  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
            raise FileExistsError("Destination already exists; it was not overwritten")
        raise ctypes.WinError(code)


def _open_posix_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags)


def _link_fd_noclobber(file_descriptor: int, stage_name: str, stage_fd: int,
                       destination_name: str, destination_fd: int) -> None:
    """Publish the validated inode on Linux without resolving a replaceable path."""
    if not sys.platform.startswith("linux"):
        raise OSError("Safe descriptor-based no-overwrite publication is supported on Linux only")

    libc = ctypes.CDLL(None, use_errno=True)
    linkat = libc.linkat
    linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_int]
    linkat.restype = ctypes.c_int
    result = linkat(file_descriptor, b"", destination_fd,
                    os.fsencode(destination_name), 0x1000)  # AT_EMPTY_PATH
    if result == 0:
        return
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError("Destination already exists; it was not overwritten")
    if code in (errno.EPERM, errno.EINVAL, errno.ENOSYS):
        # AT_EMPTY_PATH may be unavailable to an unprivileged process. The procfs
        # magic link still names the open file description rather than its path.
        result = linkat(-100, os.fsencode(f"/proc/self/fd/{file_descriptor}"),
                        destination_fd, os.fsencode(destination_name), 0x400)
        if result == 0:
            return
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileExistsError("Destination already exists; it was not overwritten")
    if code == errno.ENOENT:
        raise OSError(code,
                      "Validated staging file is no longer linkable; refusing to publish a replacement",
                      destination_name)
    raise OSError(code, os.strerror(code), destination_name)


def _page_box(page, name):
    box = page.mediabox if name == "/MediaBox" else page.cropbox
    return tuple(float(value) for value in (box.left, box.bottom, box.right, box.top))


def _annotation_record(page_number, annotation):
    record = {"page": page_number, "subtype": str(annotation.get("/Subtype"))}
    record["rect"] = tuple(float(value) for value in annotation["/Rect"])
    if "/Contents" in annotation:
        record["contents"] = str(annotation["/Contents"])
    if "/QuadPoints" in annotation:
        record["quad_points"] = tuple(float(value) for value in annotation["/QuadPoints"])
    if "/InkList" in annotation:
        record["ink_list"] = tuple(tuple(float(value) for value in stroke)
                                   for stroke in annotation["/InkList"])
    if "/BS" in annotation:
        record["border_width"] = float(annotation["/BS"].get("/W", 0))
    appearance = annotation.get("/AP")
    if appearance is not None:
        normal = appearance.get("/N")
        if normal is not None:
            normal = normal.get_object()
            record["appearance_bbox"] = tuple(float(value) for value in normal["/BBox"])
    return record


def _same_numbers(actual, expected):
    return (len(actual) == len(expected)
            and all(math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-7)
                    for left, right in zip(actual, expected)))


def _matches_annotation(actual, expected):
    if actual["page"] != expected["page"] or actual["subtype"] != expected["subtype"]:
        return False
    for key in ("rect", "quad_points", "appearance_bbox"):
        if key in expected and (key not in actual or not _same_numbers(actual[key], expected[key])):
            return False
    if "ink_list" in expected:
        if ("ink_list" not in actual or len(actual["ink_list"]) != len(expected["ink_list"])
                or any(not _same_numbers(a, b) for a, b in
                       zip(actual["ink_list"], expected["ink_list"]))):
            return False
    if "contents" in expected and actual.get("contents") != expected["contents"]:
        return False
    if "border_width" in expected and not math.isclose(
            actual.get("border_width", -1), expected["border_width"], rel_tol=1e-9, abs_tol=1e-7):
        return False
    return True


def _validate_output(source_reader, stream, expected):
    stream.seek(0)
    output_reader = PdfReader(stream, strict=True)
    if len(output_reader.pages) != len(source_reader.pages):
        raise ValueError("Written PDF page count changed")
    for index, (source_page, output_page) in enumerate(zip(source_reader.pages, output_reader.pages)):
        for name in ("/MediaBox", "/CropBox"):
            if _page_box(source_page, name) != _page_box(output_page, name):
                raise ValueError(f"Written PDF page {index + 1} changed {name}")
        if int(source_page.rotation or 0) % 360 != int(output_page.rotation or 0) % 360:
            raise ValueError(f"Written PDF page {index + 1} changed rotation")

    found = {}
    for page_number, page in enumerate(output_reader.pages):
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            name = annotation.get("/NM")
            if name in expected:
                key = str(name)
                found.setdefault(key, []).append(_annotation_record(page_number, annotation))
    if set(found) != set(expected):
        raise ValueError("Written PDF is missing one or more new annotations")
    for item_id, record in expected.items():
        if len(found[item_id]) != 1 or not _matches_annotation(found[item_id][0], record):
            raise ValueError(f"Written PDF annotation {item_id} is misplaced or changed")

    qt_sizes = _qt_stream_page_sizes(stream)
    if len(qt_sizes) != len(output_reader.pages):
        raise ValueError("QtPdf reported a different page count for the written PDF")
    for page, size in zip(output_reader.pages, qt_sizes):
        PageGeometry.from_page(page, size)


def _cleanup_staged_file(path: Path | None):
    if path is not None:
        path.unlink(missing_ok=True)


def _cleanup_stage_directory(path: Path | None):
    if path is not None:
        path.rmdir()


def write_annotated_pdf(source_path, destination_path,
                        annotations: Iterable) -> WriteResult:
    """Write and validate annotations, then publish without replacing a target.

    Linux publication links the retained validated file descriptor and fsyncs
    the destination directory. Windows publication renames the retained file
    handle with ReplaceIfExists disabled; file contents are flushed, while
    parent-directory crash durability is explicitly not guaranteed.
    """
    stage_directory = temporary = destination = None
    stream = None
    unowned_descriptor = None
    stage_fd = destination_fd = None
    error_text = None
    cleanup_errors = []
    published = crash_durable = False
    warning = None

    try:
        source = Path(source_path).expanduser()
        requested_destination = Path(destination_path).expanduser()
        if not source.is_file():
            raise ValueError("Source PDF does not exist or is not a regular file")
        source_resolved = source.resolve(strict=True)
        if not requested_destination.name or requested_destination.name in (".", ".."):
            raise ValueError("Destination must name a file")
        if not requested_destination.parent.is_dir():
            raise ValueError("Destination directory does not exist")
        destination_parent = requested_destination.parent.resolve(strict=True)
        destination = destination_parent / requested_destination.name
        if source_resolved == destination.resolve(strict=False):
            raise ValueError("Source and destination paths must be different")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Destination already exists; it will not be overwritten")
        if os.name != "nt" and not sys.platform.startswith("linux"):
            raise OSError("Safe descriptor-based publication is not supported on this platform")

        snapshot = tuple(annotations)
        generations = {item.generation for item in snapshot}
        if len(generations) > 1:
            raise ValueError("Annotation snapshot mixes document generations")
        reader = PdfReader(source_resolved, strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported by annotation persistence")
        if _contains_signature_marker(reader):
            raise ValueError("Signed PDFs are not supported by annotation persistence")
        qt_sizes = _qt_page_sizes(source_resolved)
        if len(qt_sizes) != len(reader.pages):
            raise ValueError("QtPdf and pypdf reported different page counts")

        writer = PdfWriter(clone_from=reader)
        expected = {}
        for item in snapshot:
            if (not isinstance(item.page, int) or isinstance(item.page, bool)
                    or not 0 <= item.page < len(reader.pages)):
                raise ValueError("Annotation page is outside the source PDF")
            if item.id in expected:
                raise ValueError("Annotation snapshot contains duplicate ids")
            geometry = PageGeometry.from_page(reader.pages[item.page], qt_sizes[item.page])
            annotation = convert_annotation(item, geometry)
            appearance = annotation.get("/AP")
            if appearance is not None:
                normal = appearance.get("/N")
                if normal is not None and not isinstance(normal, IndirectObject):
                    appearance[NameObject("/N")] = writer._add_object(normal)
            record = _annotation_record(item.page, annotation)
            writer.add_annotation(item.page, annotation)
            expected[item.id] = record

        stage_directory = _create_private_stage(
            destination_parent, f".{destination.name}.glyph-stage-")
        temporary = stage_directory / "validated-output.tmp"
        if os.name == "nt":
            unowned_descriptor = _create_windows_stage_fd(temporary)
        else:
            unowned_descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR
                                         | getattr(os, "O_CLOEXEC", 0), 0o600)
            stage_fd = _open_posix_directory(stage_directory)
            destination_fd = _open_posix_directory(destination_parent)

        # Qt and pypdf share this descriptor sequentially; avoid buffered
        # file-position state while retaining the publication handle.
        stream = os.fdopen(unowned_descriptor, "w+b", buffering=0)
        unowned_descriptor = None
        writer.write(stream)
        stream.flush()
        os.fsync(stream.fileno())
        _validate_output(reader, stream, expected)

        if os.name == "nt":
            import msvcrt
            _windows_publish_by_handle(msvcrt.get_osfhandle(stream.fileno()), str(destination))
            published = True
            warning = WINDOWS_DURABILITY_WARNING
        else:
            _link_fd_noclobber(stream.fileno(), temporary.name, stage_fd,
                               destination.name, destination_fd)
            published = True
            os.fsync(destination_fd)
            crash_durable = True
    except Exception as error:
        error_text = f"{type(error).__name__}: {error}"

    if unowned_descriptor is not None:
        try:
            os.close(unowned_descriptor)
        except Exception as error:
            cleanup_errors.append(f"close staged file: {type(error).__name__}: {error}")
    if stream is not None:
        try:
            stream.close()
        except Exception as error:
            cleanup_errors.append(f"close staged output: {type(error).__name__}: {error}")
    for label, descriptor in (("staging directory", stage_fd),
                              ("destination directory", destination_fd)):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception as error:
                cleanup_errors.append(f"close {label}: {type(error).__name__}: {error}")
    try:
        _cleanup_staged_file(temporary)
    except Exception as error:
        cleanup_errors.append(f"remove staged output: {type(error).__name__}: {error}")
    try:
        _cleanup_stage_directory(stage_directory)
    except Exception as error:
        cleanup_errors.append(f"remove staging directory: {type(error).__name__}: {error}")

    if cleanup_errors:
        cleanup_text = "Cleanup failed: " + "; ".join(cleanup_errors)
        error_text = f"{error_text}; {cleanup_text}" if error_text else cleanup_text
    success = error_text is None
    return WriteResult(success, destination if published else None, error_text,
                       published, crash_durable, warning)
