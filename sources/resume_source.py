"""
sources/resume_source.py
-------------------------
Unstructured source: resume file (PDF or DOCX). Extracts raw text, then
applies a handful of regex/heuristics to pull out email, phone, name (first
non-empty line), and a skills section if one exists.

Robustness contract: every file I/O and parsing step is wrapped in
try/except so a corrupted or unreadable file always returns ok=False
rather than crashing the pipeline.
"""

from __future__ import annotations
import os
import re
from .base import BaseSource, RawRecord

_EMAIL_RE      = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE      = re.compile(r"(\+?\d[\d\s\-\(\)]{8,15}\d)")
_SKILLS_LINE_RE = re.compile(
    r"^(skills?|technical skills?|tech stack)\s*[:\-]\s*(.+)$", re.IGNORECASE
)


def _extract_text_from_pdf(path: str) -> str:
    """Extract all text from a PDF using pdfplumber. Returns '' on failure."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                try:
                    text_parts.append(page.extract_text() or "")
                except Exception:
                    text_parts.append("")
        return "\n".join(text_parts)
    except Exception as exc:
        raise RuntimeError(f"PDF extraction failed: {exc}") from exc


def _extract_text_from_docx(path: str) -> str:
    """Extract all text from a DOCX file. Returns '' on failure."""
    try:
        import docx
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    except Exception as exc:
        raise RuntimeError(f"DOCX extraction failed: {exc}") from exc


def _read_txt(path: str) -> str:
    """Read a plain-text file, tolerating encoding errors."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as exc:
        raise RuntimeError(f"Text file read failed: {exc}") from exc


class ResumeSource(BaseSource):
    name = "resume"

    def extract(self, path: str) -> list:
        # --- Guard: path must exist and be a real file ----------------------
        if not path or not isinstance(path, str):
            return [RawRecord(source_name=self.name, ok=False,
                              error="no path provided")]
        if not os.path.exists(path):
            return [RawRecord(source_name=self.name, ok=False,
                              error=f"file not found: {path}")]
        if not os.path.isfile(path):
            return [RawRecord(source_name=self.name, ok=False,
                              error=f"path is not a file: {path}")]

        ext = os.path.splitext(path)[1].lower()

        # --- Extract raw text -----------------------------------------------
        try:
            if ext == ".pdf":
                text = _extract_text_from_pdf(path)
            elif ext in (".docx", ".doc"):
                text = _extract_text_from_docx(path)
            elif ext == ".txt":
                text = _read_txt(path)
            else:
                return [RawRecord(source_name=self.name, ok=False,
                                  error=f"unsupported resume format '{ext}'")]
        except Exception as exc:
            # Any I/O or parsing error -> graceful ok=False record
            return [RawRecord(source_name=self.name, ok=False,
                              error=f"could not read/parse resume: {exc}")]

        if not text or not text.strip():
            return [RawRecord(source_name=self.name, ok=False,
                              error="resume had no extractable text (scanned image?)")]

        # --- Heuristic field extraction -------------------------------------
        fields: dict = {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        # Email
        try:
            email_match = _EMAIL_RE.search(text)
            if email_match:
                fields["email"] = email_match.group(0)
        except Exception:
            pass

        # Phone
        try:
            phone_match = _PHONE_RE.search(text)
            if phone_match:
                fields["phone"] = phone_match.group(0)
        except Exception:
            pass

        # Name heuristic: first short line without '@' or digits
        try:
            for line in lines[:5]:
                if (
                    "@" not in line
                    and not any(ch.isdigit() for ch in line)
                    and 2 <= len(line.split()) <= 5
                ):
                    fields["name"] = line
                    break
        except Exception:
            pass

        # Skills section
        try:
            for line in lines:
                m = _SKILLS_LINE_RE.match(line)
                if m:
                    raw_skills = m.group(2)
                    fields["skills_raw"] = [
                        s.strip()
                        for s in re.split(r"[,|/]", raw_skills)
                        if s.strip()
                    ]
                    break
        except Exception:
            pass

        fields["resume_text"] = text
        return [RawRecord(source_name=self.name, fields=fields, raw_text=text)]