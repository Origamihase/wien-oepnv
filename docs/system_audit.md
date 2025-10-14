# Systemprüfung Wien ÖPNV Feed

## Executive Summary
- **Feed-Build ist isoliert und deterministisch**: Pfad-Validierungen, Rotations-Logging und streng gekapselte Cache-Zugriffe verhindern Seiteneffekte außerhalb der Verzeichnisse `docs/`, `data/` und `log/`.【F:src/build_feed.py†L26-L158】【F:src/utils/cache.py†L12-L103】
- **Pipeline filtert zuverlässig veraltete oder doppelte Meldungen**: Sammeln, Normalisieren, Deduplizieren und Altersbegrenzung decken sowohl Cache- als auch Live-Quellen ab und sorgen für einen konsistenten Feed.【F:src/build_feed.py†L461-L607】
- **Provider-Module sind robust und resilienzorientiert**: Gemeinsame HTTP-Retrys, spezifische Zeitlimits sowie Region-Filter und Namensharmonisierung sichern die Datenqualität für Wiener Linien, ÖBB und VOR.【F:src/utils/http.py†L11-L35】【F:src/providers/wl_fetch.py†L67-L140】【F:src/providers/oebb.py†L50-L174】【F:src/providers/vor.py†L360-L520】
- **Secrets und Zugangsdaten werden geschützt**: Tokens stammen ausschließlich aus ENV-Variablen, werden bei Bedarf auf sichere Defaults gesetzt und vor Logging maskiert.【F:src/providers/vor.py†L271-L417】【F:.github/workflows/build-feed.yml†L45-L148】
- **Stationsverzeichnis und Matching sind gepflegt**: Alias-Handling, Polygon-Checks und GTFS/OGD-Ableitungen sichern konsistente Namensräume für alle Abgleiche.【F:src/utils/stations.py†L25-L189】
- **Testsuite bestätigt Stabilität**: 233 automatisierte Tests decken Parser, Normalisierung, State-Handling und Provider-Sonderfälle ab.【efc424†L1-L3】

## Architektur & Build-Pipeline
### Cache-Isolation & Artefakte
Der Feed-Builder akzeptiert Pfade nur innerhalb einer Positivliste und erzwingt bei Abweichungen Ausnahmen. Dadurch bleiben Builds reproduzierbar und der Feed überschreibt keine fremden Dateien.【F:src/build_feed.py†L26-L118】 Die Cache-Helfer lesen ausschließlich `cache/<provider>/events.json`, validieren den Inhalt und schreiben aktualisierte Daten atomar – inklusive `fsync`, damit GitHub Actions konsistente Artefakte publizieren.【F:src/utils/cache.py†L12-L103】

### Feed-Aufbereitung
Alle aktivierten Provider werden zuerst aus den Cache-Dateien gelesen, danach optional parallel aus Netzwerkquellen mit `ThreadPoolExecutor` abgefragt. Ergebnislisten werden normalisiert, Zeitüberschreitungen protokolliert und leere Quellen markiert, sodass Warnungen sichtbar bleiben, der Feed aber dennoch entsteht.【F:src/build_feed.py†L461-L531】 Anschließend entfernt `_drop_old_items` abgelaufene oder zu alte Meldungen auf Basis von `pubDate`, `ends_at` und dem persistierten `first_seen`, während `_dedupe_items` identische GUIDs zusammenführt und längere/neuere Varianten bevorzugt.【F:src/build_feed.py†L533-L607】

### Zustandsverwaltung & Ausgabe
Der Feed-Builder liest `data/first_seen.json`, bereinigt ungültige Einträge, speichert Updates fsync-gesichert zurück und setzt frische `pubDate`-Werte, wenn Meldungen neu in den Feed aufgenommen werden. XML-Bausteine werden sanitisiert, CDATA-gesichert und mit lokalisierten Zeitangaben versehen, damit der Output valide bleibt.【F:src/build_feed.py†L376-L607】

### Automatisierung & Qualitätskontrolle
Der geplante Workflow baut alle 30 Minuten: Er aktualisiert zuerst die Provider-Caches über wiederverwendbare Jobs, setzt alle benötigten ENV-Variablen, installiert Abhängigkeiten, führt die Tests aus und generiert den Feed. Optional entdeckt er neue VOR-Stationen und committet diese nach erfolgreicher Ermittlung.【F:.github/workflows/build-feed.yml†L1-L200】

## Provider-Prüfung
### Wiener Linien (WL)
Das Modul harmonisiert Titel, Stationen und Kontexttexte, erkennt aktive Zeiträume und filtert reine Facility-Hinweise. HTTP-Zugriffe nutzen einen dedizierten User-Agent sowie Retries; fehlende Daten führen zu Warnungen statt stillen Fehlern. Stationen werden über `canonical_name` normalisiert, was den Abgleich mit `stations.json` konsistent hält.【F:src/providers/wl_fetch.py†L35-L199】

### ÖBB
Der ÖBB-Parser bereinigt Titel (z. B. doppelte Pfeile, Bahnhofsvorsätze), wandelt HTML-Beschreibungen in Klartext um und filtert Meldungen anhand der Whitelist `is_in_vienna`/`is_pendler`. Rate-Limits werden ausgewertet (`Retry-After`), Requests laufen mit Retries und eigenem User-Agent. Secrets können über `OEBB_RSS_URL` überschrieben werden, fallen aber bei Leerwerten auf die offizielle URL zurück.【F:src/providers/oebb.py†L50-L199】

### VOR
Die VOR-Anbindung zieht Zugangsdaten aus ENV (Fallback `VAO`), lädt optional Whitelists aus Dateien und schützt Tokens durch Maskierung in Logs. Station-Aufrufe laufen mit begrenzter Rotation (`MAX_STATIONS_PER_RUN`, `ROTATION_INTERVAL_SEC`) und Retry-Konfiguration. Bus-Linien lassen sich granular erlauben/filtern; ohne Access-ID deaktiviert sich der Provider kontrolliert.【F:src/providers/vor.py†L271-L520】【F:src/providers/vor.py†L857-L864】

## Stations- und Datenpflege
`stations.py` stellt lru-cached Polygone für die Wiener Stadtgrenze bereit, normalisiert Schreibweisen (Akzente, Bahnhofs-Zusätze) und liefert strukturierte Metadaten für Alias- und Koordinatenabgleiche. Dadurch erfüllen Matching-Funktionen wie `is_in_vienna`, `is_pendler` und `vor_station_ids` ihren Zweck ohne Dubletten oder inkonsistente Schreibweisen.【F:src/utils/stations.py†L25-L189】 Der Stationsworkflow aktualisiert monatlich automatisiert die Excel-Quelle und schreibt `stations.json`, sodass Linienabgleiche aktuell bleiben.【F:.github/workflows/update-stations.yml†L1-L80】

## Sicherheit & Secrets
Alle Secrets gelangen per GitHub-Workflow in die Umgebung und verlassen den Prozess nicht. VOR-Access-IDs werden maskiert (`_sanitize_access_id`), Dateipfade bleiben im Repo, und sensible Parameter wie `LOG_DIR` dürfen nur in Whitelist-Verzeichnisse verweisen. Fehlende Secrets führen zu kontrollierten Deaktivierungen statt Crashes.【F:.github/workflows/build-feed.yml†L45-L148】【F:src/build_feed.py†L26-L118】【F:src/providers/vor.py†L271-L417】【F:src/providers/vor.py†L857-L864】

## Zuverlässigkeit & Performance
Retries und Timeouts sind zentral konfigurierbar (`PROVIDER_TIMEOUT`, `HTTP_TIMEOUT`) und per ENV anpassbar, ohne Codeänderung nötig zu machen. Logging nutzt Rotationsdateien, damit Fehleranalysen möglich bleiben, und Provider-Limits (`ThreadPoolExecutor`, `MAX_STATIONS_PER_RUN`) verhindern Überlastung externer APIs.【F:src/build_feed.py†L69-L158】【F:src/build_feed.py†L503-L526】【F:src/providers/vor.py†L360-L520】

## Feed-Qualität & Konsistenz
Titel und Beschreibungen werden von HTML/CSS befreit, auf sinnvolle Satzlängen gekürzt und mit lokalisierten Datumsangaben ergänzt. Redundante Zeilen (z. B. doppelte Überschriften, „Zeitraum:“) werden entfernt, während zusätzliche Metadaten (`ext:first_seen`, `ext:starts_at`, `ext:ends_at`) erhalten bleiben. Dadurch sind sämtliche Feed-Zeilen zweckgebunden und lesbar.【F:src/build_feed.py†L200-L400】【F:src/build_feed.py†L677-L800】

## Empfehlungen & To-dos
1. **Monitoring für Cache-Anomalien**: Warnungen bei invaliden JSON-Dateien sollten in externem Monitoring auftauchen, um defekte Provider-Caches schneller zu entdecken.【F:src/utils/cache.py†L22-L60】
2. **Granulare Timeout-Defaults prüfen**: Der gemeinsame Provider-Timeout von 25 s ist ein guter Ausgangspunkt, könnte aber langfristig pro Provider weiter verfeinert werden, falls Upstream-Latenzen divergieren.【F:src/build_feed.py†L142-L155】【F:src/build_feed.py†L503-L526】
3. **VOR-Whitelist pflegen**: `data/vor_station_ids_wien.txt` sollte regelmäßig kontrolliert werden, damit neue Linien kurzfristig in die Rotation gelangen.【F:.github/workflows/build-feed.yml†L98-L176】【F:src/providers/vor.py†L287-L315】
4. **Langläufer beobachten**: Für Baustellen mit Laufzeiten > ABSOLUTE_MAX_AGE_DAYS ist eine organisatorische Entscheidung nötig, ob sie weiterhin im Feed bleiben sollen oder separat kommuniziert werden.【F:src/build_feed.py†L533-L589】

## Tests & Checks
- ✅ `pytest -q` – komplette Testsuite (233 Tests).【efc424†L1-L3】
