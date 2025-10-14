# VOR API Status

Stand: 2025-10-15T21:04:00Z

* `python scripts/test_vor_api.py` wurde erneut im Container ausgeführt. Der Lauf wird weiterhin übersprungen, weil kein Secret
  `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` verfügbar ist (`configured = false`). Damit werden keine HTTP-Anfragen ausgelöst und der
  Tageszähler in `data/vor_request_count.json` bleibt unverändert bei `count = 2` (Δ = 0).【568d23†L1-L27】
* Eine Umgebungsprüfung bestätigt, dass weder `VOR_ACCESS_ID` noch `VAO_ACCESS_ID` im aktuellen Workspace gesetzt sind. Die
  Secrets stehen in dieser Umgebung also nicht zur Verfügung und müssen vor dem Testlauf ausdrücklich exportiert werden (z. B.
  per `export VOR_ACCESS_ID=…`).【6877a4†L1-L8】
* Solange kein gültiges Token injiziert wird, blockiert das Skript die Ausführung und verhindert Abrufe ohne Secret.【F:scripts/test_vor_api.py†L102-L141】
* Frühere Tests mit ungültigen Tokens führten zu HTTP 401 (`API_AUTH`) und erhöhten den Zähler trotzdem. Sobald das Secret im
  Ausführungsumfeld bereitsteht, sollte der Test erneut ausgeführt werden, um die Antwort der API und den Einfluss auf den
  Request-Zähler zu prüfen.
