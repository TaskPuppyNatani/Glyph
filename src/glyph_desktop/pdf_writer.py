"""Safe internal PDF annotation writer boundary.

The reader renders supported native PDF annotations with QtPdf. Glyph's
editable session annotations remain separate overlay records; native PDF
annotations are not imported into that editable collection.
"""

from dataclasses import dataclass
from collections import Counter
from decimal import Decimal
import ctypes
import errno
import hashlib
import ntpath
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
from typing import Iterable

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, ByteStringObject, DictionaryObject,
                           FloatObject, IndirectObject, NameObject, NullObject, NumberObject,
                           StreamObject, TextStringObject)
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


@dataclass(frozen=True)
class SourceSnapshot:
    """Identity, metadata and content observed for a source PDF."""
    path: Path
    state: tuple
    digest: bytes


def capture_source_snapshot(path) -> SourceSnapshot:
    """Capture a stable observation; reject links and files changing during reading."""
    path = Path(path).expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    before_path = os.lstat(path)
    if not stat.S_ISREG(before_path.st_mode) or not before_path.st_ino:
        raise ValueError("Source identity cannot be verified as a regular file")
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not before.st_ino:
            raise ValueError("Source identity cannot be verified as a regular file")
        def state(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if state(before_path) != state(before):
            raise ValueError("Source changed while opening it")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        after_path = os.lstat(path)
        if state(before) != state(after) or state(before) != state(after_path):
            raise ValueError("Source changed while reading it")
        return SourceSnapshot(path, state(before), digest.digest())
    finally:
        os.close(descriptor)


def verify_source_snapshot(snapshot: SourceSnapshot) -> None:
    """Fail if a fresh stable observation differs from the captured source."""
    if not isinstance(snapshot, SourceSnapshot):
        raise TypeError("A source snapshot is required")
    if capture_source_snapshot(snapshot.path) != snapshot:
        raise ValueError("Source PDF has changed since it was captured")


@dataclass
class PreparedReplacement:
    """One-use, confirmed existing destination held through publication."""
    destination: Path
    source_identity: tuple
    destination_identity: tuple
    destination_state: tuple | None = None
    parent_identity: tuple | None = None
    parent_fd: int | None = None
    target_fd: int | None = None
    target_handle: int | None = None
    _closed: bool = False

    def close(self):
        if self._closed:
            return
        errors = []
        for attribute in ("target_fd", "parent_fd"):
            descriptor = getattr(self, attribute)
            if descriptor is not None:
                setattr(self, attribute, None)
                try:
                    os.close(descriptor)
                except Exception as error:
                    errors.append(error)
        if self.target_handle is not None:
            handle = self.target_handle
            self.target_handle = None
            if not _windows_api().CloseHandle(handle):
                errors.append(ctypes.WinError(ctypes.get_last_error()))
        self._closed = True
        if errors:
            raise OSError("Could not close prepared replacement handle: "
                          + "; ".join(str(error) for error in errors))


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
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
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


class _WindowsRenameInfoEx(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32),
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
                      _WindowsRenameInfo.FileName.offset + len(encoded) + 2)
    buffer = ctypes.create_string_buffer(buffer_size)
    info = ctypes.cast(buffer, ctypes.POINTER(_WindowsRenameInfo)).contents
    info.ReplaceIfExists = 0
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + _WindowsRenameInfo.FileName.offset,
                   encoded, len(encoded))
    return buffer


def _windows_replace_buffer(destination_name: str):
    drive, _ = ntpath.splitdrive(destination_name)
    if not ntpath.isabs(destination_name) or not drive:
        raise ValueError("Windows destination must be an absolute path")
    if drive.startswith("\\\\") and not (drive.startswith("\\\\?\\")
                                              and len(drive) == 6 and drive[5] == ":"):
        raise ValueError("Windows network or device destination paths are unsupported")
    encoded = destination_name.encode("utf-16-le")
    buffer_size = max(ctypes.sizeof(_WindowsRenameInfoEx),
                      _WindowsRenameInfoEx.FileName.offset + len(encoded) + 2)
    buffer = ctypes.create_string_buffer(buffer_size)
    info = ctypes.cast(buffer, ctypes.POINTER(_WindowsRenameInfoEx)).contents
    info.Flags = 0x1 | 0x2  # FILE_RENAME_REPLACE_IF_EXISTS | POSIX_SEMANTICS
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + _WindowsRenameInfoEx.FileName.offset,
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


def _windows_replace_by_handle(file_handle, destination_name: str):
    """Replace a locked, confirmed target with the validated handle."""
    from ctypes import wintypes

    kernel = _windows_api()
    buffer = _windows_replace_buffer(destination_name)
    if not kernel.SetFileInformationByHandle(
            wintypes.HANDLE(file_handle), 22, ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.sizeof(buffer)):
        raise ctypes.WinError(ctypes.get_last_error())


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "attributes", "creation_low", "creation_high", "access_low", "access_high",
        "write_low", "write_high", "volume_serial", "size_high", "size_low",
        "links", "index_high", "index_low")]


class _WindowsFileId(ctypes.Structure):
    _fields_ = [("volume_serial", ctypes.c_uint64), ("file_id", ctypes.c_ubyte * 16)]


def _windows_file_identity(file_handle):
    from ctypes import wintypes

    info = _WindowsFileId()
    if not _windows_api().GetFileInformationByHandleEx(
            wintypes.HANDLE(file_handle), 18, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.volume_serial, bytes(info.file_id)


def _windows_path_identity(path, *, share_mode=0x7, open_reparse=False):
    from ctypes import wintypes

    kernel = _windows_api()
    flags = 0x00200000 if open_reparse else 0
    handle = kernel.CreateFileW(str(path), 0x80, share_mode, None, 3, flags, None)
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3):
            return None, None
        raise ctypes.WinError(code)
    try:
        attributes = _WindowsFileInformation()
        if not kernel.GetFileInformationByHandle(wintypes.HANDLE(handle),
                                                  ctypes.byref(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _windows_file_identity(handle), attributes.attributes
    finally:
        kernel.CloseHandle(wintypes.HANDLE(handle))


def _open_windows_replacement_target(path):
    from ctypes import wintypes

    kernel = _windows_api()
    handle = kernel.CreateFileW(str(path), 0x80, 0x1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3):
            return None
        raise ctypes.WinError(code)
    try:
        attributes = _WindowsFileInformation()
        if not kernel.GetFileInformationByHandle(wintypes.HANDLE(handle),
                                                  ctypes.byref(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes.attributes & (0x10 | 0x400):
            raise ValueError("Replacement target must be a regular file, not a directory or reparse point")
        return handle, _windows_file_identity(handle)
    except Exception:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise


def _windows_verify_published_file(file_handle, destination_name: str):
    """Compare the named entry with the still-open validated file handle."""
    from ctypes import wintypes

    kernel = _windows_api()
    target = kernel.CreateFileW(destination_name, 0x80, 0x7, None, 3, 0x00200000, None)
    if target == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attrs = _WindowsFileInformation()
        if not kernel.GetFileInformationByHandle(wintypes.HANDLE(target), ctypes.byref(attrs)):
            raise ctypes.WinError(ctypes.get_last_error())
        expected, actual = (_windows_file_identity(handle) for handle in (file_handle, target))
        if (attrs.attributes & 0x400 or not any(expected[1]) or actual != expected):
            raise ValueError("Intended destination does not identify the validated file")
    finally:
        kernel.CloseHandle(target)


def _open_posix_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags)


def _posix_identity(info):
    return info.st_dev, info.st_ino


def _posix_state(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def prepare_replacement_target(source_path, destination_path) -> PreparedReplacement | None:
    """Pin an existing regular destination before asking the user to replace it.

    The returned one-use token must be passed to ``write_annotated_pdf`` only
    after explicit user confirmation. A missing destination returns ``None``;
    the normal writer path will then retain its no-overwrite behavior.
    """
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

    if os.name == "nt":
        source_identity, _ = _windows_path_identity(source_resolved)
        if source_identity is None:
            raise ValueError("Source PDF is no longer available")
        target = _open_windows_replacement_target(destination)
        if target is None:
            return None
        handle, destination_identity = target
        if source_identity == destination_identity:
            _windows_api().CloseHandle(handle)
            raise ValueError("Destination identifies the open source PDF and cannot be replaced")
        return PreparedReplacement(destination, source_identity, destination_identity,
                                  target_handle=handle)

    if not sys.platform.startswith("linux"):
        raise OSError("Safe destination replacement is supported on Linux and Windows only")

    source_info = os.stat(source_resolved)
    parent_fd = _open_posix_directory(destination_parent)
    target_fd = None
    try:
        path_flags = getattr(os, "O_PATH", 0) | getattr(os, "O_NOFOLLOW", 0)
        path_flags |= getattr(os, "O_CLOEXEC", 0)
        if not getattr(os, "O_PATH", 0):
            raise OSError(errno.ENOTSUP, "O_PATH is required to pin a replacement target")
        try:
            target_fd = os.open(destination.name, path_flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        target_info = os.fstat(target_fd)
        if not stat.S_ISREG(target_info.st_mode):
            raise ValueError("Replacement target must be a regular file, not a directory or symlink")
        if _posix_identity(source_info) == _posix_identity(target_info):
            raise ValueError("Destination identifies the open source PDF and cannot be replaced")
        parent_identity = _posix_identity(os.fstat(parent_fd))
        prepared = PreparedReplacement(
            destination, _posix_identity(source_info), _posix_identity(target_info),
            destination_state=_posix_state(target_info), parent_identity=parent_identity,
            parent_fd=parent_fd, target_fd=target_fd)
        parent_fd = target_fd = None
        return prepared
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _verify_prepared_replacement(source, destination, prepared: PreparedReplacement):
    if (not isinstance(prepared, PreparedReplacement) or prepared._closed
            or prepared.destination != destination):
        raise ValueError("Prepared replacement does not match this destination")
    if os.name == "nt":
        source_identity, _ = _windows_path_identity(source)
        target_identity, attributes = _windows_path_identity(destination, open_reparse=True)
        if (attributes is None or attributes & (0x10 | 0x400)
                or source_identity != prepared.source_identity
                or target_identity != prepared.destination_identity
                or _windows_file_identity(prepared.target_handle) != prepared.destination_identity):
            raise FileExistsError("Destination changed since replacement was confirmed")
        return

    source_info = os.stat(source)
    parent_info = os.fstat(prepared.parent_fd)
    target_info = os.fstat(prepared.target_fd)
    try:
        path_info = os.stat(destination.name, dir_fd=prepared.parent_fd,
                            follow_symlinks=False)
    except FileNotFoundError as error:
        raise FileExistsError("Destination changed since replacement was confirmed") from error
    if (not stat.S_ISREG(target_info.st_mode) or not stat.S_ISREG(path_info.st_mode)
            or _posix_identity(source_info) != prepared.source_identity
            or _posix_identity(parent_info) != prepared.parent_identity
            or _posix_state(target_info) != prepared.destination_state
            or _posix_state(path_info) != prepared.destination_state):
        raise FileExistsError("Destination changed since replacement was confirmed")


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


def _rename_exchange(source_fd: int, source_name: str,
                     destination_fd: int, destination_name: str):
    """Atomically exchange two entries; unsupported filesystems fail closed."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable; refusing unsafe replacement")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(source_fd, os.fsencode(source_name), destination_fd,
                 os.fsencode(destination_name), 0x2) != 0:  # RENAME_EXCHANGE
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination_name)


def _linux_replace_by_exchange(file_descriptor, temporary_name, stage_fd,
                               destination_name, destination_fd, prepared,
                               source, temporary_path, publication_state):
    _verify_prepared_replacement(source, prepared.destination, prepared)
    _rename_exchange(stage_fd, temporary_name, destination_fd, destination_name)
    publication_state.update(published=True, destination_verified=False,
                             preserve_stage=True)
    changed = False
    try:
        displaced = os.stat(temporary_name, dir_fd=stage_fd, follow_symlinks=False)
        current = os.stat(destination_name, dir_fd=destination_fd, follow_symlinks=False)
        validated = os.fstat(file_descriptor)
        displaced_state = _posix_state(displaced)
        # RENAME_EXCHANGE updates the displaced inode's ctime on Linux, so
        # compare its stable identity and content timestamps here. The full
        # state, including ctime, was checked immediately before publication.
        changed = (_posix_identity(displaced) != prepared.destination_identity
                   or displaced_state[2:4] != prepared.destination_state[2:4]
                   or not stat.S_ISREG(current.st_mode)
                   or _posix_identity(current) != _posix_identity(validated))
        if changed:
            raise FileExistsError("Destination changed after replacement was confirmed")
    except Exception as error:
        try:
            _rename_exchange(stage_fd, temporary_name, destination_fd, destination_name)
        except Exception as rollback_error:
            try:
                current = os.stat(destination_name, dir_fd=destination_fd,
                                  follow_symlinks=False)
                publication_state["destination_verified"] = (
                    stat.S_ISREG(current.st_mode)
                    and _posix_identity(current) == _posix_identity(os.fstat(file_descriptor)))
            except Exception:
                publication_state["destination_verified"] = False
            raise RuntimeError(
                "Replacement state is ambiguous; the previous destination and validated "
                f"output are retained in {temporary_path.parent}: "
                f"{rollback_error}") from error
        publication_state.update(published=False, destination_verified=False,
                                 preserve_stage=False)
        try:
            os.fsync(destination_fd)
            os.fsync(stage_fd)
        except Exception as sync_error:
            raise RuntimeError(
                "The previous destination was restored, but rollback directory sync failed: "
                f"{sync_error}") from error
        if changed:
            raise FileExistsError(
                "Destination changed after replacement was confirmed; the previous entry was restored")
        raise RuntimeError(f"Replacement verification failed; the previous entry was restored: {error}") from error
    publication_state.update(published=True, destination_verified=True,
                             preserve_stage=False)


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


def _same_number(actual, expected):
    try:
        # Match the number pypdf itself writes. FloatObject deliberately limits
        # decimal precision, so comparing the reopened value to the original
        # binary float can reject valid annotation geometry.
        serialized_expected = float(FloatObject(expected).myrepr())
        return float(actual) == serialized_expected
    except (TypeError, ValueError, OverflowError):
        return False


def _same_numbers(actual, expected):
    return (len(actual) == len(expected)
            and all(_same_number(left, right) for left, right in zip(actual, expected)))


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
    if "border_width" in expected and not _same_number(
            actual.get("border_width", -1), expected["border_width"]):
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


def _canonical_annotation_value(value, active=None):
    """Compare supported object graphs by content, not indirect object numbers."""
    if active is None:
        active = set()
    if isinstance(value, IndirectObject):
        value = value.get_object()
    if isinstance(value, (DictionaryObject, ArrayObject)):
        identity = id(value)
        if identity in active:
            raise ValueError("Cyclic annotation structure cannot be verified")
        active.add(identity)
        try:
            if isinstance(value, ArrayObject):
                return ("array", tuple(_canonical_annotation_value(item, active) for item in value))
            is_stream = isinstance(value, StreamObject)
            ignored = {"/Length", "/Filter", "/DecodeParms"} if is_stream else set()
            entries = tuple(sorted((str(key), _canonical_annotation_value(item, active))
                                   for key, item in value.items() if str(key) not in ignored))
            if is_stream:
                return ("stream", entries, value.get_data())
            return ("dict", entries)
        finally:
            active.remove(identity)
    if isinstance(value, NameObject):
        return ("name", str(value))
    if isinstance(value, TextStringObject):
        return ("text", str(value))
    if isinstance(value, ByteStringObject):
        return ("bytes", bytes(value))
    if isinstance(value, BooleanObject):
        return ("bool", bool(value.value))
    if isinstance(value, NullObject):
        return ("null",)
    if isinstance(value, (FloatObject, NumberObject)):
        number = Decimal(value.myrepr() if isinstance(value, FloatObject) else str(value))
        if not number.is_finite():
            raise ValueError("Non-finite annotation number cannot be verified")
        return ("number", number)
    raise ValueError(f"Unsupported annotation object: {type(value).__name__}")


def _canonical_page_annotation(annotation, page):
    if not isinstance(annotation, DictionaryObject):
        raise ValueError("Invalid inherited annotation")
    if "/P" in annotation:
        page_ref = annotation.raw_get("/P")
        if (not isinstance(page_ref, IndirectObject)
                or page_ref != page.indirect_reference):
            raise ValueError("Annotation page reference cannot be verified")
    return ("annotation", "/P" in annotation, tuple(sorted(
        (str(key), _canonical_annotation_value(value))
        for key, value in annotation.items() if str(key) != "/P")))


def _inherited_annotations(reader):
    pages = []
    supported = {"/Ink", "/Text", "/Highlight", "/Underline", "/StrikeOut"}
    for page in reader.pages:
        records = []
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            if not isinstance(annotation, DictionaryObject) or str(annotation.get("/Subtype")) not in supported:
                raise ValueError("Unsupported inherited annotation cannot be verified")
            records.append(_canonical_page_annotation(annotation, page))
        pages.append(Counter(records))
    return pages


def _validate_inherited_annotations(source, stream, expected):
    before = _inherited_annotations(source)
    stream.seek(0)
    output = PdfReader(stream, strict=True)
    for page_number, page in enumerate(output.pages):
        annotations = list(page.get("/Annots", []))
        inherited_count = sum(before[page_number].values())
        added_count = sum(record["page"] == page_number for record in expected.values())
        if len(annotations) != inherited_count + added_count:
            raise ValueError("Inherited annotations are missing or duplicated")
        records = []
        for reference in annotations[:inherited_count]:
            annotation = reference.get_object()
            records.append(_canonical_page_annotation(annotation, page))
        if Counter(records) != before[page_number]:
            raise ValueError("Inherited annotations are altered or relocated")


def _build_annotated_pdf(source_resolved, annotations):
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
        expected[item.id] = _annotation_record(item.page, annotation)
        writer.add_annotation(item.page, annotation)
    return reader, writer, expected


def _stream_digest(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            return digest.digest()
        digest.update(block)


def _write_validated_pdf(reader, writer, expected, stream, *, capture_digest=False):
    writer.write(stream)
    stream.flush()
    os.fsync(stream.fileno())
    digest = _stream_digest(stream) if capture_digest else None
    _validate_output(reader, stream, expected)
    return digest


class PreparedPdfOutput:
    """Own a validated, unpublished PDF until explicitly discarded."""

    def __init__(self, directory, path, stream):
        self.directory = directory
        self.path = path
        self._stream = stream
        self.source_snapshot = None
        self.validated_digest = None
        self._publication_attempted = False

    def read_bytes(self):
        """Read through the retained handle, including on Windows."""
        if self._stream is None:
            raise ValueError("Prepared PDF has been discarded")
        position = self._stream.tell()
        try:
            self._stream.seek(0)
            return self._stream.read()
        finally:
            self._stream.seek(position)

    def discard(self):
        # Keep resource references on failure so a subsequent call can retry.
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self.path is not None:
            _cleanup_staged_file(self.path)
            self.path = None
        if self.directory is not None:
            _cleanup_stage_directory(self.directory)
            self.directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.discard()


def stage_annotated_pdf(source_path, annotations: Iterable, *,
                        validate_inherited: bool = False,
                        source_snapshot: SourceSnapshot | None = None) -> PreparedPdfOutput:
    """Write and validate a private temporary PDF beside the unchanged source.

    The caller owns the returned object and must discard it when finished.
    This function provides no publication or in-place replacement operation.
    """
    source = Path(source_path).expanduser()
    if not source.is_file():
        raise ValueError("Source PDF does not exist or is not a regular file")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        raise OSError("Safe descriptor-based staging is not supported on this platform")
    source = source.resolve(strict=True)
    if source_snapshot is not None:
        if not validate_inherited:
            raise ValueError("Source-bound staging requires inherited annotation validation")
        if not isinstance(source_snapshot, SourceSnapshot) or source_snapshot.path.resolve(strict=True) != source:
            raise ValueError("Source snapshot does not match the staged source")
        verify_source_snapshot(source_snapshot)
    reader, writer, expected = _build_annotated_pdf(source, annotations)
    directory = _create_private_stage(source.parent, f".{source.name}.glyph-stage-")
    path = directory / "validated-output.tmp"
    prepared = PreparedPdfOutput(directory, None, None)
    descriptor = None
    try:
        if os.name == "nt":
            descriptor = _create_windows_stage_fd(path)
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR
                                 | getattr(os, "O_CLOEXEC", 0), 0o600)
        prepared.path = path
        prepared._stream = os.fdopen(descriptor, "w+b", buffering=0)
        descriptor = None
        digest = _write_validated_pdf(reader, writer, expected, prepared._stream,
                                      capture_digest=source_snapshot is not None)
        if validate_inherited:
            _validate_inherited_annotations(reader, prepared._stream, expected)
        if source_snapshot is not None:
            verify_source_snapshot(source_snapshot)
            if _stream_digest(prepared._stream) != digest:
                raise ValueError("Staged PDF changed during validation")
            prepared.validated_digest = digest
            prepared.source_snapshot = source_snapshot
        return prepared
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        prepared.discard()
        raise


def _digest_fd(descriptor):
    before = os.fstat(descriptor)
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    if _posix_state(before) != _posix_state(os.fstat(descriptor)):
        raise ValueError("File changed while hashing")
    return digest.digest()


def _verify_displaced_original(descriptor, snapshot):
    # Exchange changes ctime. Identity, size, mtime and the content digest
    # remain comparable; _digest_fd also checks stability during the read.
    state = _posix_state(os.fstat(descriptor))
    if state[:4] != snapshot.state[:4] or _digest_fd(descriptor) != snapshot.digest:
        raise ValueError("Displaced source differs from the original snapshot")


def publish_prepared_pdf_linux(prepared: PreparedPdfOutput) -> WriteResult:
    """One-use Linux exchange of a snapshot-bound validated output.

    Same-user writers can still race pathname checks or change files after the
    checks. Ambiguous recovery leaves the staging directory as evidence.
    """
    if not isinstance(prepared, PreparedPdfOutput):
        return WriteResult(False, error="A prepared PDF output is required")
    if prepared._publication_attempted or prepared._stream is None:
        return WriteResult(False, error="Prepared output was already consumed or discarded")
    prepared._publication_attempted = True
    stage_fd = dest_fd = original_fd = None
    exchanged = verified = durable = preserve = False
    error = None
    snapshot = prepared.source_snapshot
    destination = snapshot.path if isinstance(snapshot, SourceSnapshot) else None
    evidence = prepared.directory
    try:
        if not sys.platform.startswith("linux"):
            raise OSError("Prepared in-place publication is supported on Linux only")
        if destination is None or prepared.validated_digest is None:
            raise ValueError("Prepared output is not bound to a source snapshot")
        stage_fd = _open_posix_directory(prepared.directory)
        dest_fd = _open_posix_directory(destination.parent)
        if _posix_identity(os.fstat(dest_fd)) != _posix_identity(os.stat(prepared.directory.parent)):
            raise ValueError("Source directory changed since staging")
        staged_fd = prepared._stream.fileno()
        staged_identity = _posix_identity(os.fstat(staged_fd))
        staged_entry = os.stat(prepared.path.name, dir_fd=stage_fd, follow_symlinks=False)
        if not stat.S_ISREG(staged_entry.st_mode) or _posix_identity(staged_entry) != staged_identity:
            raise ValueError("Staged file entry changed")
        if _digest_fd(staged_fd) != prepared.validated_digest:
            raise ValueError("Validated staged output changed")
        original_fd = os.open(destination.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=dest_fd)
        original_stat = os.fstat(original_fd)
        if not stat.S_ISREG(original_stat.st_mode) or _posix_state(original_stat) != snapshot.state:
            raise ValueError("Source identity or metadata changed")
        if _digest_fd(original_fd) != snapshot.digest:
            raise ValueError("Source content changed")
        verify_source_snapshot(snapshot)
        current = os.stat(destination.name, dir_fd=dest_fd, follow_symlinks=False)
        if _posix_state(current) != snapshot.state:
            raise ValueError("Source changed before publication")
        _rename_exchange(stage_fd, prepared.path.name, dest_fd, destination.name)
        exchanged = True
        try:
            displaced = os.stat(prepared.path.name, dir_fd=stage_fd, follow_symlinks=False)
            current = os.stat(destination.name, dir_fd=dest_fd, follow_symlinks=False)
            if (_posix_identity(displaced) != _posix_identity(os.fstat(original_fd))
                    or _posix_identity(current) != staged_identity):
                raise ValueError("Exchanged entries do not match retained files")
            _verify_displaced_original(original_fd, snapshot)
            if _digest_fd(staged_fd) != prepared.validated_digest:
                raise ValueError("Published staged content changed")
            verified = True
        except Exception as failure:
            try:
                displaced = os.stat(prepared.path.name, dir_fd=stage_fd, follow_symlinks=False)
                current = os.stat(destination.name, dir_fd=dest_fd, follow_symlinks=False)
                if (_posix_identity(displaced) != _posix_identity(os.fstat(original_fd))
                        or _posix_identity(current) != staged_identity):
                    raise RuntimeError("Exchange entries changed before rollback")
                _rename_exchange(stage_fd, prepared.path.name, dest_fd, destination.name)
                if (_posix_identity(os.stat(destination.name, dir_fd=dest_fd, follow_symlinks=False))
                        != _posix_identity(os.fstat(original_fd))
                        or _posix_identity(os.stat(prepared.path.name, dir_fd=stage_fd, follow_symlinks=False))
                        != staged_identity):
                    raise RuntimeError("Rollback identities could not be verified")
                exchanged = False
                os.fsync(dest_fd)
                os.fsync(stage_fd)
            except Exception as rollback_error:
                preserve = True
                raise RuntimeError(f"Publication state ambiguous; evidence retained in {evidence}: {rollback_error}") from failure
            raise ValueError(f"Publication rejected and original entry restored: {failure}") from failure
        os.fsync(dest_fd)
        durable = True
        os.fsync(stage_fd)
        os.unlink(prepared.path.name, dir_fd=stage_fd)
        prepared.path = None
        os.fsync(stage_fd)
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        if exchanged and prepared.path is not None:
            preserve = True
            error += f"; staging evidence retained in {evidence}"
    finally:
        for descriptor in (original_fd, dest_fd, stage_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as failure:
                    error = f"{error + '; ' if error else ''}close descriptor: {failure}"
        if preserve:
            # The name may now contain the original; never discard it.
            prepared.path = None
            prepared.directory = None
        try:
            prepared.discard()
        except Exception as failure:
            error = f"{error + '; ' if error else ''}cleanup failed: {failure}"
    return WriteResult(error is None, destination if exchanged and verified else None,
                       error, exchanged, durable)


def publish_prepared_pdf_windows(prepared: PreparedPdfOutput) -> WriteResult:
    """Replace a trusted local source using the retained validated Windows handle.

    Destination and ancestor directories must be trusted: the absolute path is
    resolved again by Windows during rename, and can otherwise be redirected.
    """
    if not isinstance(prepared, PreparedPdfOutput):
        return WriteResult(False, error="A prepared PDF output is required")
    if prepared._publication_attempted or prepared._stream is None:
        return WriteResult(False, error="Prepared output was already consumed or discarded")
    prepared._publication_attempted = True
    snapshot = prepared.source_snapshot
    destination = snapshot.path if isinstance(snapshot, SourceSnapshot) else None
    target_handle = None
    published = verified = False
    error = None
    try:
        if os.name != "nt":
            raise OSError("Windows prepared publication is supported on Windows only")
        if destination is None or prepared.validated_digest is None:
            raise ValueError("Prepared output is not bound to a source snapshot")
        # Reject unsupported paths before locking or attempting replacement.
        _windows_replace_buffer(str(destination))
        import msvcrt
        staged_handle = msvcrt.get_osfhandle(prepared._stream.fileno())
        staged_identity = _windows_file_identity(staged_handle)
        if not any(staged_identity[1]):
            raise ValueError("Staged file identity cannot be verified")
        before = os.fstat(prepared._stream.fileno())
        if _stream_digest(prepared._stream) != prepared.validated_digest:
            raise ValueError("Validated staged output changed")
        if _posix_state(before) != _posix_state(os.fstat(prepared._stream.fileno())):
            raise ValueError("Staged output changed while hashing")
        target = _open_windows_replacement_target(destination)
        if target is None:
            raise FileNotFoundError("Source PDF no longer exists")
        target_handle, target_identity = target
        if not any(target_identity[1]) or target_identity == staged_identity:
            raise ValueError("Source file identity cannot be verified")
        verify_source_snapshot(snapshot)
        path_identity, attributes = _windows_path_identity(destination, open_reparse=True)
        if (attributes is None or attributes & (0x10 | 0x400)
                or path_identity != target_identity
                or _windows_file_identity(target_handle) != target_identity):
            raise ValueError("Source file changed before publication")
        _windows_replace_by_handle(staged_handle, str(destination))
        published = True
        # The retained staging file was renamed. Never unlink its former name.
        prepared.path = None
        _windows_verify_published_file(staged_handle, str(destination))
        verified = True
        if (_windows_file_identity(staged_handle) != staged_identity
                or _stream_digest(prepared._stream) != prepared.validated_digest):
            raise ValueError("Published PDF differs from the validated output")
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        if published and not verified:
            error = f"PDF was published but its destination could not be verified; actual location is unknown: {error}"
    finally:
        if target_handle is not None:
            try:
                if not _windows_api().CloseHandle(target_handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            except Exception as failure:
                error = f"{error + '; ' if error else ''}close source handle: {failure}"
        try:
            prepared.discard()
        except Exception as failure:
            error = f"{error + '; ' if error else ''}cleanup failed: {failure}"
    return WriteResult(error is None, destination if published and verified else None,
                       error, published, False, WINDOWS_DURABILITY_WARNING if published else None)


def publish_prepared_pdf(prepared: PreparedPdfOutput) -> WriteResult:
    """Publish a snapshot-bound output using the current platform's safeguards."""
    if os.name == "nt":
        return publish_prepared_pdf_windows(prepared)
    return publish_prepared_pdf_linux(prepared)


def write_annotated_pdf(source_path, destination_path, annotations: Iterable, *,
                        replacement_target: PreparedReplacement | None = None) -> WriteResult:
    """Write and validate annotations before safely publishing the retained file.

    Existing destinations are rejected unless the caller supplies a prepared,
    one-use target after explicit confirmation. Linux replacement uses an
    atomic exchange and verifies the displaced file before discarding it.
    Windows replacement uses the documented handle-based POSIX rename mode and
    holds the confirmed target against writes and deletion. Windows file
    contents are flushed, but parent-directory crash durability is not promised.
    """
    stage_directory = temporary = destination = None
    stream = None
    unowned_descriptor = None
    stage_fd = destination_fd = None
    error_text = None
    cleanup_errors = []
    published = crash_durable = destination_verified = False
    publication_state = {"published": False, "destination_verified": False,
                          "preserve_stage": False}
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
        if replacement_target is None and (destination.exists() or destination.is_symlink()):
            raise FileExistsError("Destination already exists; it will not be overwritten")
        if os.name != "nt" and not sys.platform.startswith("linux"):
            raise OSError("Safe descriptor-based publication is not supported on this platform")
        if replacement_target is not None:
            _verify_prepared_replacement(source_resolved, destination, replacement_target)

        reader, writer, expected = _build_annotated_pdf(source_resolved, annotations)

        stage_directory = _create_private_stage(
            destination_parent, f".{destination.name}.glyph-stage-")
        temporary = stage_directory / "validated-output.tmp"
        if os.name == "nt":
            unowned_descriptor = _create_windows_stage_fd(temporary)
        else:
            stage_fd = _open_posix_directory(stage_directory)
            if replacement_target is not None:
                destination_fd = os.dup(replacement_target.parent_fd)
                stage_parent_info = os.stat(stage_directory.parent)
                if _posix_identity(stage_parent_info) != replacement_target.parent_identity:
                    raise FileExistsError(
                        "Destination directory changed since replacement was confirmed")
                unowned_descriptor = os.open(
                    temporary.name, os.O_CREAT | os.O_EXCL | os.O_RDWR
                    | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=stage_fd)
            else:
                unowned_descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR
                                             | getattr(os, "O_CLOEXEC", 0), 0o600)
                destination_fd = _open_posix_directory(destination_parent)

        # Qt and pypdf share this descriptor sequentially; avoid buffered
        # file-position state while retaining the publication handle.
        stream = os.fdopen(unowned_descriptor, "w+b", buffering=0)
        unowned_descriptor = None
        _write_validated_pdf(reader, writer, expected, stream)
        if replacement_target is not None:
            _verify_prepared_replacement(source_resolved, destination, replacement_target)

        if os.name == "nt":
            import msvcrt
            if replacement_target is None:
                _windows_publish_by_handle(msvcrt.get_osfhandle(stream.fileno()), str(destination))
            else:
                _windows_replace_by_handle(msvcrt.get_osfhandle(stream.fileno()), str(destination))
            published = True
            publication_state.update(published=True, destination_verified=False)
            warning = WINDOWS_DURABILITY_WARNING
            try:
                _windows_verify_published_file(msvcrt.get_osfhandle(stream.fileno()), str(destination))
            except Exception as error:
                raise RuntimeError(f"PDF was published, but its destination could not be verified; "
                                   f"actual location is unknown: {error}") from error
            destination_verified = True
            publication_state["destination_verified"] = True
        else:
            if replacement_target is None:
                _link_fd_noclobber(stream.fileno(), temporary.name, stage_fd,
                                   destination.name, destination_fd)
                published = True
                destination_verified = True
                publication_state.update(published=True, destination_verified=True)
            else:
                _linux_replace_by_exchange(
                    stream.fileno(), temporary.name, stage_fd, destination.name,
                    destination_fd, replacement_target, source_resolved, temporary,
                    publication_state)
                published = publication_state["published"]
                destination_verified = publication_state["destination_verified"]
            os.fsync(destination_fd)
            crash_durable = True
            if replacement_target is not None:
                os.fsync(stage_fd)
                os.unlink(temporary.name, dir_fd=stage_fd)
                temporary = None
                os.fsync(stage_fd)
    except Exception as error:
        error_text = f"{type(error).__name__}: {error}"
        published = publication_state["published"]
        destination_verified = publication_state["destination_verified"]

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
    if not publication_state["preserve_stage"] and not (os.name == "nt" and published):
        try:
            _cleanup_staged_file(temporary)
        except Exception as error:
            cleanup_errors.append(f"remove staged output: {type(error).__name__}: {error}")
    if not publication_state["preserve_stage"]:
        try:
            _cleanup_stage_directory(stage_directory)
        except Exception as error:
            cleanup_errors.append(f"remove staging directory: {type(error).__name__}: {error}")

    if cleanup_errors:
        cleanup_text = "Cleanup failed: " + "; ".join(cleanup_errors)
        error_text = f"{error_text}; {cleanup_text}" if error_text else cleanup_text
    if replacement_target is not None:
        try:
            replacement_target.close()
        except Exception as error:
            cleanup_text = f"close replacement target: {type(error).__name__}: {error}"
            error_text = f"{error_text}; {cleanup_text}" if error_text else cleanup_text
    success = error_text is None
    return WriteResult(success, destination if published and destination_verified else None, error_text,
                       published, crash_durable, warning)
