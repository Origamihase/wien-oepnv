# `data/stats/`

Append-only-CSV-Ledger, die von
[`scripts/generate_markdown_stats.py`](../../scripts/generate_markdown_stats.py)
ausgewertet werden.

| Datei | Producer | Schema |
| --- | --- | --- |
| `stammstrecke_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine aggregierte Zeile pro Richtung und Cron-Tick (mittlere Verspätung aller in diesem Tick am Wien Hauptbahnhof beobachteten S-Bahn-Abfahrten) | `timestamp, weekday, hour, direction, delay_minutes` |
| `ausfaelle_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine Zeile pro ausgefallenem S-Bahn-Zug (dedupliziert via Pending-Trip-Identity-Ledger, damit derselbe Zug nicht über mehrere Cron-Ticks hinweg doppelt gezählt wird) | `timestamp, weekday, hour, direction, line` |

`line` ist der Linienname von VAO. Nannte VAO nur die Zugnummer („S28700“, vier oder mehr Ziffern), zählt der Zug in Dashboard, README und `stats-summary.json` unter „ohne Linienangabe“ (`ausfall_line_label` in `scripts/generate_markdown_stats.py`). Zehn solcher Ausfälle vom 27.05. bis 07.07.2026 tragen seit 26.10.2026 die Linie laut Fahrplan (die Linie, die an den übrigen Tagen zur selben Minute in dieselbe Richtung ab Wien Hbf fuhr): S27016 → S2, S28449 und S28280 → S3, S28044 und S28809 → S4, REX2277 → REX3, CJX2904, CJX2917, CJX2932 und CJX2946 → CJX9. S28700 (06.07. 05:57) bleibt, zu dieser Minute fuhren S3 und S2.
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
