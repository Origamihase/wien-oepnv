"""Was ein Schritt des Update-Laufs ausführt, mit den Skripten aus ``scripts/cycle/``.

Seit 2026-11-14 stehen die längeren Shell-Programme von
``.github/workflows/update-cycle.yml`` mit ihrer Begründung in
``scripts/cycle/`` (die Datei stand bei 700 Zeilen und wurde nach der Regel
„teilen statt kürzen“ geteilt, ``docs/modularisierung.md``). Ihr Schritt
behält Name, Bedingung, Zeitlimit und Umgebung und führt nur noch
``exec bash scripts/cycle/<name>.sh`` aus.

Tests, die den Code lesen, der läuft, nehmen deshalb nicht den Text des
Workflows allein: ``step_program`` liefert für einen Schritt (was er aufruft,
welche Dateien er nennt, ob er pusht) den ``run``-Text, bei einem solchen
Aufruf gefolgt vom Text des Skripts; ``workflow_program`` liefert für einen
ganzen Workflow (etwa die Suche nach einer Anfrage an die VAO an jeder
Stelle) seinen Text, gefolgt von den Skripten, die seine Schritte starten.
Nennt ein Schritt ein Skript, das fehlt, scheitert das Lesen.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CYCLE_SCRIPTS = REPO_ROOT / "scripts" / "cycle"
#: So startet ein Schritt ein Skript aus ``scripts/cycle/``: ``exec``, damit der
#: Prozess des Schritts die Shell des Skripts ist wie vorher die des Schritts.
SCRIPT_CALL = re.compile(r"^exec bash (scripts/cycle/[A-Za-z0-9_]+\.sh)$")


def started_script(step: dict[str, Any]) -> Path | None:
    """Das Skript, das *step* startet, oder ``None`` für einen Schritt mit eigenem Code."""
    match = SCRIPT_CALL.match(str(step.get("run", "")).strip())
    return REPO_ROOT / match.group(1) if match else None


def step_program(step: dict[str, Any]) -> str:
    """``run`` von *step*, bei einem Skriptaufruf gefolgt vom Text des Skripts."""
    run = str(step.get("run", ""))
    script = started_script(step)
    if script is None:
        return run
    return f"{run}\n{script.read_text(encoding='utf-8')}"


def workflow_program(workflow: Path) -> str:
    """Text von *workflow*, gefolgt vom Text jedes Skripts, das einer seiner Schritte startet."""
    text = workflow.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    scripts: list[Path] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            script = started_script(step)
            if script is not None and script not in scripts:
                scripts.append(script)
    return "\n".join([text, *(script.read_text(encoding="utf-8") for script in scripts)])
