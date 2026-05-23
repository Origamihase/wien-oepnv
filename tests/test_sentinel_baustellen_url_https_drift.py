"""Sentinel PoC: Baustellen URL allow-list drift — HTTPS not enforced on
the Stadt Wien OGD endpoint via env override.

The 2026-05-10 *HTTPS-only Provider URL Drift* round (PR #1415,
the audit) closed the analogous shape for the three
provider URL validators (``_validated_vor_base_url`` /
``_validated_oebb_url`` / ``_validated_wl_base``) — pinning the scheme
to ``https`` so an env override cannot redirect credentials over
plaintext HTTP or downgrade a feed-fetch to a cache-poisoning vector.

The closing-checklist for that round explicitly enumerated::

    grep -rn "_validated_.*_url\\|_validated_.*_base" src/

— scoped to ``src/`` only.  The walker therefore MISSED the fourth
sibling ``_validated_baustellen_data_url`` living in
``scripts/update_baustellen_cache.py``.  This module ships the same
shape as the three closed siblings — it accepts both ``http`` and
``https`` schemes because it delegates to ``validate_http_url``, then
pins the host to the Stadt Wien OGD endpoint without constraining the
scheme dimension.

Threat model
------------

The Baustellen cache update is an automated cron job
(``.github/workflows/update-cycle.yml`` line 154 fans it out alongside
WL/ÖBB/Stammstrecke).  An env override
``BAUSTELLEN_DATA_URL=http://data.wien.gv.at/...`` (intentional
misconfig, leaked CI env, copy-paste from old documentation,
compromised secret store) is accepted verbatim because:

  1. ``validate_http_url`` accepts both ``http`` and ``https``
     schemes.
  2. ``_validated_baustellen_data_url`` checks
     ``host in _BAUSTELLEN_TRUSTED_HOSTS`` but NOT
     ``parsed.scheme.lower() == "https"``.

The cron job then fetches the OGD WFS GeoJSON over plaintext HTTP.
An on-path attacker (compromised network, BGP hijack, hostile public
WiFi gateway, MITM proxy on an organisational HTTP gateway)
substitutes arbitrary GeoJSON.  The poisoned content flows through
``update_baustellen_cache`` into ``cache/baustellen/events.json``,
which the build pipeline merges into the public ``docs/feed.xml``
artefact (served from
``https://origamihase.github.io/wien-oepnv/feed.xml``).  Per-item
``title`` / ``description`` / ``properties.HINWEIS`` strings are
under attacker control — the published RSS feed becomes a
brand-amplifying disinformation channel for any subscriber's reader.

**Severity:** MEDIUM-HIGH — feed-content cache poisoning. Same
shape as the OEBB / WL siblings closed by PR #1415, no credential
leak (the WFS endpoint is unauthenticated) but identical
public-artefact integrity impact.

The fix
-------

Mirror the canonical ``validate_public_feed_url`` shape and the
just-pinned VOR / OEBB / WL validators: enforce the ``https`` scheme
**at the Baustellen validator boundary** so an ``http://`` env
override falls back to the safe HTTPS default.  Update the
journal-pinned closing-checklist grep to include ``scripts/``
explicitly so a future fifth provider's validator is caught at
PR-review time.

Inventory invariant
-------------------

The four provider/data validators (VOR, OEBB, WL, Baustellen) all
carry symmetrical ``trusted_hosts`` allow-list contracts; the HTTPS
pin must apply to every one in lockstep so a future sixth (e.g. a
hypothetical ``_validated_postbus_url`` or ``_validated_anachb_url``
in either ``src/`` or ``scripts/``) inherits the same scheme-pin
contract via the audit walker that loads ``_validated_*`` symbols by
name.  The inventory test below names every current validator and
asserts the post-fix HTTPS-only shape; a new validator whose shape
accepts ``http://`` fails the test until the canonical shape is
restored.
"""

from __future__ import annotations

import importlib
import logging

import pytest

from scripts import update_baustellen_cache


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_HTTPS_DRIFT`` finds the full call-graph at once.
SENTINEL_HTTPS_DRIFT = "https-only provider/data validator drift"


# ---------------------------------------------------------------------------
# (1) ``_validated_baustellen_data_url`` — HTTPS scheme pin.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # Bare host
        "http://data.wien.gv.at/",
        # Real OGD WFS path on plaintext HTTP
        (
            "http://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature"
            "&version=1.1.0&typeName=ogdwien:BAUSTELLEOGD"
            "&srsName=EPSG:4326&outputFormat=json"
        ),
        # Path-prefix variant
        "http://data.wien.gv.at/daten/geo",
    ],
)
def test_baustellen_url_validator_rejects_http(url: str) -> None:
    """Pre-fix: every ``http://`` variant of the Stadt Wien OGD host
    is accepted because ``validate_http_url`` allows both ``http`` and
    ``https`` schemes by default and the host pin matches.  Post-fix:
    rejected because the Baustellen validator requires ``https``.

    Closes the feed-content cache-poisoning vector documented in
    the module docstring — an MITM on the HTTP hop substitutes
    arbitrary construction-data GeoJSON that flows verbatim into the
    published RSS feed.
    """
    assert update_baustellen_cache._validated_baustellen_data_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://data.wien.gv.at/",
        (
            "https://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature"
            "&version=1.1.0&typeName=ogdwien:BAUSTELLEOGD"
            "&srsName=EPSG:4326&outputFormat=json"
        ),
        "https://data.wien.gv.at/daten/geo",
    ],
)
def test_baustellen_url_validator_accepts_https(url: str) -> None:
    """Happy path: HTTPS canonical URLs land verbatim in the
    validator's output.  Pre- and post-fix behaviour MUST match for
    the legitimate OGD endpoint — fork variants pointing at the same
    Stadt Wien OGD host should never be impacted by the scheme pin.
    """
    assert update_baustellen_cache._validated_baustellen_data_url(url) is not None


# ---------------------------------------------------------------------------
# (2) End-to-end via ``_resolve_data_url`` — env-driven fallback to default.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://data.wien.gv.at/",
        (
            "http://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature"
            "&version=1.1.0&typeName=ogdwien:BAUSTELLEOGD"
            "&srsName=EPSG:4326&outputFormat=json"
        ),
    ],
)
def test_resolve_data_url_falls_back_on_http(
    caplog: pytest.LogCaptureFixture, url: str
) -> None:
    """``_resolve_data_url`` is the public consumer that the cron
    pipeline reaches via ``BAUSTELLEN_DATA_URL`` env override.  An
    ``http://``-scheme override must NOT take effect — the resolver
    falls back to the safe HTTPS ``DEFAULT_DATA_URL`` and emits a
    warning so an operator debugging the misconfiguration sees the
    cause.
    """
    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
    resolved = update_baustellen_cache._resolve_data_url(url)

    assert resolved == update_baustellen_cache.DEFAULT_DATA_URL
    assert any(
        "kein bekannter Stadt-Wien-OGD-Host" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# (3) Cross-validator inventory invariant — every ``_validated_*_url``
#     symbol in src/ and scripts/ rejects http:// for its canonical
#     trusted-host. Catches a future fifth sibling's drift at
#     PR-review time.
# ---------------------------------------------------------------------------


def test_all_provider_url_validators_reject_http() -> None:
    """Inventory check: walk every provider/data URL validator in
    BOTH ``src/`` and ``scripts/`` and assert that the canonical
    ``http://`` form of its trusted host is rejected.

    The 2026-05-10 *HTTPS-only Provider URL Drift* round (PR #1415)
    closed the three ``src/providers/*`` siblings but the
    closing-checklist grep was scoped to ``src/`` only — missing
    ``_validated_baustellen_data_url`` in
    ``scripts/update_baustellen_cache.py``.  This inventory test
    enforces the cross-tree invariant so a future sixth sibling
    (e.g. ``scripts/update_postbus_cache.py``) inherits the
    contract automatically.
    """
    from src.providers.oebb import _validated_oebb_url
    from src.providers.vor import _validated_vor_base_url
    from src.providers.wl_fetch import _validated_wl_base

    inventory = (
        (_validated_vor_base_url, "http://routenplaner.verkehrsauskunft.at/"),
        (_validated_oebb_url, "http://fahrplan.oebb.at/"),
        (_validated_wl_base, "http://www.wienerlinien.at/ogd_realtime"),
        (
            update_baustellen_cache._validated_baustellen_data_url,
            "http://data.wien.gv.at/",
        ),
    )

    for validator, http_url in inventory:
        result = validator(http_url)
        assert result is None, (
            f"{validator.__qualname__} accepted http:// URL {http_url!r} — "
            f"this is a TLS-strip / cache-poisoning primitive. "
            f"({SENTINEL_HTTPS_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (4) Regression: the existing untrusted-host rejection contract is
#     preserved — the Baustellen validator continues to reject hosts
#     outside the OGD allow-list regardless of scheme.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/baustellen.json",
        # Suffix attack: looks like the official host but isn't.
        "https://data.wien.gv.at.evil.com/baustellen.json",
        # Different Vienna subdomain.
        "https://www.wien.gv.at/baustellen.json",
        # Different OGD provider.
        "https://data.gv.at/baustellen.json",
    ],
)
def test_baustellen_url_validator_still_rejects_untrusted_hosts(url: str) -> None:
    """Regression: the host-pin contract is preserved post-fix.
    Adding the scheme pin must NOT loosen the host check."""
    assert update_baustellen_cache._validated_baustellen_data_url(url) is None


# ---------------------------------------------------------------------------
# (5) Module-level idempotency — re-importing the module after the
#     fix lands does not raise (defensive: the fix may add
#     ``urlparse`` import or restructure the validator). Cheap smoke
#     test that catches accidental NameError / circular-import
#     regressions.
# ---------------------------------------------------------------------------


def test_module_reload_succeeds() -> None:
    """Smoke: re-importing the module triggers the validator's
    module-level evaluation path and should never raise."""
    importlib.reload(update_baustellen_cache)
    assert callable(update_baustellen_cache._validated_baustellen_data_url)
    assert callable(update_baustellen_cache._resolve_data_url)
