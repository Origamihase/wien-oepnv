# Security Review

## Zusammenfassung
- Keine bekannten CVEs in den produktiven oder Entwicklungs-Abhängigkeiten gemäß `pip-audit`.
- Secrets werden über `.env`-Dateien geladen und durch Hilfsfunktionen validiert; die CLI bietet einen Secret-Scan.
- Pfad- und Log-Schutzmechanismen reduzieren das Risiko unbeabsichtigter Datenabflüsse.

## Abhängigkeits-Sicherheit
- `pip-audit` gegen `requirements.txt` und `requirements-dev.txt` meldete keine bekannten Schwachstellen. Es empfiehlt sich, den Audit in die CI-Pipeline aufzunehmen, damit neue CVEs automatisiert erkannt werden.【037b71†L1-L1】

## Geheimnis-Management
- `src/utils/env.py` kapselt den Zugriff auf Umgebungsvariablen, erlaubt das Laden von `.env`-Dateien und protokolliert ungültige Werte ohne Secrets zu loggen, wodurch Fehlkonfigurationen sichtbar, aber Geheimnisse geschützt bleiben.【F:src/utils/env.py†L1-L158】
- Die CLI stellt unter `wien-oepnv security scan` einen Secret-Scanner bereit, der das Skript `scripts/scan_secrets.py` aufruft und zusätzliche Argumente erlaubt.【F:src/cli.py†L255-L366】
- `scripts/scan_secrets.py` und `src/utils/secret_scanner.py` scannen getrackte Dateien nach hochentropischen Strings, Bearer-Headern und typischen Zuweisungen sensibler Werte; ein `.secret-scan-ignore` kann Ausnahmen definieren.【F:scripts/scan_secrets.py†L1-L88】【F:src/utils/secret_scanner.py†L1-L152】

## Daten- und Log-Schutz
- `src/feed/config.py` erzwingt, dass konfigurierbare Pfade in die Projektordner `docs/`, `data/` oder `log/` zeigen, um Directory-Traversal oder versehentliches Überschreiben sensibler Dateien zu verhindern.【F:src/feed/config.py†L49-L200】
- Der VOR-Provider entfernt Access-IDs und Tokens konsequent aus Logmeldungen, bevor sie ausgegeben werden, wodurch sensible Header nicht im Klartext landen.【F:src/providers/vor.py†L65-L125】

## Risiken & Empfehlungen
1. Integriere `pip-audit` als festen Schritt in CI (z. B. täglicher Job oder Teil der Test-Pipeline), um CVE-Regressionen automatisch zu blockieren.【037b71†L1-L1】
2. Ergänze die CI um den bestehenden Secret-Scan oder einen Pre-Commit-Hook, damit die manuell verfügbaren Tools zuverlässig ausgeführt werden.【F:src/cli.py†L255-L366】【F:scripts/scan_secrets.py†L37-L85】
3. Erwäge, Versionsranges in `requirements*.txt` enger zu pinnen oder mit Lockfiles zu arbeiten, um Supply-Chain-Risiken durch ungetestete Minor-Releases zu reduzieren.【F:requirements.txt†L1-L7】【F:requirements-dev.txt†L1-L4】
