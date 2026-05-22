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
import re
import sys
from collections.abc import Sequence
from logging import DEBUG, INFO, getLogger
from pathlib import Path
from typing import Final, cast

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.logging_safe import setup_script_logging
from src.utils.files import atomic_write
from src.utils.http import request_safe, session_with_retries
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = getLogger("places.hafas.sync")

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
    level = DEBUG if verbose else INFO
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


# ---------------------------------------------------------------------------
# Structural validation
#
# The regex extractor in ``_extract_profile`` only enforces character-class
# and length bounds on the raw match. If a future upstream refactor moves
# the credential strings around (renames the ``salt`` key, embeds a
# placeholder string the regex still matches, accidentally lifts an
# unrelated literal), the extractor would happily produce a syntactically
# valid but semantically broken profile. Writing that profile to disk
# pollutes the HAFAS cache for the entire cron-pipeline lifetime — every
# enrichment falls through to the Google-Places tier, burning the daily
# Places quota on stations that should have been served by HAFAS.
#
# ``_validate_profile_document`` is the writer-side gate. It runs AFTER
# ``_build_profile_document`` assembles the document and BEFORE
# ``_write_profile`` commits the bytes. A validation failure raises
# :class:`HafasProfileValidationError` which ``main`` catches and turns
# into exit code 1 — no on-disk state is mutated.
# ---------------------------------------------------------------------------

_PROFILE_MAX_FIELD_LEN: Final[int] = 256
_PROFILE_MIN_VER_LEN: Final[int] = 3   # e.g. "1.0"
_PROFILE_MIN_AID_LEN: Final[int] = 8
_PROFILE_MIN_SALT_LEN: Final[int] = 8  # only applied when salt is non-empty

# ``ver`` is HAFAS's API client version. Every release observed in the
# upstream profile since 2022 has used the ``MAJOR.MINOR[.PATCH]`` shape
# (e.g. "1.45", "1.46", "1.46.LIVE"). The regex is intentionally narrow:
# a freeform-string drift would silently break the HAFAS request envelope.
_VER_VALIDATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9]+\.[0-9]+(?:\.[0-9A-Za-z_-]+)?$"
)

# ``aid`` is the deployment-scoped Application-ID. ÖBB's current value
# ("OWDL4fE4ixNiPBBm") is a 16-char base62-ish token; the regex permits
# the broader alphanumeric+separator set used by other HAFAS deployments
# but rejects whitespace / control characters / Trojan-Source primitives.
_AID_VALIDATION_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[A-Za-z0-9._\-+/]{{{_PROFILE_MIN_AID_LEN},128}}$"
)

# ``salt`` (when present) is the HMAC-MD5 ingredient. HAFAS deployments
# that use signing carry an opaque alphanumeric+padding token; ÖBB's
# current upstream omits it entirely.
_SALT_VALIDATION_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[A-Za-z0-9._\-+/=]{{{_PROFILE_MIN_SALT_LEN},256}}$"
)


class HafasProfileValidationError(ValueError):
    """Raised when the assembled HAFAS profile fails structural validation.

    A distinct subclass of :class:`ValueError` so callers (the
    ``main`` entry point and any future tooling) can differentiate
    write-time validation failures from generic value errors elsewhere
    in the pipeline.
    """


def _ensure_str(
    value: object,
    field: str,
    *,
    min_len: int = 0,
    max_len: int = _PROFILE_MAX_FIELD_LEN,
) -> str:
    """Assert *value* is a string within ``[min_len, max_len]`` characters.

    Returns the validated string. Raises
    :class:`HafasProfileValidationError` with a field-named message on
    any violation. The value bytes themselves are NEVER logged or
    embedded in the message — only the field name and the structural
    reason — because every field in this document is credential-class.
    """
    if not isinstance(value, str):
        raise HafasProfileValidationError(
            f"HAFAS profile field {field!r} must be a string, got "
            f"{type(value).__name__}"
        )
    length = len(value)
    if length < min_len:
        raise HafasProfileValidationError(
            f"HAFAS profile field {field!r} too short "
            f"(len={length}, min={min_len})"
        )
    if length > max_len:
        raise HafasProfileValidationError(
            f"HAFAS profile field {field!r} too long "
            f"(len={length}, max={max_len})"
        )
    return value


def _validate_profile_document(profile: object) -> None:
    """Structurally validate *profile* before it is committed to disk.

    Checks:
        * Top-level is a ``dict`` carrying the four mandatory keys
          (``salt`` / ``ver`` / ``auth`` / ``client``).
        * Every leaf value is a string within the per-field length bounds.
        * ``ver`` matches the canonical ``MAJOR.MINOR[.PATCH]`` shape.
        * ``auth.aid`` matches the alphanumeric+separator token shape
          and meets the minimum length floor.
        * ``salt`` is either empty (ÖBB's current state) OR matches the
          alphanumeric+padding token shape with the minimum length floor.
        * ``client`` carries the four request-envelope keys (``id`` /
          ``type`` / ``name`` / ``l``) and every value is a non-empty
          string.

    Raises :class:`HafasProfileValidationError` with a field-named
    message on any failure. Field values are never embedded in the
    message; only the field NAME and the structural reason appear.
    """
    if not isinstance(profile, dict):
        raise HafasProfileValidationError(
            f"HAFAS profile must be a dict, got {type(profile).__name__}"
        )
    profile_dict = cast(dict[str, object], profile)

    required_top = {"salt", "ver", "auth", "client"}
    missing_top = required_top - profile_dict.keys()
    if missing_top:
        raise HafasProfileValidationError(
            f"HAFAS profile missing top-level keys: {sorted(missing_top)}"
        )

    salt = _ensure_str(profile_dict["salt"], "salt", min_len=0, max_len=256)
    if salt and not _SALT_VALIDATION_RE.fullmatch(salt):
        raise HafasProfileValidationError(
            "HAFAS profile field 'salt' has invalid format "
            "(expected alphanumeric+padding token)"
        )

    ver = _ensure_str(
        profile_dict["ver"], "ver", min_len=_PROFILE_MIN_VER_LEN, max_len=32
    )
    if not _VER_VALIDATION_RE.fullmatch(ver):
        raise HafasProfileValidationError(
            "HAFAS profile field 'ver' has invalid format "
            "(expected MAJOR.MINOR[.PATCH])"
        )

    auth = profile_dict["auth"]
    if not isinstance(auth, dict):
        raise HafasProfileValidationError(
            f"HAFAS profile field 'auth' must be a dict, got "
            f"{type(auth).__name__}"
        )
    auth_dict = cast(dict[str, object], auth)
    for auth_key in ("type", "aid"):
        if auth_key not in auth_dict:
            raise HafasProfileValidationError(
                f"HAFAS profile field 'auth' missing required key "
                f"{auth_key!r}"
            )
    _ensure_str(auth_dict["type"], "auth.type", min_len=1, max_len=32)
    aid = _ensure_str(
        auth_dict["aid"], "auth.aid", min_len=_PROFILE_MIN_AID_LEN, max_len=128
    )
    if not _AID_VALIDATION_RE.fullmatch(aid):
        raise HafasProfileValidationError(
            "HAFAS profile field 'auth.aid' has invalid format "
            "(expected alphanumeric+separator token)"
        )

    client = profile_dict["client"]
    if not isinstance(client, dict):
        raise HafasProfileValidationError(
            f"HAFAS profile field 'client' must be a dict, got "
            f"{type(client).__name__}"
        )
    client_dict = cast(dict[str, object], client)
    for client_key in ("id", "type", "name", "l"):
        if client_key not in client_dict:
            raise HafasProfileValidationError(
                f"HAFAS profile field 'client' missing required key "
                f"{client_key!r}"
            )
        _ensure_str(
            client_dict[client_key], f"client.{client_key}", min_len=1
        )


def _write_profile(profile: dict[str, object], output_path: Path) -> None:
    """Persist the profile atomically with 0600 permissions.

    The credentials are not literally secret (they ship in every public
    web-app build of HAFAS) but treating them as low-sensitivity state
    keeps the on-disk shape consistent with the project's other
    auth-bearing sidecars.
    """
    scrubbed = scrub_trojan_source_primitives(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Security (Coordinate finite/range drift, committed-writer
    # defence-in-depth): ``allow_nan=False`` mirrors the canonical
    # writer-side pin established in Round 1485 at
    # :func:`src.places.merge.write_stations` and extended in Round
    # 1487 to the sibling stations / cache-events writers. The
    # profile values are regex-extracted from upstream JavaScript
    # so today's NaN risk is low, but the pin is the defensive line
    # if a future extraction strategy or upstream-schema change
    # widens the value surface. Non-standard ``NaN`` / ``Infinity``
    # literals (invalid per RFC 8259) in the committed
    # ``data/hafas_profile.json`` would break :func:`json.loads` /
    # ``JSON.parse`` / ``serde_json`` strict mode at every
    # downstream consumer.
    with atomic_write(
        output_path, mode="w", encoding="utf-8", permissions=0o600
    ) as handle:
        json.dump(
            scrubbed,
            handle,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
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
        _validate_profile_document(profile)
    except HafasProfileValidationError as exc:
        # Sanitised: ``str(exc)`` contains the field NAME and the
        # structural reason but never the field VALUE (the validator
        # never embeds values in its messages). Routing through
        # ``sanitize_log_arg`` keeps a hostile upstream's payload from
        # injecting newline / ANSI / Trojan-Source primitives into the
        # cron log line.
        LOGGER.error(
            "HAFAS profile failed structural validation: %s",
            sanitize_log_arg(str(exc)),
        )
        return 1
    try:
        _write_profile(profile, args.output)
    except OSError as exc:
        LOGGER.error(
            "Failed to write HAFAS profile: %s",
            sanitize_log_arg(type(exc).__name__),
        )
        return 1

    # Security: log only byte-length metadata, never the parsed credential
    # values themselves. The ``ver`` / ``aid`` strings reach this log line
    # via ``_extract_profile``'s dict — a credential-coded dataflow that
    # CodeQL's clear-text-logging-sensitive-data taint analysis marks as
    # secret-bearing end-to-end. Lengths give operators the "did the
    # extractor find non-empty values?" signal they need without surfacing
    # the bytes themselves. ``--verbose`` operators can inspect the
    # on-disk JSON directly when they need the literal values.
    salt_value = extracted.get("salt", "")
    LOGGER.info(
        "HAFAS profile written (ver-bytes=%d, salt-bytes=%d, aid-bytes=%d)",
        len(extracted["ver"]),
        len(salt_value),
        len(extracted["aid"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
