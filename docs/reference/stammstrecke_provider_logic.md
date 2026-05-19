# S-Bahn Stammstrecke Provider Logic

Das Stammstrecken-Verspätungs-Monitoring überwacht die Stammstrecke
am Wien Hauptbahnhof auf signifikante Verspätungen und Ausfälle und
emittiert daraus dynamisch Feed-Events. Die Logik verteilt sich auf
zwei Module:

| Komponente | Verantwortlichkeit |
| :--- | :--- |
| `scripts/update_stammstrecke_hbf.py` | Refresh-Producer (aktiv seit 2026-05-15): wird vom IFTTT-getriggerten `update-cycle.yml` (~alle 30 Min auf :00/:30) als Pipeline-Schritt aufgerufen, fragt einmal pro Tick die VOR/VAO `/departureBoard`-API am Wien Hauptbahnhof ab und hängt eine aggregierte Beobachtungs-Zeile pro Richtung an `data/stats/stammstrecke_<YYYY>.csv` sowie eine Zeile pro Ausfall an `data/stats/ausfaelle_<YYYY>.csv` (CSV-Ledger). Die geteilte Pending-Trip- und Recently-Finalised-Infrastruktur wird per Import aus dem Legacy-Modul `scripts/update_stammstrecke_status.py` wiederverwendet — letzteres wird vom Cron-Workflow nicht mehr direkt aufgerufen. |
| `src/feed/stammstrecke.py` | Feed-Renderer: liest die zuletzt geschriebenen Zeilen, prüft für jede Richtung über ein 1-Stunden-Fenster, ob mindestens zwei Beobachtungen die 9-Minuten-Schwelle überschreiten, und erzeugt — ggf. — einen schema-konformen FeedItem. |

Diese Architektur entstand in zwei Schritten:

* **2026-05-09** — Trennung von Producer (Cron-Skript) und Renderer
  (Feed-Builder). Zuvor schrieb der Cron-Job direkt in
  `cache/stammstrecke/events.json`. Seither ist das CSV-Ledger die
  einzige Quelle der Wahrheit sowohl für den RSS-Feed (1-Stunden-Fenster)
  als auch für das 30-Tage-Dashboard in `docs/statistik.md`; die
  Schwellen-Logik lebt im Feed-Builder und wird für jeden Build neu
  ausgewertet, sodass ein nachträglich angepasster Threshold sofort
  wirkt, ohne dass der Cron-Job neu laufen muss.
* **2026-05-15** — Migration von `/trip` (zwei Richtungs-Abfragen
  Floridsdorf ↔ Meidling) auf `/departureBoard` am Wien Hauptbahnhof.
  Halbiert das API-Budget (1 statt 2 Calls/Tick) und hebt die
  `numF=6`-Sampling-Lücke des Vorgängers. Eine begleitende Persistenz-
  Schicht (`cache/stammstrecke/pending_trips.json` + `recently_finalised.
  json`) trackt Trip-IDs über mehrere Cron-Ticks hinweg, damit jede
  Verspätung/jeder Ausfall genau einmal in den CSV-Ledger fließt.

## Migrations-Historie

* **Pre-2026-05-09 — `pyhafas` (`OEBBProfile`)**: Der Monitor war
  ursprünglich gegen den öffentlichen ÖBB-`mgate.exe`-Endpunkt via
  [`pyhafas`](https://pypi.org/project/pyhafas/) implementiert. Das
  2026-05-09 Audit zeigte, dass `OEBBProfile` in keiner auf PyPI
  veröffentlichten `pyhafas`-Version exportiert wird (der Import
  schlug seit Wochen still fehl, das Skript hat in der Folge nie eine
  CSV-Zeile geschrieben).
* **2026-05-09 — VOR/VAO `/trip`**: Portierung auf die im Projekt
  bereits etablierte VOR/VAO-Infrastruktur — gleicher
  authentifizierter Session-Stack, gleicher Quota-Counter, gleicher
  `request_safe`-HTTP-Layer wie die anderen VOR-Konsumenten. Im
  selben Schritt wurde das alte JSON-Cache-Layout entsorgt: der Cron-
  Job schreibt nur noch Beobachtungs-Zeilen ins CSV-Ledger; die
  Threshold- und First-Seen-Logik wanderte nach `src/feed/stammstrecke.py`.
* **2026-05-15 — VOR/VAO `/departureBoard` am Wien Hauptbahnhof**:
  Wechsel auf einen Stammstrecken-Mittelpunkt mit
  Bahnsteig-1/2-Filter und Trip-ID-Tracking über mehrere Cron-Ticks
  hinweg. Halbiert das API-Budget (1 Call/Tick statt 2), hebt die
  `numF=6`-Sampling-Lücke des Vorgängers und führt eine separate
  Ausfall-Statistik (`data/stats/ausfaelle_<YYYY>.csv`) ein. **Semantischer
  Bruch**: Die Verspätung wird ab 2026-05-15 am Hbf gemessen, davor
  am Origin (Floridsdorf bzw. Meidling) — Werte sind über den
  Stichtag hinweg nicht direkt vergleichbar.

## Cron-Producer (`scripts/update_stammstrecke_hbf.py`)

### Datenquelle und Abfrage

| Aspekt | Wert |
| :--- | :--- |
| Endpoint | `${VOR_BASE_URL}departureBoard` (offizielle VOR/VAO ReST API) |
| HTTP-Layer | `src.utils.http.request_safe` (SSRF-Guard, Slowloris-Cap, MAX_PAYLOAD_SIZE, Content-Type-Validierung) |
| Auth | `accessId` als Query-Parameter via `vor_provider.VorAuth` |
| Stop-ID | Wien Hauptbahnhof (`490134900`, gepinnt in `HAUPTBAHNHOF_VOR_ID`) |
| `duration` | `45` Minuten (`DEPARTURE_BOARD_DURATION_MIN`); 50%-Overlap zur ~30-Min-Cron-Cadence, sodass jeder Zug in zwei Ticks erscheint und die jüngere Beobachtung gewinnt |
| `products` | `3` (Bitmaske Train + S-Bahn) — schmälert die Server-Antwort vor dem clientseitigen S-Bahn-Filter |
| `maxJourneys` | bewusst weggelassen — VAO-soft-Limit (`docs/reference/departureboard.md:22`); ohne Cap liefert der Server alle Abfahrten im Fenster |
| `rtMode` | `SERVER_DEFAULT` (Echtzeitdaten aktivieren) |
| Refresh-Trigger | ~alle 30 Min auf :00/:30 — läuft als Schritt im IFTTT-getriggerten `update-cycle.yml` (`repository_dispatch: ifttt_feed_trigger`). Für Operator-Reruns siehe `manual-full-refresh.yml` oder ein `workflow_dispatch` auf `update-cycle.yml` selbst. |

**Eine Abfrage liefert beide Richtungen.** Die Klassifikation in
„nordwärts" (Praterstern) und „südwärts" (Meidling) erfolgt
clientseitig über die Endhaltestellen-Strings (siehe unten).

### Filter und Aggregation

Aus den zurückgegebenen `Departure`-Objekten überlebt nur, wer
**alle drei Filter** passiert:

1. **Bahnsteig-Filter** (`STAMMSTRECKE_HBF_TRACK_TRUNKS = {"1", "2"}`):
   Wien Hbf hat zwei Stammstrecken-Bahnsteige — Bahnsteig 1 südwärts
   (Meidling), Bahnsteig 2 nordwärts (Praterstern). Alle anderen
   Bahnsteige (3–12 inkl. Halb-Bahnsteige „1A"/„10A-B") tragen
   Fernverkehr (RJ/IC/EC/NJ), Hbf-endende REX-Züge, die Marchegger
   Ostbahn, die Pottendorfer Linie, die Westbahn — sie werden
   deterministisch ausgeschlossen. `rtTrack` überschreibt das
   geplante `track`, sodass eine kurzfristige Bahnsteig-Verlegung
   weg von Bahnsteig 1/2 das Sample korrekt verlässt.
2. **Richtungs-Klassifikation** (`HBF_SOUTHBOUND_SUBSTRINGS` /
   `HBF_NORTHBOUND_SUBSTRINGS` + `HBF_SOUTHBOUND_TERMINI` /
   `HBF_NORTHBOUND_TERMINI`): Substring-Match gegen
   `direction.lower()` zuerst, dann exakter Whitelist-Match.
   Unbekannte Termini werden mit deduplizierter INFO-Log-Zeile
   verworfen, damit Operator:innen die Whitelist erweitern können.
3. **S-Bahn-Linien-Filter** (`_is_sbahn_line`, Regex
   `_S_BAHN_LINE_RE`): Nur Linien, die das `S\d+`-Muster matchen.
   `REX`/`R`/`IC`/`Railjet` werden trotz Bahnsteig 1/2 verworfen.

**Cancellation-Branch (Ausfälle)**: Der Cancellation-Check läuft VOR
dem `rtTime`-Filter, weil VAO bei ausgefallenen Zügen oft keine
`rtTime` mehr ausliefert. Ausfälle landen mit eigener Identity-Key-
Dedup (`(direction, name, scheduled)`) im `_PendingTrip`-Ledger
(Feld `cancelled: bool`); beim Finalisieren generiert jeder
Cancellation-Eintrag eine eigene Zeile in `data/stats/ausfaelle_
<YYYY>.csv` (Spalten: `timestamp, weekday, hour, direction, line`).

**Verspätungs-Berechnung** (`_departure_delay_minutes`) —
Differenz `rtTime − time` in Minuten. Pro Pending-Trip wird beim
Finalisieren der Mittelwert über die zuletzt beobachtete
Verspätung gebildet (latest-wins-Overwrite über die Tick-Beobachtungen
des selben physischen Zugs); das Aggregat pro Richtung+Tick wird
als eine Zeile an `data/stats/stammstrecke_<YYYY>.csv` angehängt
(Spalten: `timestamp, weekday, hour, direction, delay_minutes`,
ISO 8601 `Europe/Vienna`).

* Stornierte Departures werden vom Verspätungs-Aggregat
  ausgeschlossen (sie zählen ausschließlich in `ausfaelle_<YYYY>.csv`).
* Departures ohne `rtTime` werden vom Aggregat ausgeschlossen — `0`
  einzusetzen würde den Mittelwert systematisch nach unten ziehen.
* Pures `0`-Delay aus tatsächlich verspätungsfreien Fahrten zählt
  vollwertig.

**Es werden keine Schwellenwerte oder Events mehr direkt vom Cron-
Skript erzeugt** — die Render-Phase arbeitet ausschließlich auf den
Ledger-Zeilen.

**Pending-/Finalised-Ledger** (`cache/stammstrecke/pending_trips.json`
und `cache/stammstrecke/recently_finalised.json`): Identity-Key
`(direction, name, scheduled)`. Beobachtungen ferner Züge (z. B. 40
Min in der Zukunft) werden bei späteren Ticks mit jüngerer rtTime
überschrieben (latest-wins). Erst wenn `scheduled <= now`, finalisiert
der Pass die jeweils beste Beobachtung und schreibt eine CSV-Zeile;
der Recently-Finalised-Schutz verhindert eine Doppelzählung, falls
VAO denselben Zug in einem späteren Lookahead-Fenster nochmal listet.
Legacy-Einträge ohne `cancelled`-Feld laden als `cancelled=False`.

### Resilience und API Rate-Limit

Die VOR/VAO API erlaubt **100 Requests pro Tag** (das `VAO Start`-
Kontingent). Der Monitor ist mit Abstand der dominante VOR-Konsument;
seit der 2026-05-15-Migration auf `/departureBoard` halbiert sich
das Hbf-Budget gegenüber dem `/trip`-Vorgänger:

| Komponente | Calls/Tag |
| :--- | ---: |
| Stammstrecke (~alle 30 Min × 1 Hbf-Call, IFTTT-getriggert) | 48 |
| Station-Enrichment (entfernt 2026-05-11) | 0 |
| Disruption-Polling (entfernt 2026-05-11) | 0 |
| **Tagesbudget gesamt** | **48 / 100** |

Die freiwerdenden ~52 Calls/Tag bleiben als Puffer für gelegentliche
Operator-Direktaufrufe (`workflow_dispatch`, manuelle Smoke-Tests
gegen `verify_vor_access_id.py` / `check_vor_auth.py`).

Der Producer charged ein Quota-Slot **vor** jedem Network Call via
`_charge_one_request`; eine quota-erschöpfte Stunde produziert
sauber `_QuotaExceeded`, ohne den Endpoint zu treffen. Zusätzlich
gated `update-cycle.yml` den Schritt mit
`scripts/preflight_quota_check.py --check vor --margin 1` — die
neue Margin von `1` reflektiert die EINE `/departureBoard`-Anfrage
pro Tick (vor 2026-05-15 war es `--margin 2`).

Die Circuit-Breaker-Konfiguration deckelt zusätzlich:

* `failure_threshold = 10` — nach 10 aufeinanderfolgenden Fehlern
  wechselt der Breaker `stammstrecke-hbf-vor` in den OPEN-Zustand.
* `recovery_timeout = 3600.0` (1 Stunde) — der Breaker bleibt eine
  Stunde lang OPEN, bevor ein Probe-Call zugelassen wird.

Weitere Schutzmechanismen:

* **HTTP-Timeout via `request_safe`**: Per-Call `timeout=20`
  (clamped auf `MAX_QUERY_TIMEOUT=30`) wird vom HTTP-Layer direkt
  an `requests.Session.get` durchgereicht.
* **CircuitBreakerOpen** kurzschließt sauber: wenn der Breaker
  öffnet, schreibt der Tick keine neue Zeile und kommt im nächsten
  Tick wieder durch, sobald `recovery_timeout` abgelaufen ist.
* **Atomares Schreiben** für CSV-Zeilen ist nicht nötig: einzelne
  Append-Schreibvorgänge unterhalb des `PIPE_BUF`-Limits (4 KiB) sind
  unter POSIX atomar; der Renderer toleriert zudem fehlerhafte
  Einzelzeilen über `csv.reader` + `fromisoformat`-Try/Except.
* **Atomares Persistieren des Pending-Ledgers**: Der Finalize-Pass
  schreibt zuerst `recently_finalised.json` und dann
  `pending_trips.json`; ein Crash zwischen beiden Schreibvorgängen
  führt im nächsten Tick im Schlimmsten Fall dazu, dass ein bereits
  finalisierter Trip nochmals beobachtet wird — der
  `recently_finalised`-Guard verhindert das Doppel-Recording.
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
* **Non-Finite-Literal-Schutz** (`loads_finite`): Der Parser
  rejected `NaN` / `Infinity` / `1e1000`-Literale aus einer
  kompromittierten / MITM-getarnten VAO-Antwort. Eine
  Depth-Bomb-Antwort landet als `ValueError` im pro-Tick-Error-Branch
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
| `DELAY_THRESHOLD_MINUTES` | `9.0` | Schwellenwert pro Beobachtung; wird vom Trigger-Gate konsumiert. |
| `FEED_WINDOW` | `1 h` | Zeitfenster (rückwirkend von `now`), in dem das Trigger-Gate ausgewertet wird. |
| `EPISODE_LOOKBACK` | `6 h` | Max. Rückblick zur Bestimmung des `first_seen`-Zeitpunkts der laufenden Episode. |
| `EPISODE_GAP_TOLERANCE` | `70 min` | Maximale Lücke zwischen zwei Beobachtungen, ehe eine Episode als beendet gilt (deckt einen ausgefallenen Cron-Tick ab). |
| `EVENT_SOURCE` | `"VOR/VAO"` | `source`-Feld der emittierten Events (vorher `"ÖBB"`; geändert mit der 2026-05-09-Migration, weil die Datenquelle inzwischen technisch die VOR/VAO-API ist). |
| `EVENT_CATEGORY` | `"Störung"` | RSS-`category`-Feld. |
| `EVENT_TITLE` | `"S-Bahn Stammstrecke Verspätungen"` | konstantes Item-Title. |
| `EVENT_LINK` | `https://www.wienerlinien.at/web/wienerlinien/oeffis-stoerungen-strecke` | weiterführender Link für Feed-Reader. |

### Trigger-Gate, Anzeigewert, First-Seen

Pro Richtung und pro Build:

1. Lade alle CSV-Zeilen aus dem `EPISODE_LOOKBACK`-Fenster (6 Stunden)
   und kanonisiere die Richtung (Legacy-Label `"Floridsdorf"` → kanonische
   Direction `Praterstern`).
2. Filtere auf das jüngste `FEED_WINDOW` (1 Stunde) — typischerweise
   2 Beobachtungen pro Richtung.
3. **Trigger**: emittiere ein Event genau dann, wenn **mindestens zwei**
   Beobachtungen in dieser Untermenge **strikt** über
   `DELAY_THRESHOLD_MINUTES` liegen. Die 2-aus-N-Regel verhindert,
   dass ein einzelner Ausreißer eine Richtung allein ins Event drückt;
   bei der typischen Stichprobengröße (2 Beobachtungen pro Stunde)
   müssen also beide jüngsten Ticks die Schwelle reißen.
4. **Anzeigewert**: das Event-`description`-Feld zeigt den **Mittelwert**
   (`mean`) der Beobachtungen im Feed-Fenster — für End-Nutzer:innen
   leichter interpretierbar als der Median.
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

Erst wenn das Trigger-Gate auflöst (weniger als 2 Beobachtungen
> 9 min oder das Episode-Lookback findet nichts) und später erneut
zuschlägt, bekommt das nächste Event eine **frische** `first_seen`-
Marke und damit eine neue `guid`. Dies modelliert "erneute
Verspätungsphase" als eigene Notification.

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

### Stationsnamen / Richtungs-Labels

Die in `description` angezeigten Richtungs-Labels lauten seit der
2026-05-15-Migration `"Meidling"` (südwärts, nächster Stammstrecken-
Halt nach Hbf) und `"Praterstern"` (nordwärts, nächster
Stammstrecken-Halt nach Hbf). Beide Labels sind als Konstanten
(`DIRECTION_LABEL_SOUTHBOUND` / `DIRECTION_LABEL_NORTHBOUND` im
Hbf-Producer und `DIRECTIONS` im Renderer) gepinnt, sodass die
CSV-`direction`-Spalte byte-stabil bleibt.

Vor 2026-05-15 hieß das Nord-Label `"Floridsdorf"`. Die
Umbenennungs-Begründung: Bei kurzen Wendezügen, die bereits am
Praterstern oder Wien Mitte terminieren (und nicht bis Floridsdorf
weiterfahren), bezeichnete die alte Beschriftung fälschlich einen
Endpunkt, den die meisten Züge gar nicht erreichen. Die Süd-
Beschriftung benennt seit jeher die nächste Stammstrecken-
Haltestelle nach dem Hbf — die Umbenennung gibt der Nord-
Beschriftung dieselbe Semantik.

**Backwards-Compat-Shim**: `DIRECTIONS_BY_LABEL` im Renderer mappt
das Legacy-Label `"Floridsdorf"` weiterhin auf die Praterstern-
Direction; Hbf-Cron-Skript ruft `_finalize_departed` zusätzlich für
`LEGACY_DIRECTION_LABEL_NORTHBOUND` auf. So fließen extern
wiederhergestellte alte Pending-State-Einträge oder hand-editierte
CSV-Zeilen mit `"Floridsdorf"` transparent in den Praterstern-Bucket;
neu geschrieben wird stets das kanonische `"Praterstern"`.

## Statistik-Logging und Dashboard

Die CSV-Ledger sind gleichzeitig die Datenquellen für das tägliche
Statistik-Dashboard:

* **Producer** — `scripts/update_stammstrecke_hbf.py` schreibt pro
  Cron-Tick und Richtung eine aggregierte Zeile in
  `data/stats/stammstrecke_<YYYY>.csv` (Verspätungs-Mittelwert über
  alle finalisierten Pending-Trips dieses Ticks), **unabhängig**
  davon, ob die 9-Minuten-Schwelle überschritten wird oder nicht.
  Damit reflektiert das Dashboard die *gesamte* Verteilung (auch
  on-time-Fahrten). Ausfälle landen separat in
  `data/stats/ausfaelle_<YYYY>.csv` (eine Zeile pro Cancellation).
* **Renderer** — `scripts/generate_markdown_stats.py` regeneriert
  `docs/statistik.md` täglich aus den CSV-Ledgers (30-Tage-Fenster)
  und patcht zusätzlich die `<!-- STATS:* -->`-Marker im README
  (60-Min- und 30-Tage-Snapshots für Verspätungen + Ausfälle).
* **Feed-Builder** — liest `stammstrecke_<YYYY>.csv` mit einem
  1-Stunden-Fenster über das Trigger-Gate (siehe oben).

Beide Konsumenten benutzen denselben Reader-Pfad
(`src.utils.stats.read_recent_stammstrecke_observations`) und
profitieren damit vom gleichen Bounded-Read- und Tolerance-Verhalten.
Architektur-Kontext und Diagramm: siehe Section 6 in
[`docs/architecture.md`](../architecture.md).

## Erweiterungs-Punkte

* **Schwelle anpassen**: Konstante `DELAY_THRESHOLD_MINUTES` in
  `src/feed/stammstrecke.py`. Aktuell `9.0` (dokumentierter Standard
  "deutliche Stammstrecken-Beeinträchtigung").
* **VOR-Stop-ID**: `HAUPTBAHNHOF_VOR_ID` in
  `scripts/update_stammstrecke_hbf.py`. Pinned an `490134900`
  (Wien Hauptbahnhof), sodass eine Verzeichnis-Drift nicht
  stillschweigend den Monitor umlenken kann.
* **Bahnsteig-Whitelist**: `STAMMSTRECKE_HBF_TRACK_TRUNKS` (aktuell
  `{"1", "2"}`) im Hbf-Producer. Erweiterung nur sinnvoll, wenn ÖBB
  einen weiteren Hbf-Bahnsteig auf die Stammstrecke umlenkt.
* **Richtungs-Klassifikation**: `HBF_SOUTHBOUND_SUBSTRINGS` /
  `HBF_NORTHBOUND_SUBSTRINGS` für den schnellen Substring-Match,
  `HBF_SOUTHBOUND_TERMINI` / `HBF_NORTHBOUND_TERMINI` für die
  exakte Whitelist seltener Termini. Erweiterung über die
  `Unbekannter Endpunkt am Hbf`-INFO-Logs des Hbf-Producers.
* **Direction-Labels**: `DIRECTION_LABEL_SOUTHBOUND` /
  `DIRECTION_LABEL_NORTHBOUND` (Producer) und `DIRECTIONS` (Renderer)
  müssen byteweise übereinstimmen — die CSV-`direction`-Spalte
  trägt das `target_label` verbatim.
* **HTTP-Timeout**: `QUERY_TIMEOUT` (Default-Sekunden) und
  `MAX_QUERY_TIMEOUT` (oberer Clamp) im Producer. Wird direkt an
  `request_safe(timeout=…)` durchgereicht.
* **Duration-Window**: `DEPARTURE_BOARD_DURATION_MIN` (aktuell `45`).
  Sized als 30-Min-Cron-Cadence + 15-Min-Overlap, damit jeder Zug
  zweimal beobachtet wird (latest-wins-Overwrite). Eine Verlängerung
  bläht die Antwort ohne Genauigkeits-Gewinn auf.
* **Regex für S-Bahn-Linien**: `_S_BAHN_LINE_RE`. Erfasst alle ÖBB-
  S-Bahn-Linien (`S\d+`), inklusive zukünftiger Erweiterungen
  (`S 90`, `S 100`).
* **Rate-Limit**: `BREAKER_FAILURE_THRESHOLD` /
  `BREAKER_RECOVERY_TIMEOUT`. Aktuell auf `10` / `3600 s` gesetzt. Das
  100/Tag-VAO-Budget wird durch den `_charge_one_request`-Pfad
  zusätzlich am Skript-Eingang geschützt; davor läuft im Workflow
  `scripts/preflight_quota_check.py --check vor --margin 1`.
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
