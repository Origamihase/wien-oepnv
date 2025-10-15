#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for :mod:`src.utils.ids`."""

import hashlib

from src.utils.ids import make_guid


def test_make_guid_matches_sha256_digest_for_parts():
    """The GUID uses the SHA256 digest of the pipe-joined parts."""

    parts = ("line", "station", "direction")
    expected = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    assert make_guid(*parts) == expected


def test_make_guid_treats_falsy_values_as_empty_strings():
    """Falsy values are coerced to empty strings before hashing."""

    assert make_guid("line", None, "station") == make_guid("line", "", "station")
    assert make_guid("", "", "") == make_guid(None, None, None)


def test_make_guid_is_stable_for_unicode_content():
    """Unicode input is supported and results in stable GUIDs."""

    unicode_parts = ("ßtraße", "🚉", "向东")
    first_call = make_guid(*unicode_parts)
    second_call = make_guid(*unicode_parts)

    assert first_call == second_call
    assert len(first_call) == 64  # Length of SHA256 hex digest.
