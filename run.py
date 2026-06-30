#!/usr/bin/env python3
"""
run.py
------
End-to-end command-line orchestration layer for the multi-source candidate transformer.
"""

import os
import sys
import json
import argparse
from jsonschema import validate, ValidationError

from eightfold.sources.recruiter_csv import RecruiterCSVSource
from eightfold.sources.ats_json import ATSJSONSource
from eightfold.sources.github_source import GitHubSource
from eightfold.sources.resume_source import ResumeSource
from eightfold.sources.notes_source import RecruiterNotesSource

# FIX 3: import the correct function name 'merge' (not 'merge_records')
from eightfold.merge import merge
from eightfold.project import project_profile
from eightfold.schema import DEFAULT_OUTPUT_SCHEMA

def main():
    parser = argparse.ArgumentParser(description="Eightfold Candidate Data Transformer CLI Interface")
    # FIX 1: removed all illegal [cite: ...] annotations — they are not valid Python
    parser.add_argument("--candidate-id", type=str, required=True, help="Unique canonical identifier for the candidate profile")
    parser.add_argument("--config", type=str, help="Path to runtime schema projection custom configuration JSON file")
    parser.add_argument("--output", type=str, help="Path to save the generated schema JSON data structure")

    parser.add_argument("--csv", type=str, default="sample_recruiter.csv")
    parser.add_argument("--json", type=str, default="sample_ats.json")
    parser.add_argument("--github", type=str, help="GitHub profile username or identifier")
    parser.add_argument("--resume", type=str, default="sample_resume.pdf")
    parser.add_argument("--notes", type=str, help="Path to a recruiter free-text notes file")

    args = parser.parse_args()
    raw_records = []

    # 1. Evaluate Recruiter CSV
    if os.path.exists(args.csv):
        print(f"[*] Extracting input from Recruiter CSV: {args.csv}")
        csv_source = RecruiterCSVSource()
        records = csv_source.safe_extract(args.csv)
        if isinstance(records, list):
            raw_records.extend(records)
        elif records:
            raw_records.append(records)

    # 2. Evaluate ATS JSON
    if os.path.exists(args.json):
        print(f"[*] Extracting input from ATS JSON Mapping: {args.json}")
        ats_source = ATSJSONSource()
        records = ats_source.safe_extract(args.json)
        if isinstance(records, list):
            raw_records.extend(records)
        elif records:
            raw_records.append(records)

    # 3. Evaluate GitHub
    if args.github:
        # Look up the candidate's already-known name (from CSV/ATS, extracted
        # above) so GitHubSource can corroborate identity before trusting any
        # biographical data from the profile (see github_source.py docstring).
        known_name = None
        for r in raw_records:
            if r.ok and r.fields.get("candidate_id") == args.candidate_id and r.fields.get("name"):
                known_name = r.fields["name"]
                break

        print(f"[*] Launching live GitHub API network extraction for: {args.github}")
        github_source = GitHubSource()
        records = github_source.safe_extract(args.github, known_name=known_name)
        if isinstance(records, list):
            raw_records.extend(records)
        elif records:
            raw_records.append(records)

    # 4. Evaluate Resume
    if os.path.exists(args.resume):
        print(f"[*] Extracting input text from PDF Resume file: {args.resume}")
        resume_source = ResumeSource()
        records = resume_source.safe_extract(args.resume)
        if isinstance(records, list):
            raw_records.extend(records)
        elif records:
            raw_records.append(records)

    # 5. Evaluate Recruiter Notes
    if args.notes and os.path.exists(args.notes):
        print(f"[*] Extracting input from Recruiter Notes file: {args.notes}")
        notes_source = RecruiterNotesSource()
        records = notes_source.safe_extract(args.notes)
        if isinstance(records, list):
            raw_records.extend(records)
        elif records:
            raw_records.append(records)

    if not raw_records:
        print("[-] Pipeline Aborted: No valid sample input data sources were successfully resolved.")
        sys.exit(1)

    # FIX: filter multi-candidate files (CSV/ATS) to only rows matching --candidate-id.
    # Only recruiter_csv and ats_json are capable of holding multiple candidates'
    # rows in one file, so only those source types are required to carry a
    # matching candidate_id. Single-identity sources (resume, GitHub, notes)
    # never populate candidate_id and are always passed through unfiltered.
    _MULTI_CANDIDATE_SOURCES = {"recruiter_csv", "ats_json"}

    def matches_candidate(record) -> bool:
        if record.source_name not in _MULTI_CANDIDATE_SOURCES:
            return True
        cid = record.fields.get("candidate_id")
        return cid is not None and cid == args.candidate_id

    raw_records = [r for r in raw_records if r.ok and matches_candidate(r)]
    if not raw_records:
        print(f"[-] No records found for candidate-id '{args.candidate_id}'. Check your CSV/JSON.")
        sys.exit(1)

    # 5. Merge
    print("[*] Dispatching data profiles to conflict-resolution merge matrices...")
    # FIX 3: call merge() with correct argument order (records first, then candidate_id)
    canonical_profile = merge(raw_records, candidate_id=args.candidate_id)

    profile_dict = canonical_profile.to_dict() if hasattr(canonical_profile, "to_dict") else vars(canonical_profile)

    # 6. Project or emit default
    if args.config and os.path.exists(args.config):
        print(f"[*] Running custom output layout transformation using configuration: {args.config}")
        with open(args.config, "r") as f:
            runtime_config = json.load(f)
        final_output = project_profile(canonical_profile, runtime_config)
    else:
        print("[*] No target configuration passed. Outputting default internal canonical format.")
        final_output = profile_dict

        try:
            validate(instance=final_output, schema=DEFAULT_OUTPUT_SCHEMA)
            print("[+] Canonical compliance validation successful.")
        except ValidationError as e:
            print(f"[!] Warning: Strict canonical structural validation notice: {e.message}")

    # 7. Write or print output
    output_json_string = json.dumps(final_output, indent=2)
    if args.output:
        with open(args.output, "w") as out_file:
            out_file.write(output_json_string)
        print(f"[+] Operational data structure saved to target output: {args.output}")
    else:
        print("\n=== TRANSFORMER PAYLOAD SCHEMATIC RUNTIME EMISSION ===")
        print(output_json_string)

if __name__ == "__main__":
    main()