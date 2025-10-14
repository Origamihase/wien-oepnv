# VOR API Test (2025-10-16)

- **Tool:** `python scripts/test_vor_api.py`
- **Token:** Kein `VOR_ACCESS_ID` oder `VAO_ACCESS_ID` in der Umgebung; Abruf wurde vom Skript übersprungen.
- **Ergebnis:** Keine Events erhalten (`events_returned = 0`).
- **Zähler:** `data/vor_request_count.json` blieb unverändert (`delta = 0`, Stand weiterhin 2 Requests am 2025-10-15).
- **Hinweis:** Für einen echten Abruf muss vor dem Test ein gültiger Zugangsschlüssel exportiert werden (`export VOR_ACCESS_ID=…`).
