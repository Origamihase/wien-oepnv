"""Die Erfassung der Patches einer Testdatei für den Wächter ``tests/test_build_feed_patch_targets.py``.

``_FileScan`` liest eine Datei per AST: Aliase der Fassade und ihrer Teile,
Namens-Patches in jeder Form, Lesezugriffe und Ziele, die sich nicht als
Modul auflösen lassen. ``census()`` sammelt das über ``tests/**/*.py``. Seit
2026-11-14 eine eigene Datei, aus dem Wächter geteilt (Regel und Tests stehen
dort, das Wissen über die Module in ``tests/_conftest/patch_census.py``).
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterator
from pathlib import Path

from tests._conftest.build_feed_bindings import FACADE, PACKAGE
from tests._conftest.patch_census import (
    FACADE_ALIASES,
    MODULE_LOADERS,
    MULTI_FUNCS,
    MULTIPLE_OPTIONS,
    PATCH_FUNCS,
    REIMPORT_MARKERS,
    ROOT,
    TESTS,
    Census,
    Patch,
    Read,
    _call_args,
    _call_name,
    _dotted,
    _feed_path,
    _namespace_of,
    _string,
    _string_prefix,
    module_bindings,
    module_names,
    owner_name,
)

# Der Wächter selbst (seine Selbsttests halten Patch-Formen als Strings, seine
# Konstanten die Marker eines Neuimports): keine Testdatei, die patcht.
GUARD_FILES = frozenset(
    (TESTS / "test_build_feed_patch_targets.py", TESTS / "_conftest" / "patch_census.py", Path(__file__).resolve())
)


class _FileScan:
    def __init__(self, path: Path, census: Census, text: str | None = None) -> None:
        self.path = path
        self.rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
        self.census = census
        self.text = path.read_text(encoding="utf-8") if text is None else text
        self.tree = ast.parse(self.text)
        self.aliases: dict[str, str] = {name: FACADE for name in FACADE_ALIASES}
        self.top_aliases: set[str] = set()
        # Namen, die ein Import an ein Modul außerhalb von src.build_feed bindet,
        # und Namen von Funktionen, Klassen und Objekten, die der Test selbst baut.
        self.outside: set[str] = set()
        self.objects: set[str] = set()
        # Parameter und Schleifenvariablen: was sie halten, steht erst zur Laufzeit fest.
        self.variables: set[str] = set()
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
                    if _feed_path(alias.name):
                        if alias.asname:
                            bound.append((alias.asname, alias.name))
                    elif alias.asname:
                        self._outside(alias.asname, alias.name)
                    else:
                        # ``import src.providers.wl_fetch`` bindet ``src``; das löst ``_origin`` auf.
                        self._outside(alias.name.split(".")[0], alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if node.level == 0 and node.module in ("src", PACKAGE) and _feed_path(full):
                        bound.append((alias.asname or alias.name, full))
                    elif node.level == 0 and node.module is not None:
                        self._outside(alias.asname or alias.name, node.module)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Eine Funktion oder Klasse des Tests ist kein Modul (``_fetch.attr = ...``).
                self.objects.add(node.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                target = self.resolve(node.value)
                if target is not None:
                    bound.extend((t.id, target) for t in node.targets if isinstance(t, ast.Name))
                elif self._origin(node.value) == "outside":
                    self.objects.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.arg):
                self.variables.add(node.arg)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                self.variables.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
            for local, module in bound:
                self.aliases[local] = module
                if id(node) in top and module != FACADE:
                    self.top_aliases.add(local)

    def _outside(self, local: str, module: str) -> None:
        """``local`` hält ein Modul (oder ein Objekt daraus) außerhalb von src.build_feed.

        Module der Tests (``tests.*``, relative Importe) zählen nicht: ein
        Hilfsmodul kann einen Teil von src.feed_build weiterreichen.
        """

        if module != "src" and module.split(".")[0] != "tests" and not _feed_path(module):
            self.outside.add(local)

    def _feed_build_binding(self, attr: str) -> bool:
        """``attr`` ist ein Name, den ein Modul von ``src.build_feed`` bindet (definiert oder importiert).

        Dunder-Namen (``__name__``, ``__doc__``) bindet jedes Modul; sie zählen nicht.
        """

        if attr.startswith("__") and attr.endswith("__"):
            return False
        return any(attr in names for names in module_bindings().values())

    def _origin(self, node: ast.AST) -> str:
        """``feed`` (ein Modul von src.build_feed), ``shared`` (ein Objekt, das so ein
        Modul hält), ``outside`` (nachweislich etwas anderes) oder ``unknown``."""

        if self.resolve(node) is not None:
            return "feed"
        if isinstance(node, ast.Attribute):
            if self.resolve(node.value) in module_names():
                return "shared"
            dotted = _dotted(node)
            if dotted is None:
                # Eine Kette über einen Aufruf: ``SomeClass().x`` liegt außerhalb.
                base: ast.AST = node.value
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Call) and _call_name(base) not in MODULE_LOADERS:
                    return "outside"
                return "unknown"
            head, _, rest = dotted.partition(".")
            full = f"{self.aliases.get(head, head)}.{rest}"
            if any(full.startswith(module + ".") for module in module_names()):
                return "shared"
            if head in self.aliases or _feed_path(full):
                return "unknown"
            known = head == "src" or head in self.outside or head in self.objects
            return "outside" if known and head not in self.variables else "unknown"
        if isinstance(node, ast.Name):
            known = node.id in self.outside or node.id in self.objects
            return "outside" if known and node.id not in self.variables else "unknown"
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name not in MODULE_LOADERS and "build_feed" not in name and "feed_build" not in name:
                return "outside"
            key = _string(node.args[0]) if node.args else None
            if name in ("import_module", "get") and key is not None and not _feed_path(key):
                return "outside"
            return "unknown"
        if isinstance(node, ast.Subscript) and _dotted(node.value) == "sys.modules":
            key = _string(node.slice)
            return "outside" if key is not None and not _feed_path(key) else "unknown"
        return "unknown"

    def _unresolved(self, line: int, text: str) -> None:
        self.census.unresolved.append(f"{self.rel}:{line} {text}")

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
            head, _, rest = (dotted or "").partition(".")
            expanded = f"{self.aliases[head]}.{rest}" if head in self.aliases else None
            return expanded if expanded in module_names() else None
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
                        elif self._origin(target.value) == "unknown" and self._feed_build_binding(target.attr):
                            self._unresolved(node.lineno, f"{ast.unparse(target.value)}.{target.attr}")
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
            self._scan_multiple(node, args, function)
            return
        if name in MULTI_FUNCS:
            # setattr_all(monkeypatch, bf, "X", v) / patch_all(bf, "X", ...); ein Teil
            # statt der Fassade scheitert zur Laufzeit (``build_feed_bindings._facade``).
            strings = [s for s in (_string(a) for a in args) if s is not None]
            if strings:
                self._add(node.lineno, FACADE, strings[0], True, function, None)
            return
        if name in ("setitem", "delitem", "dict") and args and _namespace_of(args[0]) is not None:
            self._scan_namespace(node, name, args, function)
            return
        # Ein String als erstes Argument ist die Punkt-Form
        # (``monkeypatch.setattr("src.build_feed.X", v)``) und läuft unten weiter.
        if name in PATCH_FUNCS and len(args) >= 2 and _string(args[0]) is None and _string_prefix(args[0]) is None:
            self._scan_object_patch(node, args, function)
            return
        if name == "setitem" and args and isinstance(args[0], ast.Attribute) and self.resolve(args[0].value):
            self.census.shared.append(f"{self.rel}:{node.lineno} {_dotted(args[0])}[...]")
            return
        if name not in ("patch", "setattr", "delattr") or not args:
            return
        dotted = _string(args[0])
        if dotted is None:
            prefix = _string_prefix(args[0])
            if prefix is not None and (_feed_path(prefix.rstrip(".")) or FACADE.startswith(prefix)):
                # ``f"src.build_feed.{name}"``: Modul und Name stehen erst zur Laufzeit fest.
                self._unresolved(node.lineno, ast.unparse(args[0]))
            return
        for module in sorted(module_names(), key=len, reverse=True):
            if dotted.startswith(module + "."):
                rest = dotted[len(module) + 1:]
                if "." in rest:
                    self.census.shared.append(f"{self.rel}:{node.lineno} {dotted}")
                else:
                    self._add(node.lineno, module, rest, False, function, None)
                return

    def _scan_object_patch(self, node: ast.Call, args: list[ast.expr], function: str) -> None:
        """``setattr``/``delattr``/``patch.object`` auf ein Objekt (nicht die Punkt-Form)."""

        target, attr = args[0], _string(args[1])
        label = attr if attr is not None else f"<{ast.unparse(args[1])}>"
        module = self.resolve(target)
        if module is not None and attr is not None:
            self._add(node.lineno, module, attr, False, function, target)
            return
        origin = "feed" if module is not None else self._origin(target)
        if origin == "shared" and attr is not None:
            self.census.shared.append(f"{self.rel}:{node.lineno} {ast.unparse(target)}.{attr}")
        elif origin == "feed" or (origin == "unknown" and attr is not None and self._feed_build_binding(attr)):
            # Ein Name in einer Variablen (``setattr(bf, name, v)``) oder ein Ziel, dessen
            # Modul der Wächter nicht bestimmen kann (``run.outputs``, ein Parameter).
            self._unresolved(node.lineno, f"{ast.unparse(target)}.{label}")

    def _scan_multiple(self, node: ast.Call, args: list[ast.expr], function: str) -> None:
        """``patch.multiple(bf, X=...)`` / ``patch.multiple("src.build_feed", X=...)``."""

        target = args[0]
        text = _string(target)
        module = self.resolve(target) or (text if text in module_names() else None)
        names = [kw.arg for kw in node.keywords if kw.arg is not None and kw.arg not in MULTIPLE_OPTIONS]
        spread = [kw.value for kw in node.keywords if kw.arg is None]
        if module is not None:
            for keyword in names:
                self._add(node.lineno, module, keyword, False, function, target)
        if text is not None:
            origin = "feed" if module is not None else ("unknown" if _feed_path(text) else "outside")
        elif _string_prefix(target) is not None:
            origin = "unknown" if _feed_path((_string_prefix(target) or "").rstrip(".")) else "outside"
        else:
            origin = "feed" if module is not None else self._origin(target)
        if origin == "unknown":
            names = [keyword for keyword in names if self._feed_build_binding(keyword)]
            for keyword in names:
                self._unresolved(node.lineno, f"{ast.unparse(target)}.{keyword}")
        if origin in ("feed", "unknown"):
            for value in spread:
                self._unresolved(node.lineno, f"{ast.unparse(target)}.<**{ast.unparse(value)}>")

    def _scan_namespace(self, node: ast.Call, name: str, args: list[ast.expr], function: str) -> None:
        """``setitem``/``delitem``/``patch.dict`` auf ``X.__dict__`` oder ``vars(X)``."""

        namespace = _namespace_of(args[0])
        assert namespace is not None
        module = self.resolve(namespace)
        origin = "feed" if module is not None else self._origin(namespace)
        if origin not in ("feed", "unknown"):
            return
        keys: list[ast.expr] = []
        if name == "dict":
            values = args[1] if len(args) > 1 else None
            if isinstance(values, ast.Dict):
                keys = [key for key in values.keys if key is not None]
                if any(key is None for key in values.keys):
                    self._unresolved(node.lineno, f"{ast.unparse(args[0])}[<**>]")
            elif values is not None:
                self._unresolved(node.lineno, f"{ast.unparse(args[0])}[<{ast.unparse(values)}>]")
            names = [kw.arg for kw in node.keywords if kw.arg not in (None, "in_dict", "values", "clear")]
            keys += [ast.Constant(value=keyword) for keyword in names]
        elif len(args) > 1:
            keys = [args[1]]
        for key in keys:
            text = _string(key)
            if module is not None and text is not None:
                self._add(node.lineno, module, text, False, function, namespace)
            elif text is None or self._feed_build_binding(text):
                self._unresolved(node.lineno, f"{ast.unparse(args[0])}[{ast.unparse(key)}]")


@functools.cache
def census() -> Census:
    result = Census()
    for path in sorted(TESTS.rglob("*.py")):
        if path.resolve() in GUARD_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if "build_feed" not in text and "feed_build" not in text:
            continue
        _FileScan(path, result).scan()
    return result
