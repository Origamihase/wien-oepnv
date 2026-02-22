from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module() -> object:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "scaffold_provider_plugin.py"
    spec = importlib.util.spec_from_file_location("scaffold_provider_plugin", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[assignment]
    return module


def test_scaffold_provider_plugin_writes_template(tmp_path: Path) -> None:
    target = tmp_path / "plugins" / "custom.py"

    from typing import cast, Any
    module = _load_module()
    exit_code = cast(Any, module).main([str(target)])

    assert exit_code == 0
    content = target.read_text(encoding="utf-8")
    assert "register_providers" in content
    assert "CUSTOM_PROVIDER_ENABLE" in content
