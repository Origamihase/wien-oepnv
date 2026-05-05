"""Tests for the resolution-quality filters in fetch_vor_haltestellen.py.

These pin the bad-match rejections that the 2026-05 cron run surfaced
(Roma Termini → Wels Hbf, Rennweg → Katschberg, Laxenburg → HLW etc.)
without regressing the legit Hainburg/Donau S7 stops, which look like
bus-stop names but are real S-Bahn stations.
"""
from __future__ import annotations

import pytest

from scripts.fetch_vor_haltestellen import (
    _MIN_ACCEPTABLE_SCORE,
    _score_candidate,
)


@pytest.mark.parametrize(
    "station, candidate, ext_id",
    [
        # Bus-stop suffix (HLW = Höhere Lehranstalt)
        ("Laxenburg-Biedermannsdorf", "Biedermannsdorf HLW", "430316500"),
        # Bus-stop suffix (Grenzweg = bus stop on the border road)
        ("Weigelsdorf", "Weigelsdorf Grenzweg", "430518600"),
        # Cemetery, not the train stop
        ("Himberg bei Wien", "Himberg (bei Wien) Friedhof", "430373600"),
        # Different region (Steiermark, not VOR)
        ("Himberg", "Himberg (Deutschfeistritz) Haltestelle Bahnhof", "460034600"),
        # Different region (Bayern)
        ("München Hauptbahnhof", "München Hauptbahnhof Süd (Bayern)", "501471100"),
        # Different region (Oberösterreich) AND a bus terminal
        ("Roma Termini", "Wels (OÖ) Hbf (Busterminal)", "444204600"),
        # Village-centre suffix (Salzburg, not the Vienna U3 Rennweg)
        ("Rennweg", "Rennweg am Katschberg Ort", "420600500"),
        # Bus terminal far away
        ("Laa an der Thaya", "Waidhofen an der Thaya Busbahnhof", "430596700"),
        # First-word mismatch + same disambiguation suffix
        ("Tulln an der Donau", "Höflein an der Donau Bahnhof", "430380000"),
        ("Haslau an der Donau", "Höflein an der Donau Bahnhof", "430380000"),
        # New 2026-05 cron false positives caught by bus-suffix filter
        ("Tulln an der Donau", "Tulln An der Wehr", "738031699"),
        ("Weigelsdorf", "Weigelsdorf Judenweg", "430586900"),
        ("Laxenburg-Biedermannsdorf", "Laxenburg Guntramsdorfer Straße", "430615800"),
        ("Himberg", "Himberg (bei Wien) Gutenhof", "430361800"),
        ("Himberg bei Wien", "Himberg (bei Wien) Gutenhof", "430361800"),
        # 2026-05-05 cron survivors: compound 'gasse'/'platz' suffix and
        # 9xx synthetic ext_id without rail token. Without the extended
        # bus-suffix end-anchor and the new 9xx-no-rail check these
        # candidates land in stations.json with a wrong vor_id (the cron
        # of 2026-05-05 22:07 published exactly these three).
        ("Weigelsdorf", "Weigelsdorf Kienergasse", "430518500"),
        ("Himberg", "Himberg (bei Wien) Hauptplatz", "430373800"),
        ("Himberg bei Wien", "Himberg (bei Wien) Hauptplatz", "430373800"),
        ("Laxenburg-Biedermannsdorf", "BIEDERMANNSDORF", "900022021"),
    ],
    ids=[
        "laxenburg→hlw",
        "weigelsdorf→grenzweg",
        "himberg→friedhof",
        "himberg→steiermark",
        "muenchen→bayern",
        "roma→oö-busterminal",
        "rennweg→katschberg-ort",
        "laa→busbahnhof",
        "tulln→hoeflein",
        "haslau→hoeflein",
        "tulln→an-der-wehr",
        "weigelsdorf→judenweg",
        "laxenburg→guntramsdorfer-straße",
        "himberg→gutenhof",
        "himberg-bei-wien→gutenhof",
        "weigelsdorf→kienergasse-compound",
        "himberg→hauptplatz-compound",
        "himberg-bei-wien→hauptplatz-compound",
        "laxenburg→BIEDERMANNSDORF-9xx",
    ],
)
def test_score_rejects_bad_match(station: str, candidate: str, ext_id: str) -> None:
    score = _score_candidate(station, candidate, ext_id)
    assert score < _MIN_ACCEPTABLE_SCORE, (
        f"Expected reject (score < {_MIN_ACCEPTABLE_SCORE}) for {station!r} → "
        f"{candidate!r}, got {score:.1f}"
    )


@pytest.mark.parametrize(
    "station, candidate, ext_id",
    [
        # Identical canonical name
        ("Wien Karlsplatz", "Wien Karlsplatz", "490065700"),
        # Suffix "Bahnhof" added by VOR — legit
        ("Pfaffstätten", "Pfaffstätten Bahnhof", "430453300"),
        # Disambiguation suffix added — legit
        ("Hohenau", "Hohenau an der March Bahnhof", "430377800"),
        ("Götzendorf", "Götzendorf/Leitha Bahnhof", "430365500"),
        ("Hennersdorf", "Hennersdorf bei Wien Bahnhof", "430372300"),
        # S7 Hainburg stops opened 2017 — names look like bus stops but
        # are real train stations. Must not be rejected.
        ("Hainburg Kulturfabrik", "Hainburg/Donau Kulturfabrik", "430368100"),
        ("Hainburg Ungartor", "Hainburg/Donau Ungartor/B9", "430367700"),
        # Wien stations that have "Straße" in the name — legitimate rail
        # stops. The bus-suffix filter must not reject them when the
        # candidate is essentially identical (ratio >= 0.85).
        ("Wien Brünner Straße", "Wien Brünner Straße", "490017600"),
        ("Wien Krottenbachstraße", "Wien Krottenbachstraße", "490072300"),
        ("Wien Geiselbergstraße", "Wien Geiselbergstraße", "490048400"),
        ("Wien Erzherzog Karl-Straße", "Wien Erzherzog-Karl-Straße", "490028800"),
        # Closes the Top-12 priority-1 gap: Guntramsdorf Bahnhof must
        # remain a high-confidence match (4xx ext_id, "Bahnhof" rail
        # token, candidate is canonical name + suffix). Regression guard
        # for the new 9xx-no-rail-token reject — Guntramsdorf is 4xx,
        # so the new rule must not apply.
        ("Guntramsdorf Südbahn", "Guntramsdorf Bahnhof", "430361600"),
        # The Karlsplatz identity match also exercises the extended
        # end-of-word "platz" rule: with the old pattern "platz" only
        # matched as a standalone token (\bplatz\b) and never on
        # "Karlsplatz". The new pattern matches it but the 0.85 ratio
        # guard saves identity matches like this one.
        ("Wien Stephansplatz", "Wien Stephansplatz", "490085600"),
    ],
    ids=[
        "karlsplatz",
        "pfaffstaetten+bahnhof",
        "hohenau+disambiguation",
        "goetzendorf+slash",
        "hennersdorf+bei-wien",
        "hainburg-kulturfabrik-S7",
        "hainburg-ungartor-S7",
        "wien-brünner-straße",
        "wien-krottenbachstraße",
        "wien-geiselbergstraße",
        "wien-erzherzog-karl-straße",
        "guntramsdorf-südbahn→bahnhof",
        "wien-stephansplatz-identity",
    ],
)
def test_score_accepts_good_match(station: str, candidate: str, ext_id: str) -> None:
    score = _score_candidate(station, candidate, ext_id)
    assert score >= _MIN_ACCEPTABLE_SCORE, (
        f"Expected accept (score ≥ {_MIN_ACCEPTABLE_SCORE}) for {station!r} → "
        f"{candidate!r}, got {score:.1f}"
    )
