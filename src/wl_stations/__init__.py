"""Wiener Linien station-directory build behind ``scripts/update_wl_stations.py``.

The modules of this package were moved verbatim out of that script (file
line limit, 2026-11-02): ``base`` (lazy ``src`` helper loaders and the
shared logger ``update_wl_stations``), ``ogd_download``, ``ogd_csv``,
``entries``, ``colocated``, ``merge`` and ``reconcile``. The script keeps
its CLI and ``main`` and re-exports every name tests and callers use.
"""
