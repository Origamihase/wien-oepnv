"""Detection patterns of :mod:`src.utils.secret_scanner`.

Moved verbatim out of ``src/utils/secret_scanner.py`` on 2026-11-02 (700-line
rule): ``detectors`` holds the generic and HTTP auth-scheme regexes, the
``tokens_*`` modules hold consecutive parts of ``_KNOWN_TOKENS``, which the
facade joins in their original order.
"""
