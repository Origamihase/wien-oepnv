# VOR API Status

Stand: 2025-10-14T14:47:55Z

* `VOR_ACCESS_ID` fällt auf den dokumentierten Standardwert `VAO` zurück; die Zugriffsdaten (`aid`) werden aktuell automatisiert aus der öffentlich auslieferbaren VAO-Webapp-Konfiguration ermittelt und erlauben wieder erfolgreiche API-Anfragen.
* Ohne zusätzliche Konfiguration nutzt der Provider automatisch alle hinterlegten Stationen aus Wien bzw. dem Pendlergürtel, da `data/stations.json` weiterhin als zentrale Quelle dient und auf Laufzeitebene um VOR-Felder ergänzt wird.
* Der direkte CSV-Export über `https://www.verkehrsauskunft.at/ogd/static/*.csv` liefert weiterhin HTTP 404/500; als Ersatz stellt `scripts/fetch_vor_haltestellen.py` die Haltestellen per `LocMatch`-Aufruf des VAO-MGate bereit.
* `data/vor-haltestellen.csv` enthält nun 54 vollständig aufgelöste Einträge (inklusive Koordinaten); `data/vor-haltestellen.mapping.json` ergänzt zur Laufzeit alle relevanten Stationen um `vor_id`, Koordinaten und optionale Aliasnamen.
