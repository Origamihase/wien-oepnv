"""Tests for comprehensive ANSI escape sequence sanitization."""
import re
from src.utils.logging import sanitize_log_message as log_sanitize
from src.utils.env import sanitize_log_message as env_sanitize

# Define a list of test cases for ANSI escape sequences
# Format: (input_string, expected_output_stripped, expected_output_no_strip)
ANSI_TEST_CASES = [
    # Standard CSI sequences (colors, cursor)
    ("\x1b[31mRed\x1b[0m", "Red", "Red"),
    ("\x1b[1;31mBold Red\x1b[0m", "Bold Red", "Bold Red"),
    ("\x1b[?25hCursor Show", "Cursor Show", "Cursor Show"),
    ("\x1b[2JClear Screen", "Clear Screen", "Clear Screen"),

    # OSC sequences (Window title, hyperlinks)
    # terminated by BEL (\x07)
    ("\x1b]0;Window Title\x07Text", "Text", "Text"),
    # terminated by ST (\x1b\)
    ("\x1b]8;;https://example.com\x1b\\Link\x1b]8;;\x1b\\", "Link", "Link"),

    # Fe sequences (single char)
    # ESC N (SS2), ESC O (SS3)
    ("\x1bNSS2", "SS2", "SS2"),
    ("\x1bOSS3", "SS3", "SS3"),
    # ESC ^ (PM), ESC _ (APC), ESC \ (ST), ESC ] (OSC start - handled by OSC regex mostly but standalone?)

    # Two-byte sequences (ESC Space-/)
    ("\x1b(B", "", ""), # SCS (Select Character Set)
    ("\x1b)0", "", ""),

    # Mixed / Real-world
    ("Normal \x1b[32mGreen\x1b[0m Text", "Normal Green Text", "Normal Green Text"),

    # Malformed / Incomplete (regex usually strips what matches)
    ("Broken \x1b[31m incomplete", "Broken  incomplete", "Broken  incomplete"),

    # Control chars that are NOT ANSI
    ("Line1\nLine2", "Line1\\nLine2", "Line1\nLine2"),
]

def test_logging_sanitize_ansi():
    """Verify that src.utils.logging.sanitize_log_message strips ANSI codes."""
    for inp, exp_stripped, exp_no_strip in ANSI_TEST_CASES:
        # Test with default strip_control_chars=True
        assert log_sanitize(inp, strip_control_chars=True) == exp_stripped

        # Test with strip_control_chars=False
        # This confirms that ANSI stripping happens independently of control char stripping
        assert log_sanitize(inp, strip_control_chars=False) == exp_no_strip

def test_env_sanitize_ansi():
    """Verify that src.utils.env.sanitize_log_message (fallback) strips ANSI codes."""
    for inp, exp_stripped, exp_no_strip in ANSI_TEST_CASES:
        # Test with default strip_control_chars=True
        assert env_sanitize(inp, strip_control_chars=True) == exp_stripped

        # Test with strip_control_chars=False
        assert env_sanitize(inp, strip_control_chars=False) == exp_no_strip

def test_complex_osc_injection():
    """Specific test for complex OSC injection attempts."""
    # Attempt to inject a window title change
    attack = "\x1b]2;Malicious Title\x07Log Message"
    assert log_sanitize(attack) == "Log Message"

    # Attempt to inject a hyperlink
    attack = "\x1b]8;;http://malicious.com\x07Click Me\x1b]8;;\x07"
    assert log_sanitize(attack) == "Click Me"

def test_fe_sequences_exclusion():
    """Verify that Fe sequences are stripped correctly, but [ and ] are handled by CSI/OSC logic."""
    # ESC [ is CSI, handled by CSI regex part
    # ESC ] is OSC, handled by OSC regex part
    # ESC \ is ST, handled by Fe part or terminator
    # ESC ^ is PM
    # ESC _ is APC

    # \x1b^Privacy Message\x1b\\ (PM terminated by ST)
    # Our Fe regex matches \x1b^ (PM start). The content 'Privacy Message' remains?
    # Wait, Fe sequences like PM/APC are often strings terminated by ST.
    # Our regex \x1b[@-Z\\^_] only matches the START of the Fe sequence (2 bytes).
    # It does NOT consume the content of PM/APC strings!

    # This is a limitation of the "simple" regex.
    # Standard terminals allow PM/APC strings.
    # If we want to strip the CONTENT of PM/APC strings, we need a more complex regex.
    # However, blocking the *start* sequence usually renders them invalid/harmless text.
    # E.g. \x1b^Message\x1b\\ -> Message (if ST is stripped too).

    # Let's verify what happens.
    pm_attack = "\x1b^Privacy Message\x1b\\"
    # \x1b^ is matched by [ ... | @-Z\\^_ ] part. Removed.
    # Privacy Message remains.
    # \x1b\\ (ST) is matched by [ ... | @-Z\\^_ ] part (ESC \). Removed.
    # Result: "Privacy Message"

    assert log_sanitize(pm_attack) == "Privacy Message"
