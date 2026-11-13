"""The stored text of the yearly Baustellen review (``ensure_review`` in ``src/markdown_stats/year_review.py``).

Topic file of the tests described in ``tests/_year_review_support.py``:
every way :func:`ensure_review` can end, and the file it writes and reads.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.markdown_stats import year_review as yr
from src.markdown_stats.review_client import ReviewAnswer
from src.utils import stats
from tests._year_review_support import GOOD, NOW, REPO, SITES, YEAR, _answer, _ensure, _never, _with


# ---- the stored review ----------------------------------------------------------


def test_the_review_file_lives_next_to_the_ledger(tmp_path: Path) -> None:
    assert yr.review_path(YEAR, stats_dir=tmp_path) == tmp_path / "baustellen_rueckblick_2026.json"
    # The default folder is looked up at call time (the tests' fixture redirects it).
    assert yr.review_path(YEAR) == stats.DEFAULT_STATS_DIR / "baustellen_rueckblick_2026.json"
    assert stats.DEFAULT_STATS_DIR != REPO / "data" / "stats"


def test_an_accepted_answer_is_stored_and_returned(tmp_path: Path) -> None:
    prompts: list[str] = []

    def ask(prompt: str) -> ReviewAnswer | None:
        prompts.append(prompt)
        return ReviewAnswer(text=f"  {GOOD}\n", model="gemini-3.6-flash")

    outcome = _ensure(tmp_path, ask)
    assert outcome == yr.ReviewOutcome(text=GOOD, asked=True, reason="angenommen (gemini-3.6-flash)")
    assert prompts == [yr.build_prompt(SITES, year=YEAR)]
    path = tmp_path / "baustellen_rueckblick_2026.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "year": 2026, "created_at": "2027-01-01T00:01:30+01:00", "model": "gemini-3.6-flash", "text": GOOD, "sites": 4,
    }
    # Written whole or not at all: no temporary file stays behind.
    assert [entry.name for entry in tmp_path.iterdir()] == ["baustellen_rueckblick_2026.json"]
    assert yr.load_review(YEAR, SITES, stats_dir=tmp_path) == GOOD


def test_the_time_of_writing_is_vienna_time(tmp_path: Path) -> None:
    yr.ensure_review(YEAR, SITES, now=datetime(2027, 1, 3, 23, 30, 5, 999, tzinfo=UTC), stats_dir=tmp_path,
                     key="k", ask=_answer())
    stored = json.loads((tmp_path / "baustellen_rueckblick_2026.json").read_text(encoding="utf-8"))
    assert stored["created_at"] == "2027-01-04T00:30:05+01:00"


def test_a_stored_review_is_returned_without_a_request(tmp_path: Path) -> None:
    _ensure(tmp_path, _answer())
    before = (tmp_path / "baustellen_rueckblick_2026.json").read_bytes()
    outcome = _ensure(tmp_path, _never, key=None)
    assert outcome == yr.ReviewOutcome(text=GOOD, asked=False, reason="gespeicherter Rückblick")
    assert (tmp_path / "baustellen_rueckblick_2026.json").read_bytes() == before


def test_a_year_without_sites_is_not_reviewed(tmp_path: Path) -> None:
    outcome = _ensure(tmp_path, _never, sites=[])
    assert outcome == yr.ReviewOutcome(text=None, asked=False, reason="keine Baustellen im Jahr")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("env", [None, "", "   "])
def test_without_a_key_nothing_is_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                                        env: str | None) -> None:
    if env is None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GEMINI_API_KEY", env)
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        outcome = _ensure(tmp_path, _never, key=None)
    assert outcome == yr.ReviewOutcome(text=None, asked=False, reason="GEMINI_API_KEY ist leer, keine Anfrage")
    assert any("GEMINI_API_KEY ist leer" in record.getMessage() for record in caplog.records)
    assert list(tmp_path.iterdir()) == []
    # An empty key as an argument is no key either, whatever the environment holds.
    monkeypatch.setenv("GEMINI_API_KEY", "aus-der-umgebung")
    assert all(_ensure(tmp_path, _never, key=empty).asked is False for empty in ("", " "))


def test_the_key_of_the_environment_reaches_the_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_ask_review(prompt: str, *, key: str) -> ReviewAnswer | None:
        seen.append((prompt[:20], key))
        return ReviewAnswer(text=GOOD, model="gemini-3.8-flash")

    monkeypatch.setattr(yr, "ask_review", fake_ask_review)
    monkeypatch.setenv("GEMINI_API_KEY", "  aus-der-umgebung ")
    outcome = yr.ensure_review(YEAR, SITES, now=NOW, stats_dir=tmp_path)
    assert outcome.text == GOOD and outcome.asked
    assert seen == [("Du schreibst für ein", "aus-der-umgebung")]
    # The argument wins over the environment.
    (tmp_path / "baustellen_rueckblick_2026.json").unlink()
    yr.ensure_review(YEAR, SITES, now=NOW, stats_dir=tmp_path, key="als-argument")
    assert seen[-1][1] == "als-argument"


def test_a_rejected_answer_is_not_stored_and_the_log_says_why(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    key = "AIza" + "C" * 35
    bad = _with(f"Bitte beachten Sie die 77 Baustellen in der Zieglergasse {key}. " + "Noch ein Satz dazu. " * 150)
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        outcome = _ensure(tmp_path, _answer(bad, model="gemini-3.5-flash-lite"))
    assert outcome.text is None and outcome.asked
    assert outcome.reason.startswith("verworfen: Länge ")
    for reason in ("Zahl oder Kennung nicht in den Daten: 77", "Straßenname nicht in den Daten: Zieglergasse",
                   "Ich-Form, Anrede oder Rat im Text: bitte", "Formatierungszeichen im Text: [, ]"):
        assert reason in outcome.reason
    assert list(tmp_path.iterdir()) == []
    (message,) = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert message.startswith("Baustellen-Rückblick 2026 von gemini-3.5-flash-lite verworfen: Länge ")
    assert "Zahl oder Kennung nicht in den Daten: 77" in message
    # Of the text itself at most 400 cleaned characters, and no credential.
    logged = message.rsplit(" Text: ", 1)[1]
    assert logged.startswith("Im Jahr 2026 sind 4 Baustellen") and len(logged) <= 400
    assert key not in message and key not in outcome.reason


def test_no_answer_is_an_outcome(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        outcome = _ensure(tmp_path, lambda prompt: None)
    assert outcome == yr.ReviewOutcome(text=None, asked=True, reason="keine Antwort von Gemini")
    assert [record.getMessage() for record in caplog.records] == ["Baustellen-Rückblick 2026: keine Antwort von Gemini."]
    assert list(tmp_path.iterdir()) == []


def test_a_client_that_raises_is_an_outcome(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def ask(prompt: str) -> ReviewAnswer | None:
        raise RuntimeError("geheim-im-fehlertext")

    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        outcome = _ensure(tmp_path, ask)
    assert outcome == yr.ReviewOutcome(text=None, asked=True, reason="Anfrage gescheitert (RuntimeError)")
    assert "geheim-im-fehlertext" not in caplog.text and "RuntimeError" in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        b"kein JSON", b"", b'["eine", "Liste"]', b'{"year": 2026}', b'{"text": 5}', b'{"text": ""}', b'{"text": NaN}',
        b"\xff\xfe\x00", b"[" * 100_000,
        json.dumps({"text": GOOD, "fill": "x" * yr.MAX_REVIEW_FILE_BYTES}).encode(),
        # A stored text that would not come out of clean_review as it is was not written by this code.
        json.dumps({"text": GOOD + "\n\nNoch ein Absatz."}).encode(),
        json.dumps({"text": GOOD.replace("26A", "13A")}).encode(),
    ],
    ids=["garbage", "empty", "list", "no-text", "text-no-string", "text-empty", "nan", "no-utf8", "deep", "oversized",
         "two-paragraphs", "fails-guard"],
)
def test_an_unusable_file_is_no_review_and_gemini_is_asked_again(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "baustellen_rueckblick_2026.json"
    path.write_bytes(content)
    assert yr.load_review(YEAR, SITES, stats_dir=tmp_path) is None
    outcome = _ensure(tmp_path, _answer())
    assert outcome.text == GOOD and outcome.asked
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == GOOD


def test_a_stored_review_that_no_longer_fits_the_ledger_is_asked_again(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The ledger of a finished year can still change; the review must follow."""
    _ensure(tmp_path, _answer())
    # The Rennweg site left the ledger: "33A"-free GOOD still names the Rennweg, 30.09.2026 and the line 71.
    changed = SITES[:3]
    with caplog.at_level(logging.INFO, logger="generate_markdown_stats"):
        assert yr.load_review(YEAR, changed, stats_dir=tmp_path) is None
    assert any("passt nicht mehr zu den Daten" in record.getMessage() for record in caplog.records)
    new = ("Im Jahr 2026 sind 3 Baustellen mit Folgen für den öffentlichen Verkehr erfasst. Je eine lag in den Bezirken "
           "Landstraße, Neubau und Donaustadt. In der Burggasse wurde eine Haltestelle verlegt, in der Eßlinger "
           "Hauptstraße wurde der Busverkehr der Linie 26A umgeleitet. Die Arbeiten an der U4-Trasse begannen im Februar.")
    outcome = _ensure(tmp_path, _answer(new), sites=changed)
    assert outcome == yr.ReviewOutcome(text=new, asked=True, reason="angenommen (gemini-3.8-flash)")
    stored = json.loads((tmp_path / "baustellen_rueckblick_2026.json").read_text(encoding="utf-8"))
    assert (stored["text"], stored["sites"]) == (new, 3)
    # A second answer that fails leaves the stored file as it is, and no text is returned.
    outcome = _ensure(tmp_path, _answer(GOOD), sites=SITES[:2])
    assert outcome.text is None and outcome.asked
    assert json.loads((tmp_path / "baustellen_rueckblick_2026.json").read_text(encoding="utf-8"))["text"] == new


def test_an_accepted_text_that_cannot_be_stored_is_still_returned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocked = tmp_path / "keine-mappe"
    blocked.write_text("eine Datei, wo die Mappe sein sollte", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        outcome = _ensure(blocked, _answer())
    assert outcome.text == GOOD and outcome.asked
    assert outcome.reason.startswith("angenommen (gemini-3.8-flash), nicht gespeichert (")
    assert any("angenommen, aber nicht gespeichert" in record.getMessage() for record in caplog.records)


def test_ensure_review_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("Fehler im Wächter")

    monkeypatch.setattr(yr, "atomic_write", broken)  # the file is written through atomic_write, whole or not at all
    assert _ensure(tmp_path, _answer()).reason == "abgebrochen (RuntimeError)" and list(tmp_path.iterdir()) == []
    monkeypatch.setattr(yr, "check_review", broken)
    assert _ensure(tmp_path, _answer()) == yr.ReviewOutcome(text=None, asked=True, reason="abgebrochen (RuntimeError)")
    assert caplog.messages[-1] == "Baustellen-Rückblick 2026: abgebrochen (RuntimeError)." and caplog.records[-1].levelname == "WARNING"
    # Before the request: the outcome says that nothing was asked.
    monkeypatch.setattr(yr, "build_prompt", broken)
    assert _ensure(tmp_path, _never) == yr.ReviewOutcome(text=None, asked=False, reason="abgebrochen (RuntimeError)")


def test_the_model_name_is_cleaned_before_it_is_stored(tmp_path: Path) -> None:
    outcome = _ensure(tmp_path, _answer(model="gemini<script>\n" + "x" * 200))
    stored = json.loads((tmp_path / "baustellen_rueckblick_2026.json").read_text(encoding="utf-8"))
    assert outcome.text == GOOD and len(stored["model"]) <= 60 and "<" not in stored["model"] and "\n" not in stored["model"]
