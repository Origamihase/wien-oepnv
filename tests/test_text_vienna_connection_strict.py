"""Regression tests for Bug 11D / 11E.

Bug 11D — ``text_has_vienna_connection`` previously matched a bare
``\\bu-bahn\\b`` token, so any text mentioning the German word
"U-Bahn" — including foreign-city contexts like ``Berliner U-Bahn``,
``Münchner U-Bahn`` or ``Hamburger U-Bahn`` — was flagged as
Wien-relevant. This is the last-resort fallback used by the ÖBB filter
when no explicit route or station was identified, so a foreign U-Bahn
mention in an ÖBB feed item slipped through silently.

Bug 11E — ``is_in_vienna(name)`` falls back to comparing a normalised
station-name token against the ``WIEN_TOKEN`` env-var (default
``"wien"``). The env value was used raw, so a deployer setting
``WIEN_TOKEN="Wien"`` (capital W) broke the comparison: the normalised
incoming token is lowercase, the env value isn't.

The fixes:

- Drop the standalone ``u-bahn`` keyword from the Wien-detection regex
  in ``text_has_vienna_connection``. U1-U6 line context is still
  matched separately when accompanied by typical Wien-shorthand
  patterns ("Linie U6 gesperrt", "(U2)", …).
- Pass the env-derived city token through ``_normalize_token`` so a
  cased env value still matches.
"""

from __future__ import annotations

import pytest

from src.utils.stations import is_in_vienna, text_has_vienna_connection


class TestTextHasViennaConnection:
    def test_berliner_u_bahn_does_not_match(self) -> None:
        assert text_has_vienna_connection("Berliner U-Bahn unterbrochen") is False

    def test_muenchner_u_bahn_does_not_match(self) -> None:
        assert text_has_vienna_connection("Münchner U-Bahn gestört") is False

    def test_die_u_bahn_nach_berlin_does_not_match(self) -> None:
        assert (
            text_has_vienna_connection("Die U-Bahn nach Berlin fährt") is False
        )

    def test_frankfurter_u_bahn_does_not_match(self) -> None:
        assert text_has_vienna_connection("Frankfurter U-Bahn-Anlage") is False

    def test_wien_u_bahn_still_matches(self) -> None:
        # The Wien word remains the dominant signal.
        assert text_has_vienna_connection("Wien U-Bahn") is True

    def test_u6_gesperrt_with_wien_station_matches(self) -> None:
        # Wien station alias resolution still keeps this true.
        assert (
            text_has_vienna_connection(
                "U6 gesperrt zwischen Stephansplatz und Karlsplatz"
            )
            is True
        )

    def test_linie_u6_with_wien_word_matches(self) -> None:
        assert text_has_vienna_connection("Linie U6 Wien") is True


class TestIsInViennaWienTokenNormalisation:
    def test_default_wien_lowercase_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Default WIEN_TOKEN ("wien") still works for variations of the
        # incoming station name.
        monkeypatch.delenv("WIEN_TOKEN", raising=False)
        assert is_in_vienna("Wien Mitte-Landstraße") is True

    def test_wien_token_capitalised_env_still_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A deployer setting WIEN_TOKEN="Wien" (capital W) used to
        # silently break the fallback because the env value was
        # compared raw against a casefolded name token.
        monkeypatch.setenv("WIEN_TOKEN", "Wien")
        # Use a name that is NOT in the directory so we hit the fallback.
        assert is_in_vienna("Wien Foobarbaz") is True

    def test_wien_token_uppercase_env_still_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WIEN_TOKEN", "WIEN")
        assert is_in_vienna("Wien Foobarbaz") is True
