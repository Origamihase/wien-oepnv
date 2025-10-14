# VOR API Status

Stand: 2025-10-18T09:20:00Z

* `python scripts/test_vor_api.py` lädt Secrets jetzt automatisch aus `.env`, `data/secrets.env` oder `config/secrets.env`, sofern vorhanden. Damit steht `VOR_ACCESS_ID` unmittelbar beim Start zur Verfügung, ohne dass vorab ein `export` nötig ist.【F:src/utils/env.py†L85-L136】【F:scripts/test_vor_api.py†L162-L181】
* Beim erneuten Test am 2025-10-18 war weiterhin kein `VOR_ACCESS_ID` vorhanden, daher wurde der Abruf übersprungen und `data/vor_request_count.json` blieb unverändert (`delta = 0`, Stand 2 Requests am 2025-10-15).【571846†L1-L24】【F:log/2025-10-18_vor_api_test.md†L1-L6】
* Sobald ein gültiges Secret bereitsteht, den Test erneut ausführen: Ungültige Tokens führen zu HTTP 401 (`API_AUTH`) und erhöhen den lokalen Tageszähler trotzdem, valide Zugänge liefern Events und dokumentieren den Request-Verbrauch transparent im Report.【F:scripts/test_vor_api.py†L102-L149】
