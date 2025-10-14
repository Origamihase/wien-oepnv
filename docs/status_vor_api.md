# VOR API Status

Stand: 2025-10-15T21:04:00Z

* `python scripts/test_vor_api.py` lädt Secrets jetzt automatisch aus `.env`, `data/secrets.env` oder `config/secrets.env`, sofern vorhanden. Damit steht `VOR_ACCESS_ID` unmittelbar beim Start zur Verfügung, ohne dass vorab ein `export` nötig ist.【F:src/utils/env.py†L85-L136】【F:scripts/test_vor_api.py†L162-L181】
* Fehlen diese Dateien, verhält sich das Skript unverändert defensiv: Der Lauf wird übersprungen, solange kein `VOR_ACCESS_ID` in der Umgebung existiert, und der Tageszähler in `data/vor_request_count.json` bleibt unverändert.【F:scripts/test_vor_api.py†L102-L149】
* Frühere Tests mit ungültigen Tokens führten zu HTTP 401 (`API_AUTH`) und erhöhten den Zähler trotzdem. Sobald das Secret im
  Ausführungsumfeld bereitsteht, sollte der Test erneut ausgeführt werden, um die Antwort der API und den Einfluss auf den
  Request-Zähler zu prüfen.
