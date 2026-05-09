# ÖBB Provider Logic (`src/providers/oebb.py`)

## Zweck des Providers
Der ÖBB Provider hat die Aufgabe, Störungsmeldungen des österreichischen Zugverkehrs (ÖBB) abzurufen und sie einer strengen Filterung zu unterziehen. Ziel ist es, ausschließlich Meldungen in den Feed aufzunehmen, die einen **strikten Wien-Bezug** aufweisen oder für Pendler in und um Wien relevant sind. Irrelevante Meldungen aus dem reinen Fern- oder Auslandsverkehr werden frühzeitig verworfen.

## Die Herausforderung der ÖBB-Rohdaten
Die von der ÖBB bereitgestellten RSS-Feeds weisen oft komplexe, mutierte Titel auf, die sowohl Kategorien, Liniencodes als auch Streckenabschnitte vermischen (z.B. `"REX 51: Störung: Wien Hbf ↔ Wr. Neustadt"`).

Ein simples Splitten (z.B. beim ersten oder letzten Doppelpunkt) ist unzureichend und fehleranfällig, da echte Stationsnamen selbst Doppelpunkte enthalten können (wie etwa `"Wien 10.: Favoriten"`). Ein naiver Split würde diese gültigen Stationsnamen zerstören.

Daher erfolgt das Parsing der Präfixe **iterativ**. In einer `while`-Schleife wird mittels regulärer Ausdrücke jeweils von vorne geprüft, ob der Anfang des Textes ein bekanntes Präfix (wie `"Störung:"` oder `"REX 51:"`) ist. Ist das der Fall, wird es abgeschnitten und die Prüfung wiederholt, bis kein bekanntes Präfix mehr gefunden wird. Dadurch bleiben reguläre Bestandteile des Titels, die Doppelpunkte enthalten, geschützt.

## Die Stationserkennung (Routing-Matrix)
Die Relevanz einer Strecke wird durch den Abgleich der erkannten Start- und Zielbahnhöfe ermittelt. Wir verwenden eine mehrstufige Logik, um zu entscheiden, welche Routen behalten und welche verworfen werden.

Die folgende Matrix veranschaulicht, wann eine Verbindung als "Wien-relevant" eingestuft wird:

| Endpunkt 1 (A) | Endpunkt 2 (B) | Ergebnis | Begründung / Bedingung |
| :--- | :--- | :--- | :--- |
| **Unbekannt** | **Unbekannt** | ❌ **Verworfen** | Komplett unbekannte Strecke, typischerweise reiner Fern-/Auslandsverkehr (z.B. Budapest ↔ Bratislava). |
| **Wien** | **Wien** | ✅ **Behalten** | Innerstädtische Verbindung. |
| **Wien** | **Pendlerbahnhof** | ✅ **Behalten** | Relevante Pendelstrecke. |
| **Wien** | **Unbekannt** | ✅ **Behalten** | Mindestens ein Endpunkt ist bekannt und liegt in Wien. |
| **Pendlerbahnhof** | **Pendlerbahnhof** | ❌ **Verworfen** | Strecke außerhalb Wiens ohne direkten Wien-Bezug. |
| **Pendlerbahnhof**| **Unbekannt** | ❌ **Verworfen** | Wenn mindestens eine Station bekannt ist, *muss* zwingend auch eine in Wien liegen (Asymmetrischer Pendler-Check). |

*Hinweis:* Wenn der strikte Modus über die Umgebungsvariable `OEBB_ONLY_VIENNA` aktiviert ist, werden Pendlerbahnhöfe ignoriert und **jeder** bekannte Endpunkt muss explizit in Wien liegen.

## Warnung für zukünftige Entwickler (Tech-Debt)
Aktuell gibt es im Projekt zwei separate Stellen, an denen Schlüsselwörter für Kategorien und Liniencodes gepflegt werden:

1. Die Menge `NON_LOCATION_PREFIXES` in der Datei `src/utils/stations.py`.
2. Der reguläre Ausdruck `base_pattern` innerhalb der Funktion `_strip_oebb_prefixes` in der Datei `src/providers/oebb.py`.

**Achtung:** Wenn in Zukunft neue Kategorien, Störungsarten oder Liniencodes der ÖBB hinzugefügt werden müssen, muss sichergestellt werden, dass diese an **beiden** Stellen ergänzt werden. Eine Divergenz dieser Listen führt zu inkonsistentem Parsing und potenziellen Fehlern bei der Stationserkennung.

---

## S-Bahn Stammstrecke Monitoring (`scripts/update_stammstrecke_status.py`)

Ergänzend zum oben beschriebenen RSS-Scraper läuft ein zweiter,
unabhängiger ÖBB-Datenpfad alle 30 Minuten in einer eigenen GitHub-
Actions-Pipeline (`.github/workflows/update-stammstrecke-status.yml`):
das **Stammstrecke-Verspätungs-Monitoring**. Die Pipeline ist absichtlich
*nicht* in `update_oebb_cache.py` integriert, weil die beiden Pfade
disjunkte Datenquellen, unterschiedliche Cache-Pfade und unterschiedliche
Failure-Modes haben.

### Datenquelle und Abfrage

| Aspekt | Wert |
| :--- | :--- |
| Bibliothek | [`pyhafas`](https://pypi.org/project/pyhafas/) ≥ 0.6.1 |
| Profil | `pyhafas.profile.OEBBProfile` |
| Origin | Wien Floridsdorf (HAFAS-ID `8100518`) |
| Destination | Wien Meidling (HAFAS-ID `8100514`) |
| `max_changes` | `0` (nur direkte S-Bahn-Verbindungen) |
| `max_journeys` | 12 (≈ eine halbe Stunde Stammstrecken-Takt) |
| Cron | `*/30 * * * *` (alle 30 Minuten) |

### Filter und Aggregation

Aus den zurückgegebenen `Journey`-Objekten werden alle `Leg`-Objekte
ausgewählt, deren `name` dem regulären Ausdruck `^\s*S\s*\d+\s*$`
entspricht (also reine S-Bahn-Linien wie `S 1`, `S 7`, `S 80`). Andere
Verkehrsmittel auf den gleichen Gleisen (`REX`, `R`, `IC`, `Railjet`)
werden verworfen — sie sind kein Stammstrecken-Produkt.

Aus den verbleibenden Legs wird der **Median** der
`departure_delay`-Werte (in Minuten) gebildet:

* Stornierte Legs (`leg.cancelled`) werden vom Median ausgeschlossen
  (kein Signal).
* Legs ohne `departure_delay` (None / nicht gesetzt) werden ebenfalls
  ausgeschlossen — `0` einzusetzen würde den Median nach unten ziehen.
* Pures `0`-Delay aus tatsächlich verspätungsfreien Fahrten zählt
  vollwertig (`departure_delay = timedelta(0)` ist nicht `None`).

### Schwellenwert und Cache-Schreibverhalten

Liegt der Median **strikt über 9 Minuten**, schreibt das Skript ein
einzelnes, schema-konformes (`docs/schema/events.schema.json`) Event
nach `cache/stammstrecke/events.json`:

```json
{
  "source": "ÖBB",
  "category": "Störung",
  "title": "S-Bahn Stammstrecke Verspätungen",
  "description": "Auf der S-Bahn-Stammstrecke … Median: <b>X.X Minuten</b> …",
  "link": "https://www.oebb.at/de/fahrplan/…/aktuelle-stoerungsmeldungen",
  "guid": "<sha256(stammstrecke|median|<iso-pubDate>)>",
  "pubDate": "2026-05-09T08:30:00+02:00",
  "starts_at": "2026-05-09T08:30:00+02:00",
  "ends_at": null,
  "_identity": "stammstrecke|median|<iso-pubDate>"
}
```

Liegt der Median ≤ 9 Minuten (oder gibt es keine S-Bahn-Legs mit
Verspätungsdaten), schreibt das Skript stattdessen ein leeres Array
`[]`. Die Cache-Datei ist damit zu jedem Zeitpunkt entweder eine
gültige (möglicherweise leere) Liste — der Feed-Builder muss niemals
einen "Datei fehlt"-Fall handhaben, sobald der erste Cron-Lauf erfolgt
ist.

### Resilience und Sicherheit

* **CircuitBreaker** (`src.utils.circuit_breaker`): 5 aufeinanderfolgende
  Fehler trippen den Breaker; in den folgenden 300 Sekunden werden
  weitere Calls ohne Upstream-Kontakt abgewiesen
  (`CircuitBreakerOpen`). Damit wird Self-DDoS gegen einen
  bekannt-defekten HAFAS-Endpoint vermieden.
* **Atomares Schreiben** (`src.utils.files.atomic_write`): TOCTOU-sicherer
  Pfad mit kryptographisch zufälligem Temp-Dateinamen, `os.fsync` und
  abschließendem `os.replace`. Ein Crash mitten im Write hinterlässt
  *keinen* halbgeschriebenen Cache-Eintrag.
* **Logging** (`src.feed.logging_safe.setup_script_logging` →
  `SafeFormatter`): Jede Diagnose-Nachricht wird durch das projekt-
  weite Sanitisierungsverfahren (Secret-Redaktion, ANSI-/BiDi-Stripping,
  Log-Injection-Escaping) geleitet, bevor sie auf stderr / in den
  Action-Log fließt.
* **Zeitzone** (`zoneinfo.ZoneInfo("Europe/Vienna")`): GitHub Actions
  läuft in UTC. Sowohl die Anfrage-Zeit (`date=` Parameter an
  `client.journeys`) als auch das gespeicherte `pubDate` /
  `starts_at` werden konsequent auf Europe/Vienna ausgerichtet, damit
  der RSS-Feed konsistente Zeitstempel liefert (Sommer-/Winterzeit
  korrekt).

### Feed-Integration

Der Feed-Builder lädt die Datei beim Build über
`src.feed.providers.read_cache_stammstrecke()` (registriert unter dem
Provider-Flag `STAMMSTRECKE_ENABLE`, default `True`). Der Loader liest
direkt aus dem fixen Pfad `cache/stammstrecke/events.json` mit dem
kanonischen Größencap (`read_capped_json`) — er nutzt **nicht** das
gehashte `cache/<sanitized-provider>_<6hex>/events.json`-Layout der
übrigen Provider, weil die Stammstrecken-Cache-Datei häufig manuell
inspiziert wird und der vorhersagbare Pfad die Operator-Erfahrung
verbessert.

### Erweiterungs-Punkte

* **Schwelle anpassen**: Konstante `DELAY_THRESHOLD_MINUTES` in
  `scripts/update_stammstrecke_status.py`. Aktuell `9` (dokumentierter
  Standard "deutliche Stammstrecken-Beeinträchtigung").
* **Stations-IDs**: `FLORIDSDORF_STATION_ID` / `MEIDLING_STATION_ID` —
  HAFAS-IDs aus dem ÖBB-SCOTTY-System.
* **Sample-Größe**: `MAX_JOURNEYS_PER_QUERY` (default 12). Höhere
  Werte stabilisieren den Median, kosten aber mehr Pyhafas-Calls.
* **Regex für S-Bahn-Linien**: `_S_BAHN_LINE_RE`. Erfasst alle ÖBB-
  S-Bahn-Linien (`S\d+`), inklusive zukünftiger Erweiterungen (`S 90`,
  `S 100`).
