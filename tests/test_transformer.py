import pytest

# FIX: Use a relative/absolute import that works whether pytest is run from
# the repo root (with the package installed or on sys.path) or from inside
# the eightfold/ package directory.
# Running `pytest` from the project root with `pip install -e .` is the
# recommended way; the import below works in both cases.
try:
    from eightfold.normalize import normalize_phone, normalize_name
except ModuleNotFoundError:
    from normalize import normalize_phone, normalize_name


# --- Parametrized Test Cases for Name Normalization ---
@pytest.mark.parametrize(
    "raw_input, expected_output",
    [
        ("   Jane Doe   ", "Jane Doe"),           # Spaces at both ends
        ("\tJohn Smith \n", "John Smith"),         # Tabs and newlines
        ("Prerana   Kuricheti", "Prerana Kuricheti"),  # Internal multi-space collapse
        ("  ", None),                              # Entirely empty whitespace string
        (None, None),                              # Null fallback safety check
    ]
)
def test_normalize_name_matrix(raw_input, expected_output):
    """Validates whitespace optimization and trimming constraints across a dynamic data matrix."""
    assert normalize_name(raw_input) == expected_output


# --- Parametrized Test Cases for Phone Normalization ---
@pytest.mark.parametrize(
    "raw_input, expected_output",
    [
        ("+1 (555) 019-9234", "+15550199234"),    # Formatting punctuation removal
        ("8919342535", "+918919342535"),            # 10-digit Indian local fallback check
        ("+91 99999 11111", "+919999911111"),       # Clean international format
        ("invalid-phone-string", None),             # Complete garbage input graceful recovery
        (None, None),                               # Null input safety validation
    ]
)
def test_normalize_phone_matrix(raw_input, expected_output):
    """Validates global standard sequence normalization formatting rules across multiple formats."""
    assert normalize_phone(raw_input) == expected_output