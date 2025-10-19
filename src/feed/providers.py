"""Provider registry used by the feed builder."""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

try:  # pragma: no cover - allow running as script or package
    from utils.cache import read_cache
    from feed.config import get_bool_env
except ModuleNotFoundError:  # pragma: no cover
    from ..utils.cache import read_cache
    from .config import get_bool_env

log = logging.getLogger(__name__)

ProviderLoader = Callable[..., Sequence[Any]]

_PLUGINS_ENV_VAR = "WIEN_OEPNV_PROVIDER_PLUGINS"


@dataclass(frozen=True)
class ProviderSpec:
    env_var: str
    loader: ProviderLoader
    cache_key: str

    @property
    def display_name(self) -> str:
        return cache_display_name(self.cache_key)


def cache_display_name(cache_key: str) -> str:
    return str(cache_key)


_REGISTRY: Dict[str, ProviderSpec] = {}
_LOADED_PLUGINS: set[str] = set()


def register_provider(env_var: str, loader: ProviderLoader, *, cache_key: str) -> None:
    """Register ``loader`` as disruption provider controlled via ``env_var``."""

    spec = ProviderSpec(env_var=env_var, loader=loader, cache_key=cache_key)
    _REGISTRY[env_var] = spec
    try:
        loader.__name__ = f"read_cache_{cache_key}"
    except (AttributeError, TypeError):  # pragma: no cover - defensive only
        pass
    setattr(loader, "_provider_cache_name", cache_key)


def unregister_provider(env_var: str) -> None:
    """Remove provider registered under ``env_var`` (mainly for tests)."""

    _REGISTRY.pop(env_var, None)


def iter_providers() -> Iterable[ProviderSpec]:
    return list(_REGISTRY.values())


def provider_statuses() -> List[Tuple[str, bool]]:
    statuses: List[Tuple[str, bool]] = []
    seen_envs: set[str] = set()
    for spec in iter_providers():
        env = spec.env_var
        if env in seen_envs:
            continue
        seen_envs.add(env)
        display = spec.display_name
        statuses.append((display, bool(get_bool_env(env, True))))
    return statuses


def resolve_provider_name(loader: ProviderLoader, env: str | None) -> str:
    provider_name = getattr(loader, "_provider_cache_name", None)
    if provider_name:
        return str(provider_name)
    if env:
        for spec in _REGISTRY.values():
            if spec.env_var == env:
                return spec.cache_key
    name = getattr(loader, "__name__", None)
    if name:
        return name
    return str(loader)


def register_default_providers() -> None:
    register_provider("WL_ENABLE", read_cache_wl, cache_key="wl")
    register_provider("OEBB_ENABLE", read_cache_oebb, cache_key="oebb")
    register_provider("VOR_ENABLE", read_cache_vor, cache_key="vor")
    register_provider("BAUSTELLEN_ENABLE", read_cache_baustellen, cache_key="baustellen")


def _derive_cache_key(env_var: str) -> str:
    base = env_var.strip().lower()
    if base.endswith("_enable"):
        base = base[: -len("_enable")]
    return base or "provider"


def _iter_plugin_specs(raw_value: str | None) -> List[str]:
    if not raw_value:
        return []
    candidates = [chunk.strip() for chunk in raw_value.split(",")]
    return [candidate for candidate in candidates if candidate]


def _register_plugin_providers(module: ModuleType, module_name: str) -> None:
    register_callable = getattr(module, "register_providers", None)
    if callable(register_callable):
        try:
            register_callable(register_provider)
        except Exception as exc:  # pragma: no cover - defensive logging path
            log.error(
                "Provider plugin %s.register_providers fehlgeschlagen: %s: %s",
                module_name,
                type(exc).__name__,
                exc,
            )

    providers_attr = getattr(module, "PROVIDERS", None)
    if not providers_attr:
        if not callable(register_callable):
            log.warning(
                "Provider-Plugin %s definiert weder register_providers noch PROVIDERS",
                module_name,
            )
        return

    if not isinstance(providers_attr, (list, tuple)):
        log.error(
            "Provider-Plugin %s.PROVIDERS ist kein Sequenz-Typ: %r",
            module_name,
            type(providers_attr).__name__,
        )
        return

    for entry in providers_attr:
        if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3):
            log.error(
                "Provider-Plugin %s.PROVIDERS enthält ungültigen Eintrag: %r",
                module_name,
                entry,
            )
            continue
        env_var = entry[0]
        loader = entry[1]
        cache_key = entry[2] if len(entry) == 3 else _derive_cache_key(str(env_var))
        if not isinstance(env_var, str) or not env_var:
            log.error(
                "Provider-Plugin %s.PROVIDERS enthält ungültige Umgebungsvariable: %r",
                module_name,
                env_var,
            )
            continue
        if not callable(loader):
            log.error(
                "Provider-Plugin %s.PROVIDERS enthält nicht-aufrufbaren Loader für %s",
                module_name,
                env_var,
            )
            continue
        register_provider(env_var, loader, cache_key=str(cache_key))


def load_provider_plugins(*, force: bool = False) -> List[str]:
    """Load provider plugins defined via :envvar:`WIEN_OEPNV_PROVIDER_PLUGINS`."""

    if force:
        _LOADED_PLUGINS.clear()

    raw_value = os.getenv(_PLUGINS_ENV_VAR)
    requested = _iter_plugin_specs(raw_value)
    loaded: List[str] = []
    for module_name in requested:
        if not force and module_name in _LOADED_PLUGINS:
            continue
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - defensive logging path
            log.error(
                "Provider-Plugin %s konnte nicht importiert werden: %s: %s",
                module_name,
                type(exc).__name__,
                exc,
            )
            continue
        _LOADED_PLUGINS.add(module_name)
        loaded.append(module_name)
        _register_plugin_providers(module, module_name)
    return loaded


def _reset_registry(*, with_defaults: bool = True) -> None:
    """Test helper to reset registry and loaded plugin state."""

    _REGISTRY.clear()
    _LOADED_PLUGINS.clear()
    if with_defaults:
        register_default_providers()


def read_cache_wl() -> List[Any]:
    return list(read_cache("wl"))


def read_cache_oebb() -> List[Any]:
    return list(read_cache("oebb"))


def read_cache_vor() -> List[Any]:
    return list(read_cache("vor"))


def read_cache_baustellen() -> List[Any]:
    return list(read_cache("baustellen"))


register_default_providers()
load_provider_plugins()


__all__ = [
    "ProviderLoader",
    "ProviderSpec",
    "iter_providers",
    "load_provider_plugins",
    "provider_statuses",
    "read_cache_baustellen",
    "read_cache_oebb",
    "read_cache_vor",
    "read_cache_wl",
    "register_provider",
    "resolve_provider_name",
    "unregister_provider",
]
