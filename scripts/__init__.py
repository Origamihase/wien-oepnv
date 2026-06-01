"""Maintenance and CI helper scripts, exposed as the ``scripts`` package.

This file intentionally carries no runtime logic. Its sole purpose is to
make ``scripts/`` a regular package so that

* the test-suite can import helpers as ``scripts.<name>`` (the convention
  already used throughout ``tests/``), and
* ``mypy --strict`` can resolve every module under a single, unambiguous
  ``scripts.<name>`` name. Without it, mypy walks the directory as a set
  of top-level modules *and* sees the same files imported as
  ``scripts.<name>`` from the tests, aborting with
  "Source file found twice under different module names".

Mirrors the (empty) ``tests/__init__.py`` marker.
"""
