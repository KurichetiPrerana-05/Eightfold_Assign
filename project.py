"""
eightfold/project.py
--------------------
Handles custom projection transformations based on a dynamic user config.
Also exposes project() and validate_output() used by pipeline.py.
"""

# FIX 1: removed 'dict' from typing imports — dict is a built-in, not a typing export.
# Using Dict for type hints (compatible with Python 3.8+).
from typing import Any, Dict, Optional

from .schema import DEFAULT_OUTPUT_SCHEMA


def project_profile(profile: Any, config: Dict) -> Dict:
    """Project a CanonicalProfile into a custom-shaped output dict per runtime config."""
    # Handle both dataclass profiles and plain dicts safely
    profile_dict = profile.to_dict() if hasattr(profile, "to_dict") else vars(profile)

    output: Dict = {"candidate_id": profile_dict.get("candidate_id", "unknown")}
    fields_config = config.get("fields", [])
    on_missing = config.get("on_missing", "null")

    for field_cfg in fields_config:
        target_path = field_cfg.get("path")
        source_path = field_cfg.get("from", target_path)
        is_required = field_cfg.get("required", False)

        val = _resolve_path(profile_dict, source_path)

        if val is None or val == [] or val == {}:
            if is_required and on_missing == "error":
                raise ValueError(f"Required field target path missing from source: '{target_path}'")
            elif on_missing == "omit":
                continue
            else:
                output[target_path] = None
        else:
            output[target_path] = val

    if config.get("include_confidence", True):
        output["overall_confidence"] = profile_dict.get("overall_confidence", 0.0)

    return output


# FIX 2: Add aliases expected by pipeline.py
def project(profile: Any, config: Optional[Dict] = None) -> Dict:
    """Alias used by pipeline.py. Falls back to full to_dict() when no config is given."""
    if not config:
        return profile.to_dict() if hasattr(profile, "to_dict") else vars(profile)
    return project_profile(profile, config)


def validate_output(instance: Dict) -> list:
    """Validate output dict against the default schema. Returns list of error strings."""
    from .schema import validate_against_schema
    return validate_against_schema(instance, DEFAULT_OUTPUT_SCHEMA)


def _resolve_path(profile_dict: Dict, path: str) -> Any:
    if not path:
        return None

    # Handle array extraction: "skills[].name"
    if "skills[].name" in path:
        skills_list = profile_dict.get("skills", [])
        extracted_names = []
        for sk in skills_list:
            if isinstance(sk, dict) and "name" in sk:
                extracted_names.append(sk["name"])
            elif hasattr(sk, "name"):
                extracted_names.append(getattr(sk, "name"))
        return extracted_names

    # Handle index notation: "emails[0]" or "phones[0]"
    if "[0]" in path:
        base_key = path.replace("[0]", "")
        arr = profile_dict.get(base_key, [])
        return arr[0] if arr and isinstance(arr, list) else None

    # Standard nested dot-notation
    if "." in path:
        parts = path.split(".")
        curr: Any = profile_dict
        for p in parts:
            if isinstance(curr, dict):
                curr = curr.get(p)
            elif hasattr(curr, p):
                curr = getattr(curr, p)
            else:
                return None
        return curr

    return profile_dict.get(path)