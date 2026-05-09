# S-Bahn Stammstrecke Provider Logic (`scripts/update_stammstrecke_status.py`)

## Zweck des Monitors

Das Stammstrecken-Verspätungs-Monitoring läuft alle 30 Minuten in einer
eigenen GitHub-Actions-Pipeline
(`.github/workflows/update-stammstrecke-status.yml`) und beobachtet
unabhängig vom RSS-basierten ÖBB-Provider die zwei Hauptachsen der
Wiener S-Bahn-Stammstrecke. Bei Median-Verspätungen über
`DELAY_THRESHOLD_MINUTES` Minuten emittiert der Monitor schema-konforme
Events nach `cache/stammstrecke/events.json`, die der Feed-Builder
beim nächsten Lauf in den RSS-Feed übernimmt.

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

## Datenquelle und Abfrage

| Aspekt | Wert |
| :--- | :--- |
| Endpoint | `${VOR_BASE_URL}trip` (offizielle VOR/VAO ReST API) |
| Library | direkter `requests`-Aufruf via `src.utils.http.fetch_content_safe` |
| Auth | `accessId` als Query-Parameter via `vor_provider.VorAuth` |
| Richtung 1 | Wien Floridsdorf (`490033400`) → Wien Meidling (`490101500`) |
| Richtung 2 | Wien Meidling (`490101500`) → Wien Floridsdorf (`490033400`) |
| `maxChange` | `0` (nur direkte S-Bahn-Verbindungen) |
| `numF` | `5` (die unmittelbar nächsten 5 S-Bahnen je Richtung — VAO-Limit `numF ≤ 6`) |
| `rtMode` | `SERVER_DEFAULT` (Echtzeitdaten aktivieren) |
| Cron | `*/30 * * * *` (alle 30 Minuten) |

**Beide Richtungen werden strikt getrennt ausgewertet.** Eine
Zusammenlegung würde die Daten verfälschen — eine Störung in eine
Richtung läuft häufig in der Gegenrichtung normal weiter, der Median
über beide Richtungen würde das Signal verdünnen.

## Filter und Aggregation (pro Richtung)

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

## Schwellenwert und Cache-Schreibverhalten

Liegt der Median für eine Richtung **strikt über 9 Minuten**, wird
genau ein schema-konformes Event (`docs/schema/events.schema.json`)
für diese Richtung erzeugt. Pro Cron-Tick entsteht damit eine Liste
mit `0`, `1` oder `2` Events in `cache/stammstrecke/events.json` —
abhängig davon, in welcher Richtung der Schwellenwert überschritten
wurde:

```json
[
  {
    "source": "ÖBB",
    "category": "Störung",
    "title": "S-Bahn Stammstrecke Verspätungen",
    "description": "Durchschnittliche Verspätung von 12.5 Minuten in Richtung Meidling [Seit 09.05.2026]",
    "link": "https://www.oebb.at/de/fahrplan/…/aktuelle-stoerungsmeldungen",
    "guid": "<sha256(stammstrecke_delay_meidling|<iso-first-seen>)>",
    "pubDate": "2026-05-09T08:30:00+02:00",
    "starts_at": "2026-05-09T08:30:00+02:00",
    "ends_at": null,
    "first_seen": "2026-05-09T08:30:00+02:00",
    "_identity": "stammstrecke_delay_meidling|<iso-first-seen>"
  }
]
```

Die richtungsspezifischen `guid`- und `_identity`-Werte stellen
sicher, dass Feed-Reader die beiden Meldungen als **separate**
Notifications anzeigen.

> **Hinweis zur Konsistenz:** Das Event-Feld `source` bleibt
> bewusst auf `"ÖBB"`, weil Feed-Reader-Subscribers den Wechsel der
> Datenquelle nicht bemerken sollen. Die VOR-API ist intern, die
> Stammstrecke selbst betreibt die ÖBB.

### first_seen-Persistenz und Episoden-stabile GUIDs

Beim Aufbau der Events liest das Skript den vorhandenen Cache und
extrahiert pro Richtung den `first_seen`-Zeitstempel. Solange die
Verspätungs-Episode anhält (jeder Cron-Tick beobachtet wieder
Median > 9), wird `first_seen` (und damit `guid` und das
`Seit DD.MM.YYYY`-Datum in der Beschreibung) **unverändert
übernommen**. Der `pubDate` rollt auf den aktuellen Beobachtungs-
Zeitpunkt fort, signalisiert also Frische ohne neue Notification
auszulösen.

| Feld | Verhalten über mehrere Cron-Ticks |
| :--- | :--- |
| `pubDate` | aktualisiert sich bei jedem Tick |
| `starts_at` | = `first_seen` (stabil pro Episode) |
| `first_seen` | Persistenz: gleicher Wert solange Episode anhält |
| `guid` | abgeleitet von `(prefix, first_seen)` → stabil pro Episode |
| `description` | "[Seit DD.MM.YYYY]" zeigt `first_seen`-Datum |

Erst wenn eine Episode endet (Median ≤ 9 *oder* API-Fehler ⇒ Cache
geleert), bekommt das nächste high-median-Event eine **frische**
`first_seen`-Marke und damit eine neue `guid`. Dies modelliert
"erneute Verspätungsphase" als eigene Notification.

Liegt der Median ≤ 9 Minuten (oder gibt es keine S-Bahn-Legs mit
Verspätungsdaten), wird **kein** Event für diese Richtung emittiert.
Liegen für beide Richtungen keine Bedingungen vor, schreibt das
Skript ein leeres Array `[]`. Die Cache-Datei ist damit zu jedem
Zeitpunkt entweder eine gültige (möglicherweise leere) Liste — der
Feed-Builder muss niemals einen "Datei fehlt"-Fall handhaben, sobald
der erste Cron-Lauf erfolgt ist.

## Self-Healing (Cache zwingend leeren)

Der Cache wird **zwingend** auf `[]` gesetzt, sobald *eine* der
folgenden Bedingungen eintritt:

| Trigger | Folge |
| :--- | :--- |
| Beliebige Exception bei jeder einzelnen Richtung (`RequestException`, JSON-Decode-Fehler, malformiertes Payload) | Cache `[]`, Exit `1` |
| `_QuotaExceeded` (VAO-Tageslimit erreicht) bei *allen* Richtungen | Cache `[]`, Exit `1` |
| `CircuitBreakerOpen` (vorgeschalteter Breaker offen) | Cache `[]`, Exit `0` |
| Median ≤ 9 Minuten in *beiden* Richtungen | Cache `[]`, Exit `0` |
| Keine S-Bahn-Legs mit Verspätungsdaten in beiden Richtungen | Cache `[]`, Exit `0` |

Damit wird sichergestellt, dass der RSS-Feed **niemals** veraltete
Stammstrecke-Warnungen aus einem früheren Run trägt — eine Recovery
oder ein API-Ausfall propagiert innerhalb höchstens eines Cron-Ticks
(30 Minuten) in den öffentlichen Feed.

Per-Direction-Isolation bleibt erhalten: ein transienter Fehler bei
*einer* Richtung verwirft nicht das Event der anderen Richtung. Nur
wenn **alle** Richtungen scheitern (oder der Breaker offen ist) wird
der Cache vollständig geleert.

## Resilience und API Rate-Limit

Die VOR/VAO API erlaubt **100 Requests pro Tag** (das `VAO Start`-
Kontingent). Der Monitor ist damit der dominante VOR-Konsument:

| Komponente | Calls/Tag |
| :--- | ---: |
| Stammstrecke (`*/30` × 2 Richtungen) | 96 |
| Station-Enrichment (monatlich, Stammstrecke-Whitelist) | ~10 (1× pro Monat) |
| Disruption-Polling (`DEFAULT_MONITOR_WHITELIST=""`) | 0 |
| **Tagesbudget gesamt** | **96 / 100** |

Der Monitor charged ein Quota-Slot **vor** jedem Network Call via
`_charge_one_request`; eine quota-erschöpfte Stunde produziert
sauber `_QuotaExceeded`, ohne den Endpoint zu treffen.

Die Circuit-Breaker-Konfiguration deckelt zusätzlich:

* `failure_threshold = 10` — nach 10 aufeinanderfolgenden Fehlern
  wechselt der Breaker in den OPEN-Zustand.
* `recovery_timeout = 3600.0` (1 Stunde) — der Breaker bleibt eine
  Stunde lang OPEN, bevor ein Probe-Call zugelassen wird.

Im Normalbetrieb produziert die Pipeline durch den Cron-Plan
(`*/30 * * * *` = 2 Ausführungen pro Stunde) und 2 Richtungen pro
Ausführung **4 Calls pro Stunde** — komfortabel unter dem Limit.

Weitere Schutzmechanismen:

* **HTTP-Timeout via `fetch_content_safe`**: Per-Call `timeout=20`
  (clamped auf `MAX_QUERY_TIMEOUT=30`) wird vom HTTP-Layer direkt
  an `requests.Session.get` durchgereicht — kein Patching der Session
  mehr notwendig wie noch in der pyhafas-Ära.
* **CircuitBreakerOpen** kurzschließt nach erstem Auftreten innerhalb
  einer Iteration: wenn der Breaker während der Abarbeitung der ersten
  Richtung öffnet, wird die zweite Richtung *nicht* mehr versucht
  (sie würde sowieso short-circuiten). Bereits gesammelte Events der
  ersten Richtung bleiben erhalten und werden geschrieben.
* **Per-Direction-Fehlerisolation**: ein transienter Fehler bei
  Richtung 1 (RequestException, Connection Reset etc.) wirft Richtung
  2 *nicht* weg. Der Cache wird mit den Events geschrieben, die wir
  haben — leere Daten bei kompletter Degradation, partielle Daten bei
  gemischtem Erfolg.
* **Atomares Schreiben** (`src.utils.files.atomic_write`): TOCTOU-
  sicherer Pfad mit kryptographisch zufälligem Temp-Dateinamen,
  `os.fsync` und abschließendem `os.replace`. Ein Crash mitten im
  Write hinterlässt *keinen* halbgeschriebenen Cache-Eintrag.
* **Logging** (`src.feed.logging_safe.setup_script_logging` →
  `SafeFormatter`): Jede Diagnose-Nachricht wird durch das projekt-
  weite Sanitisierungsverfahren (Secret-Redaktion, ANSI-/BiDi-Stripping,
  Log-Injection-Escaping) geleitet, bevor sie auf stderr / in den
  Action-Log fließt. **Wichtig:** Exception-Strings werden bewusst
  *nicht* via `%s` oder `exc_info=True` geloggt — `VorAuth` injiziert
  `accessId` in jede prepared-Request-URL, und `RequestException`
  kann die URL im Message tragen (Pattern aus `update_vor_cache.py`).
* **Zeitzone** (`zoneinfo.ZoneInfo("Europe/Vienna")`): GitHub Actions
  läuft in UTC. Sowohl die Anfrage-Zeit (`date=` / `time=` Parameter)
  als auch das gespeicherte `pubDate` / `starts_at` werden konsequent
  auf Europe/Vienna ausgerichtet, damit der RSS-Feed konsistente
  Zeitstempel liefert (Sommer-/Winterzeit korrekt).
* **JSON-Depth-Bomb-Schutz**: `_query_trips` umschließt
  `json.loads` mit `except (ValueError, RecursionError)` und
  re-raised als `ValueError` — eine deeply-nested-aber-wohlgeformte
  Antwort vom Upstream landet damit im pro-Richtungs-Error-Branch
  statt eine `BaseException`-getriebene Recursion-Failure aus dem
  Skript zu propagieren (Drift-Defence-Walker pinned).

## Stationsnamen-Auflösung

Die in `description` angezeigten Ziel-Stationsnamen ("Meidling" /
"Floridsdorf") werden **nicht hartcodiert**, sondern über das
kanonische Stationsverzeichnis (`src.utils.stations`) aufgelöst:

```
canonical_name("Wien Meidling")  →  "Wien Meidling"  (Verzeichnis-Hit)
display_name("Wien Meidling")    →  "Wien Meidling"  (kein Override)
strip "Wien "                    →  "Meidling"       (Kompakt-Form)
```

Der kompakte `in Richtung Meidling`-Stil entsteht durch Strippen
des `Wien `-Präfix nach dem Verzeichnis-Lookup — die Beschreibung
setzt Wien implizit voraus, deshalb wirkt das volle "Wien Meidling"
in dieser Stelle redundant. Wenn das Verzeichnis später eine
kanonische Umbenennung (z. B. `Wien Meidling` →
`Wien Meidling/Philadelphiabrücke`) oder einen `display_name`-Override
registriert, propagiert das automatisch in den Suffix nach
`in Richtung `.

Die Fallback-Kette in `_short_target_label` deckt drei Failure-Modi
ab:
1. `canonical_name` liefert `None` (Verzeichnis-Miss): der Seed-Name
   wird mit Strip verwendet.
2. `canonical_name` wirft (kaputtes/fehlendes
   `data/stations.json`): exception swallowing + Strip.
3. `display_name` liefert leer: ebenfalls Seed mit Strip.

## Statistik-Logging (Append-Only CSV)

Nach jeder erfolgreichen Median-Berechnung — *unabhängig* davon, ob
die Schwelle überschritten wird oder nicht — schreibt das Skript eine
Zeile in `data/stats/stammstrecke_YYYY.csv` (Spalten `timestamp,
weekday, hour, direction, delay_minutes`, ISO 8601 `Europe/Vienna`).
Damit reflektiert das Dashboard (`docs/statistik.md`, regeneriert
durch `scripts/generate_markdown_stats.py`) und der README-Snapshot
die *gesamte* Verteilung der Verspätungen und nicht nur die
Eskalationen, die als RSS-Event emittiert wurden. Der Writer
(`src.utils.stats.append_stammstrecke_row`) ist **best-effort**:
jeder I/O-Fehler wird auf WARNING-Level geloggt und geschluckt, damit
eine fehlgeschlagene Statistik niemals den Cron-Lauf kippt.
Architektur-Kontext und Diagramm: siehe Section 6 in
[`docs/architecture.md`](../architecture.md).

## Feed-Integration

Der Feed-Builder lädt die Datei beim Build über
`src.feed.providers.read_cache_stammstrecke()` (registriert unter dem
Provider-Flag `STAMMSTRECKE_ENABLE`, default `True`). Der Loader liest
direkt aus dem fixen Pfad `cache/stammstrecke/events.json` mit dem
kanonischen Größencap (`read_capped_json`) — er nutzt **nicht** das
gehashte `cache/<sanitized-provider>_<6hex>/events.json`-Layout der
übrigen Provider, weil die Stammstrecken-Cache-Datei häufig manuell
inspiziert wird und der vorhersagbare Pfad die Operator-Erfahrung
verbessert.

## Erweiterungs-Punkte

* **Schwelle anpassen**: Konstante `DELAY_THRESHOLD_MINUTES` in
  `scripts/update_stammstrecke_status.py`. Aktuell `9` (dokumentierter
  Standard "deutliche Stammstrecken-Beeinträchtigung").
* **VOR-Stations-IDs**: `FLORIDSDORF_VOR_ID` / `MEIDLING_VOR_ID` —
  Stop-IDs aus dem VOR/VAO-Stations­verzeichnis (`data/stations.json`).
  Pinned an die jeweils gültigen IDs, sodass eine Verzeichnis-Drift
  nicht stillschweigend den Monitor umlenken kann.
* **Stationsnamen-Seeds**: `FLORIDSDORF_CANONICAL_SEED` /
  `MEIDLING_CANONICAL_SEED` sind die Lookup-Keys, die durch das
  Stationsverzeichnis kanonisch aufgelöst werden. Eine Umbenennung
  in `data/stations.json` propagiert automatisch ins Description-Feld.
* **Richtungs-Tabelle**: `DIRECTIONS` (Tuple aus `_Direction`-Records).
  Jede Richtung trägt Origin, Destination, das im Description-Feld
  angezeigte Ziel-Label und den Identity-Prefix für `guid` /
  `_identity`.
* **HTTP-Timeout**: `QUERY_TIMEOUT` (Default-Sekunden) und
  `MAX_QUERY_TIMEOUT` (oberer Clamp). Wird direkt an
  `fetch_content_safe(timeout=…)` durchgereicht.
* **Sample-Größe**: `MAX_TRIPS_PER_QUERY` (aktuell `5`, VAO-Cap `6`).
  Liefert den Median über die *unmittelbar nächsten 5* anstehenden
  S-Bahnen pro Richtung — eine ungerade Sample-Größe macht den Median
  exakt zum mittleren Element (statt Mittel zweier Werte) und liefert
  dadurch eine Aussage robust gegen Ausreißer und nahe an der
  Operator-Erwartung „wie ist es *jetzt*?". Höhere Werte glätten,
  vergrößern aber die VAO-Payload und entkoppeln den Median vom
  aktuellen Betriebszustand.
* **Regex für S-Bahn-Linien**: `_S_BAHN_LINE_RE`. Erfasst alle ÖBB-
  S-Bahn-Linien (`S\d+`), inklusive zukünftiger Erweiterungen
  (`S 90`, `S 100`).
* **Rate-Limit**: `BREAKER_FAILURE_THRESHOLD` /
  `BREAKER_RECOVERY_TIMEOUT`. Aktuell auf 10 / 3600 s gesetzt. Das
  100/Tag-VAO-Budget wird durch den `_charge_one_request`-Pfad
  zusätzlich am Skript-Eingang geschützt.
