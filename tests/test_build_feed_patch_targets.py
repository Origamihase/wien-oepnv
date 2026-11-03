"""Wächter: jeder Patch eines Feed-Build-Namens trifft die Module, die ihn lesen.

Seit Etappe 8 (700-Zeilen-Regel) liegt Code von ``src/build_feed.py`` in
``src/feed_build/*.py``; die Fassade exportiert die Namen nur weiter. Ein
Patch auf ``build_feed.X`` erreicht dann nur noch Code, der ``X`` aus den
Globals der Fassade liest. Ein Test, der den falschen Ort patcht, bleibt oft
grün und prüft still weniger (oder lädt das echte Übersetzungsmodell).

Regel (wie in Etappe 6 und 7): Ein Test patcht einen Namen in dem Modul, dessen
Code ihn als Global liest. Liest mehr als ein Modul den Namen, steht
``setattr_all``/``patch_all`` aus ``tests/_conftest/build_feed_bindings.py``.
Als Leser zählt jeder Global-Load, auch auf Modulebene (symtable, damit lokale
Namen nicht mitzählen).

Geprüft wird per AST über ``tests/**/*.py``:

1. jeder Namens-Patch auf ``src.build_feed`` oder ``src.feed_build.*``:
   ``monkeypatch.setattr`` (auch in der Punkt-Form ``"src.build_feed.X"``),
   ``setattr``, ``patch.object``, ``patch.multiple``, ``patch("...")`` (auch als
   Dekorator), ``monkeypatch.delattr`` und direkte Zuweisung, auch mit
   ``target=``/``name=``/``attribute=`` als Schlüsselwort. Ein Patch eines
   Feed-Build-Namens auf ein Ziel, das sich nicht auflösen lässt (etwa einen
   Parameter einer Hilfsfunktion), schlägt fehl; die Selbsttests am Ende prüfen
   die Formen;
   Aliase werden über Importe, ``part(...)``, ``owner(...)`` und die Re-Import-Helfer
   aufgelöst. Patches auf Attribute geteilter Objekte (``json.load``,
   ``feed_config.OUT_PATH``, ``setitem`` auf ``_TRANSLATION_STATE``) werden
   getrennt gezählt und sind erlaubt;
2. ein Patch auf Modul M besteht nur, wenn M den Namen bindet und die Leser
   genau {M} sind (oder niemand ihn als Global liest);
3. ein Lesezugriff ``alias.X`` nach einem Neubinden von X in einem anderen
   Modul in derselben Testfunktion, und jeder Lesezugriff auf
   ``PROVIDERS``/``DEFAULT_PROVIDERS``/``_PROVIDERS_INITIALIZED`` über die
   Fassade, wenn die Fassade sie nur weiterreicht;
4. ein Patch-Ziel aus ``src.feed_build``, das oben in einer Datei importiert
   ist, die zugleich ``src.build_feed`` neu importiert;
5. eine Plausibilitätsprüfung: mindestens 300 Patch-Stellen und 50 Namen
   (Namens- und Attribut-Patches zusammen).
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import symtable
from collections.abc import Iterator
from pathlib import Path

import src.build_feed as build_feed
from tests._conftest.build_feed_bindings import FACADE, PACKAGE, build_feed_modules, defined_names

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
FACADE_ALIASES = {"build_feed", "bf", "build_feed_mod", "build_feed_module"}
REBOUND_STATE = {"PROVIDERS", "DEFAULT_PROVIDERS", "_PROVIDERS_INITIALIZED"}
PATCH_FUNCS = {"setattr", "delattr", "object"}
MULTI_FUNCS = {"setattr_all", "patch_all"}
REIMPORT_MARKERS = (
    'sys.modules.pop("src.build_feed"',
    "sys.modules.pop('src.build_feed'",
    'del sys.modules["src.build_feed"]',
    "importlib.reload(",
    "fresh_build_feed(",
)


@dataclasses.dataclass(frozen=True)
class Patch:
    path: str
    line: int
    module: str
    name: str
    multi: bool
    function: str
    alias_is_top_import: bool

    def where(self) -> str:
        return f"{self.path}:{self.line}"


@dataclasses.dataclass(frozen=True)
class Read:
    path: str
    line: int
    module: str
    name: str
    function: str


@dataclasses.dataclass
class Census:
    patches: list[Patch] = dataclasses.field(default_factory=list)
    shared: list[str] = dataclasses.field(default_factory=list)
    reads: list[Read] = dataclasses.field(default_factory=list)
    reimport_files: set[str] = dataclasses.field(default_factory=set)
    unresolved: list[str] = dataclasses.field(default_factory=list)


# ------------------------------------------------------------------ modules


@functools.cache
def module_names() -> tuple[str, ...]:
    return tuple(module.__name__ for module in build_feed_modules(build_feed))


@functools.cache
def module_bindings() -> dict[str, frozenset[str]]:
    return {module.__name__: frozenset(vars(module)) for module in build_feed_modules(build_feed)}


@functools.cache
def module_definitions() -> dict[str, frozenset[str]]:
    return {module.__name__: defined_names(module) for module in build_feed_modules(build_feed)}


def _global_loads(source: str) -> set[str]:
    table = symtable.symtable(source, "<module>", "exec")
    found: set[str] = set()

    def walk(tab: symtable.SymbolTable, top: bool) -> None:
        for sym in tab.get_symbols():
            if not sym.is_referenced():
                continue
            if top or sym.is_global():
                found.add(sym.get_name())
            elif tab.get_type() == "class" and not sym.is_assigned():
                found.add(sym.get_name())
        for child in tab.get_children():
            walk(child, False)

    walk(table, True)
    return found


@functools.cache
def readers_by_module() -> dict[str, frozenset[str]]:
    """Modul -> Namen, die sein Code als Global liest (auch auf Modulebene)."""

    out: dict[str, frozenset[str]] = {}
    for module in build_feed_modules(build_feed):
        path = Path(module.__file__ or "")
        out[module.__name__] = frozenset(_global_loads(path.read_text(encoding="utf-8")))
    return out


def readers(name: str) -> set[str]:
    return {module for module, names in readers_by_module().items() if name in names}


def owner_name(name: str) -> str | None:
    for module, names in module_definitions().items():
        if name in names:
            return module
    return None


# ------------------------------------------------------------------ census


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


_KEYWORD_SLOTS = {
    # Funktionsname -> Schlüsselwörter der ersten beiden Positionsargumente
    "setattr": (("target",), ("name",)),
    "delattr": (("target",), ("name",)),
    "object": (("target",), ("attribute",)),
    "patch": (("target",), ()),
    "multiple": (("target",), ()),
}


def _call_args(node: ast.Call, name: str) -> list[ast.expr]:
    """Positionsargumente, ergänzt um ``target=``/``name=``/``attribute=``."""

    args = list(node.args)
    by_keyword = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    for index, slot in enumerate(_KEYWORD_SLOTS.get(name, ())):
        if len(args) > index:
            continue
        value = next((by_keyword[key] for key in slot if key in by_keyword), None)
        if value is None:
            break
        args.append(value)
    return args


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


class _FileScan:
    def __init__(self, path: Path, census: Census, text: str | None = None) -> None:
        self.path = path
        self.rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
        self.census = census
        self.text = path.read_text(encoding="utf-8") if text is None else text
        self.tree = ast.parse(self.text)
        self.aliases: dict[str, str] = {name: FACADE for name in FACADE_ALIASES}
        self.top_aliases: set[str] = set()
        self.reimports = any(marker in self.text for marker in REIMPORT_MARKERS)
        if self.reimports:
            census.reimport_files.add(self.rel)
        self._collect_aliases()

    def _collect_aliases(self) -> None:
        top = set(id(node) for node in self.tree.body)
        for node in ast.walk(self.tree):
            bound: list[tuple[str, str]] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == FACADE or alias.name.startswith(PACKAGE + "."):
                        if alias.asname:
                            bound.append((alias.asname, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module in ("src", PACKAGE):
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if full == FACADE or full.startswith(PACKAGE + "."):
                        bound.append((alias.asname or alias.name, full))
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                target = self.resolve(node.value)
                if target is not None:
                    bound.extend((t.id, target) for t in node.targets if isinstance(t, ast.Name))
            for local, module in bound:
                self.aliases[local] = module
                if id(node) in top and module != FACADE:
                    self.top_aliases.add(local)

    def _feed_build_name(self, attr: str) -> bool:
        """``attr`` ist ein Name, den ein Modul von ``src.build_feed`` selbst definiert."""

        return any(attr in names for names in module_definitions().values())

    def resolve(self, node: ast.AST) -> str | None:
        """Der Modulname, zu dem ``node`` auswertet, oder ``None``."""

        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted in module_names():
                return dotted
            if dotted is not None and dotted.endswith(".build_feed_module"):
                return FACADE
            return None
        if isinstance(node, ast.Subscript) and _dotted(node.value) == "sys.modules":
            key = _string(node.slice)
            return key if key in module_names() else None
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "part" and len(node.args) == 2:
                short = _string(node.args[1])
                return f"{PACKAGE}.{short}" if f"{PACKAGE}.{short}" in module_names() else None
            if name == "owner" and len(node.args) == 2:
                wanted = _string(node.args[1])
                return owner_name(wanted) if wanted else None
            if name == "import_module" and node.args:
                key = _string(node.args[0])
                return key if key in module_names() else None
            if "build_feed" in name and name not in MULTI_FUNCS:
                return FACADE
        return None

    def scan(self) -> None:
        for function, node in self._walk_with_function(self.tree, "<module>"):
            if isinstance(node, ast.Call):
                self._scan_call(node, function)
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        module = self.resolve(target.value)
                        if module is not None:
                            self._add(node.lineno, module, target.attr, False, function, target.value)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                module = self.resolve(node.value)
                if module is not None and not isinstance(node.value, ast.Call):
                    self.census.reads.append(Read(self.rel, node.lineno, module, node.attr, function))

    def _walk_with_function(self, node: ast.AST, function: str) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{function}.{child.name}" if function != "<module>" else child.name
            yield name, child
            yield from self._walk_with_function(child, name)

    def _add(self, line: int, module: str, name: str, multi: bool, function: str, alias: ast.AST | None) -> None:
        top = isinstance(alias, ast.Name) and alias.id in self.top_aliases
        self.census.patches.append(Patch(self.rel, line, module, name, multi, function, top))

    def _scan_call(self, node: ast.Call, function: str) -> None:
        name = _call_name(node)
        args = _call_args(node, name)
        if name == "multiple" and args:
            # patch.multiple(bf, X=...) / patch.multiple("src.build_feed", X=...)
            module = self.resolve(args[0]) or _string(args[0])
            if module in module_names():
                for keyword in node.keywords:
                    if keyword.arg is not None:
                        self._add(node.lineno, module, keyword.arg, False, function, args[0])
            return
        if name in MULTI_FUNCS:
            # setattr_all(monkeypatch, bf, "X", v) / patch_all(bf, "X", ...)
            strings = [s for s in (_string(a) for a in args) if s is not None]
            if strings:
                self._add(node.lineno, FACADE, strings[0], True, function, None)
            return
        # Ein String als erstes Argument ist die Punkt-Form
        # (``monkeypatch.setattr("src.build_feed.X", v)``) und läuft unten weiter.
        if name in PATCH_FUNCS and len(args) >= 2 and _string(args[0]) is None:
            module = self.resolve(args[0])
            attr = _string(args[1])
            if module is not None and attr is not None:
                self._add(node.lineno, module, attr, False, function, args[0])
                return
            inner = args[0]
            if attr is not None and isinstance(inner, ast.Attribute) and self.resolve(inner.value):
                self.census.shared.append(f"{self.rel}:{node.lineno} {_dotted(inner)}.{attr}")
            elif attr is not None and isinstance(inner, ast.Name) and self._feed_build_name(attr):
                # Ziel ist ein Parameter oder eine sonst unbekannte Variable:
                # der Wächter kann das Modul nicht bestimmen.
                self.census.unresolved.append(f"{self.rel}:{node.lineno} {inner.id}.{attr}")
            return
        if name == "setitem" and args and isinstance(args[0], ast.Attribute) and self.resolve(args[0].value):
            self.census.shared.append(f"{self.rel}:{node.lineno} {_dotted(args[0])}[...]")
            return
        dotted = _string(args[0]) if args else None
        if name in ("patch", "setattr", "delattr") and dotted is not None:
            for module in sorted(module_names(), key=len, reverse=True):
                if dotted.startswith(module + "."):
                    rest = dotted[len(module) + 1:]
                    if "." in rest:
                        self.census.shared.append(f"{self.rel}:{node.lineno} {dotted}")
                    else:
                        self._add(node.lineno, module, rest, False, function, None)
                    return


@functools.cache
def census() -> Census:
    result = Census()
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if "build_feed" not in text and "feed_build" not in text:
            continue
        _FileScan(path, result).scan()
    return result


# ------------------------------------------------------------------ tests


def patch_problems(patches: list[Patch]) -> list[str]:
    problems: list[str] = []
    for patch in patches:
        if patch.multi:
            if not any(patch.name in names for names in module_bindings().values()):
                problems.append(f"{patch.where()}: no module of {FACADE} binds {patch.name}")
            continue
        bound = module_bindings().get(patch.module, frozenset())
        who = readers(patch.name)
        if patch.name not in bound:
            home = owner_name(patch.name)
            hint = f"; it is defined in {home}" if home else ""
            problems.append(f"{patch.where()}: {patch.module} does not bind {patch.name}{hint}")
        elif who and who != {patch.module}:
            if len(who) > 1:
                hint = f"use setattr_all/patch_all (read in {sorted(who)})"
            else:
                hint = f"patch {next(iter(who))} (owner: {owner_name(patch.name)})"
            problems.append(f"{patch.where()}: patch of {patch.name} on {patch.module} misses its readers; {hint}")
    return problems


def test_every_patch_hits_the_modules_that_read_the_name() -> None:
    problems = patch_problems(census().patches)
    problems += [
        f"{entry}: the guard cannot resolve the patched module; patch through an alias of "
        f"{FACADE}/{PACKAGE} or use setattr_all/patch_all"
        for entry in census().unresolved
    ]
    assert not problems, "\n".join(problems)


def test_no_read_through_another_module_after_a_rebinding() -> None:
    problems: list[str] = []
    rebinds: dict[tuple[str, str], list[Patch]] = {}
    for patch in census().patches:
        if not patch.multi and patch.function != "<module>":
            rebinds.setdefault((patch.path, patch.function), []).append(patch)
    for read in census().reads:
        for patch in rebinds.get((read.path, read.function), ()):
            if patch.name == read.name and patch.module != read.module and read.line > patch.line:
                problems.append(
                    f"{read.path}:{read.line}: reads {read.name} through {read.module} after "
                    f"{patch.where()} rebound it in {patch.module}"
                )
        if (
            read.module == FACADE
            and read.name in REBOUND_STATE
            and read.name not in module_definitions()[FACADE]
        ):
            problems.append(
                f"{read.path}:{read.line}: reads {read.name} through the facade; read it from "
                f"{owner_name(read.name)} (tests rebind it there)"
            )
    assert not problems, "\n".join(problems)


def test_no_patch_on_a_part_imported_before_a_reimport() -> None:
    problems = [
        f"{patch.where()}: {patch.module} was imported at the top of a file that imports "
        f"{FACADE} afresh; resolve it with owner(bf, {patch.name!r}) after the re-import"
        for patch in census().patches
        if patch.path in census().reimport_files and patch.alias_is_top_import
    ]
    assert not problems, "\n".join(problems)


def test_the_census_sees_the_patches() -> None:
    # Namens-Patches und Attribut-Patches auf geteilten Objekten zusammen
    # (``feed_config.OUT_PATH``, ``json.load``): beide sind Patch-Ziele.
    patches = census().patches
    shared = census().shared
    names = {patch.name for patch in patches} | {entry.rsplit(".", 1)[-1] for entry in shared}
    assert len(patches) + len(shared) >= 300, (len(patches), len(shared))
    assert len(names) >= 50, sorted(names)
    assert len(patches) >= 250 and shared, "the census no longer sees the patches"


def test_the_readers_are_computed_from_the_code() -> None:
    # Stichproben: ein Leser in einem Teil, mehrere Leser, Lesen auf Modulebene.
    assert readers("format_datetime") == {f"{PACKAGE}.base"}
    assert readers("_mask_entities") == {f"{PACKAGE}.translate_run"}
    assert readers("_get_translation_pipeline") == {f"{PACKAGE}.translator", f"{PACKAGE}.translate_run"}
    assert {FACADE, f"{PACKAGE}.base"} <= readers("feed_config")
    assert FACADE not in readers("_placeholder_nonce")


# ------------------------------------------------------------- self-tests

MUTANTS = {
    "string_setattr": 'def test_m(monkeypatch):\n    monkeypatch.setattr("src.build_feed._mask_entities", f)\n',
    "string_delattr": 'def test_m(monkeypatch):\n    monkeypatch.delattr("src.build_feed._mask_entities")\n',
    "string_setattr_kw": (
        "def test_m(monkeypatch):\n"
        '    monkeypatch.setattr(target="src.build_feed._mask_entities", value=f)\n'
    ),
    "object_keywords": (
        "from unittest import mock\nimport src.build_feed as feedmod\n"
        "def test_m():\n"
        '    with mock.patch.object(target=feedmod, attribute="_mask_entities"):\n        pass\n'
    ),
    "patch_multiple": (
        "from unittest import mock\nfrom src import build_feed\n"
        "def test_m():\n    with mock.patch.multiple(build_feed, _mask_entities=f):\n        pass\n"
    ),
    "patch_multiple_string": (
        "from unittest import mock\n"
        "def test_m():\n"
        '    with mock.patch.multiple("src.build_feed", _mask_entities=f):\n        pass\n'
    ),
}


def _scan_source(source: str) -> Census:
    result = Census()
    _FileScan(TESTS / "test_zz_mutant.py", result, text=source).scan()
    return result


def test_the_guard_flags_facade_patches_in_every_form() -> None:
    # Jede Form patcht ``_mask_entities`` auf der Fassade; gelesen wird der
    # Name nur in ``translate_run``, also muss der Wächter jede melden.
    for label, source in MUTANTS.items():
        patches = _scan_source(source).patches
        assert [(p.module, p.name) for p in patches] == [(FACADE, "_mask_entities")], label
        assert patch_problems(patches), label


def test_the_guard_reports_unresolvable_patch_targets() -> None:
    source = 'def helper(mod, mp):\n    mp.setattr(mod, "_mask_entities", None)\n'
    result = _scan_source(source)
    assert result.unresolved == ["tests/test_zz_mutant.py:2 mod._mask_entities"]
    assert not result.patches
