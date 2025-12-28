"""Utilities shared by the configuration wizard and its tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping
import re

from ..config.defaults import (
    DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS,
    DEFAULT_ENDS_AT_GRACE_MINUTES,
    DEFAULT_FEED_DESCRIPTION,
    DEFAULT_FEED_LINK,
    DEFAULT_FEED_TITLE,
    DEFAULT_FEED_TTL_MINUTES,
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_ITEM_AGE_DAYS,
    DEFAULT_OUT_PATH,
    DEFAULT_PROVIDER_FLAGS,
    DEFAULT_PROVIDER_MAX_WORKERS,
    DEFAULT_PROVIDER_TIMEOUT,
    DEFAULT_STATE_RETENTION_DAYS,
)

__all__ = [
    "ConfigOption",
    "CONFIG_OPTIONS",
    "ConfigurationError",
    "normalize_existing_values",
    "compute_non_interactive_configuration",
    "format_env_document",
    "mask_value",
    "merge_custom_entries",
    "calculate_changes",
]

_TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


class ConfigurationError(ValueError):
    """Raised when provided configuration data cannot be processed."""


@dataclass(frozen=True)
class ConfigOption:
    key: str
    label: str
    help: str
    kind: str = "string"
    default: object | None = None
    required: bool = False
    min_value: int | None = None

    def default_str(self) -> str:
        value = self.default
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, Path):
            return value.as_posix()
        return str(value)

    def normalize(self, raw: str) -> str:
        text = raw.strip()
        if not text:
            if self.required:
                raise ConfigurationError(f"{self.key} erfordert einen Wert.")
            return ""
        if self.kind == "int":
            try:
                value = int(text)
            except ValueError as exc:  # pragma: no cover - defensive
                raise ConfigurationError(f"{self.key} muss eine ganze Zahl sein: {exc}") from exc
            if self.min_value is not None and value < self.min_value:
                raise ConfigurationError(
                    f"{self.key} muss >= {self.min_value} sein (erhalten: {value})."
                )
            return str(value)
        if self.kind == "bool":
            lowered = text.casefold()
            if lowered in _TRUE_VALUES:
                return "true"
            if lowered in _FALSE_VALUES:
                return "false"
            raise ConfigurationError(
                f"{self.key} akzeptiert nur yes/no, true/false oder 1/0 (erhalten: {raw!r})."
            )
        if self.kind == "path":
            return Path(text).as_posix()
        # secret/string fallthrough – trim outer whitespace only
        return text


CONFIG_OPTIONS: tuple[ConfigOption, ...] = (
    ConfigOption(
        key="OUT_PATH",
        label="Ausgabepfad für den RSS-Feed",
        help=(
            "Relativer oder absoluter Pfad, unter dem der Feed gespeichert wird. "
            "Standard ist docs/feed.xml."
        ),
        kind="path",
        default=DEFAULT_OUT_PATH,
        required=True,
    ),
    ConfigOption(
        key="FEED_TITLE",
        label="Titel des Feeds",
        help="Wird als <title> im RSS-Feed verwendet.",
        default=DEFAULT_FEED_TITLE,
        required=True,
    ),
    ConfigOption(
        key="FEED_DESC",
        label="Beschreibung des Feeds",
        help="Kurzer Beschreibungstext für Clients.",
        default=DEFAULT_FEED_DESCRIPTION,
        required=True,
    ),
    ConfigOption(
        key="FEED_LINK",
        label="Referenz-Link",
        help="URL, auf die der Feed verweist (z. B. Projektseite oder Monitoring-Dashboard).",
        default=DEFAULT_FEED_LINK,
        required=True,
    ),
    ConfigOption(
        key="MAX_ITEMS",
        label="Maximale Anzahl an Feed-Einträgen",
        help="Begrenzt die Feed-Länge auf die jüngsten Meldungen.",
        kind="int",
        default=DEFAULT_MAX_ITEMS,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="FEED_TTL",
        label="Cache-Hinweis (TTL) in Minuten",
        help="Empfohlene Cache-Dauer für Feed-Clients.",
        kind="int",
        default=DEFAULT_FEED_TTL_MINUTES,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="MAX_ITEM_AGE_DAYS",
        label="Maximales Item-Alter (Tage)",
        help="Meldungen älter als dieser Wert werden verworfen.",
        kind="int",
        default=DEFAULT_MAX_ITEM_AGE_DAYS,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="ABSOLUTE_MAX_AGE_DAYS",
        label="Harte Altersgrenze (Tage)",
        help="Zusätzliche Sicherheitsgrenze, die Items spätestens entfernt.",
        kind="int",
        default=DEFAULT_ABSOLUTE_MAX_ITEM_AGE_DAYS,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="ENDS_AT_GRACE_MINUTES",
        label="Kulanzfenster für Endzeiten (Minuten)",
        help="Wie lange abgelaufene Meldungen noch akzeptiert werden.",
        kind="int",
        default=DEFAULT_ENDS_AT_GRACE_MINUTES,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="PROVIDER_TIMEOUT",
        label="Provider-Timeout (Sekunden)",
        help="Globale Timeout-Vorgabe für Netzwerkaufrufe.",
        kind="int",
        default=DEFAULT_PROVIDER_TIMEOUT,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="PROVIDER_MAX_WORKERS",
        label="Maximale parallele Provider-Aufrufe",
        help="0 entspricht automatischer Wahl basierend auf CPU-Kernen.",
        kind="int",
        default=DEFAULT_PROVIDER_MAX_WORKERS,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="STATE_RETENTION_DAYS",
        label="Aufbewahrung von first_seen.json (Tage)",
        help="Nach Ablauf werden veraltete First-Seen-Einträge entfernt.",
        kind="int",
        default=DEFAULT_STATE_RETENTION_DAYS,
        required=True,
        min_value=0,
    ),
    ConfigOption(
        key="WL_ENABLE",
        label="Wiener Linien Provider aktivieren",
        help="Steuert, ob der WL-Cache in den Feed einfließt.",
        kind="bool",
        default=DEFAULT_PROVIDER_FLAGS["WL_ENABLE"],
        required=True,
    ),
    ConfigOption(
        key="OEBB_ENABLE",
        label="ÖBB Provider aktivieren",
        help="Steuert, ob der ÖBB-Cache in den Feed einfließt.",
        kind="bool",
        default=DEFAULT_PROVIDER_FLAGS["OEBB_ENABLE"],
        required=True,
    ),
    ConfigOption(
        key="VOR_ENABLE",
        label="VOR Provider aktivieren",
        help="Steuert, ob der VOR-Cache in den Feed einfließt.",
        kind="bool",
        default=DEFAULT_PROVIDER_FLAGS["VOR_ENABLE"],
        required=True,
    ),
    ConfigOption(
        key="BAUSTELLEN_ENABLE",
        label="Baustellen Provider aktivieren",
        help="Steuert, ob Baustellenmeldungen berücksichtigt werden.",
        kind="bool",
        default=DEFAULT_PROVIDER_FLAGS["BAUSTELLEN_ENABLE"],
        required=True,
    ),
    ConfigOption(
        key="VOR_ACCESS_ID",
        label="VOR Access Token",
        help="Secret für die VAO/VOR-API. Wird nicht in Klartext ausgegeben.",
        kind="secret",
        default=None,
        required=False,
    ),
)

_OPTION_BY_KEY: Dict[str, ConfigOption] = {option.key: option for option in CONFIG_OPTIONS}


def normalize_existing_values(existing: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    normalized: dict[str, str] = {}
    warnings: list[str] = []
    for option in CONFIG_OPTIONS:
        raw = existing.get(option.key)
        if raw is None:
            continue
        try:
            value = option.normalize(raw)
        except ConfigurationError as exc:
            warnings.append(
                f"Bestehender Wert für {option.key} ist ungültig und wird ignoriert: {exc}"
            )
            continue
        if value:
            normalized[option.key] = value
    return normalized, warnings


@dataclass(frozen=True)
class ConfigurationComputation:
    managed: dict[str, str]
    custom: dict[str, str]
    changes: dict[str, tuple[str | None, str | None]]
    warnings: list[str]


def compute_non_interactive_configuration(
    existing: Mapping[str, str],
    overrides: Mapping[str, str],
    *,
    accept_defaults: bool,
) -> ConfigurationComputation:
    normalized_existing, warnings = normalize_existing_values(existing)
    managed = dict(normalized_existing)
    changes: dict[str, tuple[str | None, str | None]] = {}
    custom: dict[str, str] = {
        key: value for key, value in existing.items() if key not in _OPTION_BY_KEY
    }

    for key, raw_value in overrides.items():
        option = _OPTION_BY_KEY.get(key)
        if option is None:
            previous = existing.get(key)
            custom[key] = raw_value
            if raw_value != previous:
                changes[key] = (previous, raw_value)
            continue
        value = option.normalize(raw_value)
        if value:
            managed[key] = value
        else:
            managed.pop(key, None)
        previous = normalized_existing.get(key)
        if value != previous:
            changes[key] = (previous, value)

    if accept_defaults:
        for option in CONFIG_OPTIONS:
            if option.key in managed:
                continue
            default_str = option.default_str()
            if default_str:
                value = option.normalize(default_str)
                managed[option.key] = value
                previous = normalized_existing.get(option.key)
                if value != previous:
                    changes[option.key] = (previous, value)
            elif option.kind == "secret":
                warnings.append(
                    f"{option.key} ist nicht gesetzt. Ohne Token bleibt der Provider deaktiviert."
                )

    return ConfigurationComputation(managed=managed, custom=custom, changes=changes, warnings=warnings)


def merge_custom_entries(
    existing_custom: Mapping[str, str],
    overrides: Mapping[str, str],
) -> dict[str, str]:
    merged = dict(existing_custom)
    for key, value in overrides.items():
        if key in _OPTION_BY_KEY:
            continue
        merged[key] = value
    return merged


def _escape_env_value(value: str) -> str:
    if not value:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_@%+,:./-]+", value):
        return value
    # Escape control characters to prevent newline injection in generated .env files.
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )
    return f'"{escaped}"'


def format_env_document(
    managed: Mapping[str, str],
    custom: Mapping[str, str],
    *,
    include_header: bool = True,
) -> str:
    lines: list[str] = []
    if include_header:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        lines.append("# Konfigurationsdatei für den Wien-ÖPNV-Feed")
        lines.append(f"# Erstellt am {timestamp}")
        lines.append("")
    for option in CONFIG_OPTIONS:
        value = managed.get(option.key, "")
        if value:
            lines.append(f"{option.key}={_escape_env_value(value)}")
        elif option.kind == "secret":
            lines.append(f"# {option.key}=<token>  # erforderlich für VOR-API")
    if custom:
        lines.append("")
        lines.append("# Zusätzliche benutzerdefinierte Einstellungen")
        for key in sorted(custom):
            value = custom[key]
            if value:
                lines.append(f"{key}={_escape_env_value(value)}")
    if lines and lines[-1] != "":
        lines.append("")
    return "\n".join(lines)


def mask_value(value: str) -> str:
    if not value:
        return "<leer>"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def calculate_changes(
    previous: Mapping[str, str],
    current: Mapping[str, str],
) -> dict[str, tuple[str | None, str | None]]:
    keys: set[str] = set(previous) | set(current)
    diff: dict[str, tuple[str | None, str | None]] = {}
    for key in sorted(keys):
        before = previous.get(key)
        after = current.get(key)
        if before != after:
            diff[key] = (before, after)
    return diff
