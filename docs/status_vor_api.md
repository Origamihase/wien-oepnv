# VOR API Status

Stand: 2025-10-14T14:47:55Z

* `VOR_ACCESS_ID` fällt auf den dokumentierten Standardwert `VAO` zurück; die Zugriffsdaten (`aid`) werden aktuell automatisiert aus der öffentlich auslieferbaren VAO-Webapp-Konfiguration ermittelt und erlauben wieder erfolgreiche API-Anfragen.
* Über `data/stations.json` stehen weiterhin alle gepflegten Stationsdaten als zentrale Quelle bereit. Die Datei selbst bleibt ohne VOR-spezifische Felder versioniert, wird jedoch beim Laden um Informationen aus `data/vor-haltestellen.mapping.json` ergänzt, sodass `vor_id`, Koordinaten und Aliasnamen automatisch verfügbar sind.
* Ohne zusätzliche Konfiguration nutzt der Provider dadurch sämtliche Wiener bzw. Pendler-Stationen. Falls `VOR_STATION_IDS` gesetzt ist, werden diese Werte wie bisher priorisiert, ansonsten dient die aus `stations.json` geladene Liste als Fallback.
* Der direkte CSV-Export über `https://www.verkehrsauskunft.at/ogd/static/*.csv` liefert weiterhin HTTP 404/500; als Ersatz stellt `scripts/fetch_vor_haltestellen.py` die Haltestellen per `LocMatch`-Aufruf des VAO-MGate bereit und erzeugt `data/vor-haltestellen.csv` mit derzeit 54 vollständig aufgelösten Einträgen.
