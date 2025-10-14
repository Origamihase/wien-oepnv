# VOR API Test (2025-10-19)

- **Tool:** `python scripts/test_vor_api.py --access-id VAO`
- **Token:** Fallback-Zugang `VAO` wurde bewusst gesetzt, um die Fehlerreaktion zu überprüfen; der Server antwortete mit `HTTP 401 (API_AUTH)`.
- **Ergebnis:** Keine Events erhalten (`events_returned = 0`), der Lauf gilt als fehlgeschlagen (`success = false`).
- **Zähler:** `data/vor_request_count.json` stieg von 2 auf 4 Anfragen für den 15. Oktober 2025 (`delta = 2`).
- **Bewertung:** Selbst fehlgeschlagene Aufrufe verbrauchen Tageskontingent. Für einen erfolgreichen Test ist ein gültiger `VOR_ACCESS_ID` erforderlich.
