
import re
import time
import pytest
from src.providers.wl_lines import LINES_COMPLEX_PREFIX_RE

def test_lines_complex_prefix_correctness():
    """Verify that the regex matches valid complex line prefixes."""
    valid_inputs = [
        "L1, L2: Message",
        "L1, L2 (U1 Ersatz): Message",
        "L1, L2 und Rufbus 123: Message",
        "L1, L2 und Rufbus 123 (U1 Ersatz): Message",
        "U1, U2, U3 (Halt): Text",
        "1A, 2A und Rufbus N12: Text",
        "   L1, L2: Message"
    ]
    for text in valid_inputs:
        match = LINES_COMPLEX_PREFIX_RE.match(text)
        assert match is not None, f"Failed to match valid input: {text}"
        # Ensure it captured the prefix including the colon
        assert match.group(0).strip().endswith(":")

def test_lines_complex_prefix_redos_performance():
    """Verify that the regex is not vulnerable to ReDoS."""
    # Construct an attack string that triggers catastrophic backtracking in the vulnerable regex
    # The vulnerable regex had nested quantifiers around optional spaces and 'und'
    part = " und (A)"
    attack_string = "L1, L2" + part * 1000 + "!" # Ends with ! to force backtracking

    start_time = time.time()
    match = LINES_COMPLEX_PREFIX_RE.match(attack_string)
    duration = time.time() - start_time

    # It should fail to match (return None) very quickly
    assert match is None
    # 0.1s is very generous; vulnerable version took >400s for length ~200.
    # This string is length ~8000.
    assert duration < 0.5, f"Regex took too long: {duration:.4f}s"

def test_lines_complex_prefix_no_match():
    """Verify non-matching inputs."""
    invalid_inputs = [
        "Simple Prefix: Text", # Doesn't start with multiple comma separated parts
        "L1: Text", # Only one part
        "L1, L2 Text", # Missing colon
    ]
    for text in invalid_inputs:
        match = LINES_COMPLEX_PREFIX_RE.match(text)
        assert match is None, f"Should not match: {text}"
