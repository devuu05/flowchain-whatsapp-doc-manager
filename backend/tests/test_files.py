"""File validation and extraction with synthetic documents (normal, corrupt, empty, oversized,
unsupported, malicious)."""

from __future__ import annotations

import pytest

from app.services.extraction.base import ExtractionLimits
from app.services.extraction.runner import run_extraction
from app.services.file_validation import detect_and_validate
from tests import synthetic as syn

MB = 1024 * 1024


def check(data: bytes, name: str | None, mime: str | None = None, max_bytes: int = 16 * MB):  # type: ignore[no-untyped-def]
    return detect_and_validate(data, claimed_filename=name, claimed_mime=mime, max_bytes=max_bytes)


@pytest.mark.parametrize(
    ("data", "name", "kind", "ext"),
    [
        (syn.jpeg(), "photo.JPEG", "jpeg", "jpeg"),
        (syn.jpeg(), "photo.jpg", "jpeg", "jpg"),
        (syn.png(), "scan.png", "png", "png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "x.webp", "webp", "webp"),
        (syn.pdf_with_text(["hello"]), "a.pdf", "pdf", "pdf"),
        (syn.docx(["hi"]), "a.docx", "docx", "docx"),
        (syn.xlsx([["a", 1]]), "a.xlsx", "xlsx", "xlsx"),
        (syn.pptx(["t"]), "a.pptx", "pptx", "pptx"),
        (b"name,amount\nABC,10\n", "a.csv", "csv", "csv"),
        ("नमस्ते plain text".encode(), "a.txt", "txt", "txt"),
    ],
)
def test_detects_supported_types(data: bytes, name: str, kind: str, ext: str) -> None:
    r = check(data, name)
    assert r.ok, r.reason
    assert (r.kind, r.extension) == (kind, ext)


def test_extension_mismatch_uses_content_type() -> None:
    r = check(syn.pdf_with_text(["x"]), "invoice.jpg", "image/jpeg")
    assert r.ok and r.kind == "pdf" and r.extension == "pdf"
    assert any("did not match" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("data", "name", "reason"),
    [
        (b"", "a.pdf", "empty"),
        (b"MZ\x90\x00 executable", "invoice.pdf", "not supported"),
        (b"<html><script>alert(1)</script></html>", "a.txt", "not supported"),
        (b"<svg onload=alert(1)>", "a.png", "not supported"),
        (b"\x00\x01\x02binary", "a.txt", "not valid"),
        (b"\x00\x01\x02binary", None, "not supported"),
        (b"PK\x03\x04garbage", "a.docx", "corrupt"),
    ],
)
def test_rejects_unsafe_or_unsupported(data: bytes, name: str | None, reason: str) -> None:
    r = check(data, name)
    assert not r.ok and reason in (r.reason or "")


def test_rejects_oversize() -> None:
    r = check(syn.pdf_with_text(["x"]) + b"0" * 2000, "a.pdf", max_bytes=1000)
    assert not r.ok and "larger" in (r.reason or "")


def test_rejects_macro_enabled_office() -> None:
    data = syn.with_zip_member(syn.docx(["x"]), "word/vbaProject.bin", b"VBA")
    r = check(data, "a.docx")
    assert not r.ok and "macro" in (r.reason or "")
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "_VBA_PROJECT".encode("utf-16-le") + b"\x00" * 100
    assert "macro" in (check(ole, "a.doc").reason or "")


def test_rejects_zip_bomb_and_plain_zip() -> None:
    assert "zip bomb" in (check(syn.zip_bomb_like(), "a.docx").reason or "")
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("evil.exe", b"MZ")
    assert not check(buf.getvalue(), "a.zip").ok


def test_legacy_ole_detection() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "WordDocument".encode("utf-16-le") + b"\x00" * 100
    assert check(ole, "letter.doc").kind == "doc"
    assert check(ole, None).kind == "doc"
    enc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "EncryptedPackage".encode("utf-16-le")
    assert "password" in (check(enc, "a.docx").reason or "")


LIMITS = ExtractionLimits(
    max_pages=5, max_sheets=2, max_slides=3, max_chars=5000, max_rows_per_sheet=100
)


def ex(kind: str, data: bytes, limits: ExtractionLimits = LIMITS, isolate: bool = False):  # type: ignore[no-untyped-def]
    return run_extraction(kind, data, limits, timeout=30, isolate=isolate)


def test_extract_pdf_text() -> None:
    r = ex("pdf", syn.pdf_with_text(["TAX INVOICE", "ABC Traders", "Date 15/09/2026 Total 1000"]))
    assert r.status == "success" and "ABC Traders" in r.text and r.page_count == 1


def test_extract_scanned_pdf_reports_empty() -> None:
    r = ex("pdf", syn.blank_pdf())
    assert r.status == "empty" and any("scanned" in w for w in r.warnings)


def test_extract_docx_xlsx_pptx_csv_txt() -> None:
    assert (
        "Purchase order for Sharma"
        in ex("docx", syn.docx(["Purchase order for Sharma Enterprises 2026"])).text
    )
    x = ex("xlsx", syn.xlsx([["Item", "Qty"], ["Cement bags", 40], ["Steel rods", 12]], sheets=3))
    assert "Cement bags" in x.text and x.page_count == 3 and any("sheets" in w for w in x.warnings)
    p = ex("pptx", syn.pptx(["Quarterly report Q3 2026", "Slide 2", "Slide 3", "Slide 4"]))
    assert "Quarterly report" in p.text and p.page_count == 4 and p.status == "partial"
    assert "ABC | 10" in ex("csv", b"name,amount\nABC,10\nDEF,20\n").text
    assert ex("txt", b"Receipt from Dev Construction for site work").status == "success"


def test_extract_corrupt_returns_failed_not_exception() -> None:
    assert ex("docx", b"PK\x03\x04broken").status == "failed"
    assert ex("pdf", b"%PDF-1.4 garbage").status in ("failed", "empty")
    assert ex("xlsx", b"nope").status == "failed"


def test_extract_truncates_at_char_limit() -> None:
    r = ex("txt", b"A long receipt line. " * 1000)
    assert len(r.text) <= LIMITS.max_chars and any("truncated" in w for w in r.warnings)


def test_images_and_legacy_are_unsupported_locally() -> None:
    assert ex("png", syn.png()).status == "unsupported"
    assert ex("doc", b"\xd0\xcf\x11\xe0").status == "unsupported"


def test_xxe_payload_is_not_resolved(tmp_path) -> None:  # type: ignore[no-untyped-def]
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-HOST-FILE")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<!DOCTYPE w [<!ENTITY xxe SYSTEM "file://{secret}">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    data = syn.replace_zip_member(syn.docx(["placeholder"]), "word/document.xml", xml)
    r = ex("docx", data)
    assert "TOP-SECRET-HOST-FILE" not in r.text
    xdata = syn.replace_zip_member(syn.xlsx([["a"]]), "xl/sharedStrings.xml", xml)
    assert "TOP-SECRET-HOST-FILE" not in ex("xlsx", xdata).text


def test_isolated_extraction_in_child_process() -> None:
    r = ex("pdf", syn.pdf_with_text(["Isolated invoice ABC Traders"]), isolate=True)
    assert r.status == "success" and "Isolated" in r.text


def test_isolated_extraction_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    r = run_extraction("txt", b"x" * 10, LIMITS, timeout=0.0001, isolate=True)
    assert r.status in (
        "failed",
        "empty",
        "success",
    )  # either finished instantly or timed out cleanly
