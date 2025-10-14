import importlib
import logging

import src.providers.vor as vor


def test_access_id_env_normalization(monkeypatch):
    # VOR_ACCESS_ID mit Leerzeichen fällt auf den Dokumentations-Standard zurück
    monkeypatch.setenv("VOR_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == vor.DEFAULT_ACCESS_ID

    # Fallback auf VAO_ACCESS_ID, ebenfalls Leerzeichen -> Dokumentations-Standard
    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.setenv("VAO_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == vor.DEFAULT_ACCESS_ID

    # VAO_ACCESS_ID mit zusätzlichen Leerzeichen wird getrimmt
    monkeypatch.setenv("VAO_ACCESS_ID", " token ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == "token"

    # Aufräumen
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == vor.DEFAULT_ACCESS_ID


def test_invalid_int_env_uses_defaults(monkeypatch, caplog):
    monkeypatch.setenv("VOR_BOARD_DURATION_MIN", "foo")
    monkeypatch.setenv("VOR_HTTP_TIMEOUT", "bar")
    monkeypatch.setenv("VOR_MAX_STATIONS_PER_RUN", "baz")
    monkeypatch.setenv("VOR_ROTATION_INTERVAL_SEC", "qux")

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.BOARD_DURATION_MIN == 60
    assert vor.HTTP_TIMEOUT == 15
    assert vor.DEFAULT_MAX_STATIONS_PER_RUN == 2
    assert vor.MAX_STATIONS_PER_RUN == vor.DEFAULT_MAX_STATIONS_PER_RUN
    assert vor.ROTATION_INTERVAL_SEC == 1800

    for name in [
        "VOR_BOARD_DURATION_MIN",
        "VOR_HTTP_TIMEOUT",
        "VOR_MAX_STATIONS_PER_RUN",
        "VOR_ROTATION_INTERVAL_SEC",
    ]:
        assert any(name in r.getMessage() for r in caplog.records)

    for name in [
        "VOR_BOARD_DURATION_MIN",
        "VOR_HTTP_TIMEOUT",
        "VOR_MAX_STATIONS_PER_RUN",
        "VOR_ROTATION_INTERVAL_SEC",
    ]:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(vor)


def test_invalid_bus_regex_falls_back_to_defaults(monkeypatch, caplog):
    monkeypatch.setenv("VOR_BUS_INCLUDE_REGEX", "(")
    monkeypatch.setenv("VOR_BUS_EXCLUDE_REGEX", "(")

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.BUS_INCLUDE_RE.pattern == vor.DEFAULT_BUS_INCLUDE_PATTERN
    assert vor.BUS_EXCLUDE_RE.pattern == vor.DEFAULT_BUS_EXCLUDE_PATTERN

    assert any("VOR_BUS_INCLUDE_REGEX" in record.getMessage() for record in caplog.records)
    assert any("VOR_BUS_EXCLUDE_REGEX" in record.getMessage() for record in caplog.records)

    monkeypatch.delenv("VOR_BUS_INCLUDE_REGEX", raising=False)
    monkeypatch.delenv("VOR_BUS_EXCLUDE_REGEX", raising=False)
    importlib.reload(vor)


def test_station_ids_fallback_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)
    monkeypatch.delenv("VOR_STATION_NAMES", raising=False)

    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("  1001,1002\n1003  ", encoding="utf-8")
    monkeypatch.setenv("VOR_STATION_IDS_FILE", str(ids_file))

    importlib.reload(vor)

    assert vor.VOR_STATION_IDS == ["1001", "1002", "1003"]

    monkeypatch.delenv("VOR_STATION_IDS_FILE", raising=False)
    importlib.reload(vor)


def test_station_ids_fallback_from_directory(monkeypatch):
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)
    monkeypatch.delenv("VOR_STATION_NAMES", raising=False)
    monkeypatch.delenv("VOR_STATION_IDS_FILE", raising=False)

    importlib.reload(vor)

    assert {"900100", "900200", "900300"}.issubset(set(vor.VOR_STATION_IDS))


def test_base_url_prefers_secret(monkeypatch):
    monkeypatch.setenv("VOR_BASE", "https://example.com/base")
    monkeypatch.setenv("VOR_BASE_URL", "https://secret.example.com/base")

    importlib.reload(vor)

    assert vor.VOR_BASE_URL == "https://secret.example.com/base/"
    assert vor.VOR_VERSION == "v1.11.0"

    monkeypatch.delenv("VOR_BASE_URL", raising=False)
    importlib.reload(vor)
    assert (
        vor.VOR_BASE_URL
        == "https://example.com/base/v1.11.0/"
    )

    monkeypatch.delenv("VOR_BASE", raising=False)
    importlib.reload(vor)
    assert (
        vor.VOR_BASE_URL
        == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )

