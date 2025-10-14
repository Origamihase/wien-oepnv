# VOR API Test – 14. Oktober 2025

## Vorgehen
- Befehl: `python scripts/test_vor_api.py`
- Umgebung: gültiger Zugangsschlüssel muss als Secret `VOR_ACCESS_ID` vorhanden sein.
- Im aktuellen Container ist weder `VOR_ACCESS_ID` noch `VAO_ACCESS_ID` gesetzt (`{"VOR_ACCESS_ID": null, "VAO_ACCESS_ID": null}`).
  Ohne vorheriges `export VOR_ACCESS_ID=…` blockiert das Testszenario daher bewusst den Abruf.【6877a4†L1-L8】

## Ergebnisse
- Der Test wurde **abgebrochen**, weil kein `VOR_ACCESS_ID` gesetzt war und somit kein autorisierter Abruf erfolgen konnte.
- Der Request-Zähler blieb unverändert, da keine HTTP-Anfrage ausgelöst wurde.
- Das Skript weist nun explizit darauf hin, dass ein gültiger Zugangsschlüssel notwendig ist.
- Die neue Ausgabestruktur enthält zusätzlich die verwendete Basis-URL (`VOR_BASE_URL`) und markiert, ob ein Override via CLI-Parameter genutzt wurde.

## Interpretation
- Ein Testlauf ohne gültiges `VOR_ACCESS_ID`-Secret liefert keine verwertbaren Ergebnisse und würde nur den täglichen Request-Zähler erhöhen.
- Um aussagekräftige Daten zu erhalten, muss vor dem Aufruf das produktive Zugangstoken über die Umgebung bereitgestellt werden.
- Das Skript verhindert unbeabsichtigte Abrufe mit dem nicht mehr akzeptierten Fallback-Zugang.
- Optional lassen sich Token (`--access-id`) und API-Endpunkt (`--base-url`) zur Laufzeit überschreiben, sofern die VAO-Dokumentation aus Abschnitt 4 (Authentifizierung & Ergebnisformat) beachtet wird: Jeder Request benötigt `accessId=<your_key_here>` sowie `format=json` für JSON-Antworten.

## Nutzungshinweise
- Aufruf: `python scripts/test_vor_api.py [--access-id TOKEN] [--base-url https://…/] [--allow-default-token]`
- Exit-Codes:
  - `0` – Abruf erfolgreich und hat Events geliefert.
  - `1` – Fehlerhafte oder leere Antwort.
  - `2` – Testlauf wurde übersprungen (z. B. fehlendes Token).
- Die Ausgabe listet den maskierten Access Key, den gewählten Basis-Endpunkt und den Delta-Wert des Request-Zählers, damit nachvollziehbar bleibt, ob ein Test den Tageszähler erhöht hat.
