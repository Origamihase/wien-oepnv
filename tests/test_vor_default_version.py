import importlib
import os
import socket


def _restore_env(original: dict[str, str | None]) -> None:
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_default_vor_version():
    module = importlib.import_module("src.providers.vor")
    original = {key: os.environ.get(key) for key in ("VOR_BASE_URL", "VOR_BASE", "VOR_VERSION")}
    try:
        for key in original:
            os.environ.pop(key, None)
        reloaded = importlib.reload(module)
        assert reloaded.VOR_VERSION == "v1.11.0"
        assert reloaded.VOR_BASE_URL == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    finally:
        _restore_env(original)
        importlib.reload(module)


def test_base_url_infers_version(monkeypatch):
    # Mock DNS resolution to ensure example.test is accepted
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ],
    )

    module = importlib.import_module("src.providers.vor")
    original = {key: os.environ.get(key) for key in ("VOR_BASE_URL", "VOR_BASE", "VOR_VERSION")}
    try:
        for key in original:
            os.environ.pop(key, None)
        os.environ["VOR_BASE_URL"] = "https://example.test/vao/restproxy/v9.9.9/"
        reloaded = importlib.reload(module)
        assert reloaded.VOR_BASE_URL == "https://example.test/vao/restproxy/v9.9.9/"
        assert reloaded.VOR_VERSION == "v9.9.9"
    finally:
        _restore_env(original)
        importlib.reload(module)
