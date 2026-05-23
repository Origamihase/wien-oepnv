import src

def test_init_version() -> None:
    assert hasattr(src, "__version__")
    assert src.__version__ == "0.1.0"

def test_init_exports_main() -> None:
    assert hasattr(src, "main")
    # Identity comparison breaks when sibling tests reload ``src.build_feed`` via
    # ``sys.modules.pop`` (see ``_import_build_feed_without_providers``); the
    # structural check below survives the reload and is still stronger than the
    # original ``__name__``-only comparison.
    assert callable(src.main)
    assert src.main.__module__ == "src.build_feed"
    assert src.main.__qualname__ == "main"

def test_feed_health_path_in_all() -> None:
    import src.feed.config as config
    assert "FEED_HEALTH_PATH" in config.__all__
