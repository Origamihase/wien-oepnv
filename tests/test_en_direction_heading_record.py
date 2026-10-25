"""A relocation's direction headings read the same in English (2026-10-25).

For a stop moved in both directions the DE feed writes each direction as a
heading, "Richtung <first destination>: A → B" or "Richtung <destination>:
aufgelassen." (``_relocation_by_direction``). An arrow line was rendered
without the model, the bare closure went through it, and one notice read
"Towards Bhf. Atzgersdorf S: … Direction Hietzing U: Discontinued." (EN feed
2026-10-25). The bare closure is now a record line like "ersatzlos
aufgelassen", so both headings read "Towards". WL prose that opens the same
way, without an arrow pair or a closure, still goes through the model.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.build_feed as build_feed

STRAMPFERGASSE = (
    "Richtung Bhf. Atzgersdorf S: Hermesstraße nach Strampfergasse → etwa 100 Meter "
    "in Richtung Dr.-Schober-Straße. Richtung Hietzing U: aufgelassen. "
    "Haltestellenverlegung bzw. -auflassung …"
)
BAUERNFELDPLATZ = (
    "Richtung Döblinger Friedhof, Felix-Dahn-Straße: Liechtensteinstraße 42 → "
    "Liechtensteinstraße 38. Richtung Schottentor U: Liechtensteinstraße 37a "
    "gegenüber Bauernfeldplatz → …"
)


def _no_model(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    return calls


@pytest.mark.parametrize(
    ("german", "english"),
    [
        (
            STRAMPFERGASSE,
            "Towards Bhf. Atzgersdorf S: Hermesstraße after Strampfergasse → approx. 100 "
            "metres towards Dr.-Schober-Straße. Towards Hietzing U: Discontinued. "
            "stop relocation or closure …",
        ),
        (
            BAUERNFELDPLATZ,
            "Towards Döblinger Friedhof, Felix-Dahn-Straße: Liechtensteinstraße 42 → "
            "Liechtensteinstraße 38. Towards Schottentor U: Liechtensteinstraße 37a "
            "opposite Bauernfeldplatz → …",
        ),
    ],
)
def test_every_direction_heading_reads_towards_without_the_model(
    monkeypatch: pytest.MonkeyPatch, german: str, english: str
) -> None:
    calls = _no_model(monkeypatch)
    out = build_feed._translate_text_attempt(german, ident="probe", source="Wiener Linien")
    assert out == english
    assert calls == []


@pytest.mark.parametrize(
    "line",
    [
        "Richtung Hietzing U: aufgelassen.",
        "Richtung Hietzing U: aufgelassen",
        "Hartäckerstraße 65: ersatzlos aufgelassen.",
    ],
)
def test_a_closure_line_is_a_record(line: str) -> None:
    assert build_feed._RELOCATION_CLOSED_LINE_RE.search(line)


@pytest.mark.parametrize(
    "prose",
    [
        "Richtung Prater: Umstieg am Julius-Raab-Pl.",
        "Die Haltestelle wird aufgelassen.",
        "Richtung Floridsdorf: Die Haltestelle Kagran wird aufgelassen und später neu errichtet.",
    ],
)
def test_prose_that_mentions_a_closure_stays_with_the_model(prose: str) -> None:
    assert not build_feed._RELOCATION_CLOSED_LINE_RE.search(prose)


def test_the_both_ways_heading_has_one_english_name() -> None:
    glossed, mapping = build_feed._apply_domain_glossary(
        "Haltestellenverlegung bzw. -auflassung der Linien 56A und 56B.", source="Wiener Linien"
    )
    assert list(mapping.values()) == ["stop relocation or closure"], glossed
