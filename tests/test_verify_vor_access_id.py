from __future__ import annotations

from typing import Dict

import pytest

from scripts.verify_vor_access_id import ensure_required_secrets


@pytest.fixture()
def fresh_env() -> Dict[str, str]:
    return {}


def test_successful_check_prints_masked_token(capsys, fresh_env):
    fresh_env["VOR_ACCESS_ID"] = "token-value"

    success, loaded = ensure_required_secrets(environ=fresh_env, auto_load=False)

    assert success is True
    assert loaded == {}

    out = capsys.readouterr().out
    assert "Alle benötigten Secrets sind gesetzt" in out
    assert "token-value" not in out
    assert "to***ue" in out


def test_missing_secret_sets_exit_code_and_message(capsys, fresh_env):
    success, loaded = ensure_required_secrets(environ=fresh_env, auto_load=False)

    assert success is False
    assert loaded == {}

    err = capsys.readouterr().err
    assert "Fehlende Secrets" in err
    assert "VOR_ACCESS_ID" in err


def test_auto_load_invoked(monkeypatch, capsys, fresh_env):
    calls: list[Dict[str, str]] = []

    def fake_loader(*, environ):
        calls.append(dict(environ))
        environ.setdefault("VOR_ACCESS_ID", " from-file ")
        return {"dummy": {"VOR_ACCESS_ID": "from-file"}}

    monkeypatch.setattr(
        "scripts.verify_vor_access_id.load_default_env_files",
        fake_loader,
    )

    success, loaded = ensure_required_secrets(environ=fresh_env, auto_load=True)

    assert success is True
    assert loaded == {"dummy": {"VOR_ACCESS_ID": "from-file"}}
    assert calls
    assert fresh_env["VOR_ACCESS_ID"] == " from-file "

    out = capsys.readouterr().out
    assert "fr***le" in out
