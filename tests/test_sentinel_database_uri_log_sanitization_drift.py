"""Sentinel drift coverage for database / JDBC / LDAP / SSH credential
log-sanitisation across the three canonical redaction sites.

The 2026-05-16 Database Connection String secret-scanner round
(``test_sentinel_database_uri_credential_drift``) closed the *detection*
codepath for committed ``DATABASE_URL=postgres://user:pass@host/db``
shapes. It explicitly named one named-but-deferred next-round candidate:

    Log sanitization extension for ``_URL_AUTH_RE`` (src/utils/http.py)
    to include database schemes in the cross-origin redirect / error-log
    redaction path - currently scheme-restricted to ``https?|ftp``, so
    database URIs in exception messages slip past
    ``_sanitize_url_for_error``.

This round closes that named-but-deferred candidate. The vulnerability
manifested across THREE sites in TWO modules:

1. ``src/utils/http.py:_URL_AUTH_RE`` - scheme alternation restricted
   to ``https?|ftp``. Every malformed credentialled URI without ``//``
   (e.g. ``postgres:admin:secret@host``) AND every JDBC-prefixed URI
   (e.g. ``jdbc:postgresql://admin:secret@host``) silently leaked
   through ``_sanitize_url_for_error`` because:
   - ``_URL_AUTH_RE`` does not match (scheme is not http/https/ftp);
   - ``urlparse`` does not extract credentials (for malformed URIs the
     whole post-scheme fragment becomes the path; for JDBC, ``urlparse``
     treats ``jdbc`` as the scheme and the rest as opaque path).

2. ``src/utils/http.py:_sanitize_exception_msg`` - uses an HTTP-only
   pre-regex (``r"(https?://[^\\s'\\"<>]+)"``) then falls back to
   ``sanitize_log_message``. The fallback regex (#3) catches the
   canonical ``://`` form but misses malformed forms.

3. ``src/utils/logging.py:sanitize_log_message`` - the Basic-Auth-in-URL
   pattern ``r"(?i)([a-z0-9+.-]+://)([^/@\\s]+)@"`` requires ``://`` and
   therefore misses ``postgres:admin:secret@host`` and the inner
   malformed JDBC variant ``jdbc:mysql:root:rootpw@db``.

Each leak surface emits the operator-facing log line / public
``feed_health.json`` artefact with the credential preserved verbatim,
defeating defense-in-depth: even when the *scanner* catches the source
emission, an operator pasting an exception message into Slack /
GitHub Issues / docs would still leak the credential. The fix is the
canonical sibling-drift closure pattern: extend the scheme alternation
to cover the same 13+ database / broker / mail schemes as the scanner
detector, PLUS the LDAP / SSH / SFTP / SMB / CIFS adjacent families,
PLUS the JDBC ``jdbc:`` prefix.

Marker: SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import (
    _sanitize_exception_msg,
    _sanitize_url_for_error,
)
from src.utils.logging import sanitize_log_message

SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT: ``_URL_AUTH_RE`` was "
    "scheme-restricted to ``https?|ftp``; the Basic-Auth-in-URL pattern in "
    "``sanitize_log_message`` required ``://``. Every malformed (no-``//``) "
    "credentialled URI and every ``jdbc:`` inner-scheme variant slipped "
    "past all three log-sanitisation codepaths."
)


# Sentinel passwords pinned per test; each is unique so a per-case
# false-negative is unambiguous in the failure report.
_PG_PW = "supersecret_pg_pw"
_MYSQL_PW = "mysql_root_pw_x"
_MONGO_PW = "mongo_app_pw_y"
_REDIS_PW = "redis_acl_pw_z"
_KAFKA_PW = "kafka_streampw_w"
_AMQP_PW = "rabbit_brokerpw_q"
_CH_PW = "clickhouse_pw_v"
_LDAP_PW = "ldap_admin_pw"
_SSH_PW = "ssh_deploy_pw"
_SMB_PW = "smb_share_pw"


# ---------------------------------------------------------------------------
# (1) ``_sanitize_url_for_error`` - canonical malformed form (no ``//``)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,password",
    [
        (f"postgres:admin:{_PG_PW}@db.example.com:5432/prod", _PG_PW),
        (f"postgresql:app_user:{_PG_PW}@db-master.example.com/myapp", _PG_PW),
        (f"mysql:root:{_MYSQL_PW}@mysql.example.com:3306/wordpress", _MYSQL_PW),
        (f"mariadb:admin:{_MYSQL_PW}@mariadb.example.com/app", _MYSQL_PW),
        (f"mongodb:dbuser:{_MONGO_PW}@mongo.example.com:27017/myapp", _MONGO_PW),
        (f"mongodb+srv:cluster_user:{_MONGO_PW}@cluster.mongodb.net/prod", _MONGO_PW),
        (f"redis:default:{_REDIS_PW}@redis.example.com:6379/0", _REDIS_PW),
        (f"amqp:guest:{_AMQP_PW}@rabbit.example.com:5672/vhost", _AMQP_PW),
        (f"amqps:prod_user:{_AMQP_PW}@rabbit.example.com:5671/prod", _AMQP_PW),
        (f"kafka:kafka_user:{_KAFKA_PW}@broker.example.com:9092", _KAFKA_PW),
        (f"clickhouse:default:{_CH_PW}@clickhouse.example.com:9000/analytics", _CH_PW),
        (f"cassandra:app_user:{_CH_PW}@cassandra.example.com:9042/keyspace", _CH_PW),
        (f"elasticsearch:elastic:{_CH_PW}@es.example.com:9200", _CH_PW),
        (f"smtp:noreply:{_AMQP_PW}@smtp.example.com:587", _AMQP_PW),
        (f"smtps:noreply:{_AMQP_PW}@smtp.example.com:465", _AMQP_PW),
        (f"ldap:cn=admin:{_LDAP_PW}@ldap.example.com:389", _LDAP_PW),
        (f"ldaps:cn=admin:{_LDAP_PW}@ldap.example.com:636", _LDAP_PW),
        (f"ssh:deploy:{_SSH_PW}@bastion.example.com:22", _SSH_PW),
        (f"sftp:transfer:{_SSH_PW}@sftp.example.com:22/upload", _SSH_PW),
        (f"smb:domain_user:{_SMB_PW}@fileserver.example.com/share", _SMB_PW),
        (f"cifs:domain_user:{_SMB_PW}@fileserver.example.com/share", _SMB_PW),
    ],
)
def test_malformed_uri_credentials_stripped(url: str, password: str) -> None:
    """Pre-fix every malformed (no-``//``) credentialled URI across the
    13+ database / broker / mail schemes plus LDAP / SSH / SMB / CIFS
    siblings leaked through ``_sanitize_url_for_error``: ``_URL_AUTH_RE``
    did not match (scheme not in https/ftp), and ``urlparse`` did not
    extract credentials (the whole post-scheme fragment becomes the
    path). Post-fix the credential is replaced with ``***``.
    """
    sanitized = _sanitize_url_for_error(url)
    assert password not in sanitized, (
        f"Password leaked through _sanitize_url_for_error for {url!r}: "
        f"{sanitized!r}. ({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )
    assert "***" in sanitized, (
        f"Expected ``***`` marker in sanitized output for {url!r}: "
        f"{sanitized!r}. ({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) ``_sanitize_url_for_error`` - JDBC-prefixed URIs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,password",
    [
        # Canonical JDBC PostgreSQL with inner ``//``.
        (f"jdbc:postgresql://admin:{_PG_PW}@db.example.com:5432/prod", _PG_PW),
        # Canonical JDBC MySQL with inner ``//``.
        (f"jdbc:mysql://root:{_MYSQL_PW}@mysql.example.com:3306/wordpress", _MYSQL_PW),
        (f"jdbc:mariadb://admin:{_MYSQL_PW}@mariadb.example.com/app", _MYSQL_PW),
        # JDBC MongoDB.
        (f"jdbc:mongodb://dbuser:{_MONGO_PW}@mongo.example.com:27017/myapp", _MONGO_PW),
        # JDBC malformed (no inner ``//``) - extreme edge case but valid
        # JDBC drivers do accept some of these shapes.
        (f"jdbc:mysql:root:{_MYSQL_PW}@mysql.example.com:3306/wordpress", _MYSQL_PW),
        (f"jdbc:postgresql:admin:{_PG_PW}@db.example.com:5432/prod", _PG_PW),
    ],
)
def test_jdbc_uri_credentials_stripped(url: str, password: str) -> None:
    """Pre-fix every JDBC-prefixed credentialled URI leaked through
    ``_sanitize_url_for_error``: ``urlparse`` treats ``jdbc`` as the
    scheme and the rest as an opaque path so no credentials are
    extracted, AND ``_URL_AUTH_RE`` does not match (scheme not in
    https/ftp). Post-fix the ``jdbc:`` prefix is accepted as an optional
    prefix on the scheme alternation and the credential is stripped.
    """
    sanitized = _sanitize_url_for_error(url)
    assert password not in sanitized, (
        f"Password leaked through _sanitize_url_for_error for JDBC URI "
        f"{url!r}: {sanitized!r}. "
        f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )
    assert "***" in sanitized, (
        f"Expected ``***`` marker in sanitized output for JDBC URI "
        f"{url!r}: {sanitized!r}. "
        f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) ``_sanitize_exception_msg`` - real-world exception text shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg,password",
    [
        # Malformed (no ``//``) embedded in exception text.
        (
            f"Connection failed: postgres:admin:{_PG_PW}@db.example.com:5432/prod",
            _PG_PW,
        ),
        (
            f"OperationalError: redis:default:{_REDIS_PW}@redis.example.com:6379 unreachable",
            _REDIS_PW,
        ),
        # JDBC variants in exception text.
        (
            f"SQLException: jdbc:postgresql://admin:{_PG_PW}@db.example.com:5432/prod connection refused",
            _PG_PW,
        ),
        (
            f"DriverManager failed: jdbc:mysql:root:{_MYSQL_PW}@mysql.example.com:3306/wordpress",
            _MYSQL_PW,
        ),
        # Multi-URL exception message - verify every credential is
        # masked even when the message contains several DB URIs.
        (
            f"Failover error: primary=postgres://admin:{_PG_PW}@primary.example.com "
            f"replica=postgres:admin:{_MYSQL_PW}@replica.example.com",
            _PG_PW,
        ),
    ],
)
def test_sanitize_exception_msg_strips_database_credentials(
    msg: str, password: str
) -> None:
    """End-to-end exception-message sanitisation across malformed and
    JDBC URIs. Operator-facing log lines must not preserve the
    credential even when the URI fragment is embedded in surrounding
    natural-language exception text.
    """
    sanitized = _sanitize_exception_msg(msg)
    assert password not in sanitized, (
        f"Password leaked through _sanitize_exception_msg for {msg!r}: "
        f"{sanitized!r}. ({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )


def test_sanitize_exception_msg_strips_all_credentials_in_multi_uri_message() -> None:
    """Multi-URI exception message: ALL passwords are masked, including
    the second credential which exercises the malformed-URI codepath.
    """
    msg = (
        f"Failover error: primary=postgres://admin:{_PG_PW}@primary.example.com "
        f"replica=postgres:admin:{_MYSQL_PW}@replica.example.com"
    )
    sanitized = _sanitize_exception_msg(msg)
    assert _PG_PW not in sanitized, (
        f"Primary credential leaked: {sanitized!r}. "
        f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )
    assert _MYSQL_PW not in sanitized, (
        f"Replica credential leaked: {sanitized!r}. "
        f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) ``sanitize_log_message`` - direct invocation (the fallback path used
# by ``_sanitize_exception_msg`` and by every traceback formatter).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg,password",
    [
        # Malformed (no ``//``).
        (f"DB error: mysql:root:{_MYSQL_PW}@mysql.example.com:3306/wordpress", _MYSQL_PW),
        (f"Cache error: redis:default:{_REDIS_PW}@redis.example.com:6379/0", _REDIS_PW),
        # JDBC with inner ``//``.
        (
            f"JDBC error: jdbc:postgresql://admin:{_PG_PW}@db.example.com:5432/prod",
            _PG_PW,
        ),
        # JDBC inner malformed.
        (
            f"JDBC error: jdbc:mysql:root:{_MYSQL_PW}@mysql.example.com:3306/wordpress",
            _MYSQL_PW,
        ),
        # LDAP / SSH families - documented credential-carrying schemes
        # that are absent from the existing ``https?|ftp`` alternation.
        (f"Bind failure: ldap:cn=admin:{_LDAP_PW}@ldap.example.com:389", _LDAP_PW),
        (f"Deploy failure: ssh:deploy:{_SSH_PW}@bastion.example.com:22", _SSH_PW),
    ],
)
def test_sanitize_log_message_strips_database_credentials(
    msg: str, password: str
) -> None:
    """``sanitize_log_message`` (the fallback redaction path) must strip
    credentials from every credentialled URI shape, including malformed
    (no-``//``) forms and JDBC inner-scheme variants. Pre-fix the
    Basic-Auth-in-URL pattern required ``://`` so the malformed shapes
    leaked.
    """
    sanitized = sanitize_log_message(msg, strip_control_chars=False)
    assert password not in sanitized, (
        f"Password leaked through sanitize_log_message for {msg!r}: "
        f"{sanitized!r}. ({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Negative cases - structural anchors prevent false positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        # Plain email - ``mailto:`` is NOT in the credentialled-scheme
        # alternation, so the ``user@host`` fragment is preserved.
        "Contact us at mailto:support@example.com for help",
        "Send email to notifications@example.com",
        # Natural-language scheme mentions.
        "We use PostgreSQL with MySQL fallback.",
        "Connect to mongodb using your credentials.",
        # Compound word boundary: ``mypostgres`` is a code identifier,
        # not the ``postgres:`` scheme - the ``(?<![a-z0-9])`` lookbehind
        # anchor prevents mid-word matching.
        "mypostgres:admin@example.com is a variable",
        # File / URN / TEL schemes - not credentialled.
        "file:///etc/passwd is the password file",
        "urn:ietf:rfc:3986 is the URI spec",
        "tel:+1-555-1234 is a phone number",
        # Plain HTTP URL without credentials (preserved verbatim).
        "Fetched https://example.com/path?q=value",
        # Database URI WITHOUT credentials (no ``user:pass@`` fragment).
        "Connected to postgres://db.example.com:5432/prod",
        "Connected to jdbc:mysql://db.example.com:3306/app",
    ],
)
def test_negative_cases_preserved_verbatim(msg: str) -> None:
    """The structural anchor (literal ``:`` inside the auth fragment for
    non-http/ftp schemes, plus ``(?<![a-z0-9])`` lookbehind on the
    scheme literal in ``sanitize_log_message``) ensures we do not
    accidentally mask benign email addresses, scheme mentions in prose,
    file URIs, or credential-less database URIs.
    """
    sanitized = _sanitize_exception_msg(msg)
    # The benign substrings that must NOT be replaced by ``***``:
    benign_fragments = [
        "support@example.com",
        "notifications@example.com",
        "PostgreSQL",
        "mongodb",
        "mypostgres:",
        "/etc/passwd",
        "rfc:3986",
        "+1-555-1234",
        "/path?q=value",
        "db.example.com:5432/prod",
        "db.example.com:3306/app",
    ]
    for fragment in benign_fragments:
        if fragment in msg:
            assert fragment in sanitized, (
                f"Benign fragment {fragment!r} was redacted from {msg!r}: "
                f"{sanitized!r}. False positive. "
                f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
            )


# ---------------------------------------------------------------------------
# (6) Backward-compatibility: existing http/ftp/https behaviour preserved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # Canonical HTTP/HTTPS/FTP - already covered pre-fix.
        f"https://user:{_PG_PW}@example.com/foo",
        f"http://admin:{_PG_PW}@host.example.com/api",
        f"ftp://user:{_PG_PW}@ftp.example.com/dir",
        # Malformed HTTP without ``//`` - already covered pre-fix.
        f"https:user:{_PG_PW}@example.com/foo",
        f"http:user:{_PG_PW}@example.com/path",
        # User-only HTTP (no password) - preserved by the existing
        # behaviour (``[^/\\s]+@`` matches non-empty auth).
        "https://user@example.com",
    ],
)
def test_backward_compatibility_http_schemes(url: str) -> None:
    """The extended scheme alternation must preserve the pre-fix
    behaviour for HTTP/HTTPS/FTP URIs, including the malformed
    (no-``//``) forms previously covered.
    """
    sanitized = _sanitize_url_for_error(url)
    assert _PG_PW not in sanitized, (
        f"HTTP/HTTPS/FTP credential leaked - backward compatibility "
        f"regression: {url!r} -> {sanitized!r}. "
        f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Trailing path / query preserved (only the credential is masked)
# ---------------------------------------------------------------------------


def test_postgres_uri_preserves_host_path_query() -> None:
    """The credential is replaced with ``***`` but the host / port /
    path / query / fragment information is preserved so operators can
    still triage which database the failed connection targeted.
    Asserting the EXACT sanitised output pins both the credential
    redaction AND the structural preservation in one invariant (avoids
    the CodeQL ``py/incomplete-url-substring-sanitization`` false-
    positive that flags substring containment on a URL).
    """
    url = f"postgres:admin:{_PG_PW}@db.example.com:5432/prod?sslmode=require"
    sanitized = _sanitize_url_for_error(url)
    assert _PG_PW not in sanitized
    assert sanitized == "postgres:***@db.example.com:5432/prod?sslmode=require"


def test_jdbc_postgresql_preserves_host_path() -> None:
    """JDBC variants similarly preserve the host / path so operators
    can identify the connection target. Asserting the exact sanitised
    output (rather than substring containment) pins the structure-
    preservation invariant precisely.
    """
    url = f"jdbc:postgresql://admin:{_PG_PW}@db.example.com:5432/prod"
    sanitized = _sanitize_url_for_error(url)
    assert _PG_PW not in sanitized
    assert sanitized == "jdbc:postgresql://***@db.example.com:5432/prod"


# ---------------------------------------------------------------------------
# (8) PoC: end-to-end demonstration vs. the secret-scanner round
# ---------------------------------------------------------------------------


def test_poc_end_to_end_database_uri_log_leak() -> None:
    """PoC: a single exception text combining canonical and malformed
    database URIs across multiple schemes. Pre-fix the malformed and
    JDBC forms leaked their credentials verbatim into operator log
    streams; post-fix every credential is masked.

    This is the canonical sibling-drift completion for the
    2026-05-16 Database Connection String secret-scanner round - the
    scanner catches the source emission and the log sanitiser catches
    the runtime exception emission.
    """
    msg = (
        "DB connection failures:\n"
        f"  primary postgres://admin:{_PG_PW}@primary.example.com:5432/prod\n"
        f"  malformed postgres:admin:{_MYSQL_PW}@replica.example.com:5432/prod\n"
        f"  jdbc-canonical jdbc:postgresql://admin:{_MONGO_PW}@jdbc.example.com:5432/prod\n"
        f"  jdbc-malformed jdbc:mysql:root:{_REDIS_PW}@jdbc-old.example.com:3306/legacy\n"
        f"  redis redis:default:{_KAFKA_PW}@redis.example.com:6379/0\n"
        f"  ldap ldaps://cn=admin:{_LDAP_PW}@ldap.example.com:636\n"
    )
    sanitized = _sanitize_exception_msg(msg)
    for password in (_PG_PW, _MYSQL_PW, _MONGO_PW, _REDIS_PW, _KAFKA_PW, _LDAP_PW):
        assert password not in sanitized, (
            f"Credential {password!r} leaked in multi-URI PoC: "
            f"{sanitized!r}. "
            f"({SENTINEL_DATABASE_URI_LOG_SANITIZATION_DRIFT})"
        )
