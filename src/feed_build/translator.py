"""The EN translation model: loading, status and service compounds.

This module owns ``_TRANSLATION_STATE``; ``src.build_feed`` re-exports the
same dict.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from ..utils.files import atomic_write, read_capped_json
from ..utils.logging import sanitize_log_arg
from ..utils.serialize import scrub_trojan_source_primitives

from .base import log, validate_path
from .placeholders import _GLOSSARY_PLACEHOLDER_FORMAT, _PLACEHOLDER_NONCE, _resolve_glossary

__all__ = [
    "_TRANSLATION_STATE", "_TRANSLATION_MODEL_REVISION", "_mask_service_compounds", "_service_compound_defect",
    "_MarianTranslator", "_get_translation_pipeline", "_record_translation_status",
]


# ---------------- Translation engine (Helsinki-NLP/opus-mt-de-en) ----------------
#
# The translator (``_MarianTranslator``) is loaded lazily so the CLI commands
# (``feed lint``, ``feed build`` with ``WIEN_OEPNV_LANGS=de``) do not
# pay the ~300 MB model-load cost when the EN feed is not requested.
# The state is held in a single module-level dict so CodeQL does not
# misread a sentinel ``_TRANSLATION_LOAD_FAILED`` flag as an unused
# global (CodeQL's "Unused global variable" check does not follow the
# ``global`` declaration through a circuit-breaker assignment).
_TRANSLATION_STATE: dict[str, Any] = {"pipeline": None, "load_failed": False, "error": None}
_TRANSLATION_MODEL_NAME = "Helsinki-NLP/opus-mt-de-en"
# The model revision the feed loads (CVE-2026-80047, audit 2026-10-01).
# ``transformers`` 4.49 to 5.8.1 wrote a repo's ``custom_generate/generate.py``
# into the cache on every load, before the ``trust_remote_code`` check; 5.22.1
# (since 2026-11-01) is fixed. The pin stays: a later push to the repo cannot
# reach the build, and the translations stay the same. This is the commit the
# update cycle of 2026-10-01 17:01 logged (``_model_revision``), so the
# translations do not change and the translation cache keeps its epoch. To
# move to a newer revision, change it here and bump the epoch below.
_TRANSLATION_MODEL_REVISION = "1a922f3b32a8e809e17a47d4b32142d8105924e5"
# Where each build that needed the model records whether it loaded
# (:func:`_record_translation_status`, read by the Health check, point
# "Übersetzung"). Rides the Update run's commit like ``feed_watch.json``.
# ``TRANSLATION_STATUS_PATH`` overrides it; read on every write, so a test's
# override survives ``importlib.reload``.
_TRANSLATION_STATUS_DEFAULT = "data/translation_status.json"


# Service compounds: ``<Anlass>verkehr`` in a Wiener Linien or ÖBB text.
# When an operator writes about its own lines, ``Verkehr`` at the end of a
# compound is that operator's service for an occasion. The model does not
# know such compounds and loses the head or the whole sense (real model,
# 2026-10-30)::
#
#     38B/39B: Allerheiligenverkehr 01.11.2026  ->  All Saints' Worship
#     Feiertagsverkehr                          ->  Public holidays
#     Konzertverkehr                            ->  Concertation
#     Donauinselfestverkehr                     ->  Donauinselfestverkehr
#
# The occasion alone it knows ("Allerheiligen" -> "All Saints",
# "Fronleichnam" -> "Corpus Christi", "Silvester" -> "New Year's Eve"). So
# the compound is rendered from its parts: the occasion through the model
# alone, the head as "service". An inflected glossary compound
# ("des Schienenersatzverkehres") takes the glossary's English for its
# uninflected form. Kept to the model as before are compounds whose first
# part names road traffic, a mode, an area or a time of day the model
# misreads alone ("Morgen" -> "Tomorrow"); there ``Verkehr`` is traffic or
# the compound is a fixed term the model knows ("Kreisverkehr" ->
# "roundabout", "Pendelbusverkehr" -> "shuttle bus service"). Stadt Wien
# Baustellen texts are about road traffic and stay untouched.
_SERVICE_COMPOUND_SOURCES: frozenset[str] = frozenset({"Wiener Linien", "ÖBB"})
_SERVICE_COMPOUND_RE: re.Pattern[str] = re.compile(
    r"(?<![\w-])(?P<modifier>[A-ZÄÖÜ][\w-]*?[^\W\d_])(?:-V|v)erkehr(?:es|s)?(?!\w)"
)
# First parts (casefolded, compared by their end) that keep the model.
_SERVICE_COMPOUND_KEEP: tuple[str, ...] = (
    # road traffic
    "kreis", "gesamt", "individual", "gegen", "fließ", "liefer", "rad",
    "fußgänger", "fußgängerinnen", "fahrzeug", "kfz", "pkw", "lkw", "auto",
    "abbiege", "umleitung", "umleitungs", "baustelle", "baustellen",
    "anrainer", "durchzug", "ziel", "quell", "straße", "straßen", "schwer",
    "transit", "beruf", "stoß", "pendler", "reise", "urlaub",
    # modes and areas
    "bahn", "bus", "zug", "tram", "schiff", "flug", "luft", "güter",
    "personen", "fahrgast", "regional", "nah", "fern", "stadt", "ort",
    "linien", "takt", "verstärker", "pendel", "shuttle",
    # time of day the model misreads alone
    "morgen",
)
_SERVICE_MODIFIER_EN: dict[tuple[int, str], str | None] = {}


def _service_modifier_en(modifier: str) -> str | None:
    """The model's English for an occasion alone, or ``None`` when unusable.

    Memoised per model and occasion: an occasion recurs across title and description
    and across the items of a build. Unusable is a failed call, an empty or
    long answer, or one with German letters left in it. The occasion echoed
    back is fine: "Advent" and "Marathon" are English too.
    """
    pipe = _get_translation_pipeline()
    key = (id(pipe), modifier)
    if key in _SERVICE_MODIFIER_EN:
        return _SERVICE_MODIFIER_EN[key]
    english: str | None = None
    if pipe is not None:
        try:
            result = pipe(modifier, max_length=64, truncation=True)
            raw = result[0]["translation_text"]
        except Exception:
            raw = None
        if isinstance(raw, str):
            candidate = raw.strip().rstrip(".").strip()
            if (
                candidate
                and len(candidate.split()) <= 4
                and not re.search(r"[äöüÄÖÜß]", candidate)
            ):
                english = candidate
    _SERVICE_MODIFIER_EN[key] = english
    return english


def _service_compound_en(
    match: re.Match[str],
    *,
    source: str | None,
    category: str | None,
    occasions_only: bool = False,
) -> str | None:
    """English for one ``<Anlass>verkehr`` match, ``None`` to keep the model.

    *occasions_only* skips inflected glossary compounds: the ÖBB templates
    render those sentences themselves, in their own words.
    """
    modifier = match["modifier"].rstrip("-")
    uninflected = f"{modifier}verkehr"
    glossary = _resolve_glossary(source, category)
    for key, value in glossary.items():
        if key.casefold() == uninflected.casefold():
            return None if occasions_only else value
    # Fugen-s ("Feiertagsverkehr", "Fronleichnamsverkehr"), not the s of a
    # word ("Busverkehr").
    if (
        len(modifier) > 3
        and modifier.endswith("s")
        and modifier[-2] not in "aeiouäöüys"
    ):
        modifier = modifier[:-1]
    if modifier.casefold().endswith(_SERVICE_COMPOUND_KEEP):
        return None
    occasion = _service_modifier_en(modifier)
    return f"{occasion} service" if occasion else None


def _mask_service_compounds(
    text: str,
    mapping: dict[str, str],
    *,
    source: str | None,
    category: str | None,
) -> str:
    """Replace each ``<Anlass>verkehr`` of a WL/ÖBB text by a glossary placeholder.

    Runs after :func:`_apply_domain_glossary` and adds to its *mapping*, so
    the placeholders share its format and numbering.
    """
    if source not in _SERVICE_COMPOUND_SOURCES or not text:
        return text
    surface_to_placeholder: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        surface = match.group(0)
        cached = surface_to_placeholder.get(surface)
        if cached is not None:
            return cached
        english = _service_compound_en(match, source=source, category=category)
        if english is None:
            return surface
        placeholder = _GLOSSARY_PLACEHOLDER_FORMAT.format(index=len(mapping))
        mapping[placeholder] = english
        surface_to_placeholder[surface] = placeholder
        return placeholder

    return _SERVICE_COMPOUND_RE.sub(_replace, text)


def _service_compound_defect(
    source_text: str, cached: str, *, source: str | None, category: str | None
) -> str | None:
    """``"service compound"`` when *cached* lacks a compound's fixed English.

    A value cached before 2026-10-30 reads "All Saints' Worship"; only such an
    item is translated again, not every item, as an epoch move would.
    """
    if source not in _SERVICE_COMPOUND_SOURCES:
        return None
    for match in _SERVICE_COMPOUND_RE.finditer(source_text):
        english = _service_compound_en(
            match, source=source, category=category, occasions_only=True
        )
        if english is not None and english.casefold() not in cached.casefold():
            return "service compound"
    return None


def _model_revision(pipe: Any) -> str:
    """The commit of the model revision *pipe* loaded, or ``"unbekannt"``.

    Logged so the model can be pinned to the revision the feed actually
    uses: ``transformers`` 4.49 to 5.8.1 writes a repo's
    ``custom_generate/generate.py`` into the cache on every load, before the
    ``trust_remote_code`` check (CVE-2026-80047, audit 2026-10-01). A pinned
    revision keeps a later push to the repo out of the build.
    """
    commit = getattr(pipe, "commit", None)
    if not (isinstance(commit, str) and commit):
        # transformers 4 kept the commit on the model config; 5 no longer does
        # (first build after #2118 logged "Revision unbekannt").
        commit = getattr(getattr(getattr(pipe, "model", None), "config", None), "_commit_hash", None)
    return str(sanitize_log_arg(commit)) if isinstance(commit, str) and commit else "unbekannt"


class _MarianTranslator:
    """German → English with the Marian model, called directly.

    ``transformers`` 5 removed the ``translation_de_to_en`` pipeline task the
    feed used up to 4.x ("The task translation_de_to_en has been removed from
    transformers", Dependabot #2114, 2026-11-01). The model itself still runs
    in 5.x; this class does what the 4.x ``TranslationPipeline`` did for one
    string: tokenise with truncation, ``generate`` under the model's
    ``generation_config.json`` (``num_beams`` 4, ``max_length`` 512 at the
    pinned revision), decode without special tokens. Replayed on 2026-11-01
    over all 792 model inputs of the 3,003 EN texts since July: identical to
    the 4.57.6 pipeline, character for character.

    The call shape stays the pipeline's (``translator(text, max_length=…,
    truncation=True)`` → ``[{"translation_text": …}]``), so both call sites
    and every test double keep working unchanged.
    """

    def __init__(self, tokenizer: Any, model: Any, commit: str | None = None) -> None:
        self.tokenizer = tokenizer
        self.model = model
        # The commit the files came from (see ``_model_revision``).
        self.commit = commit

    def __call__(
        self, text: str, *, max_length: int | None = None, truncation: bool = True
    ) -> list[dict[str, str]]:
        import torch

        encoded = self.tokenizer(text, return_tensors="pt", truncation=truncation)
        kwargs: dict[str, Any] = {}
        if max_length is not None:
            kwargs["max_length"] = max_length
        with torch.inference_mode():
            output = self.model.generate(**encoded, **kwargs)
        return [{"translation_text": self.tokenizer.decode(output[0], skip_special_tokens=True)}]


def _load_translator() -> Any:
    """Load tokenizer and model at the pinned revision (see ``_MarianTranslator``)."""
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(  # nosec B615 - pinned revision
        _TRANSLATION_MODEL_NAME, revision=_TRANSLATION_MODEL_REVISION
    )
    model = AutoModelForSeq2SeqLM.from_pretrained(  # nosec B615 - pinned revision
        _TRANSLATION_MODEL_NAME, revision=_TRANSLATION_MODEL_REVISION
    )
    model.eval()
    return _MarianTranslator(tokenizer, model, _loaded_commit())


def _loaded_commit() -> str | None:
    """The commit of the cached snapshot the model files were loaded from.

    ``transformers`` 5 no longer records it on ``model.config._commit_hash``;
    the snapshot directory of ``config.json`` in the Hugging Face cache is
    named after it. ``None`` when it cannot be found (never fails the load).
    """
    try:
        from transformers.utils.hub import cached_file, extract_commit_hash

        path = cached_file(  # nosec B615 - pinned revision
            _TRANSLATION_MODEL_NAME, "config.json", revision=_TRANSLATION_MODEL_REVISION
        )
        commit = extract_commit_hash(path, None) if path else None
    except Exception:
        return None
    return commit if isinstance(commit, str) and commit else None


def _get_translation_pipeline() -> Any:
    """Lazily load the German → English translator.

    Returns the translator on success, ``None`` on any import or model-
    load failure (defensive: the EN feed degrades to the German
    original rather than crashing the build). The failure is kept in
    ``_TRANSLATION_STATE["error"]`` so :func:`_record_translation_status`
    can hand it to the Health check: a translation that cannot load must
    be noticed, a log line does not count. State is held in a
    module-level dict to avoid the ``global`` declaration pattern
    CodeQL misclassifies as an unused-global write.
    """
    if _TRANSLATION_STATE["pipeline"] is not None:
        return _TRANSLATION_STATE["pipeline"]
    if _TRANSLATION_STATE["load_failed"]:
        return None
    try:
        _TRANSLATION_STATE["pipeline"] = _load_translator()
        # The nonce is logged so a build whose translations fail on residual
        # placeholders (see ``_RESIDUAL_PLACEHOLDER_RE``) can be correlated
        # with the nonce the model saw. It is worthless outside this process,
        # and every source text of this run was fetched before this point.
        log.info(
            "Übersetzungsmodell %s geladen (Revision %s, Platzhalter-Nonce "
            "dieses Builds: %s).",
            _TRANSLATION_MODEL_NAME,
            _model_revision(_TRANSLATION_STATE["pipeline"]),
            _PLACEHOLDER_NONCE,
        )
    except Exception as exc:
        _TRANSLATION_STATE["load_failed"] = True
        _TRANSLATION_STATE["error"] = str(sanitize_log_arg(f"{type(exc).__name__}: {exc}"))[:300]
        log.warning(
            "Übersetzungsmodell konnte nicht geladen werden (%s) – "
            "EN-Feed nutzt Originaltext.",
            sanitize_log_arg(str(exc)),
        )
        return None
    return _TRANSLATION_STATE["pipeline"]


def _record_translation_status(now: datetime) -> None:
    """Write whether this build could load the translator, for the Health check.

    Nothing is written when the build did not need the model (every EN
    text came from the cache): the last record then stays the truth. A
    failed load counts up ``failed_builds``; the Health check shows the
    first failed build as a note and turns red from the second in a row
    (operator decision 2026-11-01, "Ja, Umstieg bauen": a translation that
    cannot load must be noticed). A write error never fails the build.
    """
    if _TRANSLATION_STATE["pipeline"] is None and not _TRANSLATION_STATE["load_failed"]:
        return
    try:
        raw_path = os.getenv("TRANSLATION_STATUS_PATH") or _TRANSLATION_STATUS_DEFAULT
        path = validate_path(Path(raw_path), "TRANSLATION_STATUS_PATH")
        previous = read_capped_json(path, label="translation_status")
        failed_before = 0
        if isinstance(previous, dict) and isinstance(previous.get("failed_builds"), int):
            failed_before = max(0, previous["failed_builds"])
        loaded = not _TRANSLATION_STATE["load_failed"]
        record: dict[str, Any] = {
            "updated": now.astimezone(UTC).isoformat(),
            "loaded": loaded,
            "failed_builds": 0 if loaded else failed_before + 1,
            "error": None if loaded else _TRANSLATION_STATE.get("error"),
            "model": _TRANSLATION_MODEL_NAME,
            "revision": _TRANSLATION_MODEL_REVISION,
        }
        # The error text comes from a library; scrub BiDi/zero-width marks
        # before it lands in a committed file (Trojan-Source walker).
        record = scrub_trojan_source_primitives(record)
        with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as f:
            json.dump(record, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "Übersetzungsstatus konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )
