"""
sources/ats_json.py
--------------------
Structured source #2: ATS JSON blob. Field names in real ATSes never match
ours, so this extractor walks a configurable set of candidate key-paths per
canonical field and takes the first that resolves. This is deliberately
more flexible than the CSV source because JSON blobs vary far more in shape
between vendors (Greenhouse, Lever, Workday, etc. all differ).

This version is built against the vendor-style schema actually shipped in
sample_ats.json:
    applicant_id, applicant_name, contact_email, mobile_number,
    current_employer, position_held, profile_summary,
    location.city_name / location.country_name,
    social_profiles.github,
    skill_tags (list[str]),
    work_history (list[{employer, role, from_date, to_date, description}]),
    academic_records (list[{school, qualification, subject, graduation_year}])
"""

from __future__ import annotations
import json
import os
from .base import BaseSource, RawRecord

# For each canonical field, a list of possible dotted key-paths to try, in
# priority order. Includes both the vendor-style schema (applicant_id,
# contact_email, ...) and a flatter fallback schema (candidate_id, email, ...)
# so this extractor tolerates either shape.
_KEY_PATHS = {
    "candidate_id": ["candidate_id", "applicant_id"],
    "name":         ["applicant_name", "name", "candidate.full_name", "personal.name"],
    "email":        ["contact_email", "email", "candidate.email", "personal.email"],
    "phone":        ["mobile_number", "phone", "candidate.phone", "personal.mobile"],
    "current_company": ["current_employer", "current_company", "work.current_employer"],
    "title":        ["position_held", "title", "work.title"],
    "headline":     ["profile_summary", "summary", "candidate.summary"],
    "location_city":    ["location.city_name", "location.city"],
    "location_country": ["location.country_name", "location.country"],
    "github_url":   ["social_profiles.github", "github_url"],
}

# List-shaped fields that need their own remapping (different key names per
# item) rather than a straight key-path lookup.
_LIST_FIELD_SOURCES = {
    # canonical_field -> (source_key, item_key_map)
    "skills_raw": ("skill_tags", None),  # list[str] passthrough
    "experience": ("work_history", {
        "company": "employer", "title": "role",
        "start": "from_date", "end": "to_date", "summary": "description",
    }),
    "education": ("academic_records", {
        "institution": "school", "degree": "qualification",
        "field": "subject", "end_year": "graduation_year",
    }),
}


def _dig(blob: dict, dotted_path: str):
    cur = blob
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _remap_list(raw_list, key_map: dict) -> list:
    """Convert a list of vendor-shaped dicts into canonical-shaped dicts."""
    remapped = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        new_item = {}
        for canon_key, vendor_key in key_map.items():
            new_item[canon_key] = item.get(vendor_key)
        remapped.append(new_item)
    return remapped


class ATSJSONSource(BaseSource):
    name = "ats_json"

    def extract(self, path_or_text: str, is_path: bool = True) -> list:
        if is_path:
            if not path_or_text or not os.path.exists(path_or_text):
                return [RawRecord(source_name=self.name, ok=False, error="file not found")]
            with open(path_or_text, encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = path_or_text or ""

        if not raw.strip():
            return [RawRecord(source_name=self.name, ok=False, error="empty JSON")]

        try:
            blob = json.loads(raw)
        except json.JSONDecodeError as exc:
            return [RawRecord(source_name=self.name, ok=False, error=f"malformed JSON: {exc}")]

        # Support both a single object and an array of candidates
        blobs = blob if isinstance(blob, list) else [blob]
        records = []
        for b in blobs:
            if not isinstance(b, dict):
                continue
            fields = {}

            # --- Scalar / nested key-path fields -----------------------------
            for canon_field, paths in _KEY_PATHS.items():
                for p in paths:
                    val = _dig(b, p)
                    if val is not None and val != "":
                        fields[canon_field] = val
                        break

            # --- List fields (skills / experience / education) -------------
            for canon_field, (source_key, key_map) in _LIST_FIELD_SOURCES.items():
                raw_list = b.get(source_key)
                if isinstance(raw_list, list) and raw_list:
                    if key_map is None:
                        # Plain passthrough list (e.g. skill_tags -> skills_raw)
                        fields[canon_field] = raw_list
                    else:
                        fields[canon_field] = _remap_list(raw_list, key_map)

            # --- Fallback: also accept already-canonical flat list fields ---
            # (covers the simplified schema variant, if ever used instead)
            for list_field in ("skills_raw", "skills", "experience", "education"):
                if list_field in b and isinstance(b[list_field], list) and list_field not in fields:
                    fields[list_field] = b[list_field]

            if fields:
                records.append(RawRecord(source_name=self.name, fields=fields))

        if not records:
            return [RawRecord(source_name=self.name, ok=False, error="no recognizable fields in JSON")]
        return records