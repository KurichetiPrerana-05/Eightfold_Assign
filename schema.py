"""
schema.py
---------
Defines the canonical candidate profile (the internal, fixed-shape record the
pipeline builds before any output projection happens), plus a JSON-schema
used to validate whatever shape we are about to emit (default OR a custom
config-driven projection).

Design note: the canonical record is intentionally richer than any single
output config. Projection (see project.py) is a *separate* read-only step
that maps canonical -> requested shape. This keeps "what we know" decoupled
from "what we show".
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical record (internal representation)
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceEntry:
    field: str          # canonical field path, e.g. "phones[0]" or "headline"
    source: str         # which source produced the winning value, e.g. "recruiter_csv"
    method: str         # how it was derived, e.g. "exact_match", "regex_extract", "merge:most_recent"


@dataclass
class SkillEntry:
    name: str                       # canonicalized skill name
    confidence: float                # 0..1
    sources: list = field(default_factory=list)   # which sources mentioned it


@dataclass
class ExperienceEntry:
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None      # YYYY-MM
    end: Optional[str] = None        # YYYY-MM or "present"
    summary: Optional[str] = None


@dataclass
class EducationEntry:
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = None


@dataclass
class Location:
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None    # ISO 3166 alpha-2


@dataclass
class Links:
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: list = field(default_factory=list)


@dataclass
class CanonicalProfile:
    candidate_id: str
    full_name: Optional[str] = None
    emails: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    location: Location = field(default_factory=Location)
    links: Links = field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: list = field(default_factory=list)         # list[SkillEntry]
    experience: list = field(default_factory=list)      # list[ExperienceEntry]
    education: list = field(default_factory=list)       # list[EducationEntry]
    provenance: list = field(default_factory=list)       # list[ProvenanceEntry]
    overall_confidence: float = 0.0
    github_repos: list = field(default_factory=list)    # top owned, non-fork repo names from GitHub
    github_repos_count: Optional[int] = None            # public_repos count from GitHub
    github_forked_repos: list = field(default_factory=list)  # owned repos that are forks of someone else's project
    github_collaborated_repos: list = field(default_factory=list)  # repos.full_name where user contributed but doesn't own (best-effort, from public events feed)
    github_username: Optional[str] = None               # raw username/profile parsed from input
    github_bio: Optional[str] = None                    # raw GitHub bio text (verified profiles only)
    github_location_raw: Optional[str] = None            # raw (unparsed) GitHub location string
    github_languages: list = field(default_factory=list) # languages inferred from GitHub repos
    github_identity_verified: Optional[bool] = None      # None = no github source ran
    github_identity_warning: Optional[str] = None         # set when name couldn't be corroborated

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# JSON-schema used to validate *output* (default or projected) shapes
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CanonicalCandidateProfile",
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "full_name": {"type": ["string", "null"]},
        "emails": {"type": "array", "items": {"type": "string"}},
        "phones": {"type": "array", "items": {"type": "string"}},
        "location": {
            "type": "object",
            "properties": {
                "city": {"type": ["string", "null"]},
                "region": {"type": ["string", "null"]},
                "country": {"type": ["string", "null"]},
            },
        },
        "links": {
            "type": "object",
            "properties": {
                "github": {"type": ["string", "null"]},
                "portfolio": {"type": ["string", "null"]},
                "other": {"type": "array", "items": {"type": "string"}},
            },
        },
        "headline": {"type": ["string", "null"]},
        "years_experience": {"type": ["number", "null"]},
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "confidence": {"type": "number"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
            },
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "summary": {"type": ["string", "null"]},
                },
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "institution": {"type": ["string", "null"]},
                    "degree": {"type": ["string", "null"]},
                    "field": {"type": ["string", "null"]},
                    "end_year": {"type": ["number", "null"]},
                },
            },
        },
        "provenance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "source": {"type": "string"},
                    "method": {"type": "string"},
                },
            },
        },
        "overall_confidence": {"type": "number"},
    },
    "required": ["candidate_id"],
}


def validate_against_schema(instance: dict, schema: dict) -> list:
    """
    Validate `instance` against a JSON-schema dict. Returns a list of human
    readable error strings (empty list = valid). Uses `jsonschema` if
    installed; otherwise falls back to a minimal hand-rolled checker that
    covers 'type' and 'required' so the pipeline never hard-crashes for
    lack of a dependency.
    """
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]
    except ImportError:
        return _fallback_validate(instance, schema)


def _fallback_validate(instance: dict, schema: dict, path: str = "") -> list:
    errors = []
    if schema.get("type") == "object":
        if not isinstance(instance, dict):
            return [f"{path or '<root>'}: expected object"]
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path or '<root>'}: missing required field '{req}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(_fallback_validate(instance[key], subschema, f"{path}.{key}" if path else key))
    elif schema.get("type") == "array":
        if not isinstance(instance, list):
            errors.append(f"{path}: expected array")
        else:
            item_schema = schema.get("items")
            if item_schema:
                for i, item in enumerate(instance):
                    errors.extend(_fallback_validate(item, item_schema, f"{path}[{i}]"))
    return errors