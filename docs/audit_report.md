# Projekt-Audit: wien-oepnv

## Vorgehen
- Vollständige Test-Suite mit `pytest` ausgeführt.
- Quellcode der Kernmodule (`src/build_feed.py`, `src/providers/*`, `src/utils/*`) sowie die begleitenden Tests und Dokumentation geprüft.
- Fokus auf Zuverlässigkeit, Effizienz und sicheren Umgang mit Secrets.

## Beobachtungen

### Zuverlässigkeit
- Pfadzugriffe für Ausgabedateien und Logs werden strikt auf die erlaubten Wurzeln `docs/`, `data/` und `log/` beschränkt. Dies verhindert unbeabsichtigte Schreibzugriffe außerhalb des Repos, selbst wenn Umgebungsvariablen manipuliert werden.【F:src/build_feed.py†L27-L139】
- Provider-Datenquellen aus Cache und Netzwerk werden parallel geladen; Fehler einzelner Quellen werden protokolliert und blockieren den Feed nicht. Netzwerkzugriffe sind in einen konfigurierbaren Timeout eingebettet, wodurch das Gesamtsystem robust gegen hängende Provider bleibt.【F:src/build_feed.py†L442-L511】
- Die Testsuite deckt 226 Tests ab und verifiziert u. a. Datumshandhabung, State-Management, Provider-Limits und Fehlerpfade. In der aktuellen Revision laufen alle Tests erfolgreich durch.【9c08e0†L1-L14】

### Effizienz
- Netzwerk-Provider werden in einem `ThreadPoolExecutor` mit dynamischer Worker-Anzahl ausgeführt; dadurch werden langsame Quellen parallelisiert und die Laufzeit verkürzt, ohne unnötige Threads zu starten.【F:src/build_feed.py†L484-L511】
- Datumsnormalisierung, Deduplikation und Altersprüfungen erfolgen in-place auf den geladenen Items. Dadurch werden Mehrfachdurchläufe vermieden und die Feedgröße kontrolliert.【F:src/build_feed.py†L514-L603】

### Sicherheit & Secrets
- Zugriffsdaten für die VOR-API werden ausschließlich über Umgebungsvariablen bezogen. Vor dem Logging werden `accessId`-Werte aus allen bekannten Formaten maskiert; begleitende Tests stellen sicher, dass keine Klartexte im Log landen.【F:src/providers/vor.py†L270-L361】【F:tests/test_vor_accessid_not_logged.py†L1-L53】
- Für Wiener Linien lässt sich eine alternative Basis-URL per Secret setzen; der Code fällt ansonsten auf den öffentlichen OGD-Endpunkt zurück und speichert keine sensiblen Daten im Repo.【F:src/providers/wl_fetch.py†L52-L68】

## Handlungsempfehlungen
- Tests regelmäßig in der Zielumgebung (z. B. CI/CD) ausführen, um die hohe Abdeckung beizubehalten.
- Beim Hinzufügen weiterer Provider auf die vorhandenen Muster achten (Timeouts, Secret-Maskierung), um die etablierten Sicherheits- und Robustheitsstandards zu halten.
- Das Logging der Request-Zähler-Datei (`data/vor_request_count.json`) produziert bei seltenen I/O-Problemen lediglich Warnungen; wer zusätzliche Transparenz benötigt, kann hier künftig eine Metrik oder Alerting integrieren.

Insgesamt zeigt das Projekt in der geprüften Fassung keine dringenden Fehler oder offensichtlichen Sicherheitsschwachstellen. Die bestehenden Mechanismen für Pfadvalidierung, Timeout-Steuerung und Secret-Schutz sind konsistent umgesetzt.
