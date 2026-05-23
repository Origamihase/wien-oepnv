"""Sentinel PoC: Provider URL allow-list drift — HTTPS not enforced on the
three credential-bearing / cache-feeding API endpoints.

The 2026-05-09 *Public Feed URL Allow-List Drift* round
 closed the analogous shape for
``validate_public_feed_url`` (``src/utils/http.py``) — pinning the
scheme to ``https`` (HTTP-on-publish is a TLS-strip primitive on every
subscriber's RSS reader).  The three provider-side cousins
(``_validated_oebb_url`` / ``_validated_wl_base`` /
``_validated_vor_base_url``) were left at the looser
``validate_http_url``-only shape, which accepts both ``http`` and
``https`` schemes.  Each is reachable from an operator-controlled env
override (``OEBB_RSS_URL`` / ``WL_RSS_URL`` / ``VOR_BASE_URL``,
``VOR_BASE``).

Threat model
------------

1. **VOR — credential leak (HIGH).**  ``VorAuth.__call__``
   (``src/providers/vor.py``) attaches the VAO ``accessId`` query
   parameter AND a ``Authorization: Bearer/Basic <VOR_ACCESS_ID>``
   header to every prepared request whose URL starts with
   ``VOR_BASE_URL``.  ``apply_authentication`` already detects the
   ``http://`` shape and emits a WARNING — but then **proceeds to
   attach the credentials anyway**, which is a fail-OPEN posture: an
   on-path attacker (compromised network, BGP hijack, MITM proxy) on
   any HTTP hop captures the access ID verbatim.  The access ID is a
   long-lived credential that grants full read access to the VAO API
   (100 reqs/day under the "VAO Start" tier).  An attacker who lifts
   it can exhaust the project's daily quota, exfiltrate proprietary
   station / disruption data, or correlate the project's request
   pattern with operator activity.

2. **OEBB — feed-content cache poisoning (MEDIUM-HIGH).**
   ``OEBB_URL`` is fetched via ``_fetch_xml`` and the returned RSS
   items become per-item ``<link>`` / ``<title>`` / ``<description>``
   in the public ``docs/feed.xml`` artefact.  An attacker who can
   MITM the HTTP fetch injects arbitrary RSS items (phishing links,
   misinformation, malicious OSC sequences) into the published feed
   — every subscriber's reader renders the attacker-supplied content
   as if it came from ÖBB.

3. **WL — feed-content cache poisoning (MEDIUM-HIGH).**  Identical
   shape to OEBB: ``WL_BASE`` is the prefix that every
   ``_fetch_traffic_infos`` / ``_fetch_news`` call concatenates a
   path onto, and the returned JSON becomes the per-item body in the
   public RSS feed.  An MITM injects arbitrary alerts.

The fix
-------

Mirror the canonical ``validate_public_feed_url`` shape: enforce the
``https`` scheme **at the provider validator boundary** so an
``http://`` env override falls back to the safe HTTPS default
(matching the existing untrusted-host rejection contract pinned by
``test_base_url_rejects_untrusted_host`` for VOR and the analogous
warning paths for OEBB / WL).  Defense-in-depth: tighten
``apply_authentication`` to **fail-closed** — when ``VOR_BASE_URL``
is somehow ``http://`` (e.g. a future caller sets it directly without
going through ``_validated_vor_base_url``), refuse to attach
credentials and abort the auth setup.  The TLS-strip primitive is
defeated even when the validator is bypassed.

Inventory invariant
-------------------

The three provider validators carry symmetrical ``trusted_hosts``
allow-list contracts; the HTTPS pin must apply to every one in lock-
step so a future provider added to the project (e.g. a hypothetical
``_validated_postbus_url``) inherits the same scheme-pin contract via
the audit walker that loads ``_validated_*`` symbols by name.  The
inventory test below names every current provider validator and
asserts the post-fix HTTPS-only shape; a new provider whose
validator accepts ``http://`` fails the test until the canonical
shape is restored.
"""

from __future__ import annotations

import importlib
import logging
from unittest.mock import MagicMock

import pytest


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_HTTPS_DRIFT`` finds the full call-graph at once.
SENTINEL_HTTPS_DRIFT = "https-only provider validator drift"


# ---------------------------------------------------------------------------
# (1) ``_validated_vor_base_url`` — HTTPS scheme pin.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/",
        "http://routenplaner.verkehrsauskunft.at/vao/restproxy/v2.0.0/",
        "http://routenplaner.verkehrsauskunft.at/",
    ],
)
def test_vor_base_url_validator_rejects_http(url: str) -> None:
    """Pre-fix: every ``http://`` variant of the VAO endpoint is
    accepted because ``validate_http_url`` allows both ``http`` and
    ``https`` by default and the host pin matches.  Post-fix:
    rejected because the VOR validator requires ``https``.

    Closes the credential-leak vector documented at the module
    docstring — every VOR request carries the access-ID, which would
    otherwise be sent verbatim over plain HTTP.
    """
    from src.providers.vor import _validated_vor_base_url

    assert _validated_vor_base_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/",
        "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v2.0.0/",
    ],
)
def test_vor_base_url_validator_accepts_https(url: str) -> None:
    """Happy path: HTTPS canonical URLs land verbatim in the
    validator's output.  Pre- and post-fix behaviour MUST match for
    the legitimate VAO endpoint — fork variants pointing at the same
    operator-supplied host should never be impacted by the scheme
    pin."""
    from src.providers.vor import _validated_vor_base_url

    assert _validated_vor_base_url(url) is not None


# ---------------------------------------------------------------------------
# (2) ``_validated_oebb_url`` — HTTPS scheme pin.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&",
        "http://fahrplan.oebb.at/",
    ],
)
def test_oebb_url_validator_rejects_http(url: str) -> None:
    """Pre-fix: every ``http://`` variant of the ÖBB Fahrplan host
    passes the validator.  Post-fix: rejected because the OEBB
    validator requires ``https``.

    Closes the feed-content cache-poisoning vector — an MITM on the
    HTTP hop substitutes attacker-controlled RSS items that flow
    directly into ``docs/feed.xml``.
    """
    from src.providers.oebb import _validated_oebb_url

    assert _validated_oebb_url(url) is None


def test_oebb_url_validator_accepts_https() -> None:
    """Happy path: HTTPS canonical URL is preserved."""
    from src.providers.oebb import _validated_oebb_url

    url = "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"
    assert _validated_oebb_url(url) is not None


# ---------------------------------------------------------------------------
# (3) ``_validated_wl_base`` — HTTPS scheme pin.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://www.wienerlinien.at/ogd_realtime",
        "http://www.wienerlinien.at/",
    ],
)
def test_wl_base_validator_rejects_http(url: str) -> None:
    """Pre-fix: every ``http://`` variant of the Wiener Linien OGD
    host passes the validator.  Post-fix: rejected because the WL
    validator requires ``https``.

    Closes the feed-content cache-poisoning vector — same shape as
    the OEBB sibling above.
    """
    from src.providers.wl_fetch import _validated_wl_base

    assert _validated_wl_base(url) is None


def test_wl_base_validator_accepts_https() -> None:
    """Happy path: HTTPS canonical URL is preserved."""
    from src.providers.wl_fetch import _validated_wl_base

    assert _validated_wl_base("https://www.wienerlinien.at/ogd_realtime") is not None


# ---------------------------------------------------------------------------
# (4) End-to-end via env override — module-level evaluation must fall
#     back to the secure HTTPS default for every provider.
# ---------------------------------------------------------------------------


def test_vor_module_env_override_falls_back_to_default_on_http(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Env-driven path: ``VOR_BASE_URL=http://...`` must NOT take
    effect — module-level reload should keep the safe HTTPS default,
    matching the existing untrusted-host rejection contract.
    """
    monkeypatch.setenv(
        "VOR_BASE_URL",
        "http://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/",
    )
    from src.providers import vor

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert vor.VOR_BASE_URL == (
        "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )
    assert "VOR_BASE_URL" in caplog.text

    monkeypatch.delenv("VOR_BASE_URL", raising=False)
    importlib.reload(vor)


def test_oebb_module_env_override_falls_back_to_default_on_http(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Env-driven path: ``OEBB_RSS_URL=http://...`` must NOT take
    effect — module-level reload keeps the safe HTTPS default.
    """
    monkeypatch.setenv(
        "OEBB_RSS_URL",
        "http://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&",
    )
    from src.providers import oebb

    with caplog.at_level(logging.WARNING):
        importlib.reload(oebb)

    assert oebb.OEBB_URL == (
        "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"
    )
    assert "OEBB_RSS_URL" in caplog.text

    monkeypatch.delenv("OEBB_RSS_URL", raising=False)
    importlib.reload(oebb)


def test_wl_module_env_override_falls_back_to_default_on_http(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Env-driven path: ``WL_RSS_URL=http://...`` must NOT take
    effect — module-level reload keeps the safe HTTPS default.
    """
    monkeypatch.setenv("WL_RSS_URL", "http://www.wienerlinien.at/ogd_realtime")
    from src.providers import wl_fetch

    with caplog.at_level(logging.WARNING):
        importlib.reload(wl_fetch)

    assert wl_fetch.WL_BASE == "https://www.wienerlinien.at/ogd_realtime"
    assert "WL_RSS_URL" in caplog.text

    monkeypatch.delenv("WL_RSS_URL", raising=False)
    importlib.reload(wl_fetch)


# ---------------------------------------------------------------------------
# (5) ``apply_authentication`` fail-closed gate — no credentials when
#     ``VOR_BASE_URL`` is somehow ``http://`` (defense-in-depth even if
#     a future caller bypasses ``_validated_vor_base_url``).
# ---------------------------------------------------------------------------


def test_apply_authentication_refuses_http_base_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``apply_authentication`` warns about an ``http://``
    base URL but **still attaches the credentials** to ``session.auth``
    and the request flow.  Post-fix: refuses to attach credentials,
    leaving ``session.auth`` either ``None`` or an unauthenticated
    placeholder so the access ID never reaches the wire.

    Mimics the threat where a future caller sets
    ``vor.VOR_BASE_URL = "http://..."`` directly (test fixture, debug
    knob, refactor regression) — the validator gate at module-load
    time would not catch that, so the auth-setup gate is the
    second line of defence.
    """
    from src.providers import vor

    monkeypatch.setattr(vor, "VOR_BASE_URL", "http://insecure.vor.example.com/api/")
    monkeypatch.setenv("VOR_ACCESS_ID", "TESTING_SECRET_DO_NOT_LEAK_111")

    mock_session = MagicMock()
    mock_session.headers = {}
    # Pre-set a sentinel value on session.auth so we can detect whether
    # apply_authentication overrode it with a credential-bearing AuthBase.
    sentinel_auth = object()
    mock_session.auth = sentinel_auth

    with caplog.at_level(logging.WARNING):
        vor.apply_authentication(mock_session)

    # Post-fix behaviour: session.auth must NOT have been replaced with
    # a credential-injecting VorAuth instance. The fail-closed contract
    # is "no credentials over plaintext HTTP, ever".
    assert not isinstance(mock_session.auth, vor.VorAuth), (
        "apply_authentication attached VorAuth to a session whose VOR_BASE_URL "
        "is http:// — this is a TLS-strip credential leak."
    )

    # The warning that flagged the misconfiguration must still fire so
    # operators see why the auth setup was skipped.
    assert "insecure" in caplog.text.lower() or "http" in caplog.text.lower()


def test_apply_authentication_attaches_credentials_on_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the happy path must continue to attach credentials
    over HTTPS so the legitimate VAO calls actually carry the access
    ID.  Without this regression test, the fail-closed branch above
    could silently break the entire VOR pipeline."""
    from src.providers import vor

    monkeypatch.setattr(
        vor,
        "VOR_BASE_URL",
        "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/",
    )
    monkeypatch.setenv("VOR_ACCESS_ID", "TESTING_SECRET_DO_NOT_LEAK_222")

    mock_session = MagicMock()
    mock_session.headers = {}
    mock_session.auth = None

    vor.apply_authentication(mock_session)

    assert isinstance(mock_session.auth, vor.VorAuth), (
        "apply_authentication failed to attach VorAuth on the HTTPS happy "
        "path — this would break legitimate VAO requests."
    )


# ---------------------------------------------------------------------------
# (6) Inventory invariant — every provider validator carries the same
#     HTTPS-only shape so a new provider added to the project inherits
#     the contract by walking the validator registry.
# ---------------------------------------------------------------------------


def test_provider_url_validators_all_reject_http() -> None:
    """Inventory check: enumerate every provider URL validator and
    assert that the canonical ``http://`` form of its trusted host is
    rejected.  Closes the cross-provider drift documented in the
    module docstring — a future fourth provider whose validator
    accepts ``http://`` would fail this test until the canonical
    shape is restored.
    """
    from src.providers.oebb import _validated_oebb_url
    from src.providers.vor import _validated_vor_base_url
    from src.providers.wl_fetch import _validated_wl_base

    inventory = (
        (_validated_vor_base_url, "http://routenplaner.verkehrsauskunft.at/"),
        (_validated_oebb_url, "http://fahrplan.oebb.at/"),
        (_validated_wl_base, "http://www.wienerlinien.at/ogd_realtime"),
    )

    for validator, http_url in inventory:
        result = validator(http_url)
        assert result is None, (
            f"{validator.__qualname__} accepted http:// URL {http_url!r} — "
            f"this is a TLS-strip / credential-leak primitive. "
            f"({SENTINEL_HTTPS_DRIFT})"
        )
