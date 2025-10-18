# VOR API Status

Stand: 2025-10-21T08:35:00Z

* `python scripts/test_vor_api.py` lädt Secrets jetzt automatisch aus `.env`, `data/secrets.env` oder `config/secrets.env`, sofern vorhanden. Damit steht `VOR_ACCESS_ID` unmittelbar beim Start zur Verfügung, ohne dass vorab ein `export` nötig ist.【F:src/utils/env.py†L85-L136】【F:scripts/test_vor_api.py†L162-L181】
* Am 2025-10-21 lieferte der Workflow `test-vor-api.yml` mit gültigem `VOR_ACCESS_ID` drei Events bei `HTTP 200`; der Tageszähler stieg von 4 auf 5 (`delta = +1`).【F:log/2025-10-21_vor_api_test.md†L1-L22】【F:data/vor_request_count.json†L1-L1】
* Frühere Tests mit dem Fallback-Token `VAO` schlugen am 2025-10-19 erwartungsgemäß mit `HTTP 401` fehl und erhöhten dennoch das Kontingent (2 → 4 am 2025-10-15).【F:log/2025-10-19_vor_api_test.md†L1-L7】
* Für aussagekräftige Ergebnisse ist weiterhin ein gültiger Schlüssel über `VOR_ACCESS_ID` erforderlich; ohne ihn werden Anfragen zwar abgelehnt, zählen aber zum Tageskontingent.【F:log/2025-10-21_vor_api_test.md†L1-L31】【F:scripts/test_vor_api.py†L102-L181】
