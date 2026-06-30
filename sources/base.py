"""
sources/base.py
----------------
Every source extractor implements the same contract: take a raw input
(file path / URL / blob), return a list of `RawRecord` (almost always one,
but a CSV could in theory contain rows for many candidates - we keep the
shape general).

A RawRecord is intentionally loose/untyped (dict-of-fields) because each
source speaks its own dialect. Normalization and merging happen later in
the pipeline, not here. This file's job is ONLY "detect + extract", per the
pipeline breakdown in the design doc.

Robustness contract: extractors must NEVER raise on missing/malformed input.
On failure they return a RawRecord with `ok=False` and an `error` string,
and the pipeline treats that as "this source contributed nothing" rather
than crashing the whole run.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawRecord:
    source_name: str                  # e.g. "recruiter_csv", "github"
    ok: bool = True
    error: Optional[str] = None
    fields: dict = field(default_factory=dict)   # loosely-typed extracted fields
    raw_text: Optional[str] = None     # full unstructured text, if applicable (for skill/summary mining)


class BaseSource:
    """Abstract extractor. Subclasses implement `extract`."""

    name: str = "base"

    def extract(self, *args, **kwargs) -> list:
        raise NotImplementedError

    def safe_extract(self, *args, **kwargs) -> list:
        """Wrapper that guarantees extract() never throws out of the pipeline."""
        try:
            return self.extract(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - intentional catch-all per robustness contract
            return [RawRecord(source_name=self.name, ok=False, error=f"{type(exc).__name__}: {exc}")]