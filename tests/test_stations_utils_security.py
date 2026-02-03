from src.utils.stations import _coerce_float, is_in_vienna
import math

def test_coerce_float_rejects_non_finite():
    # These should return None for security/safety
    assert _coerce_float("Infinity") is None
    assert _coerce_float("-Infinity") is None
    assert _coerce_float("NaN") is None
    assert _coerce_float(float("inf")) is None
    assert _coerce_float(float("nan")) is None

def test_is_in_vienna_handles_non_finite_gracefully():
    # Should be False (not in Vienna) rather than crashing or weird behavior
    # Note: Currently _coerce_float returns float('inf'), so is_in_vienna might behave unexpectedly.
    # We assert False because that is the safe default.
    assert is_in_vienna("Infinity", "16.0") is False
    assert is_in_vienna("48.0", "NaN") is False
