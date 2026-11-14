"""Wächter für ``tests/_conftest/build_feed_bindings.py`` und die Teile von build_feed.

``src/build_feed.py`` ist seit Etappe 8 zum Teil auf ``src/feed_build/*.py``
verteilt. Ein Test, der einen Namen ersetzt, den mehrere Module lesen, muss
jedes dieser Module treffen (``setattr_all``/``patch_all``); sonst prüft er
still weniger. Hier steht, dass die Hilfen das tun, auch nach einem frischen
Import, und dass die Teile die Struktur haben, auf der die Hilfen aufbauen.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.build_feed as build_feed
from tests._conftest.build_feed_bindings import (
    FACADE,
    PACKAGE,
    bindings,
    build_feed_modules,
    fresh_build_feed,
    owner,
    part,
    patch_all,
    setattr_all,
)
from tests._conftest.patch_census import readers
from tests._conftest.patch_scan import census

ROOT = Path(__file__).resolve().parents[1]
PARTS = sorted((ROOT / "src" / "feed_build").glob("*.py"))
# Namen, die mehrere Module lesen und die Tests deshalb mit setattr_all/patch_all ersetzen.
MULTI_READ = ("_get_translation_pipeline", "validate_path", "atomic_write", "feed_config", "_translate_text_attempt")


def _multi_patched() -> list[str]:
    return sorted({patch.name for patch in census().patches if patch.multi} | set(MULTI_READ))


@pytest.mark.parametrize("name", _multi_patched())
def test_bindings_cover_every_module_that_reads_the_name(name: str) -> None:
    who = readers(name)
    assert len(who) > 1, f"{name} has one reader; patch it there: {sorted(who)}"
    assert who <= {module.__name__ for module in bindings(name)}


def test_every_global_a_module_reads_is_bound() -> None:
    import builtins

    missing = [
        f"{module.__name__}: {name}"
        for module in build_feed_modules()
        for name in sorted(_reads_of(module))
        if name not in vars(module) and not hasattr(builtins, name)
    ]
    assert not missing, missing


def _reads_of(module: Any) -> set[str]:
    from tests._conftest.patch_census import readers_by_module

    return set(readers_by_module()[module.__name__])


@pytest.mark.parametrize("name", MULTI_READ)
def test_setattr_all_replaces_every_binding(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    replacement = object()
    setattr_all(monkeypatch, build_feed, name, replacement)
    assert all(getattr(module, name) is replacement for module in bindings(name))
    monkeypatch.undo()
    assert all(getattr(module, name) is not replacement for module in bindings(name))


@pytest.mark.parametrize("name", MULTI_READ)
def test_patch_all_replaces_and_restores_every_binding(name: str) -> None:
    with patch_all(build_feed, name) as replacement:
        assert all(getattr(module, name) is replacement for module in bindings(name))
    assert all(getattr(module, name) is not replacement for module in bindings(name))


def test_the_model_is_never_reached_through_setattr_all(monkeypatch: pytest.MonkeyPatch) -> None:
    # Beide Leser von _get_translation_pipeline sehen den Ersatz: der
    # Service-Kompositum-Zweig (translator) und die Übersetzung (translate_run).
    calls: list[str] = []

    def no_model() -> None:
        calls.append("asked")
        return None

    setattr_all(monkeypatch, build_feed, "_get_translation_pipeline", no_model)
    assert build_feed._translate_text("Betrieb ab Karlsplatz") == "Betrieb ab Karlsplatz"
    assert calls


@patch_all(None, "validate_path")
@patch_all(None, "atomic_write")
def test_stacked_decorators_pass_mocks_bottom_first(mock_atomic: MagicMock, mock_validate: MagicMock) -> None:
    translator = part(build_feed, "translator")
    assert vars(build_feed)["atomic_write"] is mock_atomic and translator.atomic_write is mock_atomic
    assert build_feed.validate_path is mock_validate and translator.validate_path is mock_validate


def test_the_helpers_take_the_facade_not_a_part(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``setattr_all(mp, part(bf, "outputs"), "_make_rss", f)`` ersetzte sonst nur
    # die Bindungen der Teile; die Fassade rendert mit ihrer den deutschen Feed.
    outputs = part(build_feed, "outputs")
    with pytest.raises(TypeError, match="expected the facade"):
        setattr_all(monkeypatch, outputs, "_make_rss", object())
    with pytest.raises(TypeError, match="expected the facade"):
        bindings("_make_rss", outputs)
    assert outputs._make_rss is build_feed._make_rss


def test_owner_and_part_name_the_defining_module() -> None:
    assert owner(build_feed, "_get_translation_pipeline").__name__ == f"{PACKAGE}.translator"
    assert owner(build_feed, "main").__name__ == FACADE
    assert part(build_feed, "base").__name__ == f"{PACKAGE}.base"
    with pytest.raises(LookupError):
        owner(build_feed, "no_such_name_anywhere")


def test_fresh_build_feed_brings_fresh_parts_and_the_helpers_follow_it(monkeypatch: pytest.MonkeyPatch) -> None:
    old_translator = part(build_feed, "translator")
    old_config = build_feed.feed_config
    fresh = fresh_build_feed(extra_pops=("src.feed", "src.feed.config"))
    assert fresh is not build_feed
    new_translator = part(fresh, "translator")
    assert new_translator is not old_translator
    assert new_translator is sys.modules[f"{PACKAGE}.translator"]
    # The parts import against the same fresh configuration as the facade.
    assert part(fresh, "base").feed_config is fresh.feed_config is sys.modules["src.feed.config"]
    assert fresh.feed_config is not old_config
    replacement = object()
    setattr_all(monkeypatch, fresh, "_get_translation_pipeline", replacement)
    assert new_translator._get_translation_pipeline is replacement
    assert old_translator._get_translation_pipeline is not replacement
    assert part(build_feed, "translator") is old_translator


def test_fresh_build_feed_imports_the_parts_under_the_guard() -> None:
    import builtins

    real_import = builtins.__import__
    seen: list[tuple[str, str]] = []

    def guard(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        seen.append(((globals or {}).get("__name__", ""), name))
        return real_import(name, globals, locals, fromlist, level)

    fresh_build_feed(import_guard=guard)
    importers = {importer for importer, _ in seen}
    assert FACADE in importers
    assert f"{PACKAGE}.translate_run" in importers
    assert builtins.__import__ is real_import


# ------------------------------------------------------------------ Struktur


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_every_relative_import_in_the_parts_resolves() -> None:
    problems = []
    for path in PARTS:
        package = PACKAGE
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and node.level:
                target = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                if importlib.util.find_spec(target) is None:
                    problems.append(f"{path.name}:{node.lineno} {target}")
    assert not problems, problems


def test_the_parts_import_no_cycle_not_even_for_type_checking() -> None:
    graph: dict[str, set[str]] = {}
    for path in PARTS:
        deps: set[str] = set()
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                deps.add(node.module.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 2 and node.module in (None, "feed_build"):
                deps.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 2 and node.module == "build_feed":
                deps.add("<facade>")
        graph[path.stem] = deps
    assert all("<facade>" not in deps for deps in graph.values()), graph
    done: set[str] = set()

    def visit(name: str, stack: tuple[str, ...]) -> None:
        assert name not in stack, " -> ".join((*stack, name))
        if name in done:
            return
        for dep in graph.get(name, ()):
            visit(dep, (*stack, name))
        done.add(name)

    for name in graph:
        visit(name, ())


def _module_level_calls(tree: ast.Module, name: str) -> list[int]:
    found: list[int] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == name:
                found.append(child.lineno)
            walk(child)

    walk(tree)
    return found


def test_only_placeholders_draws_the_placeholder_nonce() -> None:
    calls = {
        path.name: lines
        for path in [*PARTS, ROOT / "src" / "build_feed.py"]
        if (lines := _module_level_calls(_tree(path), "_placeholder_nonce"))
    }
    assert list(calls) == ["placeholders.py"] and len(calls["placeholders.py"]) == 1, calls
    assert build_feed._PLACEHOLDER_NONCE is part(build_feed, "placeholders")._PLACEHOLDER_NONCE


def test_shared_state_has_one_owner() -> None:
    translator = part(build_feed, "translator")
    assert build_feed._TRANSLATION_STATE is translator._TRANSLATION_STATE
    assert vars(build_feed)["log"] is part(build_feed, "base").log
    assert part(build_feed, "base").log.name == "build_feed"
    assert build_feed.main.__module__ == FACADE


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_every_part_stays_under_the_line_limit(path: Path) -> None:
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 650
