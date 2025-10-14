# VOR API manueller Test (2025-10-14)

- Befehl: `python scripts/test_vor_api.py`
- Ergebnis: Testlauf übersprungen, weil kein `VOR_ACCESS_ID` gesetzt war; Skript verweigert Nutzung des Fallback-Tokens `VAO`.
- Rückgabe: Exit-Code `2`, keine HTTP-Anfrage ausgelöst, Request-Zähler unverändert (`count = 10`).
- Nächste Schritte: Secret `VOR_ACCESS_ID` in die Umgebung laden (z. B. via `export VOR_ACCESS_ID=...`) und Test erneut ausführen.

## Diagnose (15.10.2025)

- Wiederholung des Laufs im Container bestätigt die Sperre: Der Report meldet `configured = false`,
  womit der Provider kein Secret findet und den Abruf stoppt.【568d23†L1-L27】
- Ein direkter Umgebungscheck zeigt, dass weder `VOR_ACCESS_ID` noch `VAO_ACCESS_ID` gesetzt sind (`{"VOR_ACCESS_ID": null,
  "VAO_ACCESS_ID": null}`); das Secret steht im aktuellen Workspace also nicht zur Verfügung und muss vor weiteren Tests
  exportiert werden.【6877a4†L1-L8】
