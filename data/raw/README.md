# `data/raw/`

Was jede Quelle beim Abruf geliefert hat und was der Abruf davon verworfen
hat. Rohantworten lassen sich später nicht mehr abholen; mit diesen Dateien
lässt sich nachträglich klären, ob eine Meldung überhaupt ankam und an
welcher Stelle sie verschwand.

Geschrieben von [`src/utils/raw_capture.py`](../../src/utils/raw_capture.py),
nur wenn `RAW_CAPTURE=1` gesetzt ist (im Workflow
[`update-cycle.yml`](../../.github/workflows/update-cycle.yml), Schritt
„Refresh free-API caches in parallel“). Tests, lokale Builds und der
Health-Check schreiben hier nichts.

| Datei | Producer | Inhalt |
| --- | --- | --- |
| `wl/trafficInfoList.json` | [`src/providers/wl_fetch.py`](../../src/providers/wl_fetch.py) | WL-Antwort `trafficInfoList` (`stoerunglang`, `stoerungkurz`), ohne `message.serverTime`, `trafficInfos` nach `name` sortiert |
| `wl/newsList.json` | ebenda | WL-Antwort `newsList`, ohne `message.serverTime`, `pois` nach `name` sortiert |
| `wl/verworfen.json` | ebenda | vom Abruf verworfene WL-Meldungen mit Grund (Status inaktiv, Kurzmeldung einer erledigten Störung, nur Aufzug/Fahrtreppe, außerhalb des Zeitraums, Ausschluss-Stichwort ohne Einschränkung, kein Einschränkungs-Stichwort) |
| `oebb/rss.json` | [`src/providers/oebb.py`](../../src/providers/oebb.py) | alle `<item>` des ÖBB-RSS mit jedem Kindelement als Text, nach `guid` sortiert; der Kanal-Kopf (`lastBuildDate`) fehlt |
| `oebb/verworfen.json` | ebenda | ÖBB-Meldungen, die der Wien-Filter verwarf |
| `baustellen/BAUSTELLENLINOGD.json`, `baustellen/BAUSTELLENPKTOGD.json` | [`scripts/update_baustellen_cache.py`](../../scripts/update_baustellen_cache.py) | je WFS-Layer alle Features mit ihren Properties ohne `OBJECTID`, von der Geometrie nur Typ und erste Position (`first_position`), nach `OGD_ID` bzw. Titel sortiert; Antwortkopf und Feature-`id` fehlen |
| `baustellen/verworfen.json` | ebenda | Baustellen ohne ÖPNV-Bezug oder ohne Titel und Straße; nur nach einem vollständigen Live-Abruf, nie nach dem Fallback-Sample oder einer letzten guten Antwort |

Ein Stand wird nur für eine brauchbare Antwort geschrieben, er ist also
immer die **letzte gute Antwort** dieses Teils der Quelle. Fällt ein Teil aus
(eine WL-Liste, ein Baustellen-Layer), liest der Abruf ihn von hier, statt
seine Meldungen zu verlieren (`docs/architecture.md`, „Ausfall einer
Quelle“); die `verworfen.json` dieser Quelle bleibt dann unverändert. Ein
Stand, der sich über mehrere Läufe nicht ändert, kann deshalb auch heißen,
dass die Quelle in dieser Zeit ausfiel; das zeigt das Workflow-Log
(Warnung mit Exit-Code 1 oder 3).

Jede Datei wird bei jedem Lauf überschrieben, und nur wenn sich ihr Inhalt
geändert hat. Die Git-Historie ist das Archiv:

```bash
git log --format='%h %ci' -- data/raw/wl/trafficInfoList.json   # alle Stände
git show <commit>:data/raw/wl/trafficInfoList.json                # ein Stand
git log -p -S 'Gleisbauarbeiten' -- data/raw/wl/                   # wann tauchte ein Text auf
```

Warum so und nicht komprimiert: Git speichert von zwei Ständen derselben
Datei nur den Unterschied. Gemessen am 2026-10-03 an einem Monat echter
Historie wuchs das Repo mit gzip-Dateien um das 1,6- bis 146-Fache stärker,
mit einer neuen Datei pro Lauf wuchs das Arbeitsverzeichnis um 140 MB im
Monat. Deshalb: sortierte Schlüssel, ein Wert pro Zeile, Listen in fester
Reihenfolge, keine Felder, die sich bei jedem Aufruf ändern. Eine Datei über
4 MB (`MAX_SNAPSHOT_BYTES`) wird nicht geschrieben, sondern im Log gemeldet.

Was der WL-Abruf zusammenführt statt verwirft (Bündelung nach Linien und
Thema, Anzeigetafel-Kurzmeldungen, Sammel- und Teilmeldungen), steht nicht
in `wl/verworfen.json`; es lässt sich aus `wl/*.json` mit dem Code des
jeweiligen Stands nachrechnen. Seit 2026-10-05 gehört dazu
`data/wl_resolved_tickers.json` desselben Stands: Die Kurzmeldungen einer
erledigten Störung verwirft der Abruf auch dann noch, wenn die erledigte
Meldung selbst nicht mehr in `wl/trafficInfoList.json` steht. Die Verwürfe des Feed-Builds selbst (nach dem Cache) stehen hier nicht; sie
lassen sich aus den Caches unter `cache/` und dem Code des jeweiligen Stands
nachrechnen.
