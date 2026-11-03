"""Parts of ``src/build_feed.py``, split by the 700-line rule (Etappe 8).

``src/build_feed.py`` stays the entry point and the facade: it keeps the
pipeline (``lint``, ``main``, ``_assemble_feed_items``) and re-exports every
name that src/, scripts/ or the tests read. Import from ``src.build_feed``.

Patch rule: a test replaces a name in the module whose code reads it as a
global. A name read in several modules is replaced in each of them with
``setattr_all``/``patch_all`` from ``tests/_conftest/build_feed_bindings.py``;
``tests/test_build_feed_patch_targets.py`` checks this. The package has no
imports, so importing it has no side effects.
"""
