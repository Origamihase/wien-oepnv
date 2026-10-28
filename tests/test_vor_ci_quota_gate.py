"""Guards against re-opening the CI quota leak (audit A.1 / F.1).

Audit ``docs/archive/audits/audit-2026-09-13.md`` (§ *Audit Update 19:20*,
finding **A.1**): ``.github/workflows/test-vor-api.yml`` fired a real VAO
``location.name`` request on every push matching ``scripts/**`` — a filter that
covered ~40 files. The request went out as a raw ``curl`` with
``accessId=${VOR_ACCESS_ID}`` on the command line, which meant it

* bypassed ``save_request_count`` entirely, so it never appeared in
  ``data/vor_request_count.json``;
* ran without the ``preflight_quota_check`` gate the cycle uses; and
* sat outside the ``external-api-fetch`` concurrency group, so it could race a
  Stammstrecke tick.

The consumption was real but invisible to the counter, and
``docs/architecture.md`` §7 consequently listed ``location.name`` at 0
calls/day (**F.1**). These tests pin the shape of the fix so a future edit
cannot quietly reintroduce an unaccounted VAO call from CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Workflows that carry VOR credentials or name the VAO host.
_VOR_MARKERS = ("VOR_ACCESS_ID", "VAO_ACCESS_ID", "verkehrsauskunft")


#: Workflows that carry a VOR secret without spending VAO budget. The Health
#: check receives ``VOR_ACCESS_ID`` only so ``check_secrets`` can tell whether
#: it arrives empty (operator decision 2026-10-16); it never sends a VAO
#: request, which ``test_health_check_never_calls_vao`` pins.
_NO_VAO_REQUESTS = {"health-check.yml"}


def _vor_workflows() -> list[Path]:
    found = [
        path
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
        if path.name not in _NO_VAO_REQUESTS
        and any(marker in path.read_text(encoding="utf-8") for marker in _VOR_MARKERS)
    ]
    assert found, "no VOR-touching workflow found — did the glob break?"
    return found


def _strip_comments(text: str) -> str:
    """Drop whole-line comments (YAML and, inside ``run:`` blocks, shell).

    The scans below look for *executable* request shapes. Prose that documents
    the removed ``curl`` — including this fix's own rationale comments — must
    not read as a violation.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("workflow", _vor_workflows(), ids=lambda p: p.name)
def test_vor_workflow_uses_the_shared_concurrency_group(workflow: Path) -> None:
    """Every quota-bound workflow queues in ``external-api-fetch``.

    Without a shared group two VAO-spending workflows run in parallel and the
    daily budget is decided by a race. ``test-vor-api.yml`` was the one
    exception before this fix.
    """
    text = workflow.read_text(encoding="utf-8")
    assert "group: external-api-fetch" in text, (
        f"{workflow.name} spends VAO budget but is not in the "
        "external-api-fetch concurrency group"
    )


@pytest.mark.parametrize("workflow", _vor_workflows(), ids=lambda p: p.name)
def test_no_workflow_makes_an_uncounted_vao_request(workflow: Path) -> None:
    """No workflow may hand-roll a VAO call that skips the counter.

    A raw HTTP client pointed at the VAO host cannot debit
    ``data/vor_request_count.json``. Requests must go through
    ``src.providers.vor`` (which reserves a slot) — in practice via
    ``verify_vor_access_id.py``, ``check_vor_auth.py`` or the Stammstrecke
    monitor.
    """
    text = _strip_comments(workflow.read_text(encoding="utf-8"))

    # The credential must never be interpolated into a shell request. This also
    # keeps it off the process command line, where it is world-readable.
    assert "accessId=${VOR_ACCESS_ID}" not in text, (
        f"{workflow.name} interpolates the VAO access ID into a request — "
        "route it through src.providers.vor instead"
    )

    for raw_client in ("curl", "wget", "httpie"):
        pattern = re.compile(
            rf"^\s*[^#\n]*\b{raw_client}\b[^\n]*(VOR_BASE_URL|verkehrsauskunft)",
            re.MULTILINE,
        )
        assert not pattern.search(text), (
            f"{workflow.name} calls the VAO endpoint with {raw_client}; that "
            "request cannot be charged against the 100/day budget"
        )


def test_vor_smoke_test_is_quota_gated() -> None:
    """``test-vor-api.yml`` runs the pre-flight and gates the probe on it."""
    text = (WORKFLOW_DIR / "test-vor-api.yml").read_text(encoding="utf-8")

    assert "preflight_quota_check.py --check vor" in text
    assert "steps.preflight.outputs.quota_ok == 'true'" in text, (
        "the smoke test must be skipped when the pre-flight reports no budget"
    )
    assert "verify_vor_access_id.py" in text


def _stammstrecke_workflows() -> list[Path]:
    """Workflows that run the Stammstrecke monitor, the VAO consumer."""
    return [
        path
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
        if "run: python scripts/update_stammstrecke_hbf.py"
        in _strip_comments(path.read_text(encoding="utf-8"))
    ]


def test_both_stammstrecke_workflows_are_found() -> None:
    assert {path.name for path in _stammstrecke_workflows()} == {
        "manual-full-refresh.yml",
        "update-cycle.yml",
    }


@pytest.mark.parametrize("workflow", _stammstrecke_workflows(), ids=lambda p: p.name)
def test_the_stammstrecke_poll_is_quota_gated(workflow: Path) -> None:
    """The poll runs after the pre-flight and only when it reports budget.

    Audit 2026-09-25, A.6: ``manual-full-refresh.yml`` polled without the
    pre-flight that ``update-cycle.yml`` runs.
    """
    text = _strip_comments(workflow.read_text(encoding="utf-8"))
    preflight = text.find("run: python scripts/preflight_quota_check.py --check vor")
    poll = text.find("run: python scripts/update_stammstrecke_hbf.py")
    assert preflight != -1, f"{workflow.name} polls the VAO without the pre-flight"
    assert preflight < poll, f"{workflow.name} runs the pre-flight after the poll"
    poll_step = text[text.rfind("- name:", 0, poll):poll]
    assert "if: ${{ steps.vor_preflight.outputs.quota_ok == 'true' }}" in poll_step, (
        f"{workflow.name} polls even when the pre-flight reports no budget"
    )


def test_vor_smoke_test_path_filter_is_narrow() -> None:
    """The trigger must not match every script in the repo.

    ``scripts/**`` covered ~40 unrelated files; each matching push spent a VAO
    slot. The filter is expected to name individual VOR-relevant scripts.
    """
    text = (WORKFLOW_DIR / "test-vor-api.yml").read_text(encoding="utf-8")
    assert '- "scripts/**"' not in text, (
        "the broad scripts/** trigger is back — every unrelated script edit "
        "would spend VAO budget again"
    )
    assert '- "scripts/update_stammstrecke_hbf.py"' in text


def test_verify_script_reserves_a_slot_before_the_probe() -> None:
    """The probe charges the budget before it goes on the wire."""
    source = (REPO_ROOT / "scripts" / "verify_vor_access_id.py").read_text(
        encoding="utf-8"
    )
    reserve_at = source.find("reserve_request_slot(")
    fetch_at = source.find("fetch_content_safe(")
    assert reserve_at != -1, "verify_vor_access_id.py no longer reserves a slot"
    assert fetch_at != -1
    assert reserve_at < fetch_at, (
        "the slot must be reserved BEFORE the request is issued, otherwise a "
        "refused reservation still costs a real call"
    )


def test_verify_script_skips_the_probe_when_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused reservation returns exit 3 and issues no request."""
    import src.providers.vor as vor

    import scripts.verify_vor_access_id as verify

    # ``verify.vor_module`` IS ``src.providers.vor``; patch the module directly
    # so the attribute lookup inside ``main`` resolves to the stub.
    monkeypatch.setattr(vor, "refresh_base_configuration", lambda: "")
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(
        vor, "reserve_request_slot", lambda: (False, vor.MAX_REQUESTS_PER_DAY)
    )

    def _must_not_fetch(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("a request was issued despite a refused reservation")

    monkeypatch.setattr(verify, "fetch_content_safe", _must_not_fetch)

    assert verify.main([]) == 3


def test_architecture_doc_lists_the_ci_consumer() -> None:
    """F.1: §7's budget table must name the CI smoke test.

    The table previously claimed ``location.name`` was 0 calls/day and called
    the Stammstrecke monitor the *only* automated consumer, so an operator
    planning against it under-counted the real spend.
    """
    text = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "test-vor-api.yml" in text, "§7 does not mention the CI consumer"
    assert "**einzige** automatisierte VOR-Konsument" not in text, (
        "§7 still claims a single automated VOR consumer"
    )
    assert "| **Tagesbudget gesamt** | **48 / 100** |" not in text, (
        "§7 still totals the budget without the CI smoke test"
    )


def test_health_check_never_calls_vao() -> None:
    """The Health check may hold the VOR secret but must not spend VAO budget.

    It runs the WL, ÖBB and Baustellen updaters and reads committed files;
    the Stammstrecke is judged from ``poll_status.json``, never live.
    """
    script = (REPO_ROOT / "scripts" / "health_check.py").read_text(encoding="utf-8")
    # Importing a constant (``DIRECTION_LABELS``) is fine; a request path is not.
    for marker in ("providers.vor import", "providers import vor", "verkehrsauskunft",
                   "_charge_one_request", "VorAuth", "accessId"):
        assert marker not in script, marker
    workflow = _strip_comments((WORKFLOW_DIR / "health-check.yml").read_text(encoding="utf-8"))
    run_lines = [line.strip() for line in workflow.splitlines() if line.strip().startswith("run:")]
    assert run_lines == ["run: python scripts/health_check.py"]


def test_verify_script_names_the_reason_and_masks_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """28.10.2026: the CI probe logged only "HTTPError" while the VAO refused
    every request; a 403 and a 503 looked the same. The status line is shown,
    the ``accessId`` in the URL stays masked."""
    import logging

    import requests

    import src.providers.vor as vor

    import scripts.verify_vor_access_id as verify

    secret = "s3cr3t-access-0815"
    monkeypatch.setattr(vor, "refresh_base_configuration", lambda: "")
    monkeypatch.setattr(vor, "refresh_access_credentials", lambda: secret)
    monkeypatch.setattr(vor, "VOR_ACCESS_ID", secret)
    monkeypatch.setattr(vor, "reserve_request_slot", lambda: (True, 1))
    monkeypatch.setattr(verify, "_configure_logging", lambda: None)

    def _refused(*args: object, **kwargs: object) -> bytes:
        raise requests.HTTPError(
            "403 Client Error: Forbidden for url: https://routenplaner.verkehrsauskunft.at"
            f"/vao/restproxy/v1.12.0/location.name?input=Wien&accessId={secret}"
        )

    monkeypatch.setattr(verify, "fetch_content_safe", _refused)
    with caplog.at_level(logging.ERROR, logger=verify.LOGGER.name):
        assert verify.main([]) == 1
    assert "VOR verification request failed: HTTPError: 403 Client Error: Forbidden" in caplog.text
    assert secret not in caplog.text
