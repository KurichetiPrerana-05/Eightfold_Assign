"""
sources/recruiter_csv.py
------------------------
Structured source #1: recruiter CSV export.
Expected (but not guaranteed) columns: name, email, phone, current_company, title.
Header names are matched case-insensitively with a few common aliases, since
real recruiter exports are never perfectly consistent.
"""

from __future__ import annotations
import csv
import io
import os
from .base import BaseSource, RawRecord

_HEADER_ALIASES = {
    "name": "name", "full_name": "name", "candidate_name": "name",
    "email": "email", "email_address": "email", "e-mail": "email",
    "phone": "phone", "phone_number": "phone", "mobile": "phone", "contact": "phone",
    "current_company": "current_company", "company": "current_company", "employer": "current_company",
    "title": "title", "current_title": "title", "designation": "title", "role": "title",
    # Pass candidate_id straight through so the pipeline filter can match it
    "candidate_id": "candidate_id",
}


class RecruiterCSVSource(BaseSource):
    name = "recruiter_csv"

    def extract(self, path_or_text: str, is_path: bool = True) -> list:
        if is_path:
            if not path_or_text or not os.path.exists(path_or_text):
                return [RawRecord(source_name=self.name, ok=False, error="file not found")]
            with open(path_or_text, newline="", encoding="utf-8-sig") as f:
                text = f.read()
        else:
            text = path_or_text or ""

        if not text.strip():
            return [RawRecord(source_name=self.name, ok=False, error="empty CSV")]

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return [RawRecord(source_name=self.name, ok=False, error="no header row")]

        col_map = {}
        for h in reader.fieldnames:
            key = (h or "").strip().lower().replace(" ", "_")
            if key in _HEADER_ALIASES:
                col_map[h] = _HEADER_ALIASES[key]

        records = []
        for row in reader:
            fields = {}
            for raw_col, value in row.items():
                canon = col_map.get(raw_col)
                if canon and value and value.strip():
                    fields[canon] = value.strip()
            if fields:
                records.append(RawRecord(source_name=self.name, fields=fields))

        if not records:
            return [RawRecord(source_name=self.name, ok=False, error="no usable rows found")]
        return records