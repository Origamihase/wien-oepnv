
import pytest
from src.providers.wl_lines import _detect_line_pairs_from_text

@pytest.mark.parametrize("text,expected_lines", [
    ("Gleisschaden Züge halten Währinger Gürtel ggü. 164", []),
    ("Bauarbeiten Thaliastraße 45", []),
    # Two-word street names ("Lerchenfelder Gürtel", "Währinger Gürtel")
    # are now handled by the extended ``ADDRESS_NO_RE`` pattern (Bug 12B).
    ("Unfall Lerchenfelder Gürtel 12", []),
    ("Unfall Lerchenfeldergürtel 12", []),
    ("Störung bei Nr. 5", []),
    ("Feuer bei Objekt 123", []),
    ("Tür 12 klemmt", []),
    ("Linie U6 fährt nicht", ["U6"]),
    ("Verspätung Linie 13A", ["13A"]),
    ("Währinger Gürtel 164", []),
])
def test_address_masking(text: str, expected_lines: list[str]) -> None:
    pairs = _detect_line_pairs_from_text(text)
    lines = [p[0] for p in pairs]
    # We expect exact match of lines found
    assert sorted(lines) == sorted(expected_lines)
