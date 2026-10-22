"""Content-based file type detection and safety validation.

Never trusts the claimed extension or MIME type alone: the type is determined from the
bytes (magic numbers, ZIP/OLE structure) and must be consistent with what was claimed.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from app.services.naming import split_extension

ZIP_MAX_ENTRIES = 5000
ZIP_MAX_UNCOMPRESSED = 250 * 1024 * 1024
ZIP_MAX_RATIO = 200

CANONICAL: dict[str, tuple[str, str]] = {
    # kind: (mime, default extension)
    "jpeg": ("image/jpeg", "jpg"),
    "png": ("image/png", "png"),
    "webp": ("image/webp", "webp"),
    "pdf": ("application/pdf", "pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
    "doc": ("application/msword", "doc"),
    "xls": ("application/vnd.ms-excel", "xls"),
    "ppt": ("application/vnd.ms-powerpoint", "ppt"),
    "txt": ("text/plain", "txt"),
    "csv": ("text/csv", "csv"),
}
EXT_ALIASES = {"jpe": "jpg"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass
class FileCheck:
    ok: bool
    kind: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)


def _reject(reason: str) -> FileCheck:
    return FileCheck(ok=False, reason=reason)


def _utf16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _inspect_zip(data: bytes) -> FileCheck:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return _reject("corrupt or unreadable archive")
    if len(infos) > ZIP_MAX_ENTRIES:
        return _reject("archive has too many entries")
    total = sum(i.file_size for i in infos)
    compressed = sum(i.compress_size for i in infos) or 1
    if total > ZIP_MAX_UNCOMPRESSED or total / compressed > ZIP_MAX_RATIO:
        return _reject("archive expands too much (possible zip bomb)")
    names = {i.filename for i in infos}
    lowered = {n.lower() for n in names}
    if any(i.flag_bits & 0x1 for i in infos):
        return _reject("encrypted archives are not supported")
    if any(n.endswith("vbaproject.bin") for n in lowered):
        return _reject("macro-enabled Office files are not accepted")
    if any(n.startswith("/") or ".." in n.split("/") for n in names):
        return _reject("archive contains unsafe paths")
    if "[content_types].xml" not in lowered:
        return _reject("ZIP archives are not supported (only Office documents)")
    try:
        ctypes = zf.read("[Content_Types].xml")[:200_000].lower()
    except (KeyError, zipfile.BadZipFile, OSError):
        return _reject("corrupt Office document")
    if b"macroenabled" in ctypes:
        return _reject("macro-enabled Office files are not accepted")
    if "word/document.xml" in lowered:
        return FileCheck(ok=True, kind="docx")
    if "xl/workbook.xml" in lowered:
        return FileCheck(ok=True, kind="xlsx")
    if "ppt/presentation.xml" in lowered:
        return FileCheck(ok=True, kind="pptx")
    return _reject("unsupported Office/ZIP format")


def _inspect_ole(data: bytes, claimed_ext: str) -> FileCheck:
    if _utf16("EncryptedPackage") in data:
        return _reject("password-protected Office files are not supported")
    if _utf16("_VBA_PROJECT") in data or _utf16("Macros") in data:
        return _reject("macro-enabled Office files are not accepted")
    kind_by_ext = {"doc": "doc", "xls": "xls", "ppt": "ppt"}
    if claimed_ext in kind_by_ext:
        return FileCheck(ok=True, kind=kind_by_ext[claimed_ext])
    # Infer from stream names when the extension is missing or wrong.
    if _utf16("WordDocument") in data:
        return FileCheck(ok=True, kind="doc")
    if _utf16("Workbook") in data or _utf16("Book") in data:
        return FileCheck(ok=True, kind="xls")
    if _utf16("PowerPoint Document") in data:
        return FileCheck(ok=True, kind="ppt")
    return _reject("unsupported legacy Office format")


def _looks_like_text(data: bytes) -> bool:
    return decode_text(data) is not None


def decode_text(data: bytes) -> str | None:
    """Decode UTF-8 (with/without BOM) or BOM-marked UTF-16; None if it isn't clean text."""
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def detect_and_validate(
    data: bytes,
    *,
    claimed_filename: str | None,
    claimed_mime: str | None,
    max_bytes: int,
) -> FileCheck:
    if not data:
        return _reject("file is empty")
    if len(data) > max_bytes:
        return _reject(f"file is larger than {max_bytes // (1024 * 1024)} MB")
    _, claimed_ext = split_extension(claimed_filename)
    claimed_ext = EXT_ALIASES.get(claimed_ext, claimed_ext)
    mime = (claimed_mime or "").split(";")[0].strip().lower()

    if data.startswith(b"\xff\xd8\xff"):
        result = FileCheck(ok=True, kind="jpeg")
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        result = FileCheck(ok=True, kind="png")
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        result = FileCheck(ok=True, kind="webp")
    elif data.startswith(b"%PDF-"):
        result = FileCheck(ok=True, kind="pdf")
    elif data.startswith(b"PK\x03\x04"):
        result = _inspect_zip(data)
    elif data.startswith(OLE_MAGIC):
        result = _inspect_ole(data, claimed_ext)
    elif data.lstrip()[:5].lower() in (b"<!doc", b"<html", b"<?xml", b"<svg ") or data.startswith(
        (b"MZ", b"\x7fELF", b"#!")
    ):
        return _reject("this file type is not supported")
    elif claimed_ext in ("txt", "csv") or mime in ("text/plain", "text/csv"):
        if not _looks_like_text(data):
            return _reject("text file is not valid UTF-8/UTF-16 text")
        kind = "csv" if (claimed_ext == "csv" or mime == "text/csv") else "txt"
        result = FileCheck(ok=True, kind=kind)
    else:
        return _reject("this file type is not supported")

    if not result.ok or result.kind is None:
        return result
    canonical_mime, default_ext = CANONICAL[result.kind]
    result.mime_type = canonical_mime
    # Keep the user's extension when it agrees with the detected type.
    compatible = {default_ext} | ({"jpg", "jpeg"} if result.kind == "jpeg" else set())
    result.extension = claimed_ext if claimed_ext in compatible else default_ext
    if claimed_ext and claimed_ext not in compatible:
        result.warnings.append(
            f"extension .{claimed_ext} did not match content; stored as .{result.extension}"
        )
    if mime and mime != canonical_mime and mime not in ("application/octet-stream",):
        result.warnings.append("declared MIME type did not match content")
    return result
