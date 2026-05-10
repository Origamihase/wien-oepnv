# S-Bahn Stammstrecke Provider Logic

Das Stammstrecken-Verspätungs-Monitoring überwacht die zentrale
Pendlerachse Wien Floridsdorf ↔ Wien Meidling auf signifikante
Verspätungen und emittiert daraus dynamisch Feed-Events. Die Logik
verteilt sich auf zwei Module:

| Komponente | Verantwortlichkeit |
| :--- | :--- |
| `scripts/update_stammstrecke_status.py` | Cron-Producer: fragt alle 30 Min die VOR/VAO `/trip`-API ab und hängt eine Beobachtungs-Zeile an `data/stats/stammstrecke_<YYYY>.csv` (CSV-Ledger). |
| `src/feed/stammstrecke.py` | Feed-Renderer: liest die zuletzt geschriebenen Zeilen, bestimmt für jede Richtung über ein 1-Stunden-Fenster den Median der Verspätung und erzeugt — ggf. — einen schema-konformen FeedItem. |

Diese Trennung wurde am 2026-05-09 eingeführt (zuvor schrieb der
Cron-Job direkt in `cache/stammstrecke/events.json`). Sie hat zwei
Vorteile: (a) das CSV-Ledger ist die einzige Quelle der Wahrheit
sowohl für den RSS-Feed (1-Stunden-Fenster) als auch für das
30-Tage-Dashboard in `docs/statistik.md`; (b) die Schwellen-Logik
lebt jetzt im Feed-Builder und wird für jeden Build neu ausgewertet —
ein nachträglich angepasster Threshold wirkt sofort, ohne dass der
Cron-Job neu laufen muss.

## Migration: pyhafas → VOR/VAO ReST API (2026-05-09)

Der Monitor wurde ursprünglich gegen den öffentlichen ÖBB-`mgate.exe`-
Endpunkt via [`pyhafas`](https://pypi.org/project/pyhafas/)
(`OEBBProfile`) implementiert. Das 2026-05-09 Audit zeigte, dass
`OEBBProfile` in keiner auf PyPI veröffentlichten `pyhafas`-Version
exportiert wird (der Import schlug seit Wochen still fehl, das Skript
hat in der Folge nie eine CSV-Zeile geschrieben). Der Monitor wurde
auf die im Projekt bereits etablierte VOR/VAO-Infrastruktur portiert
— gleicher authentifizierter Session-Stack, gleicher Quota-Counter,
gleicher `fetch_content_safe`-HTTP-Layer wie die Disruption-Provider.

Im selben Schritt wurde das alte JSON-Cache-Layout entsorgt: der Cron-
Job schreibt nur noch eine **Beobachtungs-Zeile** pro Lauf und
Richtung in das CSV-Ledger; die Threshold- und First-Seen-Logik
wurde nach `src/feed/stammstrecke.py` verschoben und arbeitet zur
Build-Zeit auf den Ledger-Zeilen.

## Cron-Producer (`scripts/update_stammstrecke_status.py`)

### Datenquelle und Abfrage

| Aspekt | Wert |
| :--- | :--- |
| Endpoint | `${VOR_BASE_URL}trip` (offizielle VOR/VAO ReST API) |
| HTTP-Layer | `src.utils.http.fetch_content_safe` (SSRF-Guard, Slowloris-Cap, MAX_PAYLOAD_SIZE) |
| Auth | `accessId` als Query-Parameter via `vor_provider.VorAuth` |
| Richtung 1 | Wien Floridsdorf (`490033400`) → Wien Meidling (`490101500`) |
| Richtung 2 | Wien Meidling (`490101500`) → Wien Floridsdorf (`490033400`) |
| `maxChange` | `0` (nur direkte S-Bahn-Verbindungen) |
| `numF` | `6` (`MAX_TRIPS_PER_QUERY`, VAO-Cap; eine möglichst große Median-Stichprobe pro Richtung) |
| `rtMode` | `SERVER_DEFAULT` (Echtzeitdaten aktivieren) |
| Cron | `0,30 * * * *` (alle 30 Minuten, läuft als Schritt im `update-cycle.yml` sowie eigenständig in `update-stammstrecke-status.yml`) |

**Beide Richtungen werden strikt getrennt ausgewertet.** Eine
Zusammenlegung würde die Daten verfälschen — eine Störung in eine
Richtung läuft häufig in der Gegenrichtung normal weiter.

### Filter und Aggregation pro Richtung

Aus den zurückgegebenen `Trip`-Objekten werden alle Trips ausgewählt,
die genau **einen Ride-Leg** enthalten (Walk-Vor-/Nachläufe sind
toleriert, Mehrteil-Verbindungen mit Umstieg werden verworfen) und
deren Ride-Leg ein S-Bahn-Produkt ist.

**S-Bahn-Erkennung** (`_is_sbahn_leg`) — drei orthogonale Signale, jedes
einzelne reicht:

1. `leg.category in {"S", "SB"}` — VAOs bevorzugtes Feld.
2. `leg.name` matched `^\s*S\s*\d+\s*$` (z. B. `S 1`, `S 7`, `S 80`)
   — Fallback für ältere VAO-Peers, die nur das Anzeige-Label setzen.
3. `leg.Product[].catOut` / `Product[].line` — JSON-RPC-verschachtelte
   Form, die manche VAO-Releases verwenden.

Andere Verkehrsmittel auf den gleichen Gleisen (`REX`, `R`, `IC`,
`Railjet`) werden verworfen — sie sind kein Stammstrecken-Produkt.

**Verspätungs-Berechnung** (`_leg_departure_delay_minutes`) —
Differenz `Origin.rtTime − Origin.time` in Minuten. Aus den
verbleibenden Legs wird der **Median** der Delay-Werte gebildet,
separat für jede Richtung:

* Stornierte Legs (`leg.cancelled` oder `Origin.cancelled`) werden
  vom Median ausgeschlossen (kein Signal).
* Legs ohne `Origin.rtTime` werden ebenfalls ausgeschlossen — `0`
  einzusetzen würde den Median nach unten ziehen.
* Pures `0`-Delay aus tatsächlich verspätungsfreien Fahrten zählt
  vollwertig.

Der berechnete Median pro Richtung wird als eine Zeile an
`data/stats/stammstrecke_<YYYY>.csv` angehängt (Spalten:
`timestamp, weekday, hour, direction, delay_minutes`, ISO 8601
`Europe/Vienna`). **Es werden keine Schwellenwerte oder Events mehr
direkt vom Cron-Skript erzeugt** — die Render-Phase arbeitet
ausschließlich auf den Ledger-Zeilen.

### Resilience und API Rate-Limit

Die VOR/VAO API erlaubt **100 Requests pro Tag** (das `VAO Start`-
Kontingent). Der Monitor ist mit Abstand der dominante VOR-Konsument:

| Komponente | Calls/Tag |
| :--- | ---: |
| Stammstrecke (`0,30 * * * *` × 2 Richtungen) | 96 |
| Station-Enrichment (wöchentlich, Stammstrecke-Whitelist) | ~10 (1× pro Woche) |
| Disruption-Polling (`DEFAULT_MONITOR_WHITELIST=""`) | 0 |
| **Tagesbudget gesamt** | **96 / 100** |

Der Producer charged ein Quota-Slot **vor** jedem Network Call via
`_charge_one_request`; eine quota-erschöpfte Stunde produziert
sauber `_QuotaExceeded`, ohne den Endpoint zu treffen.

Die Circuit-Breaker-Konfiguration deckelt zusätzlich:

* `failure_threshold = 10` — nach 10 aufeinanderfolgenden Fehlern
  wechselt der Breaker in den OPEN-Zustand.
* `recovery_timeout = 3600.0` (1 Stunde) — der Breaker bleibt eine
  Stunde lang OPEN, bevor ein Probe-Call zugelassen wird.

Im Normalbetrieb produziert die Pipeline durch den Cron-Plan
(`0,30 * * * *` = 2 Ausführungen pro Stunde) und 2 Richtungen pro
Ausführung **4 Calls pro Stunde** — komfortabel unter dem Limit.

Weitere Schutzmechanismen:

* **HTTP-Timeout via `fetch_content_safe`**: Per-Call `timeout=20`
  (clamped auf `MAX_QUERY_TIMEOUT=30`) wird vom HTTP-Layer direkt
  an `requests.Session.get` durchgereicht — kein Patching der Session
  mehr notwendig wie noch in der pyhafas-Ära.
* **CircuitBreakerOpen** kurzschließt nach erstem Auftreten innerhalb
  einer Iteration: wenn der Breaker während der Abarbeitung der ersten
  Richtung öffnet, wird die zweite Richtung *nicht* mehr versucht.
  Bereits gesammelte CSV-Zeilen der ersten Richtung sind über den
  best-effort-`append_stammstrecke_row`-Pfad bereits geschrieben.
* **Per-Direction-Fehlerisolation**: ein transienter Fehler bei
  Richtung 1 (RequestException, Connection Reset etc.) wirft Richtung
  2 *nicht* weg.
* **Atomares Schreiben** für CSV-Zeilen ist nicht nötig: einzelne
  Append-Schreibvorgänge unterhalb des `PIPE_BUF`-Limits (4 KiB) sind
  unter POSIX atomar; der Renderer toleriert zudem fehlerhafte
  Einzelzeilen über `csv.reader` + `fromisoformat`-Try/Except.
* **Logging** (`src.feed.logging_safe.setup_script_logging` →
  `SafeFormatter`): Jede Diagnose-Nachricht wird durch das projekt-
  weite Sanitisierungsverfahren (Secret-Redaktion, ANSI-/BiDi-Stripping,
  Log-Injection-Escaping) geleitet, bevor sie auf stderr / in den
  Action-Log fließt. **Wichtig:** Exception-Strings werden bewusst
  *nicht* via `%s` oder `exc_info=True` geloggt — `VorAuth` injiziert
  `accessId` in jede prepared-Request-URL, und `RequestException`
  kann die URL im Message tragen.
* **Zeitzone** (`zoneinfo.ZoneInfo("Europe/Vienna")`): GitHub Actions
  läuft in UTC. Sowohl die Anfrage-Zeit (`date=` / `time=` Parameter)
  als auch der CSV-`timestamp` werden konsequent auf Europe/Vienna
  ausgerichtet.
* **JSON-Depth-Bomb-Schutz**: `_query_trips` umschließt
  `json.loads` mit `except (ValueError, RecursionError)` und
  re-raised als `ValueError` — eine deeply-nested-aber-wohlgeformte
  Antwort vom Upstream landet damit im pro-Richtungs-Error-Branch
  statt eine `BaseException`-getriebene Recursion-Failure aus dem
  Skript zu propagieren.

## Feed-Renderer (`src/feed/stammstrecke.py`)

Die Funktion `compute_stammstrecke_events` wird vom Feed-Builder über
`src.feed.providers.read_cache_stammstrecke()` aufgerufen (Provider-
Flag `STAMMSTRECKE_ENABLE`, default `True`). Sie erzeugt die FeedItems
ausschließlich aus den jüngsten CSV-Zeilen, ohne irgendeinen JSON-
Cache zu lesen oder zu schreiben.

### Konstanten

| Name | Wert | Bedeutung |
| :--- | :--- | :--- |
| `DELAY_THRESHOLD_MINUTES` | `9.0` | Median > 9 Minuten innerhalb des Feed-Fensters → Event wird emittiert. |
| `FEED_WINDOW` | `1 h` | Zeitfenster (rückwirkend von `now`), in dem der Trigger-Median berechnet wird. |
| `EPISODE_LOOKBACK` | `6 h` | Max. Rückblick zur Bestimmung des `first_seen`-Zeitpunkts der laufenden Episode. |
| `EPISODE_GAP_TOLERANCE` | `70 min` | Maximale Lücke zwischen zwei Beobachtungen, ehe eine Episode als beendet gilt (deckt einen ausgefallenen Cron-Tick ab). |
| `EVENT_SOURCE` | `"VOR/VAO"` | `source`-Feld der emittierten Events (vorher `"ÖBB"`; geändert mit der Migration, weil die Datenquelle inzwischen technisch die VOR/VAO-API ist). |
| `EVENT_CATEGORY` | `"Störung"` | RSS-`category`-Feld. |
| `EVENT_TITLE` | `"S-Bahn Stammstrecke Verspätungen"` | konstantes Item-Title. |
| `EVENT_LINK` | `https://www.wienerlinien.at/web/wienerlinien/oeffis-stoerungen-strecke` | weiterführender Link für Feed-Reader. |

### Trigger-Gate, Anzeigewert, First-Seen

Pro Richtung und pro Build:

1. Lade alle CSV-Zeilen aus dem `EPISODE_LOOKBACK`-Fenster (6 Stunden).
2. Filtere auf das jüngste `FEED_WINDOW` (1 Stunde) — typischerweise
   2 Beobachtungen.
3. **Trigger**: liegt der **Median** der Verspätungswerte in dieser
   Untermenge **strikt** über `DELAY_THRESHOLD_MINUTES`, wird ein
   Event emittiert. Median statt Mean: ein einzelner Ausreißer kann
   die Richtung nicht alleine ins Event drücken.
4. **Anzeigewert**: das Event-`description`-Feld zeigt den **Mittelwert**
   (`mean`) derselben Untermenge — für End-Nutzer:innen leichter
   interpretierbar als der Median.
5. **`first_seen`**: gehe vom jüngsten Above-Threshold-Sample im
   6-Stunden-Lookback rückwärts und nimm den frühesten Zeitpunkt einer
   zusammenhängenden Folge mit `> DELAY_THRESHOLD_MINUTES` und
   Sample-Abstand `≤ EPISODE_GAP_TOLERANCE` als Episode-Start. Dieser
   Wert geht in `starts_at`, `first_seen`, `guid`-Hash und das
   `Seit DD.MM.YYYY`-Datum ein.

### Event-Schema

```json
{
  "source": "VOR/VAO",
  "category": "Störung",
  "title": "S-Bahn Stammstrecke Verspätungen",
  "description": "Durchschnittliche Verspätung von 12.5 Minuten in Richtung Meidling [Seit 09.05.2026]",
  "link": "https://www.wienerlinien.at/web/wienerlinien/oeffis-stoerungen-strecke",
  "guid": "<sha256(stammstrecke_delay_meidling|<iso-first-seen>)>",
  "pubDate": "2026-05-10T08:30:00+02:00",
  "starts_at": "2026-05-09T08:30:00+02:00",
  "ends_at": null,
  "first_seen": "2026-05-09T08:30:00+02:00",
  "_identity": "stammstrecke_delay_meidling|<iso-first-seen>"
}
```

Pro Build entsteht eine Liste mit `0`, `1` oder `2` Events
(unabhängig pro Richtung). Die richtungsspezifischen `guid`- und
`_identity`-Werte stellen sicher, dass Feed-Reader die beiden
Meldungen als **separate** Notifications anzeigen.

| Feld | Verhalten über mehrere Builds |
| :--- | :--- |
| `pubDate` | aktualisiert sich bei jedem Build (= aktueller Zeitstempel) |
| `starts_at` | = `first_seen` (stabil pro Episode) |
| `first_seen` | unverändert solange dieselbe Episode anhält |
| `guid` | abgeleitet von `(prefix, first_seen)` → stabil pro Episode |
| `description` | "[Seit DD.MM.YYYY]" zeigt `first_seen`-Datum |

Erst wenn das Trigger-Gate auflöst (Median ≤ 9 oder das Episode-
Lookback findet nichts) und später erneut zuschlägt, bekommt das
nächste Event eine **frische** `first_seen`-Marke und damit eine neue
`guid`. Dies modelliert "erneute Verspätungsphase" als eigene
Notification.

### Self-Healing & Resilienz

Da die FeedItems jetzt zur Build-Zeit aus dem CSV-Ledger generiert
werden, übernimmt das CSV-Ledger das Self-Healing implizit:

* Ein **API-Ausfall** des Producers schreibt einfach keine neue
  Zeile. Der nächste Build findet nichts im 1-Stunden-Fenster und
  emittiert kein Event — die alte Warnung verschwindet automatisch
  innerhalb spätestens einer Stunde.
* Eine **Recovery** auf der Stammstrecke schreibt `delay_minutes ≤ 9`
  in die nächste Zeile; das Trigger-Gate löst aus, das Event wird
  beim nächsten Build nicht mehr emittiert.
* Der Reader ist **best-effort, no-throw**: jede I/O-Exception unter
  `read_recent_stammstrecke_observations` wird auf WARNING-Level
  geloggt und liefert eine leere Liste zurück. Eine fehlende oder
  korrupte CSV kippt niemals den Feed-Build.
* **Malformed-Row-Tolerance**: Einzelne Zeilen, die `fromisoformat`
  oder `float()` nicht parsen, werden übersprungen (sentinel-test
  in `tests/test_sentinel_csv_size_bomb.py`).

### Stationsnamen-Auflösung

Die in `description` angezeigten Ziel-Stationsnamen ("Meidling" /
"Floridsdorf") werden **nicht hartcodiert**, sondern über das
kanonische Stationsverzeichnis (`src.utils.stations`) aufgelöst. Der
Producer legt für jede Richtung ein Tupel `(_Direction)` an, das
`target_label` aus `display_name(canonical_name(seed))` ableitet und
mit dem `Wien `-Präfix-Strip eine kompakte Form für die `description`
erzeugt:

```
canonical_name("Wien Meidling")  →  "Wien Meidling"  (Verzeichnis-Hit)
display_name("Wien Meidling")    →  "Wien Meidling"  (kein Override)
strip "Wien "                    →  "Meidling"       (Kompakt-Form)
```

Wenn das Verzeichnis später eine kanonische Umbenennung (z. B.
`Wien Meidling` → `Wien Meidling/Philadelphiabrücke`) oder einen
`display_name`-Override registriert, propagiert das beim nächsten
Cron-Lauf automatisch in die CSV-`direction`-Spalte und damit in den
Suffix nach `in Richtung `.

Die Fallback-Kette in `_short_target_label` deckt drei Failure-Modi
ab:
1. `canonical_name` liefert `None` (Verzeichnis-Miss): der Seed-Name
   wird mit Strip verwendet.
2. `canonical_name` wirft (kaputtes/fehlendes
   `data/stations.json`): exception swallowing + Strip.
3. `display_name` liefert leer: ebenfalls Seed mit Strip.

## Statistik-Logging und Dashboard

Das CSV-Ledger ist gleichzeitig die Datenquelle für das tägliche
Statistik-Dashboard:

* **Producer** — `scripts/update_stammstrecke_status.py` schreibt
  pro Cron-Tick und Richtung eine Zeile, **unabhängig** davon, ob
  die 9-Minuten-Schwelle überschritten wird oder nicht. Damit
  reflektiert das Dashboard die *gesamte* Verteilung (auch
  on-time-Fahrten).
* **Renderer** — `scripts/generate_markdown_stats.py` regeneriert
  `docs/statistik.md` täglich aus den CSV-Ledgers (30-Tage-Fenster).
* **Feed-Builder** — liest dasselbe Ledger mit einem 1-Stunden-Fenster.

Beide Konsumenten benutzen denselben Reader-Pfad
(`src.utils.stats.read_recent_stammstrecke_observations`) und
profitieren damit vom gleichen Bounded-Read- und Tolerance-Verhalten.
Architektur-Kontext und Diagramm: siehe Section 6 in
[`docs/architecture.md`](../architecture.md).

## Erweiterungs-Punkte

* **Schwelle anpassen**: Konstante `DELAY_THRESHOLD_MINUTES` in
  `src/feed/stammstrecke.py`. Aktuell `9.0` (dokumentierter Standard
  "deutliche Stammstrecken-Beeinträchtigung").
* **VOR-Stations-IDs**: `FLORIDSDORF_VOR_ID` / `MEIDLING_VOR_ID` —
  Stop-IDs aus dem VOR/VAO-Stations­verzeichnis (`data/stations.json`).
  Pinned an die jeweils gültigen IDs, sodass eine Verzeichnis-Drift
  nicht stillschweigend den Monitor umlenken kann.
* **Stationsnamen-Seeds**: `FLORIDSDORF_CANONICAL_SEED` /
  `MEIDLING_CANONICAL_SEED` sind die Lookup-Keys, die durch das
  Stationsverzeichnis kanonisch aufgelöst werden.
* **Richtungs-Tabelle**: `DIRECTIONS` in `src/feed/stammstrecke.py`
  spiegelt die Producer-Tupel wider; `target_label` muss byteweise
  zur CSV-`direction`-Spalte passen.
* **HTTP-Timeout**: `QUERY_TIMEOUT` (Default-Sekunden) und
  `MAX_QUERY_TIMEOUT` (oberer Clamp) im Producer. Wird direkt an
  `fetch_content_safe(timeout=…)` durchgereicht.
* **Sample-Größe**: `MAX_TRIPS_PER_QUERY` (aktuell `6`, VAO-Cap).
  Liefert den Median über bis zu 6 unmittelbar anstehende S-Bahnen
  pro Richtung — die VAO-Antwortgröße ist zwischen `numF=5` und
  `numF=6` identisch, der Cap maximiert die Stichprobe ohne extra
  Quota-Kosten.
* **Regex für S-Bahn-Linien**: `_S_BAHN_LINE_RE`. Erfasst alle ÖBB-
  S-Bahn-Linien (`S\d+`), inklusive zukünftiger Erweiterungen
  (`S 90`, `S 100`).
* **Rate-Limit**: `BREAKER_FAILURE_THRESHOLD` /
  `BREAKER_RECOVERY_TIMEOUT`. Aktuell auf `10` / `3600 s` gesetzt. Das
  100/Tag-VAO-Budget wird durch den `_charge_one_request`-Pfad
  zusätzlich am Skript-Eingang geschützt.
* **Window-Längen** im Renderer: `FEED_WINDOW` (1 h) und
  `EPISODE_LOOKBACK` (6 h) in `src/feed/stammstrecke.py`. Eine
  Verkürzung des Feed-Window erhöht die Reaktionszeit, vergrößert
  aber das Risiko von Flapping; eine Verlängerung beruhigt das Signal,
  verzögert aber die Recovery-Sichtbarkeit.

## Verwandte Dokumentation

* **ÖBB-RSS-Scraper-Logik** — siehe [`oebb_provider_logic.md`](oebb_provider_logic.md).
  Das ist der separate Provider, der die Störungs-RSS-Feeds der ÖBB
  ausliest; er hat keine Verbindung zum Stammstrecke-Monitor (außer
  dass beide Pendler-Verbindungen abdecken).
* **Architektur-Karte** — [`docs/architecture.md`](../architecture.md)
  §6 (Statistik & Dashboard Pipeline) und §7 (VOR/VAO API Rate-Limit
  Optimization).
