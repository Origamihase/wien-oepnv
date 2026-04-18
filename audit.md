# Audit of load_provider_plugins call sites

The following files and line numbers contain references or calls to `load_provider_plugins`:

- `src/feed/providers.py`
  - Line 178: `def load_provider_plugins(*, force: bool = False) -> List[str]:` (Definition)
  - Line 238: `"load_provider_plugins",` (Export in `__all__`)

- `src/build_feed.py`
  - Line 43: `load_provider_plugins,` (Import)
  - Line 63: `load_provider_plugins,` (Import fallback)
  - Line 110: `load_provider_plugins(force=True)` (Call inside `refresh_from_env()`)

- `tests/test_provider_plugins.py`
  - Line 23: `def test_load_provider_plugins_not_called_on_import():` (Test definition)
  - Line 24: `# To properly test that importing doesn't call load_provider_plugins,` (Comment)
  - Line 41: `# Further ensure that load_provider_plugins() call is truly gone from the file text` (Comment)
  - Line 45: `# The string 'load_provider_plugins()' should not appear as a top-level call.` (Comment)
  - Line 51: `if line.startswith("load_provider_plugins()"):` (Test logic)
  - Line 52: `assert False, "Found top-level call to load_provider_plugins()"` (Test assertion message)
  - Line 55: `def test_load_provider_plugins_via_callable(monkeypatch):` (Test definition)
  - Line 72: `loaded = provider_mod.load_provider_plugins(force=True)` (Call in test)
