#!/usr/bin/env python3
"""Run project static analysis helpers (ruff + mypy + bandit + pip-audit).

This utility mirrors the checks executed in the CI workflow so that
contributors can reproduce the results locally with a single command.
"""

from __future__ import annotations

import argparse
# Bandit B404: subprocess is required to invoke ruff/mypy/bandit/pip-audit
# from this internal helper. Inputs are static lists, never user-supplied.
import subprocess  # nosec B404
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> int:
    """Execute *command* inside the project root and stream the output."""
    print("→", " ".join(command), flush=True)
    try:
        # Enforce a 5-minute timeout for static checks.
        # Bandit B603: command is a static list, never user-supplied.
        completed = subprocess.run(  # nosec B603
            command, cwd=PROJECT_ROOT, check=False, shell=False, timeout=300
        )
        return completed.returncode
    except subprocess.TimeoutExpired:
        print(f"Command timed out after 300s: {' '.join(command)}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Allow ruff to apply autofixes before running mypy.",
    )
    parser.add_argument(
        "--ruff-args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to 'ruff check'.",
    )
    args = parser.parse_args()

    ruff_command = ["ruff", "check"]
    if args.fix:
        ruff_command.append("--fix")
    if args.ruff_args:
        ruff_command.extend(args.ruff_args)

    exit_codes = []

    exit_codes.append(_run(ruff_command))

    # Mypy is configured via pyproject.toml
    exit_codes.append(_run(["mypy"]))

    # Run bandit security check
    # -r: recursive
    # -q: quiet (only errors)
    # -c: config file (optional, we use defaults for now)
    # We target src/ and scripts/
    bandit_cmd = ["bandit", "-r", "src", "scripts", "-q"]
    exit_codes.append(_run(bandit_cmd))

    scanner = PROJECT_ROOT / "scripts" / "scan_secrets.py"
    # Enforce secret scanning failure
    exit_codes.append(_run([sys.executable, str(scanner)]))

    # C901 complexity gate vs .c901-baseline.txt allowlist. Mirrors the
    # mypy-strict allowlist pattern: rejects only NEW or worsened
    # violations, never rejects pre-existing ones (those ratchet down
    # over time via scripts/regen_c901_baseline.sh).
    complexity_gate = PROJECT_ROOT / "scripts" / "check_complexity.py"
    exit_codes.append(_run([sys.executable, str(complexity_gate)]))

    # Run pip-audit to check for known vulnerabilities in dependencies.
    # We restrict the audit to our explicit dependencies to avoid failing on
    # global toolchain packages (like ``pip`` itself) over which we have no
    # direct control in the CI runner.
    #
    # The ``--ignore-vuln`` allowlist captures advisories that affect the
    # ``transformers`` package on the ``>=4.41,<5`` line shipped with the
    # bilingual feed (Round 2026-05). All listed IDs apply to features we do
    # NOT exercise in this project:
    #
    # * The feed builder loads exactly ONE model
    #   (``Helsinki-NLP/opus-mt-de-en``) via the inline shorthand task name
    #   ``translation_de_to_en``. ``trust_remote_code`` defaults to
    #   ``False`` and we never set it; the planted-model RCE family
    #   (PYSEC-2025-211..218) requires a malicious model artefact whose
    #   custom code we would actively opt in to execute.
    # * CVE-2026-1839 is fixed only in 5.0.0rc3 (pre-release); the 4.x
    #   line we depend on per spec has no backport.
    # * The pipeline runs in the ``build-feed`` GitHub Actions workflow
    #   on an ephemeral runner with no inbound network surface and the
    #   single output is the sanitised RSS XML (``_sanitize_text`` strips
    #   the canonical Trojan-Source / zero-width family before write).
    #
    # Re-evaluate whenever ``transformers`` bumps to a version that
    # publishes fixes for these IDs on the 4.x line, or when the project
    # moves to ``>=5``.
    _TRANSFORMERS_IGNORED_VULNS = (
        "PYSEC-2025-211",
        "PYSEC-2025-212",
        "PYSEC-2025-213",
        "PYSEC-2025-214",
        "PYSEC-2025-215",
        "PYSEC-2025-216",
        "PYSEC-2025-217",
        "PYSEC-2025-218",
        "CVE-2026-1839",
    )
    pip_audit_cmd = [
        "pip-audit",
        "-r", "requirements.txt",
        "-r", "requirements-dev.txt",
    ]
    for vuln_id in _TRANSFORMERS_IGNORED_VULNS:
        pip_audit_cmd.extend(["--ignore-vuln", vuln_id])
    exit_codes.append(_run(pip_audit_cmd))

    # Return the highest exit code encountered
    return max(exit_codes) if exit_codes else 0


if __name__ == "__main__":
    sys.exit(main())
