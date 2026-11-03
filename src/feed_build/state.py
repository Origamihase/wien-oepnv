"""The state file (first_seen) and the identity and state keys of an item.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..utils.files import _reject_non_finite_constant, _reject_non_finite_float, atomic_write
from ..utils.locking import file_lock
from ..utils.logging import sanitize_log_arg

from .base import _LINE_PREFIX_RE, _LINE_TOKEN_RE, _parse_first_seen, _to_utc, _ymd_or_none, log, validate_path

__all__ = [
    "_parse_lines_from_title", "MAX_STATE_FILE_BYTES", "_load_state", "_read_state_capped", "_write_merged_state",
    "_save_state", "_identity_for_item", "_dedupe_key_for_item", "_state_key_for_item", "_lookup_state",
]


def _parse_lines_from_title(title: str) -> list[str]:
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return []

    tokens: list[str] = []
    for raw in m.group(1).split("/"):
        token = raw.strip()
        if not token:
            continue
        normalized = token.upper()
        if _LINE_TOKEN_RE.match(normalized):
            tokens.append(normalized)
    return tokens


# ---------------- State (first_seen) ----------------


# Security: defense-in-depth byte-size cap on the on-disk state file.
# The file is a small JSON object (one entry per first_seen item, each
# ~80 bytes) — production state.json is ~40 KiB. The depth-bomb catch
# tuple ``except (FileNotFoundError, json.JSONDecodeError)`` covers the
# deeply-nested attack via ``RecursionError`` (re-raises into the broad
# ``except Exception`` below), but a wide-but-flat attack such as
# ``[0]*50_000_000`` slips past both handlers entirely:
#  * ``json.load`` does NOT raise ``RecursionError`` on a flat list
#    regardless of length.
#  * ``handle.read()`` (called internally by ``json.load(handle)``)
#    buffers the whole file before parsing — a 1 GiB file allocates a
#    1 GiB string plus ~5 GiB worth of object overhead.
#  * ``MemoryError`` is a ``BaseException`` subclass — it is NOT caught
#    by any ``except Exception`` handler, so the orchestrator crashes
#    BEFORE any provider runs and the feed-build cron leaves partial
#    state with no recovery path.
# Threat model mirrors ``MAX_CACHE_FILE_BYTES`` in ``src/utils/cache.py``:
# compromised CI runner / corrupted previous run / partial flush + power
# loss plants a multi-MiB-to-multi-GiB file at ``data/first_seen.json``.
# 50 MiB is ~1200x the production state file and bounds the worst-case
# parse cost well below any cron runner's standard 1 GiB cgroup limit.
MAX_STATE_FILE_BYTES = 50 * 1024 * 1024


def _load_state() -> dict[str, dict[str, Any]]:
    path = validate_path(feed_config.STATE_FILE, "STATE_PATH")
    try:
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with file_lock(lock_file, exclusive=False):
                # Security: byte-size cap (see MAX_STATE_FILE_BYTES)
                # defeats the wide-but-flat size-bomb attack that the
                # depth-bomb / json.JSONDecodeError catch below does NOT
                # cover. Open first, then ``os.fstat`` — closes the
                # TOCTOU between ``stat`` and ``open`` that lets a
                # parallel ``_save_state`` (atomic_write rename) or a
                # symlink swap bypass the cap.
                # ``read(MAX_STATE_FILE_BYTES + 1)`` defends against
                # zero-st_size special files.
                # Security (Path-Log Sibling Drift, sibling of PR #1456):
                # ``feed_config.STATE_FILE`` is operator-controlled via
                # the ``STATE_PATH`` environment variable. Pre-fix the
                # WARNING log lines below interpolated ``path`` verbatim
                # via the bare ``%s`` format spec; a hostile path name
                # (Trojan-Source RLO, zero-width, 8-bit C1, Tag block,
                # Variation Selectors, newline log-forgery, ANSI ESC)
                # flowed into the operator log + the public
                # ``docs/feed_health.json`` artefact + the GitHub-Issue
                # auto-submission. Post-fix the SHA-256 fingerprint
                # (truncated to 12 hex chars, Trojan-Source-clean,
                # CodeQL-recognised barrier) replaces the path bytes
                # at the interpolation boundary. Mirrors the canonical
                # fix shape pinned in :func:`src.utils.files.read_capped_json`.
                path_fingerprint = hashlib.sha256(
                    str(path).encode("utf-8", errors="replace")
                ).hexdigest()[:12]
                with path.open("rb") as f:
                    if os.fstat(f.fileno()).st_size > MAX_STATE_FILE_BYTES:
                        log.warning(
                            "State-Datei [path-sha256=%s] ist zu groß "
                            "(> %d Bytes); starte mit leerem State.",
                            path_fingerprint, MAX_STATE_FILE_BYTES,
                        )
                        return {}
                    raw_bytes = f.read(MAX_STATE_FILE_BYTES + 1)
                    if len(raw_bytes) > MAX_STATE_FILE_BYTES:
                        log.warning(
                            "State-Datei [path-sha256=%s] überschreitet "
                            "%d Bytes beim Lesen; starte mit leerem State.",
                            path_fingerprint, MAX_STATE_FILE_BYTES,
                        )
                        return {}
                    # Security (reader-side non-finite literal defence,
                    # symmetric to the Round 1488 writer-side
                    # ``allow_nan=False`` pin at ``_save_state``). A
                    # planted ``NaN`` / ``Infinity`` / ``-Infinity`` /
                    # ``1e1000`` in ``data/first_seen.json`` (compromised
                    # CI runner, parallel orchestrator atomic state swap,
                    # partial flush + power loss, hostile PR landing a
                    # tampered fixture) would otherwise propagate silently
                    # as ``float('nan')`` / ``float('inf')`` through the
                    # state dict — every ``first_seen`` comparison against
                    # ``retention_cutoff`` (``fs_utc < retention_cutoff``)
                    # silently misbehaves on NaN (``False`` for every
                    # comparison) AND the round-trip back to
                    # ``_save_state`` hits the ``allow_nan=False`` pin
                    # and crashes the cron mid-write. The hooks raise
                    # ``json.JSONDecodeError`` which the ``except
                    # (FileNotFoundError, json.JSONDecodeError)`` handler
                    # below catches — fall through to the empty-state
                    # recovery path, consistent with every other
                    # corrupt-state recovery in this loader.
                    data = json.loads(
                        raw_bytes,
                        parse_constant=_reject_non_finite_constant,
                        parse_float=_reject_non_finite_float,
                    )
        data = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        # Security (Clear-Text-Logging Drift): broad ``Exception`` catch
        # may surface third-party / custom-subclass exceptions whose
        # ``__str__`` carries control bytes.  Sanitise.
        log.warning(
            "State laden fehlgeschlagen (%s) – starte leer.",
            sanitize_log_arg(str(e)),
        )
        return {}

    retention_cutoff: datetime | None = None
    if feed_config.STATE_RETENTION_DAYS > 0:
        now_utc = _to_utc(datetime.now(UTC))
        retention_cutoff = now_utc - timedelta(days=feed_config.STATE_RETENTION_DAYS)

    out: dict[str, dict[str, Any]] = {}
    for ident, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            raw_first_seen = entry.get("first_seen", "")
            fs_dt = datetime.fromisoformat(str(raw_first_seen))
            if fs_dt.tzinfo is None:
                fs_dt = fs_dt.replace(tzinfo=UTC)
            fs_utc = _to_utc(fs_dt)
        except Exception:
            log.warning(
                "State-Eintrag %s hat unparsebares first_seen: %r", ident, entry.get("first_seen")
            )
            fs_dt = datetime.now(UTC)
            fs_utc = _to_utc(fs_dt)

        if retention_cutoff and fs_utc < retention_cutoff:
            log.debug(
                "State-Eintrag %s älter als %s Tage – entferne Eintrag.",
                ident,
                feed_config.STATE_RETENTION_DAYS,
            )
            continue

        new_entry = dict(entry)
        new_entry["first_seen"] = fs_utc.isoformat()
        out[ident] = new_entry
    return out


def _read_state_capped(path: Path) -> dict[str, dict[str, Any]]:
    """Read existing state under the byte-size cap, returning ``{}`` on
    any failure mode (missing/oversized/invalid).

    Security: open-then-fstat closes the TOCTOU between the cap check
    and ``open()`` that lets a parallel writer (atomic_write rename)
    or a symlink swap bypass the cap mid-merge. ``read(MAX + 1)``
    defends against zero-st_size special files (FIFOs, ``/dev/zero``).
    """
    # Security (Path-Log Sibling Drift, sibling of PR #1456): see
    # ``_load_state`` for the rationale of fingerprinting the path
    # bytes rather than interpolating them verbatim into the WARNING
    # log lines below.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        with path.open("rb") as f:
            if os.fstat(f.fileno()).st_size > MAX_STATE_FILE_BYTES:
                log.warning(
                    "State-Datei [path-sha256=%s] ist zu groß "
                    "(> %d Bytes); überschreibe State.",
                    path_fingerprint, MAX_STATE_FILE_BYTES,
                )
                return {}
            raw_bytes = f.read(MAX_STATE_FILE_BYTES + 1)
            if len(raw_bytes) > MAX_STATE_FILE_BYTES:
                log.warning(
                    "State-Datei [path-sha256=%s] überschreitet "
                    "%d Bytes; überschreibe State.",
                    path_fingerprint, MAX_STATE_FILE_BYTES,
                )
                return {}
            # Security: mirror the parse_constant + parse_float hooks
            # pinned at ``_load_state`` (Round 1503). The sibling reader
            # ``_read_state_capped`` reads the SAME ``data/first_seen.json``
            # state file on the save path (called from ``_save_state`` to
            # merge with existing state) and faces the identical
            # threat model — a planted NaN / Infinity / 1e1000 literal
            # propagates as ``float('nan')`` / ``float('inf')`` through
            # the merge logic and round-trip-crashes the writer pin
            # (Round 1485) at the next ``allow_nan=False`` serialisation.
            existing = json.loads(
                raw_bytes,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
            return existing if isinstance(existing, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # Security (Clear-Text-Logging Drift): broad ``Exception`` catch
        # — sanitise the bound name before WARNING-level emission.
        log.warning(
            "State-Merge fehlgeschlagen (Lesefehler: %s) – überschreibe State.",
            sanitize_log_arg(str(exc)),
        )
        return {}


def _prune_expired_merged_state(
    merged_state: dict[str, dict[str, Any]], state: dict[str, dict[str, Any]]
) -> None:
    """Drop resurrected, retention-expired entries from ``merged_state`` in place.

    ``_load_state`` drops expired entries from the in-memory working set, but
    ``_save_state`` re-reads the raw on-disk file via ``_read_state_capped`` and
    merges it — so an entry this run no longer tracks (another run's items,
    parallel-writer leftovers) is otherwise resurrected and re-written every
    cycle, growing the file until it trips ``MAX_STATE_FILE_BYTES`` and the
    loader wipes *all* first_seen tracking at once. Prune only entries NOT in
    this run's ``state``: those are already within retention via ``_load_state``
    and must keep their freshly written first_seen for cross-run dedup /
    parallel-writer safety (a recent parallel-writer entry has a recent
    first_seen and is kept; unparseable first_seen -> kept).
    """
    if feed_config.STATE_RETENTION_DAYS <= 0:
        return
    retention_cutoff = _to_utc(datetime.now(UTC)) - timedelta(
        days=feed_config.STATE_RETENTION_DAYS
    )
    for ident in list(merged_state.keys()):
        if ident in state:
            continue
        entry = merged_state.get(ident)
        if not isinstance(entry, dict):
            continue
        fs_utc = _parse_first_seen(entry, None)
        if fs_utc is not None and fs_utc < retention_cutoff:
            merged_state.pop(ident, None)


def _write_merged_state(
    path: Path, state: dict[str, dict[str, Any]], deletions: set[str] | None
) -> None:
    """Merge ``state`` into the on-disk state file and atomically rewrite it.

    The caller MUST hold the exclusive state lock. Reads the existing file under
    the byte-size cap, layers this run's ``state`` on top, drops ``deletions``,
    prunes retention-expired survivors, then writes via ``atomic_write``.
    """
    # Safe merge: read existing state to avoid overwriting parallel updates.
    # Security: open-then-fstat closes the TOCTOU between the size cap and
    # ``open``. ``read(MAX + 1)`` defends against zero-st_size special files.
    # See _load_state.
    merged_state = _read_state_capped(path)
    merged_state.update(state)
    if deletions:
        for k in deletions:
            merged_state.pop(k, None)
    # Prune resurrected, retention-expired entries so the on-disk state file
    # cannot grow until it trips ``MAX_STATE_FILE_BYTES`` and the loader wipes
    # *all* first_seen tracking at once.
    _prune_expired_merged_state(merged_state, state)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as f:
        # Security (Trojan-Source / BiDi-Mark Drift Round 10):
        # ``ensure_ascii=True`` escapes every non-ASCII code point as a literal
        # ``\uXXXX`` sequence. The state dict's KEYS carry feed-item identities
        # computed by ``_identity_for_item`` — the WL/non-OEBB fallback branches
        # embed the raw provider title verbatim. A planted upstream title
        # carrying U+202E (RIGHT-TO-LEFT OVERRIDE) / zero-width / Unicode
        # line-separator / 8-bit C1 bytes would otherwise reach
        # ``data/first_seen.json`` — a file committed to ``main`` by
        # ``build-feed.yml`` — as raw UTF-8, triggering BiDi reversal in any
        # ``cat`` / ``less`` / GitHub web UI / IDE viewer. Mirrors the canonical
        # fix shape pinned in PR #1434 for ``_write_quarantine_file``. Forensic
        # intent is preserved (``json.loads`` recovers the original bytes from
        # the literal escape sequence).
        #
        # Security (Coordinate finite/range drift, committed-writer
        # defence-in-depth): ``allow_nan=False`` mirrors the canonical writer-side
        # pin (Round 1485, :func:`src.places.merge.write_stations`). A planted
        # ``NaN`` / ``Infinity`` literal in a previous-run ``data/first_seen.json``
        # survives the ``json.loads`` round-trip and re-writes verbatim without
        # the pin. ``ensure_ascii=True`` already blocks Trojan-Source primitives;
        # ``allow_nan=False`` closes the sibling RFC-8259 non-conformance drift.
        json.dump(merged_state, f, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)


def _save_state(state: dict[str, dict[str, Any]], deletions: set[str] | None = None) -> None:
    path = validate_path(feed_config.STATE_FILE, "STATE_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Separate Lock-Datei vermeidet Permission-Fehler unter Windows, wenn
    # atomic_write die Zieldatei austauscht. Die Lock-Datei wird bewusst NICHT
    # nach Gebrauch entfernt: andernfalls entstünde eine Race, in der Prozess A
    # die Datei nach dem Lock-Release unlinkt, während die parallel laufenden
    # Prozesse B und C den Pfad bereits geöffnet haben und auf separaten Inodes
    # locken — wodurch flock sich gegenseitig nicht mehr sieht. Eine persistente
    # Lock-Datei (~0 Bytes) kostet nichts und ist zwischen Läufen wiederverwendbar.
    lock_path = path.with_suffix(".lock")
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with file_lock(lock_file, exclusive=True):
                _write_merged_state(path, state, deletions)
    except (OSError, TimeoutError) as exc:
        # Security: ``file_lock(..., exclusive=True)`` re-raises on
        # acquisition failure (timeout under contention, fcntl ENOLCK,
        # NFS hiccup, …). Skipping the state save here is the
        # fail-closed-but-recoverable choice: losing one update means
        # newly-arrived items get a stale ``first_seen`` next run, but
        # *overwriting* a parallel writer's update would corrupt the
        # cross-run dedup invariant for *every* item they tracked. The
        # next successful run reconciles via the merge step above.
        # Security (Clear-Text-Logging Drift): the bound exception (an
        # OSError or TimeoutError from the lock helper) may surface a
        # custom ``__str__`` carrying control bytes — sanitise.
        # Security (Path-Log Sibling Drift, sibling of PR #1456): see
        # ``_load_state`` for the rationale of fingerprinting the path
        # bytes rather than interpolating them verbatim.
        path_fingerprint = hashlib.sha256(
            str(path).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        log.warning(
            "State-Datei [path-sha256=%s] konnte nicht gesperrt werden "
            "(%s) – Update wird übersprungen, nächster Lauf merged frisch.",
            path_fingerprint,
            sanitize_log_arg(str(exc)),
        )


def _identity_for_item(item: FeedItem) -> str:
    """
    Stabile Identität unabhängig von Titel-Kosmetik.
      - Wenn Provider _identity liefert: diese bevorzugen.
      - ÖBB: GUID/Link (vom RSS stabil).
      - WL/sonstige: Quelle|Kategorie|Linienpräfix + Start-YYYY-MM-DD.
    """
    if item.get("_identity"):
        return str(item["_identity"])

    if "_calculated_identity" in item:
        return item["_calculated_identity"]

    title = item.get("title") or ""
    sa = item.get("starts_at")
    ea = item.get("ends_at")
    sa_str = _to_utc(sa).isoformat() if isinstance(sa, datetime) else "None"
    ea_str = _to_utc(ea).isoformat() if isinstance(ea, datetime) else "None"
    fuzzy_raw = f"{title}|{sa_str}|{ea_str}"
    # ``errors="surrogatepass"`` (here and the two ``json.dumps`` hashes below):
    # a lone surrogate (U+D800-U+DFFF, reachable via a ``\uD800`` escape in an
    # upstream JSON title) would otherwise raise ``UnicodeEncodeError`` at this
    # identity-hash encode — BEFORE ``_sanitize_text`` strips it from the
    # rendered title — aborting the whole build. ``surrogatepass`` lets the
    # hash encode the bytes deterministically; the hash value is unchanged for
    # every surrogate-free input (the normal case), so no first_seen churn.
    fuzzy_hash = hashlib.sha256(
        fuzzy_raw.encode("utf-8", "surrogatepass")
    ).hexdigest()

    result: str
    source = (item.get("source") or "").lower()
    category = (item.get("category") or "").lower()
    if "öbb" in source or "oebb" in source:
        guid_or_link = item.get("guid") or item.get("link")
        if guid_or_link:
            result = f"oebb|{guid_or_link}"
        else:
            result = f"oebb|F={fuzzy_hash}"
    else:
        lines = _parse_lines_from_title(title)
        lines_part = "L=" + "/".join(lines) if lines else "L="
        start_day = _ymd_or_none(sa)
        base = f"{source}|{category}|{lines_part}|D={start_day}"
        if source and category:
            if not lines:
                if item.get("title"):
                    result = f"{base}|T={item['title']}|F={fuzzy_hash}"
                else:
                    raw = json.dumps(item, sort_keys=True, default=str)
                    hashed = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
                    result = f"{base}|H={hashed}|F={fuzzy_hash}"
            else:
                result = f"{base}|F={fuzzy_hash}"
        # Fallback: Ohne Quelle/Kategorie Titel oder vollständigen Hash anhängen
        elif item.get("title"):
            result = f"{base}|T={item['title']}|F={fuzzy_hash}"
        else:
            raw = json.dumps(item, sort_keys=True, default=str)
            hashed = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
            result = f"{base}|H={hashed}|F={fuzzy_hash}"

    item["_calculated_identity"] = result
    return result


def _dedupe_key_for_item(
    it: FeedItem, *, warn_on_missing: bool = True
) -> tuple[str, bool]:
    """Return the deduplication key used for ``it`` and indicate fallback usage."""
    # Use explicit _identity if present
    if it.get("_identity"):
        return str(it.get("_identity")), False

    guid = it.get("guid")
    source = (it.get("source") or "").lower()

    if "öbb" in source or "oebb" in source:
        guid_or_link = guid or it.get("link")
        if guid_or_link:
            return f"oebb|{guid_or_link}", False

    if guid:
        return str(guid), False

    key = _identity_for_item(it)

    if warn_on_missing:
        log.warning(
            "Item ohne guid/_identity – Fallback-Schlüssel (_identity_for_item) %s",
            key,
        )
    return key, True


def _state_key_for_item(it: FeedItem) -> str:
    """Return the persistent ``first_seen`` state key for ``it``.

    Prefers the provider ``guid`` — stable across upstream title/description
    edits and the read-side title enrichment — so an item's ``first_seen``
    (and the age-based retirement that relies on it) survives those changes.
    Falls back to the content identity when no guid is present, so EVERY
    item, provider-independently, still gets a first_seen entry.
    """
    guid = it.get("guid")
    if guid:
        return str(guid)
    return _identity_for_item(it)


def _lookup_state(
    it: FeedItem, state: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any] | None]:
    """Return ``(stable_key, entry)`` for ``it``'s first_seen state.

    Prefers the guid-based key (:func:`_state_key_for_item`); on a miss it
    falls back to a legacy identity-keyed entry so first_seen migrates
    without a reset. Shared by the writer (:func:`_update_item_state`) and
    the age check (:func:`_drop_old_items`) so both stay consistent.
    """
    key = _state_key_for_item(it)
    entry = state.get(key)
    if entry is None:
        legacy = _identity_for_item(it)
        if legacy != key:
            entry = state.get(legacy)
    return key, entry
