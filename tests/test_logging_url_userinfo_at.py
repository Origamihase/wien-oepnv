"""URL credentials whose user part contains an unencoded ``@``.

urllib / requests split the userinfo of ``scheme://user@domain:pw@host`` at
the LAST ``@``, so the password is ``pw``. ``sanitize_log_message`` stopped at
the first ``@`` and logged ``scheme://***@domain:pw@host``: the password
stayed readable. Found while working through the CodeQL alerts (06.10.2026).
"""

from __future__ import annotations

import time

import pytest

from src.utils.logging import sanitize_log_message


@pytest.mark.parametrize(
    "url",
    [
        "smtps://noreply@example.com:smtp_pw@smtp.example.com:465",
        "https://ops@example.com:http_pw@api.example.com/path",
        "postgres:ops@example.com:pg_pw@db.example.com",
    ],
)
def test_password_after_unencoded_at_is_masked(url: str) -> None:
    out = sanitize_log_message(f"connect {url} failed")
    assert "_pw" not in out
    assert "***@" in out


def test_plain_urls_keep_their_shape() -> None:
    assert sanitize_log_message("https://user:pw@host/x") == "https://***@host/x"
    assert sanitize_log_message("https://host/a@b") == "https://host/a@b"
    assert sanitize_log_message("mailto:user@host") == "mailto:user@host"


def test_long_authority_without_at_stays_bounded() -> None:
    # Same generous ceiling as tests/test_redos_credential_masking.py: the
    # whole sweep takes about 2 s here (input is capped), quadratic
    # backtracking would take minutes.
    hostile = ("x://" + "a" * 5_000) * 20
    start = time.perf_counter()
    sanitize_log_message(hostile)
    assert time.perf_counter() - start < 30.0
