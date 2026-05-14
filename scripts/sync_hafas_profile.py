#!/usr/bin/env python3
"""Synchronise the ÖBB HAFAS (Scotty) Mgate profile from upstream.

The HAFAS Mgate API of ÖBB requires up to three credential-shaped values
to authenticate every request:

* ``salt`` — optional HMAC ingredient for the ``mac`` query-parameter.
  Not every HAFAS deployment uses it; ÖBB's current profile has none.
* ``ver`` — HAFAS API client version pinned by ÖBB.
* ``aid`` — deployment-scoped Application-ID inside the ``auth`` block.

ÖBB rotates these values without notice, so a long-lived hardcoded
triple in the repository would silently break the HAFAS fallback inside
:mod:`src.places.hafas_client`. This helper extracts the live values
from the upstream ``public-transport/hafas-client`` project (the
canonical community mirror of ÖBB's web-app profile) and persists them
atomically to ``data/hafas_profile.json``. The CI workflow runs this
script directly before ``scripts/update_station_directory.py`` so the
cron pipeline always picks up the freshest credentials.

The upstream OEBB profile is split across two JavaScript modules — the
entry point ``p/oebb/index.js`` and the imported ``p/oebb/base.js``
(``index.js`` spreads ``baseProfile`` from ``./base.js``). The
credential triple lives in ``base.js`` today; the sync resolves both
files and merges the matches so a future upstream refactor that moves
fields back into ``index.js`` does not silently break the cron job.

The fetch routes through :func:`src.utils.http.request_safe` so the
project's SSRF / DNS-rebinding / size-bomb defences apply uniformly.

Exit codes:
    0 — Profile fetched, parsed and written successfully.
    1 — Upstream returned a non-2xx status, the response could not be
        decoded as UTF-8, or one of the mandatory credential fields
        (``ver`` / ``aid``) was missing from both source files.
    2 — Network / DNS / connect-timeout failure or SSRF rejection by
        :func:`request_safe`.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.logging_safe import setup_script_logging
from src.utils.files import atomic_write
from src.utils.http import request_safe, session_with_retries
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = logging.getLogger("places.hafas.sync")

_PROFILE_SOURCE_URL: Final[str] = (
    "https://raw.githubusercontent.com/public-transport/hafas-client/main/p/oebb/index.js"
)

# Sibling module the entry-point imports via ``import baseProfile from
# './base.js'``. We resolve it relative to the configured source URL so
# a ``--source-url`` override applied at the entry-point automatically
# inherits the correct base.
_BASE_SUFFIX: Final[str] = "base.js"

_USER_AGENT: Final[str] = (
    "wien-oepnv-hafas-sync/1.0 "
    "(+https://github.com/Origamihase/wien-oepnv; cron-pipeline)"
)

# The hafas-client source is hand-edited JavaScript; the credential
# strings appear as quoted literals inside a ``profile`` / default-export
# object. We extract each independently rather than parsing JavaScript
# because the upstream layout (line breaks, key ordering, comments) is
# not stable across releases.
_SALT_RE: Final[re.Pattern[str]] = re.compile(
    r"""salt\s*:\s*(?:Buffer\s*\.\s*from\s*\(\s*)?['"]([^'"\\]{8,256})['"]""",
)
_VER_RE: Final[re.Pattern[str]] = re.compile(
    r"""\bver\s*:\s*['"]([0-9A-Za-z.\-_]{1,32})['"]""",
)
_AID_RE: Final[re.Pattern[str]] = re.compile(
    r"""\baid\s*:\s*['"]([0-9A-Za-z.\-_]{1,128})['"]""",
)

# Tight wall-clock cap. The fetch happens at the very start of the
# station-update workflow, so a slow upstream here directly delays the
# whole cron tick.
_FETCH_TIMEOUT_S: Final[float] = 15.0

# Hard size cap. The upstream source files are < 4 KiB today; we cap an
# order of magnitude above that to absorb future growth while keeping a
# malicious / corrupted mirror from streaming megabytes through the
# Slowloris-defence read budget.
_MAX_RESPONSE_BYTES: Final[int] = 256 * 1024

_DEFAULT_OUTPUT_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1] / "data" / "hafas_profile.json"
)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT_PATH,
        help=(
            "Destination JSON file (default: ./data/hafas_profile.json). "
            "The write is atomic so a partial fetch cannot leave the "
            "directory inconsistent."
        ),
    )
    parser.add_argument(
        "--source-url",
        default=_PROFILE_SOURCE_URL,
        help=(
            "Override the upstream hafas-client entry-point URL. Must be "
            "an https URL; routed through request_safe so SSRF guards apply."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_FETCH_TIMEOUT_S,
        help=f"Per-request wall-clock cap in seconds (default: {_FETCH_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Emit DEBUG-level logs.",
    )
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    setup_script_logging(level)


def _sibling_url(entry_url: str, sibling_name: str) -> str:
    """Derive the sibling URL by replacing the entry-point filename.

    Pure string transform — no URL parsing, so the result is byte-exact
    against the entry point's host / path / query and inherits whatever
    allow-list ``request_safe`` already applied to the entry URL.
    """
    base, _slash, _filename = entry_url.rpartition("/")
    if not base:
        return entry_url
    return f"{base}/{sibling_name}"


def _fetch_source(
    session: requests.Session, url: str, timeout_s: float
) -> str | None:
    """Download a single upstream source file.

    Returns the decoded JavaScript source as a string, or ``None`` if
    the upstream is unreachable / responded with a non-2xx / served an
    undecodable body. The caller chains multiple files to assemble the
    full ÖBB profile.
    """
    try:
        response = request_safe(
            session,
            url,
            method="GET",
            max_bytes=_MAX_RESPONSE_BYTES,
            timeout=timeout_s,
            # raw.githubusercontent.com serves the file as text/plain.
            # Some CDN paths return application/javascript or
            # octet-stream — accept the canonical set so a future
            # content-type negotiation tweak doesn't break the sync.
            allowed_content_types=(
                "text/plain",
                "text/javascript",
                "application/javascript",
                "application/octet-stream",
            ),
            headers={
                "Accept": "text/plain, text/javascript, */*;q=0.1",
                "User-Agent": _USER_AGENT,
            },
        )
    except requests.RequestException as exc:
        LOGGER.error(
            "HAFAS profile fetch failed (network): %s",
            sanitize_log_arg(type(exc).__name__),
        )
        return None
    except ValueError as exc:
        LOGGER.error(
            "HAFAS profile fetch rejected by request_safe: %s",
            sanitize_log_arg(type(exc).__name__),
        )
        return None
    try:
        text: str = response.content.decode("utf-8")
    except UnicodeDecodeError:
        LOGGER.error("HAFAS profile source is not valid UTF-8")
        return None
    return text


def _fetch_combined_source(entry_url: str, timeout_s: float) -> str | None:
    """Fetch the entry-point source and its sibling ``base.js`` module.

    Returns the concatenation of both bodies (separated by a newline)
    so the field-extraction regexes can scan a single string. ``None``
    if the primary fetch failed; a missing sibling is tolerated — the
    extractor will fail the mandatory-field check later if any required
    value cannot be found across the merged source.
    """
    session = session_with_retries(
        user_agent=_USER_AGENT,
        timeout=(min(5.0, timeout_s), timeout_s),
    )
    try:
        primary = _fetch_source(session, entry_url, timeout_s)
        if primary is None:
            return None
        sibling_url = _sibling_url(entry_url, _BASE_SUFFIX)
        if sibling_url == entry_url:
            return primary
        sibling = _fetch_source(session, sibling_url, timeout_s)
        if sibling is None:
            LOGGER.info(
                "Sibling base.js unavailable; falling back to entry-point source only"
            )
            return primary
        return f"{primary}\n{sibling}"
    finally:
        session.close()


def _extract_profile(source: str) -> dict[str, str] | None:
    """Return the credential strings, or ``None`` if a required one is missing.

    ``ver`` and ``aid`` are mandatory — without them no HAFAS request
    can be built. ``salt`` is optional: ÖBB's current upstream profile
    omits it (the API accepts unsigned requests). When ``salt`` is
    absent the returned dict simply omits the key; the on-disk profile
    then carries an empty string so downstream consumers can treat
    "no salt" as a known state rather than a parse failure.
    """
    ver_match = _VER_RE.search(source)
    aid_match = _AID_RE.search(source)

    missing: list[str] = []
    if ver_match is None:
        missing.append("ver")
    if aid_match is None:
        missing.append("aid")
    if missing or ver_match is None or aid_match is None:
        LOGGER.error(
            "HAFAS profile source is missing required fields: %s",
            sanitize_log_arg(",".join(missing)),
        )
        return None

    extracted: dict[str, str] = {
        "ver": ver_match.group(1),
        "aid": aid_match.group(1),
    }
    salt_match = _SALT_RE.search(source)
    if salt_match is not None:
        extracted["salt"] = salt_match.group(1)
    else:
        LOGGER.info(
            "Upstream HAFAS profile carries no salt; storing empty value "
            "(ÖBB does not currently require mac signing)"
        )
    return extracted


def _build_profile_document(extracted: dict[str, str]) -> dict[str, object]:
    """Render the canonical on-disk profile JSON.

    The shape mirrors the structure :mod:`src.places.hafas_client`
    expects: a flat ``salt`` / ``ver`` triple at the top level, an
    ``auth`` block describing the authentication scheme, and a
    ``client`` block carrying the ÖBB webapp's identifying handshake
    fields.
    """
    return {
        "salt": extracted.get("salt", ""),
        "ver": extracted["ver"],
        "auth": {"type": "AID", "aid": extracted["aid"]},
        "client": {
            "id": "OEBB",
            "type": "WEB",
            "name": "webapp",
            "l": "vs_webapp",
        },
    }


def _write_profile(profile: dict[str, object], output_path: Path) -> None:
    """Persist the profile atomically with 0600 permissions.

    The credentials are not literally secret (they ship in every public
    web-app build of HAFAS) but treating them as low-sensitivity state
    keeps the on-disk shape consistent with the project's other
    auth-bearing sidecars.
    """
    scrubbed = scrub_trojan_source_primitives(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(
        output_path, mode="w", encoding="utf-8", permissions=0o600
    ) as handle:
        json.dump(
            scrubbed,
            handle,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    source = _fetch_combined_source(args.source_url, max(1.0, float(args.timeout)))
    if source is None:
        return 2

    extracted = _extract_profile(source)
    if extracted is None:
        return 1

    profile = _build_profile_document(extracted)
    try:
        _write_profile(profile, args.output)
    except OSError as exc:
        LOGGER.error(
            "Failed to write HAFAS profile: %s",
            sanitize_log_arg(type(exc).__name__),
        )
        return 1

    salt_value = extracted.get("salt", "")
    LOGGER.info(
        "HAFAS profile written (ver=%s, salt-bytes=%d, aid-bytes=%d)",
        sanitize_log_arg(extracted["ver"]),
        len(salt_value),
        len(extracted["aid"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
