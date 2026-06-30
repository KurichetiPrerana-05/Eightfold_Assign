"""
pipeline.py
-----------
Top-level orchestrator.

Flow:  detect -> extract -> normalize (in sources) -> merge -> project -> validate

Usage (programmatic):
    from eightfold.pipeline import run
    result = run(sources_config, output_config)
    # result.profile  -> CanonicalProfile
    # result.output   -> dict (projected, JSON-serializable)
    # result.errors   -> list[str] (validation errors; empty = ok)
    # result.warnings -> list[str] (soft issues: failed sources, etc.)
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .sources.base import RawRecord
from .sources.recruiter_csv import RecruiterCSVSource
from .sources.ats_json import ATSJSONSource
from .sources.github_source import GitHubSource
from .sources.resume_source import ResumeSource
from .sources.notes_source import RecruiterNotesSource
from .merge import merge
from .project import project, validate_output
from .schema import CanonicalProfile


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    profile: CanonicalProfile
    output: dict
    errors: list = field(default_factory=list)    # schema validation errors
    warnings: list = field(default_factory=list)  # soft issues (failed sources)
    failed_sources: list = field(default_factory=list)  # sources that returned ok=False


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_SOURCES = {
    "recruiter_csv": RecruiterCSVSource(),
    "ats_json": ATSJSONSource(),
    "github": GitHubSource(),
    "resume": ResumeSource(),
    "recruiter_notes": RecruiterNotesSource(),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    sources_config: dict,
    output_config: Optional[dict] = None,
    candidate_id: Optional[str] = None,
) -> PipelineResult:
    """
    Run the full pipeline.

    Parameters
    ----------
    sources_config : dict mapping source type to input spec.
      Examples:
        {
          "recruiter_csv": {"path": "inputs/recruiter.csv"},
          "github":        {"url": "https://github.com/johndoe"},
          "resume":        {"path": "inputs/resume.pdf"},
          "ats_json":      {"path": "inputs/ats.json"},
          "recruiter_notes": {"path": "inputs/notes.txt"}
        }
    output_config : optional projection config (see project.py).
    candidate_id  : optional fixed ID; derived from email if omitted.
    """
    all_records: list[RawRecord] = []
    warnings: list[str] = []
    failed_sources: list[str] = []

    # ---- Extract (two-phase) -----------------------------------------------
    # Phase 1: structured sources first (CSV, ATS, resume, notes) so we can
    # extract a known_name to pass to identity-gated unstructured sources.
    STRUCTURED = {"recruiter_csv", "ats_json", "resume", "recruiter_notes"}
    UNSTRUCTURED = {"github"}

    for source_type, spec in (sources_config or {}).items():
        if source_type not in STRUCTURED:
            continue
        source = _SOURCES.get(source_type)
        if source is None:
            warnings.append(f"Unknown source type '{source_type}' - skipped")
            continue
        records = _dispatch(source_type, source, spec)
        for r in records:
            if not r.ok:
                warnings.append(f"[{source_type}] extraction failed: {r.error}")
                failed_sources.append(source_type)
            all_records.append(r)

    # Pick the first name we found from structured sources.
    known_name: str | None = next(
        (r.fields.get("name") for r in all_records if r.ok and r.fields.get("name")),
        None,
    )

    # Phase 2: unstructured/identity-sensitive sources with known_name hint.
    for source_type, spec in (sources_config or {}).items():
        if source_type in STRUCTURED:
            continue
        source = _SOURCES.get(source_type)
        if source is None:
            warnings.append(f"Unknown source type '{source_type}' - skipped")
            continue
        records = _dispatch(source_type, source, spec, known_name=known_name)
        for r in records:
            if not r.ok:
                warnings.append(f"[{source_type}] extraction failed: {r.error}")
                failed_sources.append(source_type)
            all_records.append(r)

    if not all_records:
        # Nothing at all - return an empty profile
        empty = CanonicalProfile(candidate_id=candidate_id or "unknown")
        return PipelineResult(
            profile=empty,
            output=empty.to_dict(),
            warnings=["No sources provided any data"],
        )

    # ---- Merge -------------------------------------------------------------
    profile = merge(all_records, candidate_id=candidate_id)

    # ---- Project -----------------------------------------------------------
    try:
        output = project(profile, output_config)
    except ValueError as exc:
        output = profile.to_dict()
        warnings.append(f"Projection error: {exc}")

    # ---- Validate ----------------------------------------------------------
    errors = validate_output(output)

    return PipelineResult(
        profile=profile,
        output=output,
        errors=errors,
        warnings=warnings,
        failed_sources=failed_sources,
    )


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------

def _dispatch(source_type: str, source, spec: dict, known_name: str | None = None) -> list:
    """Route a source spec to the right extractor call signature."""
    if source_type == "recruiter_csv":
        path = spec.get("path") or spec.get("text")
        is_path = "path" in spec
        return source.safe_extract(path, is_path=is_path)

    elif source_type == "ats_json":
        path = spec.get("path") or spec.get("text")
        is_path = "path" in spec
        return source.safe_extract(path, is_path=is_path)

    elif source_type == "github":
        url = spec.get("url") or spec.get("username")
        # Pass known_name so the identity gate can corroborate the profile.
        return source.safe_extract(url, known_name=known_name)

    elif source_type == "resume":
        path = spec.get("path")
        return source.safe_extract(path)

    elif source_type == "recruiter_notes":
        path = spec.get("path") or spec.get("text")
        is_path = "path" in spec
        return source.safe_extract(path, is_path=is_path)

    return [RawRecord(source_name=source_type, ok=False, error="unhandled spec")]