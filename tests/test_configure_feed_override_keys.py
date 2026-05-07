"""Verify that ``configure_feed.py --set`` rejects malformed keys.

The wizard interpolates the key raw into ``f"{key}={_escape_env_value(value)}"``
when rendering the ``.env`` document, while the env-file reader strictly
validates keys against ``^(?:export\\s+)?([A-Za-z_][A-Za-z0-9_]*)\\s*$``. A
``--set $'EVIL\\nINJECT=val=foo'`` invocation would therefore embed a literal
newline into the file, splitting the run into two assignments on the next
read — turning a writer/reader asymmetry into a config-injection primitive.
The tests here pin the new validation that rejects those inputs at the
``--set`` boundary so the writer can never produce a document the reader
couldn't safely ingest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "configure_feed.py"


def _load_configure_feed_module() -> ModuleType:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "configure_feed_under_test_override_keys", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "argument",
    [
        "OUT_PATH=docs/feed.xml",
        "FEED_TITLE=Wien",
        "MAX_ITEMS=10",
        "_PRIVATE=value",
        "ALL_CAPS_AND_DIGITS_42=ok",
    ],
)
def test_parse_overrides_accepts_valid_keys(argument: str) -> None:
    module = _load_configure_feed_module()
    overrides = module._parse_overrides([argument])
    expected_key = argument.split("=", 1)[0]
    assert expected_key in overrides


@pytest.mark.parametrize(
    "argument",
    [
        # Embedded newline — the actual injection vector.
        "EVIL\nINJECT=val",
        # Carriage return variant.
        "EVIL\rINJECT=val",
        # Tab — control char that some shells preserve.
        "EVIL\tINJECT=val",
        # Spaces — would also break the reader's grammar.
        "EVIL INJECT=val",
        # Hyphen — disallowed by the env-file regex.
        "EVIL-INJECT=val",
        # Leading digit — invalid identifier.
        "1EVIL=val",
        # Empty key after stripping — also invalid (caught by separate check).
        # Lowercase letters are intentionally allowed; the env-file parser
        # accepts ``[A-Za-z_]`` (case-insensitive). We only reject control
        # chars, separators, leading digits, etc.
        # Newline inside an otherwise-valid key.
        "OK\nKEY=val",
        # Vertical tab — still a whitespace control char.
        "EVIL\vINJECT=val",
    ],
)
def test_parse_overrides_rejects_malformed_keys(argument: str) -> None:
    module = _load_configure_feed_module()
    with pytest.raises(SystemExit) as excinfo:
        module._parse_overrides([argument])
    # The error message must name the key (so operators can fix the typo)
    # but our exit-code path is the standard SystemExit.
    assert "Ungültiger Schlüssel" in str(excinfo.value) or "Ungültig" in str(
        excinfo.value
    )


def test_parse_overrides_still_rejects_no_equals_and_empty_key() -> None:
    """Existing failure modes must keep failing."""
    module = _load_configure_feed_module()
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        module._parse_overrides(["NO_EQUALS_HERE"])
    with pytest.raises(SystemExit, match="darf nicht leer"):
        module._parse_overrides(["=value"])
