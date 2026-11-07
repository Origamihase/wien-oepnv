"""Fetching and assembling events from the Wiener Linien API.
# No ThreadPoolExecutor used here (Thread Pool Resource Leaks checked)

The helpers live in the ``wl_*`` sibling modules (700-line rule,
2026-11-02); this module keeps ``fetch_events`` and re-exports them.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, UTC
from typing import Any
from collections.abc import Iterable

import requests

from ..utils.http import session_with_retries
from ..utils import raw_capture
from ..utils.ids import make_guid

from .wl_lines import (
    _detect_line_pairs_from_text,
    _ensure_line_prefix,
    _line_display_from_pairs,
    _line_tokens_from_pairs,
    _make_line_pairs_from_related,
    _merge_line_pairs,
)
from .wl_plausibility import (
    incident_begin,
    reset_corrections,
)
from . import wl_resolved
from .wl_text import (
    KW_EXCLUDE,
    KW_RESTRICTION,
    _title_core,
    _topic_key_from_title,
)
from .wl_api import (
    WL_BASE,
    WL_INFO_URL,
    WL_SESSION_HEADERS,
    WL_USER_AGENT,
    _WL_TRUSTED_HOSTS,
    _get_json,
    _validated_wl_base,
)
from .wl_item_fields import (
    _as_list,
    _best_ts,
    _coerce_dict,
    _effective_end,
    _effective_start,
    _is_active,
    _is_finished,
    _is_inactive_status,
    _iso,
    _status_drop_reason,
)
from .wl_title_context import (
    _build_context_suffix,
    _description_info_score,
    _facility_drop_reason,
    _normalize_whitespace,
    _stop_names_from_related,
    _title_or_heading,
    _title_quality_key,
)
from .wl_source_outage import (
    SourceIncompleteError,
    _extract_wl_items,
    _fallback_parts,
    _list_items,
    fallback_parts,
)
from .wl_ticker_fold import (
    _categories_compatible,
    _content_tokens,
    _earliest,
    _fold_display_tickers,
    _gate_text,
    _incident_ids,
    _is_headline_only,
    _says_nothing_new,
)
from .wl_aggregates import (
    _drop_covered_aggregates,
    _drop_covered_subsets,
    _lead_line,
    _stop_of_notice,
)


log = logging.getLogger(__name__)

_HALT_SUFFIX_RE = re.compile(r"\(\d+\s+Halt(?:e)?\)$")

# Security: ``MAX_WL_FETCH_TIMEOUT`` is the Slowloris-defence ceiling for the
# public ``fetch_events`` API. The ``timeout`` parameter flows through
# ``_fetch_traffic_infos`` / ``_fetch_news`` -> ``_get_json`` ->
# ``fetch_content_safe`` as both the connect and read budget for every Wiener
# Linien OGD request. The current call site in ``build_feed.py`` uses
# ``effective_timeout`` (already capped at ``feed_config.MAX_PROVIDER_TIMEOUT``)
# and ``scripts/update_wl_cache.py`` uses the 20-second default, so the
# production blast radius is currently zero. But ``fetch_events`` is exported
# as a public API (``__all__``) and a future caller passing an env-controlled
# or user-controlled value (e.g. a hypothetical ``WL_FETCH_TIMEOUT`` env var
# or a CLI flag wired into a maintenance script) would otherwise inherit the
# unbounded shape — ``timeout=99999`` (intentional misconfig, leaked CI env,
# compromised secret store) lets a sluggish or attacker-controlled upstream
# peer hold the worker for ~28 hours per fetch, stalling the cron pipeline.
# Capping inside the function (defense-in-depth) means every caller — current
# and future — inherits the ceiling without having to remember to add it.
# 25 seconds matches ``feed_config.MAX_PROVIDER_TIMEOUT`` (the orchestrator-
# level Slowloris ceiling) so no orchestrator-capped value is ever rejected,
# while the 20-second default (a conservative WL-OGD response budget) is
# preserved unchanged. TIGHTEN-only contract mirrors ``MAX_OEBB_FETCH_TIMEOUT``
# (``src/providers/oebb.py``) — same parameter-boundary defense-in-depth
# pattern documented in the 2026-05-07 Slowloris-Cap Drift Round 4 journal
# entry, applied to the WL sibling that round explicitly named as still-open.
MAX_WL_FETCH_TIMEOUT = 25


def _wl_identity(
    prefix: str,
    line_pairs: list[tuple[str, str]],
    real_start: datetime | None,
    topic_key: str,
) -> str:
    """Build a stable, collision-resistant ``_identity`` for a WL item.

    The key is line-set + start-day + ``topic_key``. ``_dedupe_items``
    (``build_feed.py``) keys on ``_identity`` FIRST and never reaches the
    finer ``guid``, so whatever this function conflates is dropped from the
    feed without a trace.

    ``topic_key`` used to be folded in only when the line set or the start
    date was missing, on the assumption that lines + day identify a
    disruption on their own. The live data disproves it — the Wiener Linien
    routinely publish several unrelated disruptions for one line on one day
    (2026-09-12, ``feed lint``: 4 of 83 items dropped):

        wl|störung|L=44|D=2026-09-11
            44: Veranstaltung Züge halten Rosensteingasse …
            44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz
            44: Fahrtbehinderung Veranstaltung
        wl|hinweis|L=49A,50B|D=2026-08-25
            49A/50B: Mondweg
            49A/50B: Hüttergasse        <- anderer Ort, verworfen

    Folding ``topic_key`` in unconditionally costs no stability, because it
    is not a new signal at this layer: the bucket key in :func:`fetch_events`
    and the per-item ``guid`` are BOTH already built from
    ``(category, topic_key, line set)``. So

    * true duplicates never reach this comparison — they were merged into one
      bucket upstream, by exactly that triple;
    * ``first_seen`` does not churn: :func:`_state_key_for_item` keys it on
      the ``guid``, which already moves whenever ``topic_key`` moves.

    What the change does drop is the legacy ``_identity``-keyed ``first_seen``
    fallback in ``_lookup_state`` for pre-guid entries. Measured on the live
    state: of 53 cached WL items, 48 resolve via ``guid``, 5 are new and 0
    depend on that fallback.
    """
    id_lines = ",".join(sorted(_line_tokens_from_pairs(line_pairs)))
    id_day = (
        real_start.date().isoformat()
        if isinstance(real_start, datetime)
        else "None"
    )
    return f"wl|{prefix}|L={id_lines}|D={id_day}|TK={topic_key}"


def _fetch_traffic_infos(
    timeout: int = 20, session: requests.Session | None = None
) -> Iterable[dict[str, Any]]:
    # explizit KEINE Facility-Feeds
    params = [("name", "stoerunglang"), ("name", "stoerungkurz")]
    data = _get_json("trafficInfoList", params=params, timeout=timeout, session=session)
    return _list_items("trafficInfoList", "trafficInfos", data)


def _fetch_news(
    timeout: int = 20, session: requests.Session | None = None
) -> Iterable[dict[str, Any]]:
    data = _get_json("newsList", timeout=timeout, session=session)
    return _list_items("newsList", "pois", data)


# ---------------- Public API ----------------

def fetch_events(timeout: int = 20) -> list[dict[str, Any]]:
    # Security: clamp ``timeout`` to ``MAX_WL_FETCH_TIMEOUT`` to defeat the
    # Slowloris vector documented at the constant declaration above. Without
    # the cap a caller passing ``timeout=99999`` would let a sluggish or
    # attacker-controlled upstream peer stall the cron for ~28 hours per fetch.
    # ``min(...)`` instead of ``if ...:`` to avoid bumping the McCabe complexity
    # of this already-baselined function (``.c901-baseline.txt`` lists
    # ``src/providers/wl_fetch.py::fetch_events``, and the gate fails on any
    # change of its value). Same TIGHTEN-only contract as the ``if`` form:
    # legitimate values below the cap pass through unchanged.
    timeout = min(timeout, MAX_WL_FETCH_TIMEOUT)
    now = datetime.now(UTC)
    reset_corrections()
    raw_capture.reset_drops("wl")
    _fallback_parts.clear()
    raw: list[dict[str, Any]] = []

    with session_with_retries(WL_USER_AGENT, raise_on_status=False) as session:
        session.headers.update(WL_SESSION_HEADERS)
        # A) TrafficInfos (Störungen)
        infos = list(_fetch_traffic_infos(timeout=timeout, session=session))
        stale = wl_resolved.stale_tickers(infos, _is_finished)
        for ti in infos:
            attrs = _coerce_dict(ti.get("attributes"))
            drop_reason = _status_drop_reason(ti, stale)
            if drop_reason:
                raw_capture.note_drop("wl", drop_reason, ti.get("title") or ti.get("name"))
                continue

            title_raw = str(ti.get("title") or ti.get("name") or "Meldung").strip()
            # Neither an empty nor a lines-only title reaches the feed (see
            # ``_title_or_heading``).
            desc_raw = str(ti.get("description") or "").strip()
            title = _title_or_heading(title_raw, desc_raw)
            # Do NOT strip HTML here, we need to preserve links (Task 3)
            desc = desc_raw
            # Facility check is TITLE-driven (mirrors the ÖBB sibling
            # _is_facility_or_weather_only, which inspects only the title): a
            # facility word in the free-text DESCRIPTION is a side-mention and
            # must NOT drop a genuine line disruption (e.g. "U4: Streckensperre"
            # whose description also notes an out-of-service lift). Only a
            # facility-only TITLE drops the item. The description only says
            # whether the title is a stop's name (``is_stop_notice``).
            facility = _facility_drop_reason(title_raw, desc_raw, ti)
            if facility:
                raw_capture.note_drop("wl", facility, title_raw)
                continue

            tinfo = _coerce_dict(ti.get("time"))
            start = _iso(tinfo.get("start")) or _best_ts(ti)
            end = _effective_end(desc_raw, _iso(tinfo.get("end")), start)

            real_start = _effective_start(title_raw, desc_raw, start, end, now)

            # We check activity based on the API start time (publication/validity start),
            # NOT the event start time extracted from the title.
            # This ensures advance notices (Vorankündigungen) are shown.
            if not _is_active(start, end, now):
                raw_capture.note_drop("wl", "außerhalb des Zeitraums", title_raw)
                continue

            blob_for_relevance = _gate_text(title_raw, desc_raw)
            if KW_EXCLUDE.search(blob_for_relevance) and not KW_RESTRICTION.search(
                blob_for_relevance
            ):
                raw_capture.note_drop("wl", "Ausschluss-Stichwort ohne Einschränkung", title_raw)
                continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines)
            if not line_pairs:
                # Fallback: aus Titeltext (inkl. „Rufbus Nxx“, aber ohne Datum/Zeit/Adresse)
                line_pairs = _detect_line_pairs_from_text(title_raw)

            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            for k in ("status", "state", "station", "location", "reason", "towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            # stabile Identity für first_seen
            topic_key = _topic_key_from_title(title_raw)
            identity = _wl_identity("störung", line_pairs, real_start, topic_key)
            wl_ids = _incident_ids(ti)

            raw.append(
                {
                    "source": "Wiener Linien",
                    "category": "Störung",
                    "title": title,
                    "title_core": _title_core(title_raw),
                    "topic_key": topic_key,
                    "desc": desc,
                    "extras": extras,
                    "lines_pairs": line_pairs,  # [(tok, disp), …]
                    "stop_names": set(stop_names),
                    "pubDate": start,  # Publication/Creation date remains original
                    "starts_at": real_start, # Effective start date (for calendar)
                    "ends_at": end,
                    "_identity": identity,
                    "wl_ids": wl_ids,
                    # A display ticker's start can be an old one (``incident_begin``).
                    "incident_start": start if wl_ids else None,
                }
            )

        # B) News/Hinweise
        for poi in _fetch_news(timeout=timeout, session=session):
            attrs = _coerce_dict(poi.get("attributes"))
            if _is_inactive_status(
                poi.get("status"), attrs.get("status"), attrs.get("state")
            ):
                raw_capture.note_drop("wl", "Status inaktiv", poi.get("title") or poi.get("name"))
                continue

            # Mirror the TrafficInfo branch's title fallback so a POI
            # with empty ``title`` but a populated ``name`` (real WL
            # News payload shape) doesn't collapse to the literal
            # placeholder "Hinweis".
            title_raw = str(
                poi.get("title") or poi.get("name") or "Hinweis"
            ).strip()
            # Neither an empty nor a lines-only title reaches the feed (see
            # ``_title_or_heading``).
            desc_raw = str(poi.get("description") or "").strip()
            title = _title_or_heading(title_raw, desc_raw)
            # Do NOT strip HTML here, we need to preserve links (Task 3)
            desc = desc_raw
            # Title-driven facility check (see the trafficInfo branch above):
            # a facility word in the description / subtitle is only a
            # side-mention and must not drop a genuine line disruption. WL's
            # lift records (``elevatorId``) drop by that field; the title of
            # a stop relocation is the stop's name and is not read as a
            # subject (``_facility_drop_reason``).
            facility = _facility_drop_reason(title_raw, desc_raw, poi)
            if facility:
                raw_capture.note_drop("wl", facility, title_raw)
                continue

            tinfo = _coerce_dict(poi.get("time"))
            start = _iso(tinfo.get("start")) or _best_ts(poi)
            end = _effective_end(desc_raw, _iso(tinfo.get("end")), start)

            real_start = _effective_start(title_raw, desc_raw, start, end, now)

            if not _is_active(start, end, now):
                raw_capture.note_drop("wl", "außerhalb des Zeitraums", title_raw)
                continue

            text_for_filter = _gate_text(
                title_raw,
                str(poi.get("subtitle") or ""),
                desc_raw,
                str(attrs.get("status") or ""),
                str(attrs.get("state") or ""),
            )
            if not KW_RESTRICTION.search(text_for_filter):
                raw_capture.note_drop("wl", "kein Einschränkungs-Stichwort", title_raw)
                continue

            rel_lines = _as_list(poi.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines)
            if not line_pairs:
                line_pairs = _detect_line_pairs_from_text(title_raw)

            rel_stops = _as_list(poi.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            if poi.get("subtitle"):
                extras.append(str(poi["subtitle"]).strip())
            for k in ("station", "location", "towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            topic_key = _topic_key_from_title(title_raw)
            identity = _wl_identity("hinweis", line_pairs, real_start, topic_key)

            raw.append(
                {
                    "source": "Wiener Linien",
                    "category": "Hinweis",
                    "title": title,
                    "title_core": _title_core(title_raw),
                    "topic_key": topic_key,
                    "desc": desc,
                    "extras": extras,
                    "lines_pairs": line_pairs,  # [(tok, disp), …]
                    "stop_names": set(stop_names),
                    "pubDate": start,
                    "starts_at": real_start,
                    "ends_at": end,
                    "_identity": identity,
                }
            )

    # C) Bündelung: LINIEN-SET + TOPIC
    buckets: dict[str, dict[str, Any]] = {}
    for ev in raw:
        line_toks_sorted = ",".join(sorted(_line_tokens_from_pairs(ev["lines_pairs"])))
        key = make_guid(
            "wl",
            ev["category"],
            ev["topic_key"],
            line_toks_sorted,
        )
        b = buckets.get(key)
        if not b:
            buckets[key] = {
                "source": ev["source"],
                "category": ev["category"],
                "title": ev["title"],
                "title_core": ev.get("title_core", ""),
                "topic_key": ev["topic_key"],
                "desc_base": ev["desc"],
                "extras": list(ev["extras"]),
                "lines_pairs": list(ev["lines_pairs"]),  # geordnete Paare
                "stop_names": set(ev["stop_names"]),
                "pubDate": ev["pubDate"],
                "starts_at": ev["starts_at"],
                "ends_at": ev["ends_at"],
                "_identity": ev["_identity"],  # stabil weiterreichen
                "wl_ids": set(ev.get("wl_ids") or ()),
                "incident_start": ev.get("incident_start"),
            }
        else:
            current_title = b["title"]
            current_core = b.get("title_core", "")
            current_desc = b.get("desc_base", "")

            base_score = _description_info_score(
                current_desc,
                title=current_title,
                stop_names=b["stop_names"],
                extras=b["extras"],
            )
            candidate_score = _description_info_score(
                ev.get("desc", ""),
                title=ev["title"],
                stop_names=ev["stop_names"],
                extras=ev["extras"],
            )

            if _title_quality_key(ev["title"], ev.get("title_core", "")) > _title_quality_key(
                current_title, current_core
            ):
                b["title"] = ev["title"]
                b["title_core"] = ev.get("title_core", "")

            if candidate_score > base_score:
                b["desc_base"] = ev.get("desc", "")

            b["lines_pairs"] = _merge_line_pairs(b["lines_pairs"], ev["lines_pairs"])
            b["stop_names"].update(ev["stop_names"])
            b["wl_ids"].update(ev.get("wl_ids") or ())

            # Use the earliest pubDate for the bucket (to show when first detected/announced)
            if ev["pubDate"] and (
                not b["pubDate"] or ev["pubDate"] < b["pubDate"]
            ):
                b["pubDate"] = ev["pubDate"]
            b["incident_start"] = _earliest(b.get("incident_start"), ev.get("incident_start"))

            # A notice (Hinweis) spans its phases and starts at the earliest
            # of them, like the end below and the fuzzy merge
            # (``_merge_validity``). The latest start turned
            # "11: Gleisbauarbeiten" (01.09.–15.10.) plus "… ab 20.10.2026"
            # into works that only begin on 20.10. while the first phase
            # was running. Disruptions keep the latest start: WL reuses old
            # display tickers for a new incident, and their stale start
            # would date it back (docs/architecture.md, 27.09.). An unknown
            # start never erases a known one.
            es, bs = ev["starts_at"], b["starts_at"]
            if es and (
                not bs or (es < bs if b["category"] == "Hinweis" else es > bs)
            ):
                b["starts_at"] = es

            be, ee = b["ends_at"], ev["ends_at"]
            b["ends_at"] = None if (be is None or ee is None) else max(be, ee)
            for x in ev["extras"]:
                if x not in b["extras"]:
                    b["extras"].append(x)

    if len(_fallback_parts) >= 2:
        # Beide Listen aus der letzten guten Antwort: Das ist ein Ausfall der
        # ganzen Quelle, der Cache bleibt stehen (Begründung oben).
        raise SourceIncompleteError("WL: beide Listen ohne brauchbare Antwort")
    if not _fallback_parts:
        # Die Verwürfe einer alten Antwort sagen nichts über diesen Abruf.
        raw_capture.write_drops("wl")

    # Anzeigetafel-Kurzmeldungen in die ausführliche Meldung derselben
    # Störung übernehmen (Begründung am Helfer).
    _fold_display_tickers(buckets)

    # D) Finale Items mit Linien-Präfix im Titel
    items: list[dict[str, Any]] = []
    for b in buckets.values():
        lines_disp = _line_display_from_pairs(b["lines_pairs"])
        lines_tok = set(_line_tokens_from_pairs(b["lines_pairs"]))

        base_title = b["title"]
        context_suffix, _extras_for_context = _build_context_suffix(
            b, base_title, lines_disp
        )
        title_with_lines = _ensure_line_prefix(base_title, lines_disp)
        if context_suffix:
            title_with_lines = (
                f"{title_with_lines} – {context_suffix}" if title_with_lines else context_suffix
            )

        # Anzahl Halte ins Titelende
        halt_cnt = len(b["stop_names"])
        if halt_cnt > 0 and not _HALT_SUFFIX_RE.search(title_with_lines):
            title_with_lines += f" ({halt_cnt} Halt{'e' if halt_cnt != 1 else ''})"

        title_final = re.sub(r"[<>«»‹›]+", "", title_with_lines).strip()

        # Beschreibung aufbauen (ohne „Linien: …“ in extras)
        desc = b["desc_base"]

        if b["stop_names"]:
            stops_formatted = ", ".join(sorted(b["stop_names"]))
            desc += f" | Haltestelle: {stops_formatted}"
        elif b["extras"]:
            locations = [
                x for x in b["extras"]
                if x.lower().startswith(("station:", "location:")) or ":" not in x
            ]
            if locations:
                locations_formatted = " / ".join(locations)
                desc += f" | {locations_formatted}"

        # Keine Extras oder Haltestellen mehr anhängen (Task: Strict 2-line Layout)
        # Keine aggressive HTML-Entfernung mehr (Task: Fix h2Artifacts)
        desc = re.sub(r"\s{2,}", " ", desc).strip()

        guid = make_guid(
            "wl",
            b["category"],
            b["topic_key"],
            ",".join(sorted(lines_tok)),
        )
        items.append(
            {
                "source": b["source"],
                "category": b["category"],
                "title": title_final,  # plain text
                "description": desc,  # plain text
                "link": WL_INFO_URL,
                "guid": guid,
                # None erlaubt; a rewritten ticker does not date it back
                "pubDate": incident_begin(b["pubDate"], b.get("incident_start")),
                "starts_at": b["starts_at"],
                "ends_at": b["ends_at"],
                "_identity": b["_identity"],  # stabil für first_seen
                **({"_wl_ids": sorted(b["wl_ids"])} if b.get("wl_ids") else {}),
                **({"_incident_start": b["incident_start"]} if b.get("incident_start") else {}),
                "_lines_set": lines_tok,  # für Sammel-vs.-Einzel
                # Für E) und F): was die Meldung über ihre Linien hinaus sagt,
                # und alles, was sie sagt.
                "_title_tokens": _content_tokens(b["title"])
                - {tok.casefold() for tok in lines_tok},
                "_text": f"{title_final} {desc}",
                "_lead_tokens": _content_tokens(f"{b['title']} {_lead_line(desc)}"),
                "_stop": _stop_of_notice(desc),
            }
        )

    # E) Sammel-vs.-Einzel und F) Subset-Bereinigung (Begründung an den Helfern)
    filtered = _drop_covered_subsets(_drop_covered_aggregates(items))

    # Aufräumen interner Felder + Sortierung
    for it in filtered:
        it.pop("_lines_set", None)
        it.pop("_title_tokens", None)
        it.pop("_text", None)
        it.pop("_lead_tokens", None)
        it.pop("_stop", None)

    filtered.sort(
        key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, x["guid"])
    )
    log.info("WL: %d Items nach Filter/Dedupe", len(filtered))
    return filtered


__all__ = [
    "SourceIncompleteError",
    "fallback_parts",
    "fetch_events",
    "MAX_WL_FETCH_TIMEOUT",
    "WL_BASE",
    "WL_INFO_URL",
    "_WL_TRUSTED_HOSTS",
    "_best_ts",
    "_categories_compatible",
    "_coerce_dict",
    "_content_tokens",
    "_effective_end",
    "_effective_start",
    "_extract_wl_items",
    "_facility_drop_reason",
    "_fetch_news",
    "_fetch_traffic_infos",
    "_fold_display_tickers",
    "_gate_text",
    "_get_json",
    "_incident_ids",
    "_is_headline_only",
    "_is_inactive_status",
    "_iso",
    "_normalize_whitespace",
    "_says_nothing_new",
    "_stop_names_from_related",
    "_title_or_heading",
    "_validated_wl_base",
    "_wl_identity",
    "datetime",
    "log",
    "session_with_retries",
]
