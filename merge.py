"""
merge.py
--------
Merges a list of RawRecord objects (from different sources) into a single
CanonicalProfile.

Merge / conflict-resolution policy
------------------------------------
Each canonical field has a defined "winner picks" rule:

  field             | policy
  ------------------|--------------------------------------------------------
  full_name         | Prefer longer name; prefer structured sources
  emails            | Union across all sources, deduplicated
  phones            | Union across all sources, E.164-normalised, deduplicated
  location          | First non-null wins, structured sources take priority
  links             | Union (different sub-fields from different sources)
  headline          | Prefer GitHub bio > ATS summary > notes; longest wins
  years_experience  | Max of inferred experience spans + explicit mentions
  skills            | Union; confidence = fraction of sources that mention it
  experience        | Prefer ATS/CSV (structured); deduplicate by company+title
  education         | Same; deduplicate by institution
  provenance        | Always populated per field
  overall_confidence| Weighted average of field confidences

Source priority order (for single-winner fields):
  recruiter_csv > ats_json > resume > github > recruiter_notes

Robustness: this function never raises.  Bad / missing fields from a source
are silently skipped; the field stays null in the canonical record.
"""

from __future__ import annotations
import uuid
from typing import Optional

from .schema import (
    CanonicalProfile, SkillEntry, ExperienceEntry, EducationEntry,
    Location, Links, ProvenanceEntry,
)
from .normalize import (
    normalize_email, normalize_phone, normalize_name, normalize_skill,
    normalize_date, normalize_country, years_between,
)
from .sources.base import RawRecord

# Lower index = higher priority when picking a single winner.
_SOURCE_PRIORITY = [
    "recruiter_csv",
    "ats_json",
    "resume",
    "github",
    "recruiter_notes",
]


def _priority(source_name: str) -> int:
    try:
        return _SOURCE_PRIORITY.index(source_name)
    except ValueError:
        return len(_SOURCE_PRIORITY)  # unknown -> lowest priority


def _prov(field: str, source: str, method: str) -> ProvenanceEntry:
    return ProvenanceEntry(field=field, source=source, method=method)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def merge(records: list, candidate_id: Optional[str] = None) -> CanonicalProfile:
    """
    Merge a list of RawRecord objects into one CanonicalProfile.

    Parameters
    ----------
    records       : list of RawRecord (ok=True entries are used; ok=False are
                    skipped but their source names are recorded for auditability).
    candidate_id  : if not supplied, a deterministic ID is derived from the
                    best email found; failing that, a fresh UUID4 is used.
    """
    ok_records = [r for r in records if r.ok]

    # Sort by source priority so simple "first-wins" merges prefer better sources.
    ok_records.sort(key=lambda r: _priority(r.source_name))

    profile = CanonicalProfile(candidate_id=candidate_id or "TEMP")
    prov: list[ProvenanceEntry] = []

    # ---- full_name ---------------------------------------------------------
    profile.full_name, name_prov = _merge_name(ok_records)
    if name_prov:
        prov.append(name_prov)

    # ---- emails ------------------------------------------------------------
    profile.emails, email_provs = _merge_emails(ok_records)
    prov.extend(email_provs)

    # ---- phones ------------------------------------------------------------
    profile.phones, phone_provs = _merge_phones(ok_records)
    prov.extend(phone_provs)

    # ---- location ----------------------------------------------------------
    profile.location, loc_prov = _merge_location(ok_records)
    if loc_prov:
        prov.append(loc_prov)

    # ---- links -------------------------------------------------------------
    profile.links, link_provs = _merge_links(ok_records)
    prov.extend(link_provs)

    # ---- headline ----------------------------------------------------------
    profile.headline, hl_prov = _merge_headline(ok_records)
    if hl_prov:
        prov.append(hl_prov)

    # ---- skills ------------------------------------------------------------
    profile.skills, skill_provs = _merge_skills(ok_records)
    prov.extend(skill_provs)

    # ---- github (repos + everything else the adapter pulled) ---------------
    # Surfaced regardless of the identity-verification outcome, so the UI
    # can always show *something* about what github_source.py found at the
    # supplied URL/username -- including, crucially, the warning text when
    # the profile's name didn't corroborate against a higher-priority
    # source. Previously only repos/repos_count made it into the canonical
    # profile and the rest (bio, raw location, languages, warning) was
    # silently dropped here, so it never reached the frontend.
    for r in ok_records:
        if r.source_name == "github":
            f = r.fields
            # repos/repos_count: present under plain keys when verified, or
            # *_unverified when not -- check both so collab/owned repo data
            # still surfaces (with the warning attached) even when the
            # identity gate didn't corroborate the profile.
            repos = f.get("repos") or f.get("repos_unverified")
            if isinstance(repos, list) and repos:
                profile.github_repos = repos
                prov.append(_prov("github_repos", "github", "exact_match"))
            repos_count = f.get("repos_count")
            if repos_count is None:
                repos_count = f.get("repos_count_unverified")
            if repos_count is not None:
                profile.github_repos_count = repos_count
                prov.append(_prov("github_repos_count", "github", "exact_match"))
            forked = f.get("forked_repos") or f.get("forked_repos_unverified")
            if isinstance(forked, list) and forked:
                profile.github_forked_repos = forked
                prov.append(_prov("github_forked_repos", "github", "exact_match"))
            collab = f.get("collaborated_repos") or f.get("collaborated_repos_unverified")
            if isinstance(collab, list) and collab:
                profile.github_collaborated_repos = collab
                prov.append(_prov("github_collaborated_repos", "github", "events_feed_inferred"))

            github_url = f.get("github_url")
            if github_url:
                profile.github_username = github_url.rstrip("/").split("/")[-1]

            warning = f.get("identity_warning")
            if warning:
                profile.github_identity_verified = False
                profile.github_identity_warning = warning
                prov.append(_prov("github_identity_warning", "github", "name_mismatch"))
                profile.github_bio = f.get("headline_unverified")
                profile.github_location_raw = f.get("location_raw_unverified")
                profile.github_languages = f.get("languages_unverified") or []
            else:
                profile.github_identity_verified = True
                profile.github_bio = f.get("headline")
                profile.github_location_raw = f.get("location_raw")
                profile.github_languages = f.get("languages") or []
            break

    # ---- experience --------------------------------------------------------
    profile.experience, exp_provs = _merge_experience(ok_records)
    prov.extend(exp_provs)

    # ---- education ---------------------------------------------------------
    profile.education, edu_provs = _merge_education(ok_records)
    prov.extend(edu_provs)

    # ---- years_experience --------------------------------------------------
    profile.years_experience, yoe_prov = _merge_yoe(ok_records, profile.experience)
    if yoe_prov:
        prov.append(yoe_prov)

    # ---- candidate_id ------------------------------------------------------
    if not candidate_id:
        if profile.emails:
            profile.candidate_id = profile.emails[0]
        else:
            profile.candidate_id = str(uuid.uuid4())

    profile.provenance = prov

    # ---- overall_confidence ------------------------------------------------
    profile.overall_confidence = _compute_confidence(profile, ok_records)

    return profile


# ---------------------------------------------------------------------------
# Field-level mergers
# ---------------------------------------------------------------------------

def _merge_name(records: list):
    best = None
    best_src = None
    for r in records:
        raw = r.fields.get("name")
        normed = normalize_name(raw)
        if normed:
            if best is None or len(normed) > len(best):
                best = normed
                best_src = r.source_name
    if best:
        return best, _prov("full_name", best_src, "exact_match")
    return None, None


def _merge_emails(records: list):
    seen = set()
    result = []
    provs = []
    for r in records:
        raw = r.fields.get("email") or r.fields.get("email_address")
        normed = normalize_email(raw)
        if normed and normed not in seen:
            seen.add(normed)
            result.append(normed)
            provs.append(_prov(f"emails[{len(result)-1}]", r.source_name, "regex_extract"))
    return result, provs


def _merge_phones(records: list):
    seen = set()
    result = []
    provs = []
    for r in records:
        raw = r.fields.get("phone") or r.fields.get("phone_number") or r.fields.get("mobile")
        normed = normalize_phone(raw)
        if normed and normed not in seen:
            seen.add(normed)
            result.append(normed)
            provs.append(_prov(f"phones[{len(result)-1}]", r.source_name, "normalize:E164"))
    return result, provs


def _merge_location(records: list):
    for r in records:
        fields = r.fields
        city = fields.get("location_city") or fields.get("city")
        country_raw = fields.get("location_country") or fields.get("country")
        country = normalize_country(country_raw) if country_raw else None
        region = fields.get("location_region") or fields.get("region")

        # GitHub gives a raw "location_raw" string - parse best-effort
        if not city and not country:
            loc_raw = fields.get("location_raw")
            if loc_raw:
                parts = [p.strip() for p in loc_raw.split(",")]
                city = parts[0] if parts else None
                country_raw = parts[-1] if len(parts) > 1 else None
                country = normalize_country(country_raw) if country_raw else None

        if city or country or region:
            loc = Location(city=city, region=region, country=country)
            return loc, _prov("location", r.source_name, "field_map")

    return Location(), None


def _merge_links(records: list):
    links = Links()
    provs = []
    for r in records:
        f = r.fields
        if not links.github and f.get("github_url"):
            links.github = f["github_url"]
            provs.append(_prov("links.github", r.source_name, "exact_match"))
        if not links.portfolio and f.get("portfolio_url"):
            links.portfolio = f["portfolio_url"]
            provs.append(_prov("links.portfolio", r.source_name, "exact_match"))
        for other in (f.get("other_links") or []):
            if other and other not in links.other:
                links.other.append(other)
    return links, provs


def _merge_headline(records: list):
    """Prefer longer / richer headline; structured sources win ties."""
    best = None
    best_src = None
    for r in records:
        raw = r.fields.get("headline") or r.fields.get("bio") or r.fields.get("summary")
        if raw and isinstance(raw, str) and raw.strip():
            candidate = raw.strip()
            if best is None or len(candidate) > len(best):
                best = candidate
                best_src = r.source_name
    if best:
        return best, _prov("headline", best_src, "longest_wins")
    return None, None


def _merge_skills(records: list):
    """
    Aggregate skills across all sources.
    Confidence = (sources mentioning skill) / (total ok sources).
    """
    total_sources = max(len(records), 1)
    skill_map: dict[str, dict] = {}  # canonical_name -> {sources, count}

    for r in records:
        # Skills may come as a list in "skills_raw", or as "languages" (GitHub)
        raw_skills: list = []
        if isinstance(r.fields.get("skills_raw"), list):
            raw_skills.extend(r.fields["skills_raw"])
        if isinstance(r.fields.get("languages"), list):
            raw_skills.extend(r.fields["languages"])
        # ATS JSON sometimes has a skills list
        if isinstance(r.fields.get("skills"), list):
            for s in r.fields["skills"]:
                if isinstance(s, str):
                    raw_skills.append(s)
                elif isinstance(s, dict):
                    raw_skills.append(s.get("name", ""))

        for raw in raw_skills:
            canon = normalize_skill(raw)
            if not canon:
                continue
            if canon not in skill_map:
                skill_map[canon] = {"sources": [], "count": 0}
            if r.source_name not in skill_map[canon]["sources"]:
                skill_map[canon]["sources"].append(r.source_name)
                skill_map[canon]["count"] += 1

    skills = [
        SkillEntry(
            name=name,
            confidence=round(info["count"] / total_sources, 2),
            sources=info["sources"],
        )
        for name, info in skill_map.items()
    ]
    # Sort by confidence desc, then name
    skills.sort(key=lambda s: (-s.confidence, s.name))

    provs = []
    for i, sk in enumerate(skills):
        provs.append(_prov(f"skills[{i}].{sk.name}", ",".join(sk.sources), "union:confidence_weighted"))

    return skills, provs


def _merge_experience(records: list):
    """
    Collect experience entries. Prefer structured sources (recruiter_csv,
    ats_json). Deduplicate by (company, title) pair.

    Records are processed in source-priority order, so a higher-priority
    source's entry is normally added first. However, if a later (lower
    priority) source provides start/end dates for the *same* (company,
    title) pair that the earlier entry lacks, we upgrade the existing entry
    in place rather than silently dropping the richer data — a dateless
    scalar field (e.g. recruiter_csv's current_company/title) should not
    suppress a structured, dated entry from ats_json.
    """
    seen: dict = {}   # (company, title) -> index into result
    result: list = []
    provs: list = []

    def _upgrade_if_richer(key, candidate: "ExperienceEntry", source_name: str, idx_in_result: int):
        """If candidate has dates the existing entry lacks, replace it in place."""
        existing = result[idx_in_result]
        gained_dates = (not existing.start and candidate.start) or (
            (not existing.end or existing.end == "present") and candidate.end and candidate.end != "present"
        )
        gained_summary = not existing.summary and candidate.summary
        if gained_dates or gained_summary:
            result[idx_in_result] = ExperienceEntry(
                company=existing.company or candidate.company,
                title=existing.title or candidate.title,
                start=existing.start or candidate.start,
                end=candidate.end if gained_dates else existing.end,
                summary=existing.summary or candidate.summary,
            )
            provs.append(_prov(f"experience[{idx_in_result}]", source_name, "upgraded_with_dates"))

    for r in records:
        # Direct structured list (ATS JSON)
        exp_list = r.fields.get("experience")
        if isinstance(exp_list, list):
            for e in exp_list:
                if not isinstance(e, dict):
                    continue
                company = (e.get("company") or "").strip() or None
                title = (e.get("title") or "").strip() or None
                start = normalize_date(e.get("start"))
                end_raw = (e.get("end") or "").strip().lower()
                end = "present" if end_raw in ("present", "current", "now", "") else normalize_date(e.get("end"))
                key = (company or "", title or "")
                entry = ExperienceEntry(
                    company=company, title=title, start=start, end=end,
                    summary=(e.get("summary") or "").strip() or None,
                )
                if key in seen:
                    _upgrade_if_richer(key, entry, r.source_name, seen[key])
                    continue
                seen[key] = len(result)
                result.append(entry)
                provs.append(_prov(f"experience[{len(result)-1}]", r.source_name, "structured_list"))
            continue

        # Single current-employer from CSV / ATS scalar fields
        company = r.fields.get("current_company")
        title = r.fields.get("title")
        if company or title:
            company = (company or "").strip() or None
            title = (title or "").strip() or None
            key = (company or "", title or "")
            entry = ExperienceEntry(company=company, title=title, end="present")
            if key in seen:
                _upgrade_if_richer(key, entry, r.source_name, seen[key])
                continue
            seen[key] = len(result)
            result.append(entry)
            provs.append(_prov(f"experience[{len(result)-1}]", r.source_name, "current_employer_field"))

    return result, provs


def _merge_education(records: list):
    seen = set()
    result = []
    provs = []

    for r in records:
        edu_list = r.fields.get("education")
        if isinstance(edu_list, list):
            for e in edu_list:
                if not isinstance(e, dict):
                    continue
                institution = (e.get("institution") or "").strip() or None
                degree = (e.get("degree") or "").strip() or None
                fld = (e.get("field") or "").strip() or None
                end_year = e.get("end_year")
                key = (institution or "", degree or "")
                if key in seen:
                    continue
                seen.add(key)
                entry = EducationEntry(institution=institution, degree=degree,
                                       field=fld, end_year=end_year)
                result.append(entry)
                provs.append(_prov(f"education[{len(result)-1}]", r.source_name, "structured_list"))

    return result, provs


def _merge_yoe(records: list, experience: list) -> tuple:
    """
    Derive years_experience:
    1. Explicit years_experience_raw from notes/ATS
    2. Sum of individual experience spans
    3. Max single span (fallback)
    """
    # 1. Explicit declaration
    for r in records:
        raw = r.fields.get("years_experience_raw")
        if raw is not None:
            try:
                return float(raw), _prov("years_experience", r.source_name, "explicit_field")
            except (ValueError, TypeError):
                pass

    # 2. Derive from experience list
    total = 0.0
    counted = 0
    for exp in experience:
        yrs = years_between(exp.start, exp.end)
        if yrs is not None:
            total += yrs
            counted += 1

    if counted:
        return round(total, 1), _prov("years_experience", "derived", "sum_experience_spans")

    return None, None


def _compute_confidence(profile: CanonicalProfile, records: list) -> float:
    """
    Overall confidence: fraction of key fields that are populated, weighted by
    number of sources that contributed.

    Includes ALL major profile sections — not just the easy-to-fill scalar
    fields. A profile missing location/education/links should score lower
    than one with every section populated, even if names/emails/skills are
    all present. location and links are objects (always truthy even when
    empty), so they need an explicit "has any real value" check rather than
    a plain truthiness check like the list/string fields use.
    """
    has_location = bool(
        profile.location and (profile.location.city or profile.location.region or profile.location.country)
    )
    has_links = bool(
        profile.links and (profile.links.github
                            or profile.links.portfolio or profile.links.other)
    )

    key_fields = [
        profile.full_name,
        profile.emails,
        profile.phones,
        profile.headline,
        profile.skills,
        profile.experience,
        profile.education,
        has_location,
        has_links,
    ]
    filled = sum(1 for f in key_fields if f)
    field_score = filled / len(key_fields)

    source_score = min(len(records) / 3, 1.0)  # saturates at 3 sources

    return round((field_score * 0.7 + source_score * 0.3), 2)