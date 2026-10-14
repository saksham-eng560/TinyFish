"""Checking uploaded resumes before anything reads or stores them (docs/HIREFLOW_PLAN.md §5.3).

The size limit, the real file type from its first bytes (not the name or the browser's content type),
and a clean filename. The stored copy gets a random name and the detected type.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

MAX_RESUME_BYTES = 5 * 1024 * 1024
# A .docx is a zip: refuse ones that would unpack to something huge (a "zip bomb")
MAX_DOCX_UNPACKED_BYTES = 60 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TYPES = {"pdf": "application/pdf", "docx": DOCX_TYPE, "txt": "text/plain", "md": "text/markdown"}


class UploadRejected(ValueError):
    """The file can't be accepted; the message says why, in plain words."""


@dataclass(frozen=True)
class CheckedUpload:
    kind: str  # pdf | docx | txt | md
    content_type: str
    filename: str  # cleaned, for display only (never used as a path)

    @property
    def extension(self) -> str:
        return self.kind


def clean_filename(name: str | None, kind: str) -> str:
    """Just the file's own name: no folders, no control or odd characters, a sane length, the right extension."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    stem = re.sub(r"\s+", " ", base.rsplit(".", 1)[0] if "." in base else base).strip(" .") or "resume"
    return f"{stem[:120]}.{kind}"


def sniff(data: bytes) -> str | None:
    """What the bytes really are: pdf, docx, text, or None."""
    if data[:1024].lstrip(b"\x00\t\r\n \xef\xbb\xbf").startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                names = {info.filename for info in infos}
                if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                    return None
                if len(infos) > MAX_DOCX_ENTRIES or sum(i.file_size for i in infos) > MAX_DOCX_UNPACKED_BYTES:
                    raise UploadRejected("That Word file unpacks to something far too large to be a resume.")
            return "docx"
        except zipfile.BadZipFile:
            return None
    if b"\x00" not in data[:8192]:
        try:
            data[:65536].decode("utf-8")
            return "text"
        except UnicodeDecodeError as exc:
            if exc.start >= 65536 - 4:  # cut in the middle of a character at the sample's end
                return "text"
    return None


def check_resume_upload(filename: str | None, data: bytes) -> CheckedUpload:
    if not data:
        raise UploadRejected("That file is empty.")
    if len(data) > MAX_RESUME_BYTES:
        raise UploadRejected("That file is over 5 MB. Export a smaller PDF and try again.")
    claimed = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    actual = sniff(data)
    if actual is None:
        raise UploadRejected("That doesn't look like a PDF, Word (.docx) or text file. Upload your resume as a PDF or .docx.")
    if actual == "text":
        if claimed not in ("txt", "md", ""):
            raise UploadRejected(f"That file is named .{claimed} but it's plain text. Upload it as .txt, or as a PDF / .docx.")
        kind = claimed or "txt"
    else:
        if claimed and claimed != actual:
            raise UploadRejected(f"That file is named .{claimed} but it's really a {actual.upper()} file. Rename it to .{actual} and try again.")
        kind = actual
    return CheckedUpload(kind=kind, content_type=TYPES[kind], filename=clean_filename(filename, kind))
