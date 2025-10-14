# VOR API Test (2025-10-18)

- **Tool:** `python scripts/test_vor_api.py`
- **Token:** Weiterhin kein `VOR_ACCESS_ID`/`VAO_ACCESS_ID` verfügbar; das Skript verweigert den Abruf.
- **Ergebnis:** Abruf übersprungen, es wurden keine Events geliefert (`events_returned = 0`).
- **Zähler:** `data/vor_request_count.json` blieb unverändert (`delta = 0`, weiterhin 2 Requests am 2025-10-15).
- **Hinweis:** Sobald ein gültiger Schlüssel bereitsteht, den Test erneut ausführen, um Daten und Request-Zähler zu verifizieren.
