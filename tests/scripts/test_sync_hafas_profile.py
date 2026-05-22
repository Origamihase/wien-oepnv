"""Tests for :mod:`scripts.sync_hafas_profile`.

The script is the writer-side guard against upstream HAFAS profile
drift. These tests pin two contracts:

1. ``_validate_profile_document`` rejects every structurally-invalid
   profile shape the regex extractor could conceivably produce against
   a future upstream refactor (renamed key, embedded placeholder string,
   accidentally-matched unrelated literal).
2. ``main`` short-circuits with exit code 1 BEFORE
   :func:`_write_profile` touches the disk when validation fails — so
   a corrupt profile never reaches the on-disk cache.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from scripts import sync_hafas_profile as script


def _valid_extracted() -> dict[str, str]:
    """Return the minimal extracted-profile dict that builds a valid document.

    Values mirror the current ÖBB upstream observed in
    ``data/hafas_profile.json`` so the success path runs through the
    same validator gates the cron job would hit.
    """
    return {"ver": "1.45", "aid": "OWDL4fE4ixNiPBBm"}


def _valid_profile() -> dict[str, Any]:
    """Return the assembled profile that mirrors ``data/hafas_profile.json``."""
    return script._build_profile_document(_valid_extracted())


# ---------------------------------------------------------------------------
# Validator — happy path
# ---------------------------------------------------------------------------


def test_validate_accepts_canonical_profile() -> None:
    """The current ÖBB upstream profile shape must pass validation
    unchanged. Regression-pins that the validator's allow-list is wide
    enough to not break the actual production payload."""
    script._validate_profile_document(_valid_profile())


def test_validate_accepts_profile_with_salt() -> None:
    """When upstream restores HMAC signing, the ``salt`` field will be
    a non-empty alphanumeric token. The validator must accept it."""
    profile = _valid_profile()
    profile["salt"] = "abcdef0123456789"
    script._validate_profile_document(profile)


def test_validate_accepts_versioned_patch_release() -> None:
    """Future HAFAS releases may bump from ``1.45`` to ``1.46.LIVE``."""
    profile = _valid_profile()
    profile["ver"] = "1.46.LIVE"
    script._validate_profile_document(profile)


# ---------------------------------------------------------------------------
# Validator — structural failures
# ---------------------------------------------------------------------------


def test_validate_rejects_non_dict_profile() -> None:
    with pytest.raises(script.HafasProfileValidationError, match="must be a dict"):
        script._validate_profile_document(["not", "a", "dict"])


def test_validate_rejects_missing_top_level_key() -> None:
    profile = _valid_profile()
    profile.pop("ver")
    with pytest.raises(
        script.HafasProfileValidationError, match="missing top-level keys"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_non_string_salt() -> None:
    profile = _valid_profile()
    profile["salt"] = 12345
    with pytest.raises(
        script.HafasProfileValidationError, match="'salt' must be a string"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_oversized_field() -> None:
    profile = _valid_profile()
    profile["salt"] = "A" * (script._PROFILE_MAX_FIELD_LEN + 1)
    with pytest.raises(
        script.HafasProfileValidationError, match="'salt' too long"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_short_ver() -> None:
    profile = _valid_profile()
    profile["ver"] = "x"
    with pytest.raises(
        script.HafasProfileValidationError, match="'ver' too short"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_malformed_ver() -> None:
    """``ver`` must match ``MAJOR.MINOR[.PATCH]`` — a freeform string
    extracted from an upstream comment would otherwise corrupt the
    HAFAS request envelope."""
    profile = _valid_profile()
    profile["ver"] = "TODO"
    with pytest.raises(
        script.HafasProfileValidationError, match="'ver' has invalid format"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_non_dict_auth() -> None:
    profile = _valid_profile()
    profile["auth"] = "AID"
    with pytest.raises(
        script.HafasProfileValidationError, match="'auth' must be a dict"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_missing_auth_aid() -> None:
    profile = _valid_profile()
    profile["auth"] = {"type": "AID"}
    with pytest.raises(
        script.HafasProfileValidationError,
        match="'auth' missing required key 'aid'",
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_short_auth_aid() -> None:
    profile = _valid_profile()
    profile["auth"] = {"type": "AID", "aid": "short"}
    with pytest.raises(
        script.HafasProfileValidationError, match="'auth.aid' too short"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_aid_with_whitespace() -> None:
    """A regex-extracted ``aid`` containing whitespace points at a
    drifted upstream — the canonical token is solid alphanumeric."""
    profile = _valid_profile()
    profile["auth"] = {"type": "AID", "aid": "OWDL4fE4 ixNiPBBm"}
    with pytest.raises(
        script.HafasProfileValidationError,
        match="'auth.aid' has invalid format",
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_non_dict_client() -> None:
    profile = _valid_profile()
    profile["client"] = "webapp"
    with pytest.raises(
        script.HafasProfileValidationError, match="'client' must be a dict"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_missing_client_key() -> None:
    profile = _valid_profile()
    profile["client"] = {"id": "OEBB", "type": "WEB", "name": "webapp"}
    with pytest.raises(
        script.HafasProfileValidationError,
        match="'client' missing required key 'l'",
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_empty_client_value() -> None:
    profile = _valid_profile()
    profile["client"] = {"id": "OEBB", "type": "WEB", "name": "", "l": "vs_webapp"}
    with pytest.raises(
        script.HafasProfileValidationError, match="'client.name' too short"
    ):
        script._validate_profile_document(profile)


def test_validate_rejects_short_non_empty_salt() -> None:
    """A non-empty salt that's only a couple of bytes long is almost
    certainly an extraction artefact (e.g. matched the wrong literal).
    The validator's minimum-length floor catches it."""
    profile = _valid_profile()
    profile["salt"] = "abc"
    with pytest.raises(
        script.HafasProfileValidationError, match="'salt' has invalid format"
    ):
        script._validate_profile_document(profile)


# ---------------------------------------------------------------------------
# main() integration — corrupt profile never touches disk
# ---------------------------------------------------------------------------


def test_main_returns_1_on_validation_failure_without_writing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The crucial cron-pipeline contract: when the upstream extractor
    returns a syntactically valid but semantically broken triple, ``main``
    must exit with code 1 BEFORE :func:`_write_profile` touches the
    on-disk JSON. Otherwise the next cron tick caches the corrupt
    profile, every HAFAS enrichment fails, and the fallthrough drains
    the Google Places quota."""
    output = tmp_path / "hafas_profile.json"

    # ``ver="TODO"`` passes the extractor's loose regex but is rejected by
    # ``_validate_profile_document`` — the validator gate fires before
    # the writer is called.
    with patch.object(
        script, "_fetch_combined_source", return_value="<js source>"
    ), patch.object(
        script, "_extract_profile", return_value={"ver": "TODO", "aid": "shortaid"}
    ):
        caplog.set_level(logging.ERROR, logger=script.LOGGER.name)
        exit_code = script.main(["--output", str(output)])

    assert exit_code == 1
    assert not output.exists(), (
        "The writer must not run when validation rejects the profile; "
        "an unbuffered partial write would still cache a broken profile."
    )
    assert any(
        "structural validation" in record.getMessage()
        for record in caplog.records
    ), "Operators need a clear log line naming the validation failure."


def test_main_succeeds_when_extracted_triple_is_canonical(
    tmp_path: Path,
) -> None:
    """End-to-end smoke for the happy path: a canonical extracted triple
    rounds through validation and reaches :func:`_write_profile`."""
    output = tmp_path / "hafas_profile.json"

    with patch.object(
        script, "_fetch_combined_source", return_value="<js source>"
    ), patch.object(script, "_extract_profile", return_value=_valid_extracted()):
        exit_code = script.main(["--output", str(output)])

    assert exit_code == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ver"] == "1.45"
    assert payload["auth"] == {"type": "AID", "aid": "OWDL4fE4ixNiPBBm"}
    assert payload["client"] == {
        "id": "OEBB",
        "type": "WEB",
        "name": "webapp",
        "l": "vs_webapp",
    }
