# `data/stats/`

Append-only-CSV-Ledger, die von
[`scripts/generate_markdown_stats.py`](../../scripts/generate_markdown_stats.py)
ausgewertet werden.

| Datei | Producer | Schema |
| --- | --- | --- |
| `stammstrecke_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine aggregierte Zeile pro Richtung und Cron-Tick (mittlere Verspätung aller in diesem Tick am Wien Hauptbahnhof beobachteten S-Bahn-Abfahrten) | `timestamp, weekday, hour, direction, delay_minutes` |
| `ausfaelle_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine Zeile pro ausgefallenem S-Bahn-Zug (dedupliziert via Pending-Trip-Identity-Ledger, damit derselbe Zug nicht über mehrere Cron-Ticks hinweg doppelt gezählt wird, und je Abfahrt: ein Eintrag mit Zugnummer zur Minute eines Linieneintrags derselben Richtung ist derselbe Zug) | `timestamp, weekday, hour, direction, line` |
| `stoerungen_YYYY.csv` | [`src/build_feed.py:_update_item_state`](../../src/build_feed.py) — hängt eine Zeile an, sobald ein Auftreten einer Meldung zum ersten Mal im Feed steht: eine neue Identität oder eine wiederkehrende WL-Meldung, die `_restart_recurring_occurrences` als neues Auftreten erkannt hat | `timestamp, weekday, hour, provider, location_name` |

`line` ist der Linienname von VAO. Nannte VAO nur die Zugnummer (vier oder mehr Ziffern, etwa „S28624“), zählt der Zug in Dashboard, README und `stats-summary.json` unter „ohne Linienangabe“ (`ausfall_line_label` in `scripts/generate_markdown_stats.py`). Vom 19.05. bis 07.07.2026 führte die VAO-Abfahrtstafel viele Züge doppelt, unter der Linie („S2“) und unter der Zugnummer, zur selben Minute in dieselbe Richtung. Weil die Identität den Namen enthält, zählten beide; seit 26.10.2026 steht der Eintrag mit der Linie für den Zug, und der Zwilling mit der Zugnummer fällt weg (`_without_train_number_twins` in `scripts/update_stammstrecke_hbf.py`). Neun Ausfallzeilen dieser Zeit waren solche Zwillinge und sind gestrichen: sechs Züge standen doppelt im Ledger (18.06. 11:32 S3, 30.06. 23:39 REX3, 04.07. 20:32 S2, 05.07. 16:27 S2, 07.07. 14:03 und 17:33 CJX9), drei fuhren laut ihrem Linieneintrag (27.05. 14:57 S4, 05.07. 08:23 CJX9, 06.07. 05:57 S2). Zwei Zugnummern ohne Zwilling tragen die Linie laut Fahrplan (die Linie, die an den übrigen Tagen zur selben Minute in dieselbe Richtung ab Wien Hbf fuhr): S27016 → S2 (04.07. 00:45), CJX2904 → CJX9 (04.07. 07:03).

Eine WL-guid trägt kein Datum: „U6: Rettungseinsatz“ am 25.10. hat die guid
des Einsatzes vom 23.10. Seit 25.09.2026 stellt der Feed eine solche Meldung
als neues Auftreten nach vorn, `stoerungen_2026.csv` zählte sie aber nicht,
weil die guid schon einen Eintrag hatte. Vom 25.09. bis 27.10.2026 erreichten
so 1.060 Auftreten den Feed ohne Zeile; die alte Regel zählte im selben
Nachbau 348. Diese 1.060
Zeilen sind nachgetragen (Entscheidung Michael 27.10.2026 22:04 MEZ: „Nachtragen
aus der Historie“): Nachbau aller 1.690 Feed-Läufe mit ihrem echten Cache, der
Zustand jeweils aus dem Lauf davor, einmal mit alter und einmal mit neuer
Zählregel; jede Zeile trägt die Bauzeit des Laufs, in dem das Auftreten zum
ersten Mal im Feed stand, und den Ort, den `extract_location_name` dort
liefert. Feed und Zustand waren in allen Läufen gleich, nur die Zeilen kamen
hinzu.

In `stammstrecke_2026.csv` fehlt die Tickzeile vom 08.06.2026 20:30 Richtung
Praterstern (395,33 Minuten): VAO gab drei Zügen eine Echtzeit am Folgetag
(1160 bis 1199 Minuten). Seit 27.10.2026 gilt eine Echtzeit zwölf Stunden oder
mehr neben dem Plan als keine Echtzeit (`IMPLAUSIBLE_DELAY` in
`scripts/update_stammstrecke_hbf.py`); die Lesungen der übrigen Züge dieses
Ticks lassen sich nicht wiederherstellen.

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
