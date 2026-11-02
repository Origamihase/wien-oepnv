"""Regression tests for silent-direction detection (audit B.1 / B.2).

Audit ``docs/archive/audits/audit-2026-09-13.md`` (§ *Audit Update 19:20*):

**B.1** — the Stammstrecke monitor stopped producing rows for the northbound
direction (``Praterstern``) on 2026-08-14. The cause is upstream and real (a
closure diverting those services off the two trunk platforms at Wien Hbf), not
a code defect, and the direction must stay wired up so the restart is picked up
automatically.

**B.2** — the actual defect: nothing noticed for 30 days. ``_process_tick``
logged the per-direction counts at INFO and returned ``"ok"`` regardless, and
``health_check.py`` did not look at the Stammstrecke ledgers at all, so the
published 30-day figures kept presenting a half-corridor sample as a
whole-corridor one.

The detection rule is deliberately *relative* (a direction is silent only while
a peer direction is demonstrably active) rather than an absolute staleness
threshold. Measured over the healthy period 2026-05-17..08-13 (7589 rows), the
gap between consecutive rows has a median of 0.5 h and a p99 of 3.5-3.7 h, but
reaches 7.8 h (Meidling) and 11.5 h (Praterstern) overnight. A 6 h absolute rule
would have produced 5 false alarms; the relative rule produced none across 2113
hourly samples while still flagging the real outage at all 692 samples after it
began. ``test_quiet_night_in_both_directions_is_not_an_alarm`` pins exactly that
distinction — it is the test that stops someone "simplifying" the rule back into
an absolute threshold.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
