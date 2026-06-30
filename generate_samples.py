#!/usr/bin/env python3
"""
generate_samples.py
-------------------
One-time utility: reads the 3 sample resume PDFs and produces:
  - sample_recruiter.csv   (structured source, simulates a recruiter export)
  - sample_ats.json        (semi-structured ATS blob, with its own field names)

Run from the eightfold/ directory:
    python generate_samples.py

Both output files are placed in the same directory and can be fed straight
into run.py via --csv sample_recruiter.csv --json sample_ats.json
"""

import re
import csv
import json
import os
import pdfplumber

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

EMAIL_RE    = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Match phone but avoid matching short embedded numeric IDs in URLs
PHONE_RE    = re.compile(r"(\+?\d[\d\s\-\(\)]{8,14}\d)")
GITHUB_RE   = re.compile(r"github\.com/[\w\-]+")
LOCATION_RE = re.compile(r"([A-Za-z\s]+),\s*(India|USA|US|UK|Singapore|Germany|Canada|Australia)", re.IGNORECASE)

# Experience: "Company, Title  MM/YYYY – MM/YYYY" or "Title, Company  Month YYYY – Month YYYY"
EXP_RE = re.compile(
    r"([A-Za-z][^\n,]+),\s*([^\n,]+?)\s+"
    r"(\d{2}/\d{4}|[A-Z][a-z]+ \d{4})\s*[–\-]\s*(\d{2}/\d{4}|[A-Z][a-z]+ \d{4}|Present|present|June \d{4}|July \d{4})"
)

# Education: "Institution, Degree  YYYY – YYYY"
EDU_RE = re.compile(
    r"([A-Za-z][^\n,]+),\s*(B\.?Tech|B\.E\.?|M\.?Tech|BTech|Senior Secondary|Secondary)[^\n]*?(\d{4})\s*[–\-]\s*(\d{4})",
    re.IGNORECASE
)

# Skills sections — covers "Skills:", "Technical Skills:", "SKILLS\n..."
SKILLS_SECTION_RE = re.compile(
    r"(?:TECHNICAL\s+)?SKILLS?\s*[:\-]?\s*\n(.*?)(?=\n[A-Z]{3,}|\Z)",
    re.DOTALL | re.IGNORECASE
)


def extract_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_resume(pdf_path: str) -> dict:
    """Extract structured fields from one resume PDF."""
    text = extract_text(pdf_path)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # --- name: first short line with 2-5 words, no digits, no @ ---
    name = None
    for line in lines[:5]:
        if "@" not in line and not any(c.isdigit() for c in line) and 2 <= len(line.split()) <= 5:
            name = line.title()
            break

    # --- email ---
    email_m = EMAIL_RE.search(text)
    email = email_m.group(0) if email_m else None

    # --- phone: pick the longest clean match (avoids short embedded IDs) ---
    phones = PHONE_RE.findall(text)
    phone = None
    for p in phones:
        digits = re.sub(r"\D", "", p)
        # A real phone has 10+ digits; IDs embedded in URLs are shorter
        if len(digits) >= 10:
            phone = p.strip()
            break

    # --- location ---
    loc_m = LOCATION_RE.search(text)
    city, country = (loc_m.group(1).strip(), loc_m.group(2).strip()) if loc_m else (None, None)

    # --- links ---
    gh_m = GITHUB_RE.search(text)
    github_url   = ("https://" + gh_m.group(0)) if gh_m else None

    # --- headline / profile summary ---
    headline = None
    for section_kw in ("PROFILE", "SUMMARY"):
        idx = text.find(section_kw)
        if idx != -1:
            after = text[idx + len(section_kw):].strip()
            # Grab up to the next ALL-CAPS section header
            snippet = re.split(r"\n[A-Z]{3,}", after)[0].strip()
            headline = " ".join(snippet.split())[:300]
            break

    # --- skills ---
    skills = []
    sm = SKILLS_SECTION_RE.search(text)
    if sm:
        block = sm.group(1)
        # Split on commas, pipes, slashes, and newlines; clean each token
        raw_tokens = re.split(r"[,|/\n]", block)
        for tok in raw_tokens:
            tok = tok.strip()
            # Remove leading bullets, dashes, section sub-labels like "Languages:"
            tok = re.sub(r"^[•\-\*]+\s*", "", tok)
            tok = re.sub(r"^[A-Za-z ]+:\s*", "", tok)
            if tok and 1 < len(tok) < 40:
                skills.append(tok)

    # --- current company / title (first experience entry) ---
    current_company, title = None, None
    exp_list = []

    # Try to find EXPERIENCE section first
    exp_idx = max(text.find("EXPERIENCE"), text.find("PROFESSIONAL EXPERIENCE"))
    if exp_idx != -1:
        exp_block = text[exp_idx:]
        # Stop at next major section
        next_section = re.search(r"\n[A-Z]{3,}(?:\s+[A-Z]+)*\n", exp_block[1:])
        if next_section:
            exp_block = exp_block[: next_section.start() + 1]

        # Pattern: "Company, Title, Specialization  Date – Date"  (3-part)
        #          "Company, Title  Date – Date"             (2-part)
        #          "Title, Company  Date – Date"
        THREE_PART_RE = re.compile(
            r"([A-Za-z][^\n,]+),\s*([^\n,]+?),\s*([^\n,]+?)\s+"
            r"(\d{2}/\d{4}|[A-Z][a-z]+ \d{4})\s*[–\-]\s*(\d{2}/\d{4}|[A-Z][a-z]+ \d{4}|[A-Z][a-z]+ \d{4}|present|Present)"
        )
        title_keywords = {"intern", "engineer", "developer", "analyst", "manager", "lead", "consultant", "architect"}

        used_spans = []
        for m3 in THREE_PART_RE.finditer(exp_block):
            company_str = m3.group(1).strip()
            title_str   = f"{m3.group(2).strip()}, {m3.group(3).strip()}"
            start_raw, end_raw = m3.group(4), m3.group(5)
            exp_list.append({
                "company": company_str,
                "title":   title_str,
                "start":   _normalise_date(start_raw),
                "end":     _normalise_date(end_raw),
                "summary": None,
            })
            used_spans.append((m3.start(), m3.end()))

        for m in EXP_RE.finditer(exp_block):
            # Skip if this span was already matched by the 3-part pattern
            if any(s <= m.start() < e for s, e in used_spans):
                continue
            part_a, part_b, start_raw, end_raw = m.group(1), m.group(2), m.group(3), m.group(4)
            # Heuristic: if part_b looks like a job title word it's "Company, Title"
            if any(kw in part_b.lower() for kw in title_keywords):
                company_str, title_str = part_a.strip(), part_b.strip()
            else:
                company_str, title_str = part_b.strip(), part_a.strip()

            exp_list.append({
                "company": company_str,
                "title": title_str,
                "start": _normalise_date(start_raw),
                "end": _normalise_date(end_raw),
                "summary": None,
            })

        if exp_list:
            current_company = exp_list[0]["company"]
            title = exp_list[0]["title"]

    # --- education ---
    edu_list = []
    edu_idx = text.find("EDUCATION")
    if edu_idx != -1:
        edu_block = text[edu_idx:]
        next_section = re.search(r"\n[A-Z]{3,}(?:\s+[A-Z]+)*\n", edu_block[1:])
        if next_section:
            edu_block = edu_block[: next_section.start() + 1]
        for m in EDU_RE.finditer(edu_block):
            inst = m.group(1).strip().rstrip(",")
            degree = m.group(2).strip()
            end_year = int(m.group(4))
            edu_list.append({
                "institution": inst,
                "degree": degree,
                "field": None,
                "end_year": end_year,
            })

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "city": city,
        "country": country,
        "github_url": github_url,
        "headline": headline,
        "skills": skills,
        "current_company": current_company,
        "title": title,
        "experience": exp_list,
        "education": edu_list,
    }


def _normalise_date(raw: str) -> str:
    """Convert resume date strings to YYYY-MM for ATS output."""
    raw = raw.strip()
    if raw.lower() in ("present", "current", "now"):
        return "present"
    # MM/YYYY -> YYYY-MM
    m = re.match(r"^(\d{2})/(\d{4})$", raw)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    # Month YYYY
    months = {
        "jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
        "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12",
    }
    m2 = re.match(r"^([A-Za-z]+)\s+(\d{4})$", raw)
    if m2:
        mon = months.get(m2.group(1).lower()[:3], "01")
        return f"{m2.group(2)}-{mon}"
    # bare year
    m3 = re.match(r"^(\d{4})$", raw)
    if m3:
        return f"{m3.group(1)}-01"
    return raw


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_csv(candidates: list, out_path: str):
    """
    Recruiter CSV — flat row per candidate.
    Fields: name, email, phone, current_company, title, location_city,
            location_country, github_url, headline
    """
    fieldnames = [
        "name", "email", "phone", "current_company", "title",
        "location_city", "location_country", "github_url", "headline",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for c in candidates:
            writer.writerow({
                "name":             c["name"],
                "email":            c["email"],
                "phone":            c["phone"],
                "current_company":  c["current_company"],
                "title":            c["title"],
                "location_city":    c["city"],
                "location_country": c["country"],
                "github_url":       c["github_url"],
                "headline":         c["headline"],
            })
    print(f"[+] Wrote {out_path}  ({len(candidates)} rows)")


def write_ats_json(candidates: list, out_path: str):
    """
    ATS JSON blob — semi-structured, intentionally uses different field names
    from the canonical schema (as the assignment specifies) to exercise the
    field-mapping layer.

    ATS field names  ->  canonical field
    applicant_name   ->  full_name
    contact_email    ->  emails[0]
    mobile_number    ->  phones[0]
    current_employer ->  experience[0].company
    position_held    ->  experience[0].title
    skill_tags       ->  skills[]
    work_history     ->  experience[]
    academic_records ->  education[]
    profile_summary  ->  headline
    social_profiles  ->  links{}
    """
    ats_records = []
    for i, c in enumerate(candidates, start=1):
        record = {
            "applicant_id":     f"ATS-{i:04d}",
            "applicant_name":   c["name"],
            "contact_email":    c["email"],
            "mobile_number":    c["phone"],
            "current_employer": c["current_company"],
            "position_held":    c["title"],
            "profile_summary":  c["headline"],
            "location": {
                "city_name":    c["city"],
                "country_name": c["country"],
            },
            "social_profiles": {
                "github":   c["github_url"],
            },
            "skill_tags": c["skills"],
            "work_history": [
                {
                    "employer":    e["company"],
                    "role":        e["title"],
                    "from_date":   e["start"],
                    "to_date":     e["end"],
                    "description": e["summary"],
                }
                for e in c["experience"]
            ],
            "academic_records": [
                {
                    "school":       e["institution"],
                    "qualification": e["degree"],
                    "subject":      e["field"],
                    "graduation_year": e["end_year"],
                }
                for e in c["education"]
            ],
        }
        ats_records.append(record)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ats_records, f, indent=2)
    print(f"[+] Wrote {out_path}  ({len(ats_records)} records)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    resume_dir = os.path.join(base, "data_inputs")

    resume_files = sorted(
        f for f in os.listdir(resume_dir) if f.endswith(".pdf")
    )

    if not resume_files:
        print("[-] No PDF resumes found in data_inputs/")
        return

    print(f"[*] Parsing {len(resume_files)} resume(s)...")
    candidates = []
    for fname in resume_files:
        path = os.path.join(resume_dir, fname)
        print(f"    {fname}")
        parsed = parse_resume(path)
        candidates.append(parsed)

    write_csv(candidates, os.path.join(base, "sample_recruiter.csv"))
    write_ats_json(candidates, os.path.join(base, "sample_ats.json"))
    print("\n[✓] Done. You can now run:")
    print("    python run.py --candidate-id C001 --csv sample_recruiter.csv --json sample_ats.json --resume data_inputs/candidate_1_resume.pdf")


if __name__ == "__main__":
    main()