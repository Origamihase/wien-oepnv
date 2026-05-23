# `data/stats/`

Append-only-CSV-Ledger, die von
[`scripts/generate_markdown_stats.py`](../../scripts/generate_markdown_stats.py)
ausgewertet werden.

| Datei | Producer | Schema |
| --- | --- | --- |
| `stammstrecke_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine aggregierte Zeile pro Richtung und Cron-Tick (mittlere Verspätung aller in diesem Tick am Wien Hauptbahnhof beobachteten S-Bahn-Abfahrten) | `timestamp, weekday, hour, direction, delay_minutes` |
| `ausfaelle_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine Zeile pro ausgefallenem S-Bahn-Zug (dedupliziert via Pending-Trip-Identity-Ledger, damit derselbe Zug nicht über mehrere Cron-Ticks hinweg doppelt gezählt wird) | `timestamp, weekday, hour, direction, line` |
| `stoerungen_YYYY.csv` | [`src/build_feed.py:_update_item_state`](../../src/build_feed.py) — hängt eine Zeile an, sobald eine strikt neue Event-Identity gesehen wird | `timestamp, weekday, hour, provider, location_name` |

Alle Zeitstempel sind ISO-8601 mit Offset, verankert auf `Europe/Vienna`.
Eine Datei pro Kalenderjahr. Die Dateien werden im Append-Modus geöffnet;
die Rotation an der Jahresgrenze übernehmen die Writer in
[`src/utils/stats.py`](../../src/utils/stats.py).

Die Dateien werden vom Workflow
[`update-cycle.yml`](../../.github/workflows/update-cycle.yml)
(IFTTT-getriggert, ca. 30-Min-Cadence) automatisch zusammen mit dem
regenerierten Dashboard [`docs/statistik.md`](../../docs/statistik.md)
committet. Die Dashboard-Regeneration innerhalb des Workflows ist
auf den ersten Cycle-Tick nach Mitternacht `Europe/Vienna` gekoppelt;
die zugrundeliegenden CSV-Ledger werden bei jedem Cycle-Tick committet.
