"""The repository secrets the workflows use, and the Health check on them.

Moved verbatim from ``scripts/health_check.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.health_check.common import Check


# The Feed-Wächter (scripts/feed_watch.py) runs at the end of every Update run.
# Its state is committed with the feed, so it is current whenever the feed is.
@dataclass(frozen=True)
class WorkflowSecret:
    """One repository secret that a workflow under ``.github/workflows`` uses.

    ``kind``:

    * ``required`` — a feed-pipeline step cannot do its job without it. The
      Health check gets it passed and turns red when it arrives empty.
    * ``optional`` — the code has a built-in default; empty is fine and only
      shown as a detail line.
    * ``tooling`` — used by a repository tool workflow, not by the feed
      pipeline. Not passed to the Health check, which has no use for it.
    """

    name: str
    kind: str
    purpose: str


# The complete list of repository secrets the workflows use. Operator decision
# 2026-10-16 (Michael): secrets that no longer reach their consumer must not go
# unnoticed, and the Health check is the place where that shows. Until then a
# missing secret fell back silently: the Google Places key had reached no
# workflow since 2026-05-11, and ``VOR_VERSION`` / ``VOR_BASE`` were passed on
# although no such secret existed. ``tests/test_workflow_secrets.py`` keeps
# this list, the workflows' ``secrets.*`` references and the env names the
# code reads in step, so the class cannot come back unnoticed.
WORKFLOW_SECRETS: tuple[WorkflowSecret, ...] = (
    WorkflowSecret("VOR_ACCESS_ID", "required",
                   "Stammstrecken-Monitor (VOR/VAO ReST API)"),
    WorkflowSecret("VOR_BASE_URL", "optional",
                   "VAO-Host, Standard in src/providers/vor.py"),
    # ``tooling`` secrets are not passed to the Health check; whether they
    # still work shows in ``Werkzeuge`` (latest run of claude-code-review.yml)
    # and ``Nebenschritte`` (Repository-Metadaten in seo-guard.yml), since
    # 2026-10-31 (Architektur-Review, Fund B).
    WorkflowSecret("CLAUDE_CODE_OAUTH_TOKEN", "tooling",
                   "Claude-Workflows (Code-Review, @claude)"),
    # A key of the free tier without billing (operator decision 2026-10-31).
    # A missing or rejected key fails the KI-Wächter run, and that shows in
    # "Nebenschritte".
    WorkflowSecret("GEMINI_API_KEY", "tooling",
                   "KI-Wächter (Gemini API, Gratis-Kontingent)"),
    WorkflowSecret("REPO_ADMIN_TOKEN", "tooling",
                   "SEO-Guard: Beschreibung und Homepage des Repositorys"),
)


def check_secrets() -> Check:
    """Red when a secret the feed pipeline needs arrives empty in this run.

    The Health-check workflow passes every ``required`` and ``optional``
    secret of :data:`WORKFLOW_SECRETS`. A secret that is not stored in the
    repository arrives as an empty string, exactly as it would in the Update
    run. Only emptiness is checked and only names are printed, never a value.
    Whether a stored key is still *accepted* shows in ``Stammstrecke-Abruf``:
    a rejected key makes every poll fail, and that check turns red after
    three hours.
    """
    name = "Secrets"
    checked = [s for s in WORKFLOW_SECRETS if s.kind in {"required", "optional"}]
    empty = [s for s in checked if not os.environ.get(s.name, "").strip()]
    missing = [s for s in empty if s.kind == "required"]
    defaulted = [s for s in empty if s.kind == "optional"]
    if missing:
        return Check(
            name,
            ok=False,
            summary="FEHLT — " + ", ".join(s.name for s in missing)
            + " leer oder nicht als Repository-Secret hinterlegt",
            detail="; ".join(f"{s.name}: {s.purpose}" for s in missing),
        )
    present = [s.name for s in checked if s not in empty]
    detail = "; ".join(
        f"{s.name} leer, es gilt der Standard aus dem Code ({s.purpose})" for s in defaulted
    )
    return Check(name, ok=True, summary="OK — " + ", ".join(present) + " gesetzt",
                 detail=detail)
