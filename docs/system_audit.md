# Systemprüfung Wien ÖPNV Feed

## Zusammenfassung
- Die Feed-Generierung liest ausschließlich aus den versionierten Cache-Dateien (`cache/<provider>/events.json`) und prüft alle Pfade gegen eine Positivliste. Dadurch bleiben Build-Prozess und Artefakte innerhalb des Repositoriums isoliert.【F:src/build_feed.py†L26-L55】【F:src/utils/cache.py†L12-L60】
- Beim Einsammeln der Provider-Einträge werden Cache- und Netzwerkquellen getrennt behandelt, Zeitüberschreitungen protokolliert und Ergebnisse normalisiert. Abgelaufene oder duplizierte Meldungen werden verlässlich entfernt, bevor der Feed entsteht.【F:src/build_feed.py†L461-L589】【F:src/build_feed.py†L592-L607】
- Alle Provider nutzen gemeinsam konfigurierte HTTP-Sessions mit Retries und individuellen Timeouts, sodass API-Ausfälle oder Rate Limits robuste Wiederholversuche auslösen.【F:src/utils/http.py†L15-L34】【F:src/providers/wl_fetch.py†L323-L356】【F:src/providers/oebb.py†L101-L151】【F:src/providers/vor.py†L398-L406】
- Secrets wie die VOR `accessId` werden ausschließlich per Umgebung bereitgestellt, vor dem Logging maskiert und optional über eine Whitelist von Stations-IDs eingeschränkt. Ein Fallback-Token wird nicht mehr verwendet.【F:src/providers/vor.py†L260-L415】
- Stationsdaten werden zentral normalisiert, Polygone für Wien ausgewertet und Aliasnamen kanonisiert. So bleibt der Abgleich zwischen Feed-Meldungen und Verzeichnis konsistent.【F:src/utils/stations.py†L46-L188】
- Die vollständige Testsuite (`pytest`) läuft fehlerfrei und deckt Parser, Filter, State-Handling sowie Provider-spezifische Sonderfälle ab (267 Tests).【fa60a0†L1-L38】

## Beobachtungen & Empfehlungen
1. **Pfad-Validierung & Logs** – Die Whitelist für Ausgabepfade umfasst `docs`, `data` und `log`. Falls zukünftige Artefakte (z. B. temporäre Debug-Dateien) benötigt werden, sollte die Liste gezielt erweitert werden, statt die Validierung zu lockern.【F:src/build_feed.py†L26-L68】
2. **Timeout-Konfiguration** – Der globale Provider-Timeout beträgt standardmäßig 25 s. Bei deutlich langsameren APIs könnte es sinnvoll sein, pro Provider granularere Grenzen anzubieten, wird aktuell aber über ENV-Variablen anpassbar gehalten.【F:src/build_feed.py†L146-L155】【F:src/build_feed.py†L503-L526】
3. **Station-Whitelist** – Für den VOR-Provider existieren sowohl ENV-gesteuerte Listen als auch dateibasierte Fallbacks. Die Pflege der Datei `data/vor_station_ids_wien.txt` sollte Teil des Betriebsprozesses sein, um Überraschungen bei neuen Linien zu vermeiden.【F:src/providers/vor.py†L291-L315】
4. **Fehlerbehandlung bei Cache-Leseproblemen** – Fehlerhafte JSON-Dateien führen zu Warnungen und einem leeren Datensatz. Eine ergänzende Beobachtung in Monitoring/Alerting würde helfen, solche Situationen frühzeitig zu erkennen.【F:src/utils/cache.py†L22-L60】
5. **Feed-Abdeckung** – Durch `_drop_old_items` werden Einträge nach Ablauf oder Maximalalter entfernt. Für sehr langfristige Baustellen sollte sichergestellt werden, dass `ABSOLUTE_MAX_AGE_DAYS` ausreichend groß bleibt, sonst fallen sie trotz aktiver `ends_at`-Werte heraus.【F:src/build_feed.py†L533-L589】

## Tests & Checks
- ✅ `pytest` – komplette Testsuite (267 Tests).【fa60a0†L1-L38】

