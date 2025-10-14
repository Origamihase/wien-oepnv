# Systemprüfung Wien ÖPNV Feed

## Vorgehen
- Manuelle Codeprüfung aller produktiven Module inklusive Feed-Pipeline, Provider-Adapter, Hilfsfunktionen und Betriebsskripte. Fokus auf Fehlerpfade, Pfad-Validierung, Datenbereinigung sowie Secret-Verwendung.【F:src/build_feed.py†L26-L188】【F:src/utils/cache.py†L12-L60】【F:src/providers/vor.py†L260-L415】【F:scripts/update_all_stations.py†L1-L74】
- Ausführung der vollständigen Test-Suite (`pytest`) zur Absicherung der Parser, Normalisierungen und Provider-spezifischen Sonderfälle (233 Tests).【9e0fe0†L1-L3】
- Probeausführung der Feed-Generierung zur Überprüfung der End-to-End-Befüllung und der Log-Ausgaben.【5875cb†L1-L3】

## Ergebnisse nach Prüfkriterien
### 1. Fehler oder unmittelbarer Handlungsbedarf?
- **Pfad- und Dateisicherheit**: Jeder Ausgabepfad wird gegen eine Positivliste geprüft; ungültige Werte lösen Exceptions aus, wodurch versehentliche Schreibzugriffe außerhalb des Repos verhindert werden.【F:src/build_feed.py†L26-L112】
- **Cache-Leseprobleme**: Fehlerhafte oder fehlende JSON-Dateien werden abgefangen, protokolliert und führen lediglich zu leeren Ergebnislisten statt zu Abstürzen.【F:src/utils/cache.py†L20-L60】
- **State-Handhabung**: Persistierte GUIDs werden vor jeder Feed-Ausgabe bereinigt und nur für aktuelle Items gespeichert, womit Speicherlecks vermieden werden.【F:src/build_feed.py†L790-L838】
- **Empfohlene Vorsorge**: Für kritische Warnungen (z. B. leere Caches) existiert Logging, jedoch kein automatisches Monitoring; Einbindung in ein Betriebs-Monitoring wird empfohlen.

### 2. Läuft alles zuverlässig und effizient?
- **Getrennte Cache- und Netzlast**: Provider werden zuerst aus lokalen Caches gelesen und nur bei Bedarf parallel über das Netzwerk geladen. Timeouts werden pro Provider mit Retries abgesichert, um Hänger zu vermeiden.【F:src/build_feed.py†L460-L557】【F:src/utils/http.py†L10-L33】
- **Item-Bereinigung**: Duplikate werden fuzzy-identifiziert, ältere Items nach konfigurierbaren Grenzen verworfen und Feed-Einträge nach Relevanz sortiert – so bleibt der Feed fokussiert und performant.【F:src/build_feed.py†L430-L589】【F:src/build_feed.py†L640-L738】
- **Testabdeckung**: Die umfangreiche Testsuite deckt Parser, Filter und Provider-Edge-Cases ab und läuft fehlerfrei, was eine stabile Basis für Deployments bietet.【9e0fe0†L1-L3】

### 3. Werden Secrets sicher eingesetzt?
- **Konfigurationsquellen**: Der VOR-Zugang (`VOR_ACCESS_ID`) wird ausschließlich aus Umgebungsvariablen gelesen und fällt andernfalls auf den VAO-Standardtoken zurück; Logging maskiert Secrets implizit, weil sie nicht ausgegeben werden.【F:src/providers/vor.py†L260-L323】
- **Stations-Whitelists**: Zulässige Stationen können per ENV oder gepflegter Datei gesteuert werden; fehlende Werte lösen Fallbacks aus, ohne dass Hardcodierte Secrets im Repo landen.【F:src/providers/vor.py†L291-L342】
- **Request-Drosselung**: Ein täglicher Request-Zähler mit Datei-Lock verhindert unkontrollierte API-Last und läuft transaktionssicher, womit Missbrauch vorhandener Tokens erschwert wird.【F:src/providers/vor.py†L200-L275】

### 4. Funktionieren APIs und Stationen-Abgleich?
- **HTTP-Robustheit**: Gemeinsame Sessions mit Retry-Backoff schützen vor temporären Fehlern der Provider-APIs.【F:src/utils/http.py†L10-L33】
- **Stationsnormalisierung**: Aliasnamen, Koordinaten, Polygon-Prüfungen und ÖBB/VOR-Kennungen werden zentral normalisiert, wodurch Feed-Items konsistent dem Stationsverzeichnis zugeordnet werden.【F:src/utils/stations.py†L1-L160】
- **Aktualisierungsskripte**: `update_all_stations.py` führt die einzelnen Aktualisierungsskripte sequentiell aus und bricht bei Fehlern mit aussagekräftigem Statuscode ab – damit bleiben Stationsdaten synchron mit den Provider-APIs.【F:scripts/update_all_stations.py†L1-L74】

### 5. Wird der Feed korrekt befüllt?
- **Pipeline**: `_collect_items` vereint Cache- und Live-Daten, `_drop_old_items` und `_dedupe_items` halten den Feed schlank, `_make_rss` generiert validiertes XML und speichert gleichzeitig den State atomar.【F:src/build_feed.py†L460-L838】
- **Validierung**: Jeder Lauf schreibt den Feed über eine temporäre Datei und nutzt `os.fsync`, bevor atomar ersetzt wird – fehlerhafte Zwischenstände gelangen nicht in die Produktion.【F:src/build_feed.py†L820-L869】
- **Manueller Test**: Die Probeausführung erzeugte einen Feed mit 10 Items und protokolliert erwartete Warnungen für leere Provider-Caches, womit der End-to-End-Fluss bestätigt ist.【5875cb†L1-L3】

## Handlungsempfehlungen
1. **Monitoring erweitern** – Warnungen zu leeren Caches und State-Speicherfehlern in ein zentrales Monitoring aufnehmen, um Datenlücken schneller zu erkennen.【F:src/build_feed.py†L500-L536】【F:src/build_feed.py†L823-L838】
2. **Timeouts pro Provider prüfen** – Der globale Timeout von 25 s deckt Normalfälle ab; für langsame Provider könnte eine feinere Konfiguration (z. B. via ENV) fest im Betriebsschema dokumentiert werden.【F:src/build_feed.py†L144-L155】【F:src/build_feed.py†L500-L526】
3. **Pflege der Stations-Whitelist** – Die Datei `data/vor_station_ids_wien.txt` sollte regelmäßig automatisiert validiert werden, damit neue Linien rechtzeitig in den Feed gelangen.【F:src/providers/vor.py†L300-L342】
4. **Regelmäßige Feed-Probeläufe** – Der getestete CLI-Lauf sollte in die Betriebs-Checkliste aufgenommen werden, um vor Releases sicherzustellen, dass alle Caches & States konsistent sind.【F:src/build_feed.py†L846-L869】【5875cb†L1-L3】

## Durchgeführte Checks
- ✅ `pytest -q` – komplette Testsuite (233 Tests).【9e0fe0†L1-L3】
- ✅ `python -m src.build_feed` – End-to-End Feed-Erzeugung (Warnung erwartet, wenn Provider-Caches leer sind).【5875cb†L1-L3】
