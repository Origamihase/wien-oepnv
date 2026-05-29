#!/usr/bin/env python3
"""Generate a minimal provider plugin skeleton for the feed builder."""

from __future__ import annotations

import argparse
from pathlib import Path
import textwrap

TEMPLATE = textwrap.dedent(
    '''from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

Item = dict[str, object]


def register_providers(register_provider: Callable) -> None:
    """Register the custom provider with the feed pipeline."""

    def load_custom_events() -> Iterable[Item]:
        """Return iterable of events ready for the feed pipeline."""

        # Example implementation:
        now = datetime.now(timezone.utc)
        return [
            {
                "id": "custom-1",
                "title": "Example Event",
                "link": "https://example.com/event",
                "pubDate": now,
                "description": "This is a sample event from the custom provider.",
                "guid": "custom-1",
            }
        ]

    register_provider("CUSTOM_PROVIDER_ENABLE", load_custom_events, cache_key="custom")


# Alternatively expose a PROVIDERS constant instead of register_providers:
# PROVIDERS = [
#     ("CUSTOM_PROVIDER_ENABLE", load_custom_events, "custom"),
# ]
'''
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="Path to the plugin module to create")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the target file if it already exists",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    if target.exists() and not args.overwrite:
        parser.error(f"{target} exists – use --overwrite to replace it.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.strip() + "\n", encoding="utf-8")
    print(f"Provider plugin scaffold written to {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
