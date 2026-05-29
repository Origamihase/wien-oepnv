"""Regression tests for the round-10 scripts bundle.

Pins seven defence-in-depth fixes across the auxiliary maintenance
scripts:

1. ``scripts/scaffold_provider_plugin.py`` template no longer carries
   a duplicate ``timezone`` import (ruff ``F811``).
2. ``scripts/check_overpass_status.py`` ``_resolve_endpoint`` raises
   on an unrecognised ``--endpoint`` override instead of silently
   substituting the default mirror.
3. ``scripts/check_overpass_status.py`` ``main`` maps that
   ``ValueError`` to exit code 2.
4. ``scripts/update_baustellen_cache.py`` parses ``YYYY-MM-DDZ``
   shapes as Vienna-local midnight (the trailing ``Z`` is a date-shape
   marker, NOT a UTC tz indicator).
5. ``scripts/update_stammstrecke_hbf.py`` ``_query_departure_board``
   surfaces an HTTP-200 VAO error envelope (``errorCode``) as a
   ``ValueError`` rather than treating it as "0 departures".
6. ``scripts/update_stammstrecke_hbf.py`` ``_collect_hbf_observations``
   keeps cancelled departures that lack ``rtTrack``, so terminus-based
   direction resolution can still route them into the Ausfaelle
   ledger.
7. ``scripts/enrich_station_aliases.py`` reads the VOR CSV via
   ``utf-8-sig`` so a BOM-prefixed file doesn't drop the first row.
"""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from scripts import check_overpass_status, update_baustellen_cache
from scripts import enrich_station_aliases as enrich
import scripts.update_stammstrecke_hbf as hbf_script


# ---------------------------------------------------------------------------
# Fix #1 — scaffold template has no duplicate timezone import
# ---------------------------------------------------------------------------


def test_scaffold_template_is_a_valid_python_module() -> None:
    """The generated plugin must parse as valid Python with no F811
    drift — pre-fix the template carried
    ``from datetime import datetime, timezone, timezone`` which ruff
    flagged on every scaffolded module until hand-edited."""
    from scripts.scaffold_provider_plugin import TEMPLATE

    tree = ast.parse(TEMPLATE)
    # Walk imports and confirm every alias inside any ``from datetime``
    # statement is unique.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            names = [alias.name for alias in node.names]
            assert len(names) == len(set(names)), (
                f"datetime import has duplicate names: {names}"
            )


# ---------------------------------------------------------------------------
# Fix #2 + #3 — check_overpass_status endpoint validation + main exit code
# ---------------------------------------------------------------------------


def test_resolve_endpoint_raises_on_unknown_override() -> None:
    """Pre-fix an unrecognised ``--endpoint`` silently fell back to the
    default mirror. Operators believed they tested mirror X but actually
    tested mirror Y. Now raises so ``main`` can surface the misconfig.
    """
    with pytest.raises(ValueError, match="not on the trusted"):
        check_overpass_status._resolve_endpoint(
            "https://overpass-api.de/api/interpreter/"  # trailing slash
        )


def test_main_returns_2_on_invalid_endpoint_override() -> None:
    """The ``ValueError`` raised by ``_resolve_endpoint`` must be caught
    by ``main`` and mapped to exit code 2."""
    code = check_overpass_status.main(
        ["--endpoint", "https://attacker.example.com/api/interpreter"]
    )
    assert code == 2


# ---------------------------------------------------------------------------
# Fix #4 — baustellen YYYY-MM-DDZ stays at Vienna midnight
# ---------------------------------------------------------------------------


def test_parse_datetime_keeps_date_only_z_at_vienna_midnight() -> None:
    """``2026-03-22Z`` must yield ``2026-03-22 00:00 Vienna`` — pre-fix
    it was expanded to ``2026-03-22T00:00:00+00:00`` (UTC midnight) and
    then converted to Vienna time, shifting to ``02:00`` (DST) and
    risking a date flip across DST boundaries."""
    parsed = update_baustellen_cache._parse_datetime("2026-03-22Z")
    assert parsed is not None
    vienna = ZoneInfo("Europe/Vienna")
    assert parsed == datetime(2026, 3, 22, 0, 0, tzinfo=vienna)


# ---------------------------------------------------------------------------
# Fix #5 — VAO errorCode payload surfaces as ValueError
# ---------------------------------------------------------------------------


def test_query_departure_board_raises_on_vao_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP-200 response with an ``errorCode`` payload must NOT be
    silently treated as "0 departures". Pre-fix a VAO auth/quota
    failure (``H730``) was returned as an empty list and the cron tick
    reported "ok" — the dashboard then showed unexplained multi-hour
    gaps. Now raises so the diagnostic branch logs the failure.
    """
    poisoned_payload = {"errorCode": "H730", "errorText": "QUOTA_EXCEEDED"}

    class _FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = '{"errorCode": "H730"}'
        content = b'{"errorCode": "H730"}'

        def json(self, **_kwargs: Any) -> dict[str, Any]:
            return poisoned_payload

    monkeypatch.setattr(
        hbf_script, "request_safe", lambda *_a, **_kw: _FakeResponse()
    )
    # Avoid touching the live VOR base URL / auth.
    monkeypatch.setattr(
        hbf_script.vor_provider, "VOR_BASE_URL", "https://example.invalid/"
    )

    session = MagicMock()
    when = datetime(2026, 5, 15, 8, 0, tzinfo=ZoneInfo("Europe/Vienna"))
    with pytest.raises(ValueError, match="H730"):
        hbf_script._query_departure_board(session, when=when, timeout=1)


# ---------------------------------------------------------------------------
# Fix #6 — cancelled departures without rtTrack are routed, not dropped
# ---------------------------------------------------------------------------


def test_collect_observations_keeps_cancelled_no_track_with_known_terminus() -> None:
    """A cancelled S-Bahn departure missing ``rtTrack`` but bound for a
    known northern terminus must surface in the Praterstern bucket as
    an Ausfall — not vanish into the ``dropped_no_track`` counter.

    The pre-fix gate ran the track check BEFORE the cancellation check,
    so a cancelled train whose ``rtTrack`` had been removed by VAO (the
    canonical "train never arrives at any platform" shape) was silently
    dropped and never appeared in the Ausfaelle ledger.
    """
    dep = {
        "name": "S40",
        "Product": [{"catOut": "S 40"}],
        "date": "2026-05-15",
        "time": "08:00:00",
        # No ``rtTrack`` / ``track`` — the cancellation removed it.
        "direction": "Wien Praterstern",
        "cancelled": True,
    }
    by_direction, _stats = hbf_script._collect_hbf_observations([dep])
    # Praterstern bucket must contain the cancellation observation.
    praterstern_bucket = by_direction.get(
        hbf_script.DIRECTION_LABEL_NORTHBOUND
    )
    assert praterstern_bucket is not None
    assert any(obs.cancelled for obs in praterstern_bucket), (
        "cancelled-no-track departure was not routed into the bucket"
    )


# ---------------------------------------------------------------------------
# Fix #7 — VOR CSV is read via utf-8-sig (BOM-tolerant)
# ---------------------------------------------------------------------------


def test_load_vor_names_preserves_first_row_with_bom_prefix(
    tmp_path: Path,
) -> None:
    """A BOM-prefixed VOR CSV must NOT drop the first row.

    Pre-fix the file was read with ``encoding="utf-8"`` so the BOM
    survived as part of the first header cell (``"\\ufeffStopPointId"``)
    — ``row.get("StopPointId")`` then returned ``None`` for the first
    row only, silently losing that stop's name from the alias index.
    """
    csv_path = tmp_path / "vor-haltestellen.csv"
    csv_path.write_text(
        "﻿StopPointId;StopPointName\n"
        "900100;Wien Hauptbahnhof\n"
        "900101;Wien Meidling\n",
        encoding="utf-8",
    )

    names = enrich._load_vor_names(csv_path)
    # All three rows survive — pre-fix the first one was dropped.
    assert names["900100"] == "Wien Hauptbahnhof"
    assert names["900101"] == "Wien Meidling"
