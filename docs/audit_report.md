# Auditbericht

## Zusammenfassung
- Alle 233 automatisierten Tests laufen fehlerfrei durch. Damit sind u. a. Cache-Verarbeitung, Feed-Erzeugung, Stationsabgleich und Fehlerbehandlung abgedeckt.
- Die Feed-Generierung arbeitet ausschließlich mit Repository-internen Cache-Dateien, greift kontrolliert auf Umgebungsvariablen zu und schützt Dateipfade vor Ausbrüchen aus dem Projektverzeichnis.
- Es sind keine hartcodierten Secrets im Repository hinterlegt; alle sensiblen Einstellungen werden per Umgebungsvariablen erwartet und validiert.

## Teststatus
`pytest` wurde im Container ausgeführt; alle Tests bestanden ohne Fehler. Damit sind Funktionen wie `_collect_items`, das Schreiben des Feed-Files ohne Netzwerkzugriff sowie die Validierung von Stationsdaten abgedeckt.

## Sicherheit und Secrets
- Pfadangaben werden durch `_resolve_env_path` und `_validate_path` auf erlaubte Verzeichnisse begrenzt, wodurch unbeabsichtigte Dateizugriffe verhindert werden.【F:src/build_feed.py†L30-L68】
- Umgebungsvariablen für Betriebsparameter werden mit `get_int_env` bzw. `get_bool_env` eingelesen, invaliden Werte protokolliert und sichere Defaults verwendet.【F:src/build_feed.py†L132-L157】【F:src/utils/env.py†L17-L74】
- In der Codebasis befinden sich keine hart hinterlegten API-Schlüssel oder anderen Secrets; API-Zugänge werden ausschließlich über env-Variablen aktiviert (`*_ENABLE`).【F:src/build_feed.py†L97-L120】

## Zuverlässigkeit der Provider- und Feed-Logik
- Die Feed-Erzeugung greift auf lokal versionierte Cache-Dateien zurück und kann auch ohne aktuelle Providerdaten einen gültigen Feed erzeugen, wobei Warnungen protokolliert werden.【F:tests/test_build_feed_cache.py†L35-L88】
- Tests stellen sicher, dass Cache-Inhalte korrekt geladen und sortiert werden und dass Datumsformate robust gegen Formatierungsfehler sind.【F:tests/test_build_feed_cache.py†L91-L140】

## Stationsabgleich
- Die Stations-Hilfsfunktionen erkennen Alias-Kollisionen und melden diese per Log, sodass Inkonsistenzen beim Abgleich auffallen.【F:tests/test_station_alias_collision.py†L7-L33】
- Weitere Tests (z. B. `tests/test_vor_*`, `tests/test_wl_*`) validieren das Zusammenspiel mit VOR- und Wiener-Linien-Daten sowie die Aktualisierung der Stationsverzeichnisse.

## Handlungsempfehlungen
- Aktuell besteht kein unmittelbarer Handlungsbedarf. Empfohlen wird, die vorhandenen Tests regelmäßig in der CI laufen zu lassen und bei Änderungen an den Provider-Schnittstellen gezielt neue Tests hinzuzufügen.
