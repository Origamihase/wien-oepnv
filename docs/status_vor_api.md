# VOR API Status

Stand: 2025-10-13T22:36:16Z

* `VOR_ACCESS_ID` fällt auf den dokumentierten Standardwert `VAO` zurück.
* Über `data/stations.json` stehen `vor_id`-Einträge zur Verfügung und werden als Fallback für `VOR_STATION_IDS` geladen.
* Ohne zusätzliche Konfiguration nutzt der Provider damit automatisch alle hinterlegten Stationen aus Wien bzw. dem Pendlergürtel.
