# VOR API Test – 14. Oktober 2025

## Vorgehen
- Befehl: `python scripts/test_vor_api.py`
- Umgebung: gültiger Zugangsschlüssel muss als Secret `VOR_ACCESS_ID` vorhanden sein.
- Die Umgebung lädt Secrets jetzt automatisch aus `.env`, `data/secrets.env` oder `config/secrets.env`, sofern vorhanden. Ohne entsprechende Datei bleibt der Test weiterhin blockiert.【F:src/utils/env.py†L85-L136】【F:scripts/test_vor_api.py†L162-L181】

## Ergebnisse
- Der Test wurde **abgebrochen**, weil kein `VOR_ACCESS_ID` gesetzt war und somit kein autorisierter Abruf erfolgen konnte.
- Der Request-Zähler blieb unverändert, da keine HTTP-Anfrage ausgelöst wurde.
- Das Skript weist nun explizit darauf hin, dass ein gültiger Zugangsschlüssel notwendig ist.
- Die neue Ausgabestruktur enthält zusätzlich die verwendete Basis-URL (`VOR_BASE_URL`) und markiert, ob ein Override via CLI-Parameter genutzt wurde.

## Interpretation
- Ein Testlauf ohne gültiges `VOR_ACCESS_ID`-Secret liefert keine verwertbaren Ergebnisse und würde nur den täglichen Request-Zähler erhöhen.
- Um aussagekräftige Daten zu erhalten, muss vor dem Aufruf das produktive Zugangstoken über die Umgebung bereitgestellt werden.
- Das Skript verhindert unbeabsichtigte Abrufe, solange kein Secret bereitsteht, und vermeidet damit den gesperrten Fallback-Zugang.
- Optional lassen sich Token (`--access-id`) und API-Endpunkt (`--base-url`) zur Laufzeit überschreiben, sofern die VAO-Dokumentation aus Abschnitt 4 (Authentifizierung & Ergebnisformat) beachtet wird: Jeder Request benötigt `accessId=<your_key_here>` sowie `format=json` für JSON-Antworten.

## Nutzungshinweise
- Aufruf: `python scripts/test_vor_api.py [--access-id TOKEN] [--base-url https://…/]`
- Exit-Codes:
  - `0` – Abruf erfolgreich und hat Events geliefert.
  - `1` – Fehlerhafte oder leere Antwort.
  - `2` – Testlauf wurde übersprungen (z. B. fehlendes Token).
- Die Ausgabe listet den maskierten Access Key, den gewählten Basis-Endpunkt und den Delta-Wert des Request-Zählers, damit nachvollziehbar bleibt, ob ein Test den Tageszähler erhöht hat.

## Update 18. Oktober 2025

- Wiederholter Aufruf von `python scripts/test_vor_api.py` ohne vorhandenes `VOR_ACCESS_ID` führte erneut zu einem übersprungenen Lauf (`skipped = true`).【571846†L1-L24】
- Der Request-Zähler blieb unverändert bei zwei Anfragen vom 15. Oktober 2025 (`delta = 0`).【571846†L1-L24】【F:log/2025-10-18_vor_api_test.md†L1-L6】
- Für eine valide Aussage über die Datenlage muss vor dem nächsten Test ein gültiges Secret bereitgestellt werden (z. B. via `.env`).【F:README.md†L363-L377】
