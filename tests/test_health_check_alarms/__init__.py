"""The paths by which an outage reaches the operator: a red health check.

A red run is what makes GitHub mail the maintainer, so every rule that turns
a check red, and ``main()``'s exit code, needs a test that fails when the rule
breaks. The test-suite audit of 2026-10-28 broke seven rules one at a time:
five left the whole suite green (a failed fetch, exit 1, read as OK; a feed
without a new build for 4 h; a station run older than 8 days or with a failed
sub-script; ``main()`` returning 0 with a red check). Both red health checks
between 14.10. and 28.10.2026 (Wiener Linien 21.10., ÖBB 27.10.) were exit 1.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
