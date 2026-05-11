from __future__ import annotations

from pathlib import Path

import pytest

from src.utils import configuration_wizard as wizard


def test_compute_non_interactive_defaults() -> None:
    result = wizard.compute_non_interactive_configuration({}, {}, accept_defaults=True)

    assert result.managed["FEED_TITLE"] == wizard.CONFIG_OPTIONS[1].default_str()
    assert result.managed["WL_ENABLE"] == "true"
    assert "VOR_ACCESS_ID" not in result.managed
    assert any("VOR_ACCESS_ID" in warning for warning in result.warnings)


def test_compute_non_interactive_overrides_and_custom_entries() -> None:
    existing = {"FEED_TITLE": "Custom Feed", "CUSTOM_FLAG": "enabled"}
    overrides = {"MAX_ITEMS": "25", "EXTRA_SETTING": "value"}

    result = wizard.compute_non_interactive_configuration(existing, overrides, accept_defaults=False)

    assert result.managed["FEED_TITLE"] == "Custom Feed"
    assert result.managed["MAX_ITEMS"] == "25"
    assert result.custom["CUSTOM_FLAG"] == "enabled"
    assert result.custom["EXTRA_SETTING"] == "value"
    assert (None, "25") in result.changes.values()


def test_format_env_document_includes_secret_placeholder(tmp_path: Path) -> None:
    managed = {
        "OUT_PATH": "docs/feed.xml",
        "FEED_TITLE": "Titel",
        "FEED_DESC": "Beschreibung",
        "FEED_LINK": "https://example.invalid",
        "MAX_ITEMS": "10",
        "FEED_TTL": "15",
        "MAX_ITEM_AGE_DAYS": "365",
        "ABSOLUTE_MAX_AGE_DAYS": "540",
        "ENDS_AT_GRACE_MINUTES": "10",
        "PROVIDER_TIMEOUT": "25",
        "PROVIDER_MAX_WORKERS": "0",
        "STATE_RETENTION_DAYS": "60",
        "WL_ENABLE": "true",
        "OEBB_ENABLE": "true",
        "BAUSTELLEN_ENABLE": "true",
    }
    document = wizard.format_env_document(managed, {})

    assert "# VOR_ACCESS_ID" in document
    assert "OUT_PATH=docs/feed.xml" in document


def test_format_env_document_escapes_newlines() -> None:
    managed = {
        "FEED_TITLE": "Titel",
        "FEED_DESC": "Line 1\nLine 2",
        "FEED_LINK": "https://example.invalid",
        "MAX_ITEMS": "10",
        "FEED_TTL": "15",
        "MAX_ITEM_AGE_DAYS": "365",
        "ABSOLUTE_MAX_AGE_DAYS": "540",
        "ENDS_AT_GRACE_MINUTES": "10",
        "PROVIDER_TIMEOUT": "25",
        "PROVIDER_MAX_WORKERS": "0",
        "STATE_RETENTION_DAYS": "60",
        "WL_ENABLE": "true",
        "OEBB_ENABLE": "true",
        "BAUSTELLEN_ENABLE": "true",
    }

    document = wizard.format_env_document(managed, {})

    assert 'FEED_DESC="Line 1\\nLine 2"' in document


def test_calculate_changes_handles_add_and_remove() -> None:
    previous = {"FEED_TITLE": "Alt", "OLD_KEY": "value"}
    current = {"FEED_TITLE": "Neu", "NEW_KEY": "value"}

    diff = wizard.calculate_changes(previous, current)

    assert diff["FEED_TITLE"] == ("Alt", "Neu")
    assert diff["OLD_KEY"] == ("value", None)
    assert diff["NEW_KEY"] == (None, "value")


@pytest.mark.parametrize(
    "value, expected",
    [
        # Empty marker is preserved.
        ("", "<leer>"),
        # Short secrets (≤8 chars) reveal nothing — previously a 5-char value
        # leaked 4/5 ≈ 80% of its content via the ``ab***de`` shape.
        ("a", "***"),
        ("abcd", "***"),
        ("abcde", "***"),
        ("abcdefgh", "***"),
        # 9-20 chars: only 2 chars at each end so a 16-char access ID exposes
        # 4/16 = 25% (was 25% before; boundary unchanged).
        ("abcdefghi", "ab***hi"),
        ("abcdefghijklmnop", "ab***op"),
        ("abcdefghijklmnopqrst", "ab***st"),
        # >20 chars: 4 chars at each end is acceptable because the relative
        # leak is small (e.g. 8/40 = 20% for a 40-char token).
        ("abcdefghijklmnopqrstu", "abcd***rstu"),
        ("abcdefghijklmnopqrstuvwxyz0123456789", "abcd***6789"),
    ],
)
def test_mask_value_tiered_redaction(value: str, expected: str) -> None:
    """Tiered masking prevents 50% leak on short secrets; mirrors `_mask_secret`."""
    assert wizard.mask_value(value) == expected
