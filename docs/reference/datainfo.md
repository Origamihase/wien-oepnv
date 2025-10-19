---
title: "GET /datainfo"
description: "Beschreibung des Datainfo-Service inklusive Struktur der Betreiber-, Produkt- und Kategorienlisten für nachgelagerte Filter."
---

# GET /datainfo

## Kurzbeschreibung
Liefert Metadaten zu Betreibern, Verwaltungen, Produktklassen und Produkten, die in der VAO ReST API verfügbar sind. Die Informationen können zur Filterung von Trip- und Stationboard-Ergebnissen genutzt werden.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |

## Antwort

- `Operator`-Elemente liefern Kurz- und Langnamen sowie zugehörige `administration`-Codes.
- `Product`-Elemente enthalten Kategorie (`catOutL`), Klassencodes (`cls`) und Icon-Definitionen.
- `ProductCategory` gruppiert Produkte einer Produktklasse.

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/datainfo" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  -H "Accept: application/json"
```
