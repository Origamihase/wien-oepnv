"""The model load logs the revision it loaded (audit 2026-10-01, CVE-2026-80047).

``transformers`` 4.49 to 5.8.1 writes a repo's ``custom_generate/generate.py``
into the cache on every ``from_pretrained``, before the ``trust_remote_code``
check. The feed loads one model, ``Helsinki-NLP/opus-mt-de-en``, pinned to
the commit the load logged on 2026-10-01, so a later push to the repo cannot
reach the build. The log line keeps naming the commit it loaded.

Mutations checked against this file (each one caught, by the test named):

* the revision is not logged → ``test_the_load_logs_the_revision``.
* a missing commit crashes the load → ``test_a_pipeline_without_a_commit_logs_unknown``.
* the revision is not passed to the pipeline → ``test_the_model_is_pinned_to_a_commit``.
"""

from __future__ import annotations

import logging
import re
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import src.build_feed as build_feed

COMMIT = "6fd7cbd8b0ad3c8e5c5a5c9c2b6b3c2d1e0f9a8b"


def _load(monkeypatch: pytest.MonkeyPatch, pipe: Any) -> Any:
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=lambda *_a, **_kw: pipe))
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "pipeline", None)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "load_failed", False)
    return build_feed._get_translation_pipeline()


def test_the_load_logs_the_revision(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    pipe = SimpleNamespace(model=SimpleNamespace(config=SimpleNamespace(_commit_hash=COMMIT)))
    with caplog.at_level(logging.INFO):
        assert _load(monkeypatch, pipe) is pipe
    assert f"Revision {COMMIT}" in caplog.text


def test_a_pipeline_without_a_commit_logs_unknown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipe = SimpleNamespace()
    with caplog.at_level(logging.INFO):
        assert _load(monkeypatch, pipe) is pipe
    assert "Revision unbekannt" in caplog.text
    assert build_feed._TRANSLATION_STATE["load_failed"] is False


def test_the_model_is_pinned_to_a_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    # A later push to the model repo must not reach the build (CVE-2026-80047).
    seen: dict[str, Any] = {}

    def _pipeline(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs, task=args[0] if args else None)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=_pipeline))
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "pipeline", None)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "load_failed", False)
    build_feed._get_translation_pipeline()
    assert seen["model"] == "Helsinki-NLP/opus-mt-de-en"
    assert seen["revision"] == build_feed._TRANSLATION_MODEL_REVISION
    assert re.fullmatch(r"[0-9a-f]{40}", build_feed._TRANSLATION_MODEL_REVISION)
