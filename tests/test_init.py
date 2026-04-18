import src

def test_init_version():
    assert hasattr(src, "__version__")
    assert src.__version__ == "0.1.0"

def test_init_exports_main():
    assert hasattr(src, "main")
    from src.build_feed import main as build_feed_main
    # Using == for function names to avoid proxy object/import issues with 'is'
    assert src.main.__name__ == build_feed_main.__name__
