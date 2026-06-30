"""
normalize.py
------------
Pure, side-effect-free normalization functions. Every function here must be
deterministic: same input -> same output, always. Never throw on garbage
input -> return None and let the caller decide what "missing" means.
"""

from __future__ import annotations
import re
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Phone numbers -> E.164
# ---------------------------------------------------------------------------

_DEFAULT_LOCAL_COUNTRY_CODE = "91"


def normalize_phone(raw: Optional[str], default_country_code: str = _DEFAULT_LOCAL_COUNTRY_CODE) -> Optional[str]:
    """
    Best-effort normalization to E.164 (+<countrycode><number>, digits only
    after the '+'). Returns None if we cannot confidently parse a number,
    rather than emitting a malformed phone string.
    Handles: None, non-str, empty, whitespace-only, all non-digit garbage.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        # Accept ints/floats that slipped through (e.g. CSV numeric parsing)
        try:
            raw = str(int(raw))
        except (ValueError, TypeError):
            return None

    s = raw.strip()
    if not s:
        return None

    has_plus = s.startswith("+")
    digits = re.sub(r"[^\d]", "", s)

    if not digits:
        return None

    # Reject clearly nonsensical digit strings (too short or too long for E.164)
    if len(digits) < 7 or len(digits) > 15:
        return None

    # Already has explicit country code (e.g. +1 415..., +91 98...)
    if has_plus:
        return f"+{digits}"

    # 10-digit local number with no country code -> assume default country
    if len(digits) == 10:
        return f"+{default_country_code}{digits}"

    # 11-13 digit number that already embeds a country code without '+'
    if 11 <= len(digits) <= 13:
        return f"+{digits}"

    return None


# ---------------------------------------------------------------------------
# Dates -> YYYY-MM
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}

_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{2})$"),          lambda m: f"{m.group(1)}-{m.group(2)}"),
    (re.compile(r"^(\d{4})/(\d{2})$"),           lambda m: f"{m.group(1)}-{m.group(2)}"),
    (re.compile(r"^(\d{4})-(\d{2})-\d{2}$"),     lambda m: f"{m.group(1)}-{m.group(2)}"),
    (re.compile(r"^(\d{2})/(\d{4})$"),            lambda m: f"{m.group(2)}-{m.group(1)}"),
    (re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$"),
     lambda m: (f"{m.group(2)}-{_MONTHS[m.group(1).lower()[:3]]}"
                if m.group(1).lower()[:3] in _MONTHS else None)),
    (re.compile(r"^(\d{4})$"),                    lambda m: f"{m.group(1)}-01"),
]


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """
    Normalize a free-form date string to YYYY-MM. Returns None if unparsable.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.lower() in ("present", "current", "now", "ongoing"):
        return "present"

    for pattern, fn in _DATE_PATTERNS:
        m = pattern.match(s)
        if m:
            result = fn(m)
            if result and "??" not in result:
                return result
    return None


# ---------------------------------------------------------------------------
# Skill name canonicalization
# ---------------------------------------------------------------------------

_SKILL_ALIASES = {
    "js": "JavaScript", "javascript": "JavaScript",
    "ts": "TypeScript", "typescript": "TypeScript",
    "py": "Python", "python": "Python", "python3": "Python",
    "reactjs": "React", "react.js": "React", "react": "React",
    "nodejs": "Node.js", "node.js": "Node.js", "node": "Node.js",
    "ml": "Machine Learning", "machine learning": "Machine Learning",
    "dl": "Deep Learning", "deep learning": "Deep Learning",
    "nlp": "NLP", "natural language processing": "NLP",
    "cv": "Computer Vision", "computer vision": "Computer Vision",
    "tf": "TensorFlow", "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "sql": "SQL", "mysql": "MySQL", "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "aws": "AWS", "gcp": "GCP", "azure": "Azure",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "docker": "Docker",
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "c++": "C++", "cpp": "C++", "c#": "C#", "csharp": "C#",
    "git": "Git", "github": "Git",
    "rest api": "REST APIs", "rest apis": "REST APIs", "restful api": "REST APIs",
    "genai": "Generative AI", "generative ai": "Generative AI",
    "llm": "LLMs", "llms": "LLMs",
}


# Some ATS exports tag skills with a category prefix separated by an em-dash,
# en-dash, hyphen, or colon, e.g. "Programming Languages — C" or
# "Frameworks: Django". We only want the actual skill name after the
# separator, not the category label.
_CATEGORY_PREFIX_RE = re.compile(
    r"^.*?(?:programming languages?|frameworks?|tools?|libraries|databases?|"
    r"platforms?|technologies|skills?)\s*[\u2014\u2013\-:]\s*(.+)$",
    re.IGNORECASE,
)


def _strip_category_prefix(raw: str) -> str:
    """Strip a leading category label like 'Programming Languages — ' from a skill tag."""
    m = _CATEGORY_PREFIX_RE.match(raw)
    if m:
        return m.group(1).strip()
    return raw


def normalize_skill(raw: Optional[str]) -> Optional[str]:
    """Map a free-text skill mention to one canonical name."""
    if not raw or not isinstance(raw, str):
        return None
    pre_cleaned = _strip_category_prefix(raw.strip())
    key = pre_cleaned.strip().lower()
    if not key:
        return None
    if key in _SKILL_ALIASES:
        return _SKILL_ALIASES[key]
    cleaned = re.sub(r"\s+", " ", pre_cleaned.strip())
    if not cleaned:
        return None
    if len(cleaned) <= 4 and cleaned.isupper():
        return cleaned
    return cleaned.title() if cleaned.islower() else cleaned


# ---------------------------------------------------------------------------
# Country -> ISO 3166 alpha-2
# ---------------------------------------------------------------------------

_COUNTRY_TO_ISO2 = {
    "india": "IN", "united states": "US", "usa": "US", "u.s.a.": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB",
    "canada": "CA", "germany": "DE", "france": "FR", "australia": "AU",
    "singapore": "SG", "uae": "AE", "united arab emirates": "AE",
}


def normalize_country(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return _COUNTRY_TO_ISO2.get(s.lower())


# ---------------------------------------------------------------------------
# Name / email helpers
# ---------------------------------------------------------------------------

def normalize_email(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s):
        return s
    return None


def normalize_name(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip())
    return cleaned if cleaned else None


def years_between(start_yyyymm: Optional[str], end_yyyymm: Optional[str]) -> Optional[float]:
    """Compute elapsed years between two YYYY-MM strings ('present' allowed for end)."""
    if not start_yyyymm or not isinstance(start_yyyymm, str):
        return None
    try:
        sy, sm = (int(x) for x in start_yyyymm.split("-"))
    except (ValueError, AttributeError):
        return None

    if end_yyyymm == "present" or not end_yyyymm:
        end = datetime.utcnow()
        ey, em = end.year, end.month
    else:
        try:
            ey, em = (int(x) for x in end_yyyymm.split("-"))
        except (ValueError, AttributeError):
            return None

    months = (ey - sy) * 12 + (em - sm)
    return round(max(months, 0) / 12, 1)