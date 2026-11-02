"""Helpers for the station-directory refresh orchestrator.

Moved verbatim out of ``scripts/update_all_stations.py`` (which stays the
CLI entry point and re-exports every name):

* :mod:`src.stations_refresh.diff` - before/after diff and Markdown report.
* :mod:`src.stations_refresh.quarantine` - auto-quarantine and exact dedup.
* :mod:`src.stations_refresh.heartbeat` - heartbeat sidecar writer.
"""
