# ruff: noqa: S603, S607
"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_ki_waechter.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import ki_waechter as kw

REPO = Path(__file__).resolve().parents[2]

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
<lastBuildDate>Tue, 27 Oct 2026 16:00:59 +0100</lastBuildDate>
{items}
</channel></rss>
"""

_ITEM = "<item><title>{t}</title><description>{d}</description><guid>{g}</guid></item>"


_DE_DEFAULT = [
        ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Heute]", "g1"),
        ("89A: Streckensperre", "Wegen einer Streckensperre des Algenwegs wird die Linie 89A umgeleitet.", "g2"),
        ("40A: Bauernfeldplatz", "Liechtensteinstraße 42 → Liechtensteinstraße 38.", "g3"),
]

_EN_DEFAULT = [
        ("40A: Construction works", "Buses stop at Liechtensteinstraße 39. [Today]", "g1"),
        ("89A: Line closure", "Due to a line closure of the algae path, line 89A is diverted.", "g2"),
        ("40A: Bauernfeldplatz", "Liechtensteinstraße 42 → Liechtensteinstraße 38.", "g3"),
]


def _write_feeds(root: Path, de: list[tuple[str, str, str]], en: list[tuple[str, str, str]],
                 built: str = "Tue, 27 Oct 2026 16:00:59 +0100") -> None:
    (root / "docs").mkdir(exist_ok=True)
    for name, rows in (("feed.xml", de), ("feed.en.xml", en)):
        items = "\n".join(_ITEM.format(t=t, d=d, g=g) for t, d, g in rows)
        feed = _FEED.format(items=items).replace("Tue, 27 Oct 2026 16:00:59 +0100", built)
        (root / "docs" / name).write_text(feed, encoding="utf-8")


def _write_repo(root: Path) -> None:
    _write_feeds(root, _DE_DEFAULT, _EN_DEFAULT)
    (root / "cache" / "wl_x").mkdir(parents=True)
    events = [
        {"guid": "g1", "title": "40A: Busse halten Liechtensteinstraße 39",
         "description": "Bauarbeiten Busse halten Liechtensteinstraße 39"},
        {"guid": "g2", "title": "89A: Streckensperre",
         "description": "<p>Wegen einer angeordneten Streckensperre des Algenwegs wird die Linie 89A umgeleitet.</p>"},
        {"guid": "g3", "title": "40A: Bauernfeldplatz",
         "description": "Haltestellenverlegung der Linie 40A Von: Liechtensteinstraße 42 Nach: Liechtensteinstraße 38"},
        {"guid": "g9", "title": "13A: Falschparker", "description": "Fahrtbehinderung"},
    ]
    (root / "cache" / "wl_x" / "events.json").write_text(json.dumps(events), encoding="utf-8")


@pytest.fixture()
def prep(tmp_path: Path) -> kw.Prepared:
    _write_repo(tmp_path)
    read, listing = kw.working_tree_reader(tmp_path)
    return kw.prepare([kw.Snapshot("abc1234", "", read, listing)], "abc1234")


def _answer(*funde: dict[str, object], geprueft: tuple[int, ...] = (1, 2, 3)) -> dict[str, object]:
    return {"geprueft": list(geprueft), "funde": list(funde), "vermutungen": []}


# --- 24 hours of feed versions from the git history --------------------------------


def _git(root: Path, *args: str, when: str = "") -> None:
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), **env})


def _history(root: Path) -> None:
    """Four Update cycles: one older than 24 h, then 40A changes its time line and 13A comes and goes."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.org")
    _git(root, "config", "user.name", "t")
    _write_repo(root)
    a40 = ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Heute]", "g1")
    a40b = ("40A: Bauarbeiten", "Busse halten Liechtensteinstraße 39. [Seit 14:00]", "g1")
    b13 = ("40A: Falschparker", "Busse halten Liechtensteinstraße 39 nicht.", "g9")
    old = ("U6: Alt", "Text von vorgestern.", "g0")
    steps = [
        ("2026-10-26T10:00:00+01:00", [old], "Mon, 26 Oct 2026 10:00:00 +0100"),
        ("2026-10-27T08:00:00+01:00", [a40], "Tue, 27 Oct 2026 08:00:00 +0100"),
        ("2026-10-27T12:00:00+01:00", [a40b, b13], "Tue, 27 Oct 2026 12:00:00 +0100"),
        ("2026-10-27T16:00:00+01:00", [a40b], "Tue, 27 Oct 2026 16:00:00 +0100"),
    ]
    for when, rows, built in steps:
        _write_feeds(root, rows, rows, built)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "chore: update cycle", when=when)


# --- the Gemini call -----------------------------------------------------------------


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self.content = json.dumps(payload).encode("utf-8")


def _ok_payload(text: str, reason: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": "nachgedacht", "thought": True}, {"text": text}]},
                        "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 30000, "candidatesTokenCount": 400, "thoughtsTokenCount": 9000},
    }


def _overloaded_then(monkeypatch: pytest.MonkeyPatch, replies: list[_Response]) -> list[str]:
    calls: list[str] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return replies.pop(0)

    monkeypatch.setattr(kw, "request_safe", fake_request)
    return calls


# --- the gate of the workflow ----------------------------------------------------


def _run_gate(tmp_path: Path, event: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Run the gate script of ``ki-waechter.yml`` for *event* and return its outputs.

    Since 2026-11-05 the gate runs only on a dispatch of the Update cycle
    (``repository_dispatch``), a pull request or a manual start, and names the
    commits to audit; the record and the clock are read by the cycle
    (``src/ki_waechter/dispatch.py``, ``tests/test_ki_waechter/test_dispatch.py``).
    Nothing counts the Gemini requests any more (Google does), so the gate
    calls no API and the harness needs no fake ``gh``.
    """
    doc = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    script = doc["jobs"]["gate"]["steps"][0]["run"]
    tmp_path.mkdir(parents=True, exist_ok=True)
    output, summary = tmp_path / "out", tmp_path / "summary"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "EVENT": event,
        "OWNER_REPO": "o/r",
        "PR_HEAD_REPO": "o/r",
        "REV_INPUT": "",
        "MODEL_INPUT": "",
        "CYCLE_RUN": "",
        **(extra or {}),
    }
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True)
    output.touch()
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


__all__ = [
    "REPO",
    "_DE_DEFAULT",
    "_EN_DEFAULT",
    "_FEED",
    "_ITEM",
    "_Response",
    "_answer",
    "_git",
    "_history",
    "_ok_payload",
    "_overloaded_then",
    "_run_gate",
    "_write_feeds",
    "_write_repo",
    "prep",
]
