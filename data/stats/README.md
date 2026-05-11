# `data/stats/`

Append-only CSV ledgers consumed by
[`scripts/generate_markdown_stats.py`](../../scripts/generate_markdown_stats.py).

| File | Producer | Schema |
| --- | --- | --- |
| `stammstrecke_YYYY.csv` | [`scripts/update_stammstrecke_status.py`](../../scripts/update_stammstrecke_status.py) — appends one row per successful HAFAS median calculation | `timestamp, weekday, hour, direction, delay_minutes` |
| `stoerungen_YYYY.csv` | [`src/build_feed.py:_update_item_state`](../../src/build_feed.py) — appends one row when a strictly new event identity is seen | `timestamp, weekday, hour, provider, location_name` |

All timestamps are ISO 8601 with offset, anchored to `Europe/Vienna`.
One file per calendar year. Files are opened in append mode; rotation
on the year boundary is handled by the writers in
[`src/utils/stats.py`](../../src/utils/stats.py).

Files are committed automatically by
[`update-cycle.yml`](../../.github/workflows/update-cycle.yml)
(IFTTT-triggered ~30-min cadence) alongside the regenerated
[`docs/statistik.md`](../../docs/statistik.md) dashboard. The
dashboard refresh inside that workflow is gated to the first cycle
tick after midnight Europe/Vienna; the underlying CSV ledger
commits on every cycle tick.
