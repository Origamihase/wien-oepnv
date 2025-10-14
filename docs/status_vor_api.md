# VOR API Status

Stand: 2025-10-14T14:47:55Z

* Die Zugriffsdaten (`aid`) werden aktuell automatisiert aus der öffentlich auslieferbaren VAO-Webapp-Konfiguration ermittelt und erlauben wieder erfolgreiche API-Anfragen.
* Der direkte CSV-Export über `https://www.verkehrsauskunft.at/ogd/static/*.csv` liefert weiterhin HTTP 404/500; als Ersatz stellt `scripts/fetch_vor_haltestellen.py` die Haltestellen per `LocMatch`-Aufruf des VAO-MGate bereit.
* `data/vor-haltestellen.csv` enthält nun 54 vollständig aufgelöste Einträge (inklusive Koordinaten), wodurch alle relevanten Stationen in `data/stations.json` mit `vor_id` versehen sind.
