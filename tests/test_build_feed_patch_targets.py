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
   ``target=``/``name=``/``attribute=`` als Schlüsselwort, dazu ``setitem`` und
   ``patch.dict`` auf ``__dict__``/``vars()`` eines Moduls. Lässt sich das Ziel
   nicht auflösen (ein Parameter einer Hilfsfunktion, ein Attribut wie
   ``run.outputs``, ein Aufruf wie ``part(bf, name)``, ein zusammengesetzter
   String wie ``f"src.build_feed.{name}"``), schlägt der Patch fehl, wenn ein
   Modul von ``src.build_feed`` den Namen bindet (Dunder-Namen ausgenommen);
   ebenso ein Patch, dessen Name in einer Variablen steht. Ausgenommen sind
   Ziele, die nachweislich außerhalb liegen: ein importiertes anderes Modul
   (``from src.providers import wl_fetch``, ``import scripts.x as y``) und
   Attribute geteilter Objekte, Funktionen und Objekte, die der Test selbst
   baut. Seit 2026-11-14 gilt das für jede Form; die Selbsttests am Ende
   prüfen die Formen. Ungeprüft bleibt ein Aufruf, bei dem weder Modul noch
   Name feststehen (die Schleife in ``setattr_all``); ``setattr_all`` und
   ``patch_all`` mit einem Teil statt der Fassade scheitern zur Laufzeit;
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

from tests._conftest.build_feed_bindings import FACADE, PACKAGE
from tests._conftest.patch_census import (
    REBOUND_STATE,
    TESTS,
    Census,
    Patch,
    module_bindings,
    module_definitions,
    owner_name,
    readers,
)
from tests._conftest.patch_scan import _FileScan, census


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
    "patch_multiple_target_keyword": (
        "from unittest import mock\n"
        "def test_m():\n    with mock.patch.multiple(target=bf, _mask_entities=f):\n        pass\n"
    ),
    "namespace_setitem": 'def test_m(monkeypatch):\n    monkeypatch.setitem(bf.__dict__, "_mask_entities", f)\n',
    "namespace_patch_dict": (
        "from unittest import mock\nimport src.build_feed as feedmod\n"
        "def test_m():\n"
        '    with mock.patch.dict(vars(feedmod), {"_mask_entities": f}):\n        pass\n'
    ),
}


def _scan_source(source: str) -> Census:
    result = Census()
    _FileScan(TESTS / "test_zz_mutant.py", result, text=source).scan()
    return result


def test_the_guard_reports_attribute_targets_and_variable_names() -> None:
    # Seit 2026-11-14 (Ausgaben von ``main`` in ``src.feed_build.outputs``): Beide
    # Formen blieben dem Wächter verborgen. ``mp.setattr(bf, name, v)`` mit
    # ``name = "_make_rss"`` traf danach nur noch die Fassade, nicht den Leser
    # in ``outputs``.
    source = (
        "import src.build_feed as bf\n"
        "def helper(run, mp, name):\n"
        '    mp.setattr(run.outputs, "_save_state", None)\n'
        '    mp.setattr(run.outputs, "atomic_write", None)\n'
        "    mp.setattr(bf, name, None)\n"
        '    mp.setattr(run.report, "add_warning", None)\n'
    )
    result = _scan_source(source)
    assert result.unresolved == [
        "tests/test_zz_mutant.py:3 run.outputs._save_state",
        "tests/test_zz_mutant.py:4 run.outputs.atomic_write",
        "tests/test_zz_mutant.py:5 bf.<name>",
    ]
    assert not result.patches


def test_the_guard_reports_every_form_it_cannot_resolve() -> None:
    # Seit 2026-11-14 für jede Form, die der Wächter kennt: ein Ziel, das sich
    # nicht als Modul auflösen lässt, und ein Name, der erst zur Laufzeit feststeht.
    source = (
        "import sys\n"
        "from unittest import mock\n"
        "import src.build_feed as bf\n"
        "from tests._conftest.build_feed_bindings import part\n"
        "def helper(run, mp, outputs, which, name):\n"
        '    mp.setattr(outputs, "atomic_write", None)\n'
        "    run.outputs._make_rss = None\n"
        "    mock.patch.multiple(run.outputs, _make_rss=None)\n"
        "    mock.patch.multiple(bf, **{name: None})\n"
        '    mp.setattr(part(bf, which), "_make_rss", None)\n'
        '    mp.setattr(sys.modules.get(name), "_make_rss", None)\n'
        '    mp.setattr(f"src.build_feed.{name}", None)\n'
        '    mock.patch("src.feed_build." + name)\n'
        "    mp.setitem(bf.__dict__, name, None)\n"
        '    mock.patch.dict(vars(run.outputs), {"_make_rss": None})\n'
    )
    result = _scan_source(source)
    assert [entry.split(" ", 1)[1] for entry in result.unresolved] == [
        "outputs.atomic_write",
        "run.outputs._make_rss",
        "run.outputs._make_rss",
        "bf.<**{name: None}>",
        "part(bf, which)._make_rss",
        "sys.modules.get(name)._make_rss",
        "f'src.build_feed.{name}'",
        "'src.feed_build.' + name",
        "bf.__dict__[name]",
        "vars(run.outputs)['_make_rss']",
    ]
    assert [int(entry.split(" ", 1)[0].rsplit(":", 1)[1]) for entry in result.unresolved] == list(range(6, 16))
    assert not result.patches


def test_the_guard_leaves_targets_outside_the_feed_build_alone() -> None:
    # Importierte andere Module, Dunder-Namen, Attribute geteilter Objekte und
    # Funktionen des Tests selbst sind keine Ziele in src.build_feed.
    source = (
        "import src.providers.wl_fetch\n"
        "from src.providers import wl_api\n"
        "import scripts.update_baustellen_cache as ubc\n"
        "import src.build_feed as bf\n"
        "def test_m(mp, stub):\n"
        '    mp.setattr(wl_api, "validate_http_url", None)\n'
        '    mp.setattr(src.providers.wl_fetch, "datetime", None)\n'
        '    mp.setattr(ubc, "configure_logging", None)\n'
        '    mp.setattr(stub.fetch, "__name__", "wl")\n'
        '    mp.setattr(bf.feed_config, "OUT_PATH", None)\n'
        "    def _fetch():\n"
        "        pass\n"
        '    _fetch._provider_concurrency_key = "group"\n'
    )
    result = _scan_source(source)
    assert result.unresolved == [] and result.patches == []
    assert result.shared == ["tests/test_zz_mutant.py:10 bf.feed_config.OUT_PATH"]


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
    # Ein Parameter mit dem Namen eines importierten Moduls kann alles halten.
    source = (
        "from src.providers import wl_fetch\n"
        'def helper(mp, wl_fetch):\n    mp.setattr(wl_fetch, "_make_rss", None)\n'
    )
    assert _scan_source(source).unresolved == ["tests/test_zz_mutant.py:3 wl_fetch._make_rss"]
