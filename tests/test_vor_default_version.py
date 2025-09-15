import importlib
import os


def test_default_vor_version():
    module = importlib.import_module("src.providers.vor")
    original_env = os.environ.pop("VOR_VERSION", None)
    try:
        reloaded = importlib.reload(module)
        assert reloaded.VOR_VERSION == "v1.11.0"
    finally:
        if original_env is not None:
            os.environ["VOR_VERSION"] = original_env
        else:
            os.environ.pop("VOR_VERSION", None)
        importlib.reload(module)
