# VOR API Test – 14. Oktober 2025

## Vorgehen
- Befehl: `python scripts/test_vor_api.py`
- Umgebung: gültiger Zugangsschlüssel muss als Secret `VOR_ACCESS_ID` vorhanden sein.

## Ergebnisse
- Der Test wurde **abgebrochen**, weil kein `VOR_ACCESS_ID` gesetzt war und somit kein autorisierter Abruf erfolgen konnte.
- Der Request-Zähler blieb unverändert, da keine HTTP-Anfrage ausgelöst wurde.
- Das Skript weist nun explizit darauf hin, dass ein gültiger Zugangsschlüssel notwendig ist.

## Interpretation
- Ein Testlauf ohne gültiges `VOR_ACCESS_ID`-Secret liefert keine verwertbaren Ergebnisse und würde nur den täglichen Request-Zähler erhöhen.
- Um aussagekräftige Daten zu erhalten, muss vor dem Aufruf das produktive Zugangstoken über die Umgebung bereitgestellt werden.
- Das Skript verhindert unbeabsichtigte Abrufe mit dem nicht mehr akzeptierten Fallback-Zugang.
