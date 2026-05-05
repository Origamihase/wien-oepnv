
from src.utils.logging import sanitize_log_message

def test_multiline_leak() -> None:
    msg = "Token: \n harmless \n SUPER_SECRET_VALUE"
    sanitized = sanitize_log_message(msg)
    print(f"Original: {repr(msg)}")
    print(f"Sanitized: {repr(sanitized)}")
    assert "SUPER_SECRET_VALUE" not in sanitized
    assert "Token: ***" in sanitized or "Token: \\n ***" in sanitized or "Token: \\n harmless \\n ***" in sanitized

def test_multiline_header_leak() -> None:
    # Multiline headers must be indented (folding) to be treated as part of the value
    msg = "Authorization: Bearer\n  token"
    sanitized = sanitize_log_message(msg)
    print(f"Sanitized: {repr(sanitized)}")
    assert "token" not in sanitized
    assert "Authorization: ***" in sanitized
