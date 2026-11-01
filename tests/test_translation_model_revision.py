"""The translator loads the pinned model revision and logs it (audit 2026-10-01).

``transformers`` 4.49 to 5.8.1 wrote a repo's ``custom_generate/generate.py``
into the cache on every ``from_pretrained``, before the ``trust_remote_code``
check (CVE-2026-80047). The feed loads one model, ``Helsinki-NLP/opus-mt-de-en``,
pinned to the commit the load logged on 2026-10-01, so a later push to the repo
cannot reach the build. Since 2026-11-01 the model is called directly
(``_MarianTranslator``): ``transformers`` 5 removed the ``translation_de_to_en``
pipeline task (Dependabot #2114).

Mutations checked against this file (each one caught, by the test named):

* the revision is not logged → ``test_the_load_logs_the_revision``.
* a missing commit crashes the load → ``test_a_model_without_a_commit_logs_unknown``.
* the revision is not passed to tokenizer or model → ``test_the_model_is_pinned_to_a_commit``.
* ``max_length`` is dropped or special tokens are kept → ``test_the_translator_keeps_the_pipeline_shape``.
* a failed load is not remembered → ``test_a_failed_load_keeps_its_error``.
* the snapshot commit is not used (transformers 5 has no ``_commit_hash`` on
  the config; the first build after #2118 logged "Revision unbekannt") →
  ``test_transformers_5_logs_the_snapshot_commit``.
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


class _Tokenizer:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.decoded: list[dict[str, Any]] = []

    def __call__(self, text: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((text, kwargs))
        return {"input_ids": [[1, 2]], "attention_mask": [[1, 1]]}

    def decode(self, ids: Any, **kwargs: Any) -> str:
        self.decoded.append(kwargs)
        return f"EN<{ids}>"


class _Model:
    def __init__(self, commit: str | None) -> None:
        self.config = SimpleNamespace(_commit_hash=commit) if commit else SimpleNamespace()
        self.generate_kwargs: list[dict[str, Any]] = []
        self.evaluated = False

    def eval(self) -> _Model:
        self.evaluated = True
        return self

    def generate(self, **kwargs: Any) -> list[str]:
        self.generate_kwargs.append(kwargs)
        return ["ids"]


def _fake_transformers(
    monkeypatch: pytest.MonkeyPatch, model: _Model, tokenizer: _Tokenizer, seen: dict[str, Any]
) -> None:
    def _tok(name: str, **kwargs: Any) -> _Tokenizer:
        seen["tokenizer"] = (name, kwargs)
        return tokenizer

    def _mdl(name: str, **kwargs: Any) -> _Model:
        seen["model"] = (name, kwargs)
        return model

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=_tok),
            AutoModelForSeq2SeqLM=SimpleNamespace(from_pretrained=_mdl),
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=_NoGrad))
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "pipeline", None)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "load_failed", False)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "error", None)


class _NoGrad:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> None:
        return None


def test_the_load_logs_the_revision(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _fake_transformers(monkeypatch, _Model(COMMIT), _Tokenizer(), {})
    with caplog.at_level(logging.INFO):
        translator = build_feed._get_translation_pipeline()
    assert isinstance(translator, build_feed._MarianTranslator)
    assert f"Revision {COMMIT}" in caplog.text


def test_a_model_without_a_commit_logs_unknown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _fake_transformers(monkeypatch, _Model(None), _Tokenizer(), {})
    with caplog.at_level(logging.INFO):
        assert build_feed._get_translation_pipeline() is not None
    assert "Revision unbekannt" in caplog.text
    assert build_feed._TRANSLATION_STATE["load_failed"] is False


def test_the_model_is_pinned_to_a_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    # A later push to the model repo must not reach the build (CVE-2026-80047).
    seen: dict[str, Any] = {}
    model = _Model(COMMIT)
    _fake_transformers(monkeypatch, model, _Tokenizer(), seen)
    build_feed._get_translation_pipeline()
    for key in ("tokenizer", "model"):
        name, kwargs = seen[key]
        assert name == "Helsinki-NLP/opus-mt-de-en"
        assert kwargs == {"revision": build_feed._TRANSLATION_MODEL_REVISION}
    assert model.evaluated
    assert re.fullmatch(r"[0-9a-f]{40}", build_feed._TRANSLATION_MODEL_REVISION)


def test_the_translator_keeps_the_pipeline_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    tokenizer, model = _Tokenizer(), _Model(COMMIT)
    _fake_transformers(monkeypatch, model, tokenizer, {})
    translator = build_feed._get_translation_pipeline()
    assert translator("Kranarbeiten", max_length=64, truncation=True) == [{"translation_text": "EN<ids>"}]
    assert tokenizer.calls == [("Kranarbeiten", {"return_tensors": "pt", "truncation": True})]
    assert model.generate_kwargs == [
        {"input_ids": [[1, 2]], "attention_mask": [[1, 1]], "max_length": 64}
    ]
    assert tokenizer.decoded == [{"skip_special_tokens": True}]


def test_a_failed_load_keeps_its_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # transformers 5 with the old pipeline call: the load fails, the EN feed
    # falls back to German and the error must reach the Health check.
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace())
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "pipeline", None)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "load_failed", False)
    monkeypatch.setitem(build_feed._TRANSLATION_STATE, "error", None)
    assert build_feed._get_translation_pipeline() is None
    assert build_feed._TRANSLATION_STATE["load_failed"] is True
    assert "ImportError" in str(build_feed._TRANSLATION_STATE["error"])
    assert build_feed._get_translation_pipeline() is None  # no second attempt in the same build


def test_transformers_5_logs_the_snapshot_commit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    snapshot = f"/cache/hub/models--Helsinki-NLP--opus-mt-de-en/snapshots/{COMMIT}/config.json"
    seen: dict[str, Any] = {}

    def _cached_file(name: str, filename: str, **kwargs: Any) -> str:
        seen["cached_file"] = (name, filename, kwargs)
        return snapshot

    def _extract(path: str, _commit: Any) -> str:
        return path.split("/snapshots/")[1].split("/")[0]

    _fake_transformers(monkeypatch, _Model(None), _Tokenizer(), {})
    monkeypatch.setitem(sys.modules, "transformers.utils", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "transformers.utils.hub",
        SimpleNamespace(cached_file=_cached_file, extract_commit_hash=_extract),
    )
    with caplog.at_level(logging.INFO):
        translator = build_feed._get_translation_pipeline()
    assert translator.commit == COMMIT
    assert f"Revision {COMMIT}" in caplog.text
    assert seen["cached_file"] == (
        "Helsinki-NLP/opus-mt-de-en", "config.json", {"revision": build_feed._TRANSLATION_MODEL_REVISION}
    )
