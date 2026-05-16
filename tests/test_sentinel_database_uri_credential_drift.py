"""Sentinel PoC: silent-undetection drift for database connection
strings with embedded credentials (``<scheme>://<user>:<pass>@<host>``).

Pre-fix every database URI with credentials in the canonical
``<scheme>://<user>:<password>@<host>`` shape — covering PostgreSQL,
MySQL/MariaDB, MongoDB (+srv), Redis, AMQP/AMQPS (RabbitMQ), Kafka,
ClickHouse, Cassandra, ElasticSearch, plus the JDBC-prefixed variants —
was **SILENTLY UNDETECTED ENTIRELY** by the secret scanner across BOTH
detection branches:

1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): the regex body alphabet
   ``[A-Za-z0-9+/=_-]`` excludes ``:``, ``/``, ``@``, so the entropy
   matcher splits at every URI delimiter. The resulting fragments
   (``postgres``, ``admin``, ``secret123``, ``db.example.com``,
   ``5432``, ``prod``) are each below the 24-char floor, so no
   entropy finding fires for ANY part of the URI.

2. **Assignment heuristic** (``_SENSITIVE_ASSIGN_RE``): even with a
   sensitive variable name (``DATABASE_URL``, ``MONGO_URI``,
   ``REDIS_URL``), the unquoted-value branch SKIPS values containing
   ``()[]:`` characters — and every database URI contains ``://``
   AND ``:`` (port number / user-pass separator). The check at
   ``src/utils/secret_scanner.py:_scan_content`` rejects the URI
   verbatim, so the assignment heuristic produces ZERO findings.

The combined effect: a committed ``.env`` file with
``DATABASE_URL=postgres://admin:supersecret@prod-db.example.com:5432/prod``
ships to production with NO scanner alert at any stage.

Threat model
------------

Database connection strings with embedded credentials are arguably the
HIGHEST-VALUE secrets in any application's source tree. The leak
surface:

* **Plaintext password recovery**: the URI password is base64-decodable
  trivially — no offline cracking needed. An attacker reading the
  committed source has the password in plain text.

* **Production data access**: the URI typically targets the production
  database. The attacker gains read AND write access to all customer
  data, billing records, session stores, audit logs.

* **Lateral movement amplifier**: cracked credentials often work for
  related infrastructure (admin web UIs, replica databases, backup
  storage) due to shared password reuse.

* **Schema reconnaissance**: even read-only access via SELECT queries
  yields the full schema, table relationships, and data sample for
  social-engineering preparation.

* **Persistence amplifier**: an attacker can INSERT a backdoor user
  / admin token / persistence record into the database that survives
  later password rotation. Mitigated by full audit-log review after
  credential rotation.

Per-scheme severity:

* **PostgreSQL / MySQL / MariaDB**: HIGH — relational databases
  typically store the application's primary data plane.
* **MongoDB (+srv)**: HIGH — NoSQL primary data; the ``mongodb+srv``
  variant uses DNS SRV records for cluster discovery (Atlas
  default).
* **Redis**: MEDIUM-HIGH — session store / cache leak enables session
  hijacking; some applications use Redis for queues containing
  sensitive job payloads.
* **AMQP / AMQPS**: MEDIUM-HIGH — message broker access enables
  reading message queues that may contain PII / billing data /
  internal RPC calls.
* **Kafka**: HIGH — event streams often carry the full audit log
  and downstream analytics ingest.
* **ClickHouse / Cassandra / ElasticSearch**: HIGH — analytics
  warehouses with full historical data.
* **SMTP / SMTPS**: MEDIUM — email-sending creds enable phishing
  amplification from the victim's authenticated sender.
* **JDBC-prefixed**: same as underlying scheme; the ``jdbc:`` prefix
  is just the Java client convention.

Real-world emission patterns
----------------------------

* ``.env`` files committed to source: ``DATABASE_URL=postgres://...``
* ``docker-compose.yml`` with hardcoded ``environment:`` entries
* Heroku ``app.json`` defaults with ``DATABASE_URL`` literal
* Settings files (``settings.py``, ``application.yml``,
  ``config/database.yml``) with embedded credentials
* Python notebook outputs printing the env (``os.environ``)
* Documentation README snippets with example URIs (often live!)
* Migration scripts (``alembic.ini``, Rails ``database.yml``)
* CI/CD workflow files with hardcoded fallback URIs
* Kubernetes ConfigMap manifests
* Terraform output blocks revealing DB credentials

Fix
---

Add a single regex to ``_KNOWN_TOKENS`` that matches the canonical
``[jdbc:]<scheme>://<user>:<pass>@<host>`` shape for the top database
/ broker schemes::

    _DATABASE_URI_RE = re.compile(
        r"(?i)\b(?:jdbc:)?"
        r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\\+srv)?|redis|"
        r"amqp|amqps|kafka|clickhouse|cassandra|elasticsearch|smtp|smtps)"
        r"://[^@\\s/:]+:[^@\\s/]+@[^\\s/]+"
    )

The regex anchors on:
  * Optional ``jdbc:`` prefix (Java convention)
  * A specific database/broker scheme literal (case-insensitive)
  * ``://`` separator
  * ``[^@\\s/:]+:`` user (non-empty, no ``@``, whitespace, ``/``, or
    ``:``)
  * ``[^@\\s/]+@`` password (non-empty, no ``@``, whitespace, or
    ``/``)
  * ``[^\\s/]+`` host

The structural requirement ``user:pass@`` prevents matching URIs
without credentials (e.g., ``postgres://localhost/db`` does NOT
match — the URI is credential-less and benign). Natural-language
mentions of database names are not affected because they don't have
the ``://user:pass@`` structure.

Marker: SENTINEL_DATABASE_URI_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_DATABASE_URI_DRIFT = (
    "Database connection string credential silent-undetection drift: "
    "entropy fallback splits at URI delimiters AND assignment "
    "heuristic skips values containing ``:``; both branches produce "
    "zero findings for committed credentials."
)

_DB_REASON = "Database Connection String gefunden"


# Realistic database URIs per scheme (each contains plaintext-recoverable
# password embedded in the canonical URI shape):
_POSTGRES_URI = "postgres://admin:secret123@db.example.com:5432/prod"
_POSTGRESQL_URI = "postgresql://app_user:p4ssw0rd!@db-master.example.com/myapp"
_MYSQL_URI = "mysql://root:rootpw@mysql.example.com:3306/wordpress"
_MARIADB_URI = "mariadb://admin:mariapw@mariadb.example.com/app"
_MONGODB_URI = "mongodb://dbuser:mongopw@mongo.example.com:27017/myapp"
_MONGODB_SRV_URI = "mongodb+srv://cluster_user:atlaspw@cluster0.abc.mongodb.net/prod"
_REDIS_URI = "redis://default:secretpw@redis.example.com:6379/0"
_AMQP_URI = "amqp://guest:guestpw@rabbit.example.com:5672/vhost"
_AMQPS_URI = "amqps://prod_user:rabbitsecret@rabbit.example.com:5671/prod"
_KAFKA_URI = "kafka://kafka_user:streampw@broker.example.com:9092"
_CLICKHOUSE_URI = "clickhouse://default:chpw@clickhouse.example.com:9000/analytics"
_CASSANDRA_URI = "cassandra://app_user:casspw@cassandra.example.com:9042/keyspace"
_ELASTICSEARCH_URI = "elasticsearch://elastic:elasticpw@es.example.com:9200"
_SMTP_URI = "smtps://noreply@example.com:smtp_pw@smtp.gmail.com:465"
_JDBC_PG_URI = "jdbc:postgresql://admin:jdbcpw@db.example.com/prod"
_JDBC_MYSQL_URI = "jdbc:mysql://root:jdbc_mysql_pw@mysql.example.com:3306/app"


# ---------------------------------------------------------------------------
# (1) Per-scheme attribution PoCs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri,label",
    [
        (_POSTGRES_URI, "PostgreSQL"),
        (_POSTGRESQL_URI, "PostgreSQL (full name)"),
        (_MYSQL_URI, "MySQL"),
        (_MARIADB_URI, "MariaDB"),
        (_MONGODB_URI, "MongoDB"),
        (_MONGODB_SRV_URI, "MongoDB+SRV (Atlas)"),
        (_REDIS_URI, "Redis"),
        (_AMQP_URI, "AMQP (RabbitMQ)"),
        (_AMQPS_URI, "AMQPS (RabbitMQ TLS)"),
        (_KAFKA_URI, "Kafka"),
        (_CLICKHOUSE_URI, "ClickHouse"),
        (_CASSANDRA_URI, "Cassandra"),
        (_ELASTICSEARCH_URI, "ElasticSearch"),
        (_JDBC_PG_URI, "JDBC PostgreSQL"),
        (_JDBC_MYSQL_URI, "JDBC MySQL"),
    ],
)
def test_database_uri_with_credentials_detected(
    tmp_path: Path, uri: str, label: str
) -> None:
    """Every canonical database connection string with embedded
    credentials must be detected as ``Database Connection String
    gefunden``. Pre-fix every URI was SILENTLY UNDETECTED across
    both the entropy fallback (delimiter-split prevents 24-char
    floor match) AND the assignment heuristic (``:`` in value
    triggers skip).
    """
    file_path = tmp_path / ".env"
    file_path.write_text(f"DATABASE_URL={uri}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert _DB_REASON in reasons, (
        f"{label}: URI did not yield Database Connection String "
        f"attribution; got reasons {reasons!r}. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Case-insensitive scheme matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "POSTGRES://admin:pw@host/db",
        "Postgres://admin:pw@host/db",
        "PostgreSQL://admin:pw@host/db",
        "MYSQL://root:rootpw@host/app",
        "MongoDB://user:pw@host/db",
        "REDIS://default:pw@host:6379",
    ],
)
def test_database_uri_case_insensitive_scheme(
    tmp_path: Path, uri: str
) -> None:
    """The scheme literal must match case-insensitively (HTTP URI
    schemes are case-insensitive per RFC 3986 §3.1)."""
    file_path = tmp_path / "config.env"
    file_path.write_text(f"DB={uri}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert _DB_REASON in reasons, (
        f"Case-insensitive scheme {uri!r} did not match. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the detector does NOT match benign URIs
#     or non-credentialled connection strings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No credentials in URI (benign)
        "postgres://localhost/db",
        "postgres://localhost:5432/db",
        "postgres://host.example.com/db",
        "mysql://mysql.example.com/wordpress",
        "mongodb://cluster.example.com/myapp",
        # Just user, no password (benign-ish; the user-only form is
        # unusual but not always a credential leak)
        "postgres://admin@host/db",
        # Natural-language mentions
        "We use PostgreSQL with MySQL fallback.",
        "Connect to mongodb using your credentials.",
        "Run the postgres database container.",
        # HTTP URI with embedded credentials (covered by URL auth
        # detection separately, not by the database URI detector)
        "https://admin:pw@api.example.com/v1",
        # Different scheme prefix (not in our list)
        "ssh://user@host",
        "git+ssh://user@github.com/repo.git",
        "sftp://user@host",
        # Empty user or password (technically valid URI but not a
        # credential leak in the meaningful sense)
        "postgres://:password@host/db",  # empty user
        "postgres://user:@host/db",  # empty password
    ],
)
def test_no_false_positive_on_benign_uris(
    tmp_path: Path, text: str
) -> None:
    """The detector must not flag credential-less URIs, non-database
    schemes, natural-language prose, or HTTP URIs (which have their
    own URL-credential detection path).
    """
    file_path = tmp_path / "config.env"
    file_path.write_text(f"{text}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    db_findings = [f for f in findings if f.reason == _DB_REASON]
    assert not db_findings, (
        f"False-positive Database Connection String for {text!r}; "
        f"got findings {db_findings!r}. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Bare URI without variable assignment context
# ---------------------------------------------------------------------------


def test_database_uri_in_comment(tmp_path: Path) -> None:
    """A database URI in a code comment (e.g., README curl example,
    inline doc) must still be flagged."""
    file_path = tmp_path / "README.md"
    file_path.write_text(
        f"## Connecting to the database\n\n"
        f"Set your `DATABASE_URL` env var to {_POSTGRES_URI}\n",
        encoding="utf-8",
    )
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert _DB_REASON in reasons, (
        f"URI in README/comment context not detected. reasons={reasons!r}. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Multi-DB config file PoC: a realistic ``.env`` with multiple
#     database credentials must yield separate findings for each.
# ---------------------------------------------------------------------------


def test_multi_db_env_file_yields_separate_findings(tmp_path: Path) -> None:
    """A real-world ``.env`` file with multiple database URIs must
    flag each as a separate Database Connection String finding.
    The threat model: a single committed ``.env`` leaks ALL
    embedded database credentials in one shot."""
    file_path = tmp_path / ".env.production"
    file_path.write_text(
        f"DATABASE_URL={_POSTGRES_URI}\n"
        f"MONGO_URI={_MONGODB_SRV_URI}\n"
        f"REDIS_URL={_REDIS_URI}\n"
        f"RABBITMQ_URL={_AMQPS_URI}\n",
        encoding="utf-8",
    )
    findings = scan_repository(tmp_path, paths=[file_path])
    db_findings = [f for f in findings if f.reason == _DB_REASON]
    assert len(db_findings) >= 4, (
        f"Multi-DB ``.env`` should yield at least 4 Database Connection "
        f"String findings; got {len(db_findings)}. "
        f"All findings: {findings!r}. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Membership invariant
# ---------------------------------------------------------------------------


def test_database_uri_reason_in_known_tokens() -> None:
    """The Database Connection String reason must be in
    ``_KNOWN_TOKENS``."""
    from src.utils.secret_scanner import _KNOWN_TOKENS

    reasons = {reason for _regex, reason in _KNOWN_TOKENS}
    assert _DB_REASON in reasons, (
        f"Database Connection String reason missing from _KNOWN_TOKENS. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Cross-detector regression: existing detectors continue to fire
# ---------------------------------------------------------------------------


def test_anthropic_detection_still_works(tmp_path: Path) -> None:
    """Adding the database URI detector must NOT break Anthropic."""
    anthropic = "sk-ant-api03-" + "A" * 95 + "B"
    file_path = tmp_path / "ai.py"
    file_path.write_text(f'KEY = "{anthropic}"\n', encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    assert "Anthropic API Key gefunden" in [f.reason for f in findings], (
        f"Regression: Anthropic broke. ({SENTINEL_DATABASE_URI_DRIFT})"
    )


def test_pem_private_key_still_detected(tmp_path: Path) -> None:
    """PEM private key detection must NOT be affected by the new
    detector."""
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        + "A" * 64
        + "\n-----END PRIVATE KEY-----\n"
    )
    file_path = tmp_path / "key.pem"
    file_path.write_text(pem, encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    assert "Private Key (PEM) gefunden" in [f.reason for f in findings], (
        f"Regression: PEM detection broke. "
        f"({SENTINEL_DATABASE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) Masking contract
# ---------------------------------------------------------------------------


def test_database_uri_masking_contract(tmp_path: Path) -> None:
    """The raw URI (with embedded password) must NEVER appear unmasked
    in finding output. The password is the canonical-recoverable
    secret; leaving it in CI logs / PR comments would defeat the
    purpose of detection."""
    file_path = tmp_path / ".env"
    file_path.write_text(f"DATABASE_URL={_POSTGRES_URI}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    for finding in findings:
        if finding.reason == _DB_REASON:
            assert "secret123" not in finding.match, (
                f"Masking VIOLATED: raw password 'secret123' appears in "
                f"finding.match={finding.match!r}. "
                f"({SENTINEL_DATABASE_URI_DRIFT})"
            )
