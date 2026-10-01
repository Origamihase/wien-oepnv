"""The model load logs the revision it loaded (audit 2026-10-01, CVE-2026-80047).

``transformers`` 4.49 to 5.8.1 writes a repo's ``custom_generate/generate.py``
into the cache on every ``from_pretrained``, before the ``trust_remote_code``
check. The feed loads one model, ``Helsinki-NLP/opus-mt-de-en``; logging the
commit it loaded lets the model be pinned to that revision, so a later push to
the repo cannot reach the build.

Mutations checked against this file (each one caught, by the test named):

* the revision is not logged → ``test_the_load_logs_the_revision``.
* a missing commit crashes the load → ``test_a_pipeline_without_a_commit_logs_unknown``.
"""

from __future__ import annotations

import logging
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
