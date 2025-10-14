# VOR API Status

Stand: 2025-10-19T09:30:00Z

* `python scripts/test_vor_api.py` lädt Secrets jetzt automatisch aus `.env`, `data/secrets.env` oder `config/secrets.env`, sofern vorhanden. Damit steht `VOR_ACCESS_ID` unmittelbar beim Start zur Verfügung, ohne dass vorab ein `export` nötig ist.【F:src/utils/env.py†L85-L136】【F:scripts/test_vor_api.py†L162-L181】
* Am 2025-10-19 wurde ein Testlauf mit bewusst gesetztem Fallback-Token `VAO` durchgeführt; die API verweigerte den Zugriff mit `HTTP 401`, es kamen keine Events zurück und der Tageszähler erhöhte sich trotzdem um zwei Anfragen (2 → 4 am 2025-10-15).【F:log/2025-10-19_vor_api_test.md†L1-L7】【F:data/vor_request_count.json†L1-L1】
* Für aussagekräftige Ergebnisse ist ein gültiger Schlüssel über `VOR_ACCESS_ID` erforderlich; fehlgeschlagene Tests verbrauchen das Kontingent dennoch, wie der oben dokumentierte Lauf zeigt.【F:log/2025-10-19_vor_api_test.md†L1-L7】【F:scripts/test_vor_api.py†L102-L149】
