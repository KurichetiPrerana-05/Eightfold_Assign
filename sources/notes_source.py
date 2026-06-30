"""
sources/notes_source.py
-----------------------
Unstructured source: recruiter notes (.txt free text).

Applies lightweight regex heuristics to mine:
  - email addresses
  - phone numbers
  - skill mentions (against the canonical alias table)
  - a rough location mention
  - years-of-experience clue ("X years of experience")

All extractions are best-effort.  Unknown / ambiguous content is surfaced in
`raw_text` so downstream stages can still reference it.
"""

from __future__ import annotations
import os
import re
from .base import BaseSource, RawRecord

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(\+?[\d][\d\s\-\(\)]{8,15}\d)")
_YOE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*years?\s+(?:of\s+)?(?:experience|exp\.?)", re.IGNORECASE)
_SKILL_TOKENS = re.compile(r"[A-Za-z][A-Za-z0-9\+\#\.]+")

# Import the alias table from normalize to avoid duplicating it here.
from eightfold.normalize import normalize_skill as _ns  # noqa: E402

# Minimum length to consider a word as a potential skill token
_MIN_SKILL_LEN = 2


class RecruiterNotesSource(BaseSource):
    name = "recruiter_notes"

    def extract(self, path_or_text: str, is_path: bool = True) -> list:
        if is_path:
            if not path_or_text or not os.path.exists(path_or_text):
                return [RawRecord(source_name=self.name, ok=False, error="file not found")]
            with open(path_or_text, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        else:
            text = path_or_text or ""

        if not text.strip():
            return [RawRecord(source_name=self.name, ok=False, error="empty notes")]

        fields: dict = {}

        # Email
        m = _EMAIL_RE.search(text)
        if m:
            fields["email"] = m.group(0)

        # Phone
        pm = _PHONE_RE.search(text)
        if pm:
            fields["phone"] = pm.group(1).strip()

        # Years of experience
        ym = _YOE_RE.search(text)
        if ym:
            fields["years_experience_raw"] = float(ym.group(1))

        # Skills: run every word/token through the normalizer; keep those
        # that map to a known canonical skill (alias table hit).
        known_skills = []
        seen = set()
        for tok in _SKILL_TOKENS.findall(text):
            if len(tok) < _MIN_SKILL_LEN:
                continue
            canon = _ns(tok)
            # Only keep if it was actually mapped (i.e. key existed in alias dict),
            # not just title-cased. We detect this by checking the lower token.
            if canon and canon not in seen and tok.lower() in _get_alias_keys():
                known_skills.append(canon)
                seen.add(canon)
        if known_skills:
            fields["skills_raw"] = known_skills

        return [RawRecord(source_name=self.name, fields=fields, raw_text=text)]


# ---- helpers ---------------------------------------------------------------

_alias_keys: set | None = None


def _get_alias_keys() -> set:
    """Lazy-load the alias key set from normalize module."""
    global _alias_keys
    if _alias_keys is None:
        from eightfold import normalize as _norm
        _alias_keys = set(getattr(_norm, "_SKILL_ALIASES", {}).keys())
    return _alias_keys