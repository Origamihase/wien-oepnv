
import pytest
from src.utils.stations_validation import _extract_float

@pytest.mark.parametrize("input_val", [
    "NaN", "nan", "Infinity", "-Infinity", "inf", "-inf"
])
def test_extract_float_rejects_non_finite(input_val):
    # Current behavior: accepts them.
    # Desired behavior: returns None.
    assert _extract_float(input_val) is None

def test_extract_float_accepts_finite():
    assert _extract_float("12.345") == 12.345
    assert _extract_float("-48.2") == -48.2
    assert _extract_float(12.345) == 12.345
    assert _extract_float(10) == 10.0

def test_extract_float_rejects_garbage():
    assert _extract_float("foo") is None
    assert _extract_float("") is None
