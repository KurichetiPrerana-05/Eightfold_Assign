# Eightfold — Multi-Source Candidate Data Transformer

A modular Python pipeline that extracts candidate information from multiple sources (CSV, ATS JSON, GitHub, PDF resumes, recruiter notes), merges them using conflict-resolution rules, and outputs a validated canonical profile in JSON.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Prerequisites](#prerequisites)
5. [Installation](#installation)
6. [Sample Data](#sample-data)
7. [Running the CLI](#running-the-cli)
8. [Running the Web UI](#running-the-web-ui)
9. [Using the Python API](#using-the-python-api)
10. [Configuration — Custom Output Projection](#configuration--custom-output-projection)
11. [Supported Data Sources](#supported-data-sources)
12. [Merge & Conflict Resolution Policy](#merge--conflict-resolution-policy)
13. [Output Schema](#output-schema)
14. [Running Tests](#running-tests)
15. [Troubleshooting](#troubleshooting)

---

## Project Overview

Eightfold ingests candidate data from heterogeneous sources and produces a single, clean **CanonicalProfile**. The pipeline follows a strict flow:

```
detect → extract → normalize → merge → project → validate → output
```

Key features:
- **Five source adapters**: Recruiter CSV, ATS JSON, GitHub API, PDF Resume, Recruiter Notes
- **Identity-verification safeguard on GitHub enrichment**: bio/location/languages from a GitHub profile are only trusted into canonical fields if the profile's display name loosely matches a name already known from a higher-priority source (CSV/ATS); otherwise they're kept under `*_unverified` keys so a wrong/test username can't pollute a candidate record
- **Conflict resolution**: each field has a defined winner-picks rule (union, longest-wins, max, priority-order)
- **Provenance tracking**: every field records which source won and which extraction method was used
- **Confidence scoring**: overall and per-skill confidence scores (0–1)
- **Schema validation**: output is validated against a JSON-schema before saving
- **Custom projection**: a `runtime_config.json` can remap, rename, and filter output fields
- **Optional web UI**: `server.py` exposes the pipeline over a small FastAPI HTTP API, with `frontui.html` as a ready-made browser front end (file uploads, GitHub input, per-field provenance view)

---

## Architecture

```
eightfold-connected/
├── server.py             ← FastAPI server (web UI + HTTP API for the pipeline)
└── eightfold/
    ├── run.py             ← CLI entry point
    ├── pipeline.py        ← Programmatic API (run() function)
    ├── merge.py           ← Conflict-resolution merge engine
    ├── normalize.py       ← Field normalizers (email, phone, name, dates)
    ├── project.py         ← Output projection & validation
    ├── schema.py          ← CanonicalProfile dataclass + JSON-schema
    ├── frontui.html        ← Browser front end served by server.py
    ├── sources/
    │   ├── base.py          ← RawRecord + BaseSource contract
    │   ├── recruiter_csv.py ← CSV adapter
    │   ├── ats_json.py      ← ATS JSON adapter (Greenhouse, Lever, Workday, etc.)
    │   ├── github_source.py ← GitHub public API adapter (with identity-verification gate)
    │   ├── resume_source.py ← PDF/DOCX resume adapter (pdfplumber / python-docx)
    │   └── notes_source.py  ← Recruiter free-text notes adapter
    ├── data_inputs/
    │   ├── candidate_1_resume.pdf
    │   ├── candidate_2_resume.pdf
    │   └── candidate_3_resume.pdf
    ├── sample_recruiter.csv ← hand-collected, real candidate data (not script-generated)
    ├── sample_ats.json      ← hand-collected, real candidate data (not script-generated)
    ├── runtime_config.json ← Example custom output projection config
    ├── requirements.txt
    └── tests/
        └── test_transformer.py
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.9 or higher |
| pip | latest recommended |

---

## Installation

### Step 1 — Clone or unzip the project

```bash
# If cloning from a repo:
git clone <repository-url> eightfold
cd eightfold

# Or after unzipping:
cd eightfold/eightfold
```

### Step 2 — Create a virtual environment

```bash
python -m venv venv
```

### Step 3 — Activate the virtual environment

**macOS / Linux:**
```bash
source venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```powershell
venv\Scripts\Activate.ps1
```

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

The `requirements.txt` includes:

| Package | Purpose |
|---|---|
| `pytest>=8.0.0` | Test runner |
| `jsonschema>=4.20.0` | Output schema validation |
| `pdfplumber>=0.11.0` | PDF text extraction |
| `python-docx>=1.1.0` | DOCX support (optional sources) |
| `requests>=2.31.0` | GitHub API HTTP calls |

> **Note:** GitHub calls actually use Python's built-in `urllib`, so `requests` is not a hard runtime dependency of `github_source.py` — it's listed for convenience/compatibility with other tooling.

If you also want the optional web UI (`server.py` + `frontui.html`), install the extra packages it needs (not in `requirements.txt` by default):

```bash
pip install fastapi uvicorn python-multipart
```

### Step 5 — (Optional) Install the package in editable mode

This is recommended so that absolute imports work correctly when running tests or the CLI from any directory:

```bash
pip install -e .
```

> **Note:** If there is no `setup.py` or `pyproject.toml`, you can run the CLI directly from inside the `eightfold/` package directory, where the modules live.

---

## Sample Data

`sample_recruiter.csv` and `sample_ats.json` are **not generated by a script** — they are hand-built by manually collecting and typing in details for each real, consented candidate (resume, GitHub username, LinkedIn export, etc.). This keeps the sample data realistic and tied to actual candidates used to test the pipeline end-to-end, rather than synthetic/parsed placeholders.

To add a new candidate to the sample data:
1. Collect their details (name, email, phone, GitHub, location, skills, experience, education, etc.) with their consent
2. Add a new row to `sample_recruiter.csv` following the existing column structure
3. Add a matching entry to `sample_ats.json` following the existing JSON structure
4. Place their resume (PDF/DOCX) in `data_inputs/` if you also want to test the resume source adapter

---

## Running the CLI

The main entry point is `run.py`. All commands below assume you are inside the `eightfold/` directory (where `run.py` lives) with the virtual environment activated.

### Basic usage — minimal required arguments

```bash
python run.py --candidate-id <ID>
```

`--candidate-id` is the only required argument. It filters multi-candidate files (CSV, ATS JSON) to the matching row.

### Example 1 — Default sources (CSV + ATS JSON)

```bash
python run.py \
  --candidate-id ATS-0001 \
  --csv sample_recruiter.csv \
  --json sample_ats.json
```

### Example 2 — Add a PDF resume

```bash
python run.py \
  --candidate-id ATS-0001 \
  --csv sample_recruiter.csv \
  --json sample_ats.json \
  --resume data_inputs/candidate_1_resume.pdf
```

### Example 3 — Add GitHub profile enrichment

```bash
python run.py \
  --candidate-id ATS-0001 \
  --csv sample_recruiter.csv \
  --json sample_ats.json \
  --github octocat
```

### Example 4 — Add recruiter notes

```bash
python run.py \
  --candidate-id ATS-0001 \
  --csv sample_recruiter.csv \
  --json sample_ats.json \
  --notes path/to/notes.txt
```

### Example 6 — Save output to a JSON file

```bash
python run.py \
  --candidate-id ATS-0001 \
  --csv sample_recruiter.csv \
  --json sample_ats.json \
  --output output_profile.json
```

### Example 7 — Apply a custom output projection

```bash
python run.py \
  --candidate-id ATS-0001 \
  --csv sample_recruiter.csv \
  --json sample_ats.json \
  --config runtime_config.json \
  --output projected_output.json
```

### All CLI arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--candidate-id` | ✅ Yes | — | Unique ID to match rows in CSV/JSON |
| `--csv` | No | `sample_recruiter.csv` | Path to recruiter CSV file |
| `--json` | No | `sample_ats.json` | Path to ATS JSON file |
| `--resume` | No | `sample_resume.pdf` | Path to PDF/DOCX resume |
| `--github` | No | — | GitHub username or profile URL for live API enrichment |
| `--notes` | No | — | Path to a recruiter free-text notes `.txt` file |
| `--config` | No | — | Path to `runtime_config.json` for custom projection |
| `--output` | No | — | Path to write output JSON (prints to stdout if omitted) |

---

## Running the Web UI

`server.py` (in the project root, one level above `eightfold/`) exposes the pipeline over HTTP and serves `frontui.html` as a browser-based front end — useful if you'd rather drag-and-drop files and type a GitHub URL into a form than use the CLI.

### Install the extra dependencies

```bash
pip install fastapi uvicorn python-multipart
```

### Start the server

```bash
# From the project root (the directory containing server.py)
python server.py
```

This starts the API at `http://127.0.0.1:8000` and serves the UI at `http://127.0.0.1:8000/`. Open that URL in a browser.

### What the UI lets you do

- Upload a recruiter CSV, ATS JSON, and/or resume (PDF/DOCX)
- Type a GitHub username/URL
- Upload a recruiter notes `.txt` file
- Click one of the three bundled sample candidates to pre-fill from `sample_recruiter.csv` / `sample_ats.json` / `data_inputs/`
- See the merged profile, per-field provenance (which source won each field), warnings, and any failed sources

### API endpoints (if you want to call it programmatically instead)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/run` | `POST` (multipart/form-data) | Run the pipeline against uploaded files / typed GitHub value |
| `/api/sample/{candidate_id}` | `GET` | Run the pipeline against the bundled sample data for `ATS-0001` / `ATS-0002` / `ATS-0003`, optionally layering `?github=...` on top |
| `/` | `GET` | Serves `frontui.html` |

`/api/run` accepts these form fields, all optional except `candidate_id`: `csv`, `json`, `resume`, `github` (text), `notes`, `config`.

---

## Using the Python API

You can also drive the pipeline programmatically using `pipeline.py`:

```python
from eightfold.pipeline import run

sources_config = {
    "recruiter_csv": {"path": "sample_recruiter.csv"},
    "ats_json":      {"path": "sample_ats.json"},
    "resume":        {"path": "data_inputs/candidate_1_resume.pdf"},
    "github":        {"username": "octocat"},
}

result = run(sources_config, candidate_id="ATS-0001")

print(result.profile.full_name)       # CanonicalProfile object
print(result.output)                   # dict — projected, JSON-serializable
print(result.errors)                   # list[str] — schema validation errors
print(result.warnings)                 # list[str] — soft issues (failed sources)
print(result.failed_sources)           # list[str] — sources that returned ok=False
```

### With custom projection config

```python
import json
from eightfold.pipeline import run

with open("runtime_config.json") as f:
    output_config = json.load(f)

result = run(sources_config, output_config=output_config, candidate_id="ATS-0001")
print(result.output)
```

---

## Configuration — Custom Output Projection

`runtime_config.json` controls the shape of the output. Example:

```json
{
  "fields": [
    { "path": "full_name",     "type": "string",   "required": true },
    { "path": "primary_email", "from": "emails[0]","type": "string",   "required": true },
    { "path": "phone",         "from": "phones[0]","type": "string",   "normalize": "E164" },
    { "path": "skills",        "from": "skills[].name", "type": "string[]", "normalize": "canonical" }
  ],
  "include_confidence": true,
  "on_missing": "null"
}
```

| Key | Description |
|---|---|
| `fields[].path` | Output field name |
| `fields[].from` | Source path in the canonical profile (supports `[]` for list traversal) |
| `fields[].required` | If `true`, missing value is flagged as a validation error |
| `fields[].normalize` | Optional normalization hint (`E164`, `canonical`) |
| `include_confidence` | Append `overall_confidence` to output |
| `on_missing` | What to emit for null fields: `"null"` or `"omit"` |

---

## Supported Data Sources

### 1. Recruiter CSV (`recruiter_csv`)
Standard spreadsheet export from a recruiter's ATS or CRM. Expected columns include `name`, `email`, `phone`, `current_company`, `title`, etc.

### 2. ATS JSON (`ats_json`)
Vendor-neutral JSON blob. The adapter tries multiple dotted key-paths per canonical field, supporting Greenhouse, Lever, Workday, and custom schemas.

### 3. GitHub (`github`)
Calls the GitHub public API (`https://api.github.com/users/<username>`) to fetch name, bio, location, public repos, and programming languages. No authentication token required for public profiles; rate-limited to 60 requests/hour unauthenticated per IP.

Includes an **identity-verification safeguard**: a GitHub username/URL is just operator-supplied text, with no guarantee it actually belongs to the candidate (wrong handle, placeholder like `octocat`, a same-named stranger). If a `known_name` (from a higher-priority source like CSV/ATS) is passed in and it doesn't loosely match the GitHub profile's own display name, the bio/location/languages are written to `*_unverified` fields instead of the canonical ones — so they never silently fill gaps with an unrelated person's data. The profile URL itself is always kept under `github_url`, since linking to whatever was typed in is harmless even if it turns out to be the wrong account.

### 4. PDF / DOCX Resume (`resume`)
Uses `pdfplumber` (PDF) or `python-docx` (DOCX) to extract raw text, then applies regex/heuristic patterns for email, phone, name, and a skills section if one exists. Every read/parse step is wrapped so a corrupted or scanned (image-only) file degrades to `ok=False` rather than crashing the pipeline.

### 5. Recruiter Notes (`recruiter_notes`)
Free-text file (`.txt`) containing a recruiter's notes about the candidate. Years of experience, skill mentions, and summary text are extracted.

---

## Merge & Conflict Resolution Policy

When the same field is present in multiple sources, the following rules apply:

| Field | Policy |
|---|---|
| `full_name` | Prefer longer name; structured sources take priority |
| `emails` | Union across all sources, deduplicated |
| `phones` | Union across all sources, E.164-normalised, deduplicated |
| `location` | First non-null wins; structured sources have priority |
| `links` | Union (GitHub, portfolio filled from different sources) |
| `headline` | Prefer longest value; GitHub bio > ATS summary > notes |
| `years_experience` | Max of explicit declaration, derived from experience spans |
| `skills` | Union; `confidence` = fraction of sources that mention the skill |
| `experience` | Prefer ATS/CSV; deduplicated by `(company, title)` pair |
| `education` | Prefer ATS/CSV; deduplicated by `(institution, degree)` pair |

**Source priority** (highest to lowest) for single-winner fields:

```
recruiter_csv > ats_json > resume > github > recruiter_notes
```

---

## Output Schema

The default output is a JSON object validated against this schema:

```json
{
  "candidate_id": "ATS-0001",
  "full_name": "Jane Doe",
  "emails": ["jane@example.com"],
  "phones": ["+919999911111"],
  "location": {
    "city": "Coimbatore",
    "region": null,
    "country": "IN"
  },
  "links": {
    "github": "https://github.com/janedoe",
    "portfolio": null,
    "other": []
  },
  "headline": "Full Stack Developer with 4 years of experience...",
  "years_experience": 4.0,
  "skills": [
    { "name": "python", "confidence": 1.0, "sources": ["recruiter_csv", "ats_json", "resume"] }
  ],
  "experience": [
    { "company": "Acme Corp", "title": "Backend Engineer", "start": "2022-01", "end": "present", "summary": null }
  ],
  "education": [
    { "institution": "MIT", "degree": "BTech", "field": "Computer Science", "end_year": 2022 }
  ],
  "provenance": [
    { "field": "full_name", "source": "recruiter_csv", "method": "exact_match" }
  ],
  "overall_confidence": 0.85
}
```

### What `overall_confidence` actually measures

`overall_confidence` is a **completeness/coverage score, not an agreement score**. It does not check whether sources agree with each other on a field's value — it only checks whether key sections of the profile ended up populated, and how many sources contributed overall. It is computed as:

```
field_score  = (number of key sections populated) / 9
source_score = min(number of contributing sources / 3, 1.0)   # saturates at 3 sources
overall_confidence = round(field_score * 0.7 + source_score * 0.3, 2)
```

The 9 key sections checked are: `full_name`, `emails`, `phones`, `headline`, `skills`, `experience`, `education`, `location`, `links`.

So a score of `1.0` means every one of those 9 sections has *some* value and at least 3 sources contributed — it does **not** mean every source agreed on every field. A candidate run with only CSV + ATS (no resume/GitHub) will score lower simply for having fewer contributing sources, even if those two sources fully agree on everything. Per-skill confidence (in `skills[].confidence`) is a separate, narrower metric — that one *does* measure cross-source agreement, as the fraction of sources that mention a given skill.

---

## Running Tests

Tests are in `tests/test_transformer.py` and cover phone and name normalization with parametrized matrices.

### Run all tests

```bash
pytest tests/
```

### Run with verbose output

```bash
pytest tests/ -v
```

### Run a specific test

```bash
pytest tests/test_transformer.py::test_normalize_phone_matrix -v
```

### Expected output

```
============================= test session starts ==============================
collected 10 items

tests/test_transformer.py::test_normalize_name_matrix[   Jane Doe   -Jane Doe] PASSED
tests/test_transformer.py::test_normalize_name_matrix[...] PASSED
tests/test_transformer.py::test_normalize_phone_matrix[+1 (555) 019-9234-+15550199234] PASSED
...
============================== 10 passed in 0.12s ==============================
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'eightfold'`

The package is not on the Python path. Install it in editable mode from the project root:

```bash
pip install -e .
```

Or run scripts from inside the `eightfold/` directory where the modules live.

---

### `[-] No records found for candidate-id '...'`

The `--candidate-id` value does not match any row in your CSV or JSON. Check the `applicant_id` or `candidate_id` values in your files:

```bash
python -c "import json; data=json.load(open('sample_ats.json')); [print(d.get('applicant_id')) for d in data]"
```

---

### GitHub API rate limit hit (`HTTP 403`/`429` in the `github` source warning)

The GitHub adapter uses the unauthenticated public API, limited to 60 requests/hour per IP — and that IP is often shared (CI runners, sandboxes, office networks), so you can hit the limit faster than expected. There is currently no authenticated/token mode in `github_source.py`. If you hit the limit, wait an hour, or test from a different network. This is a soft failure — the rest of the pipeline still runs, the GitHub source just contributes nothing (check `result.warnings` / `failed_sources`).

---

### Web UI / `server.py` won't start (`ModuleNotFoundError: No module named 'fastapi'`)

The web UI's dependencies aren't in `requirements.txt` (they're optional). Install them separately:

```bash
pip install fastapi uvicorn python-multipart
```

---

### `pdfplumber` fails to extract text from a resume

Some PDFs are image-only scans. `pdfplumber` can only extract text from text-layer PDFs. Use an OCR tool (e.g., `pytesseract` + `pdf2image`) to pre-process scanned PDFs before passing them to the pipeline.

---

### `[!] Warning: Strict canonical structural validation notice: ...`

This is a soft warning — the pipeline still completes. The output field mentioned does not match the expected JSON-schema type. Check your `runtime_config.json` field mapping or the raw source data for that candidate.
