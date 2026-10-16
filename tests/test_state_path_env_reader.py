"""``STATE_PATH`` has one meaning: the feed's first-seen *file*.

Operator decision 2026-10-10: every purpose gets its own variable, no name has
two meanings. The guard lived in ``tests/places/test_quota.py`` until the
Google Places quota was removed on 2026-10-16; the class it guards is not
about Google, so it stays.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_state_path_env_is_read_only_by_feed_config() -> None:
    """Guard the class: no module besides the feed config reads ``STATE_PATH``."""
    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"""(?:getenv|\.get|environ\[|resolve_env_path)\(?\s*["']STATE_PATH["']""")
    readers = sorted(
        str(path.relative_to(root))
        for folder in ("src", "scripts")
        for path in (root / folder).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )
    assert readers == ["src/feed/config.py"]
