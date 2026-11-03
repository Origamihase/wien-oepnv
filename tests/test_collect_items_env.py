import sys
from pathlib import Path
import types
import pytest
from tests._conftest.build_feed_bindings import fresh_build_feed, part


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda timeout=None: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda timeout=None: [])
    vor = types.ModuleType("providers.vor")
    setattr(vor, "fetch_events", lambda timeout=None: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    return fresh_build_feed()


@pytest.mark.parametrize(
    "disabled_env,expected",
    [
        ("WL_ENABLE", [{"p": "oebb"}, {"p": "vor"}]),
        ("OEBB_ENABLE", [{"p": "wl"}, {"p": "vor"}]),
        ("VOR_ENABLE", [{"p": "wl"}, {"p": "oebb"}]),
    ],
)
def test_disabling_provider_suppresses_items(
    monkeypatch: pytest.MonkeyPatch,
    disabled_env: str,
    expected: list[dict[str, str]],
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        part(build_feed, "providers_setup"),
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda timeout=None: [{"p": "wl"}]),
            ("OEBB_ENABLE", lambda timeout=None: [{"p": "oebb"}]),
            ("VOR_ENABLE", lambda timeout=None: [{"p": "vor"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "1")
    monkeypatch.setenv("OEBB_ENABLE", "1")
    monkeypatch.setenv("VOR_ENABLE", "1")
    monkeypatch.setenv(disabled_env, "0")

    items = build_feed._collect_items()
    assert items == expected


@pytest.mark.parametrize(
    "value",
    ["0", " 0 ", "false", " False ", "FALSE", "\t0\n", "\nfalse\t"],
)
def test_env_disabling_ignores_whitespace_and_case(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        part(build_feed, "providers_setup"),
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda timeout=None: [{"p": "wl"}]),
            ("OEBB_ENABLE", lambda timeout=None: [{"p": "oebb"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", value)
    monkeypatch.setenv("OEBB_ENABLE", "1")

    items = build_feed._collect_items()
    assert items == [{"p": "oebb"}]


def test_enabling_vor_yields_items(monkeypatch: pytest.MonkeyPatch) -> None:
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        part(build_feed, "providers_setup"),
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda timeout=None: []),
            ("OEBB_ENABLE", lambda timeout=None: []),
            ("VOR_ENABLE", lambda timeout=None: [{"p": "vor"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "0")
    monkeypatch.setenv("OEBB_ENABLE", "0")
    monkeypatch.setenv("VOR_ENABLE", "1")

    items = build_feed._collect_items()
    assert items == [{"p": "vor"}]
