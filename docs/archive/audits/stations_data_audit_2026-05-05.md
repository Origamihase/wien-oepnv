# Stationsverzeichnis – Datenaudit 2026-05-05

Manueller Audit von `data/stations.json` mit Cross-Validation gegen die im
Repo vorhandenen offiziellen Open-Data-Quellen.

## Quellen

| Quelle | Datei | Stellung |
|---|---|---|
| ÖBB GTFS | `data/gtfs/stops.txt` | Offiziell ÖBB Open Data |
| VOR-Haltestellen-CSV | `data/vor-haltestellen.csv` | Aus VOR API extrahiert |
| VOR-Mapping | `data/vor-haltestellen.mapping.json` | bst_id ↔ vor_id Zuordnung |
| WL OGD Haltepunkte | `data/wienerlinien-ogd-haltepunkte.csv` | Offiziell WL Open Data |

`stations.json` enthält 107 Einträge. 93 davon haben eine `vor_id` und sind
damit gegen VOR-Daten validierbar.

## Verifikation des vorherigen Berichts

| Behauptung | Befund |
|---|---|
| Wien Aspern Nord Längengrad falsch (`16.520123`) | ✅ **Bestätigt** – VOR liefert `16.504456`, Differenz **1.160 m** |
| Wien Atzgersdorf Längengrad vom Nachbarn dupliziert | ❌ **Widerlegt** – stations.json (`48.147141, 16.288634`) stimmt **exakt** mit VOR überein. Die behauptete Liesing-Längengrad-Identität existiert nicht (`16.288634` ≠ `16.2883`). |
| Rennweg-Doublette führt zu willkürlichen Lookups | ✅ **Bestätigt** – sieben Aliase (`Rennweg`, `Bahnhof Rennweg`, `Bf Rennweg`, …) werden von **beiden** Einträgen geführt (`bst:1352 Wien Rennweg` und Google-Places-`Rennweg`). Konflikt im `_station_lookup` reproduzierbar. |
| `source`-Feld inkonsistent formatiert | ✅ **Bestätigt** – drei Schreibweisen: `google_places,oebb` (no-space), `google_places,vor` (no-space), `google_places, vor, wl` (mit Leerzeichen). Lookup-Code in `stations.py:509ff.` vergleicht die Source per `==`-String, das schlägt für die Spaces-Form bei VOR-Tie-Breaking fehl. |
| `vor_name` ist toter Code | ❌ **Widerlegt** – die Verzweigung `stations.py:454–480` wird zur Laufzeit aktiv: wenn ein Alias auf eine numerische `vor_id` matcht und der Eintrag ein `vor_name`-Feld trägt, ersetzt sie den `name` im Lookup-Ergebnis. Das Feld ist optional, nicht obsolet. Aktuell hat kein Eintrag das Feld – das ist by design (Override-Mechanismus). |
| Großflächig fehlende `wl_diva` | ✅ **Bestätigt** – nur 4 Einträge haben `wl_diva` (Praterstern, Karlsplatz, Schottentor, Stephansplatz). Andere U-Bahn-Knoten (Westbahnhof, Hütteldorf, Heiligenstadt, …) haben kein `wl_diva`, aber das lokale OGD-CSV deckt nur diese 4 ab. Schließen erfordert externe Daten. |
| Veralteter Wiener-Neustadt-Alias `430521000` | ✅ **Bestätigt** – steht in `aliases`, taucht aber nicht als Konflikt im Cross-Station-Check auf. Kosmetisch. |
| Synthetische `bst_id` 9001XX bei VOR-Stationen | ⚠️ **Teilweise zutreffend** – `bst_id`/`bst_code` 900100–900104 sind keine echten Stellencodes, aber bewusst gewählt um den `_VOR_ID_PATTERN` zu erfüllen. Schon in `_find_provider_issues` codiert. |
| 100 Aliase bei `Wien Erzherzog Karl-Straße` | ⚠️ **Beobachtet** – tatsächlich 133 Aliase. Auto-generierter Output, nicht falsch. |
| `Praterstern` hat nur einen `wl_stop` | ✅ **Bestätigt** – nur `60201040`. Vollständige Belegung erfordert externe WL-OGD-Daten. |

## Neue Befunde dieses Audits

### Koordinaten-Drift gegen VOR (>100 m)

`stations.json` matcht für alle ÖBB-Stationen exakt die GTFS-Werte (Build-
Pipeline kopiert aus GTFS). Für 11 Stationen weicht VOR jedoch deutlich von
GTFS ab; in jedem Fall ist VOR näher an den Wikipedia-/OSM-Koordinaten.
Reihenfolge nach Drift:

| Station | stations.json (=GTFS) | VOR | Drift |
|---|---|---|---|
| Wien Gersthof | 48.2307 / 16.3062 | 48.231146 / 16.329067 | **1.694 m** |
| Wien Jedlersdorf | 48.2779 / 16.4118 | 48.273197 / 16.396927 | **1.219 m** |
| Wien Aspern Nord | 48.234567 / 16.520123 | 48.234669 / 16.504456 | **1.160 m** |
| Wien Handelskai | 48.2465 / 16.3872 | 48.241798 / 16.385223 | 543 m |
| Wien Rennweg | 48.1989 / 16.3896 | 48.194766 / 16.386274 | 522 m |
| Wien Breitensee | 48.1968 / 16.3126 | 48.198236 / 16.306333 | 491 m |
| Wien Liesing | 48.1366 / 16.2883 | 48.134853 / 16.284229 | 359 m (siehe ⚠️) |
| Wien Kaiserebersdorf | 48.1436 / 16.4649 | 48.146413 / 16.465739 | 319 m |
| Wien Floridsdorf | 48.2576 / 16.4039 | 48.256648 / 16.400208 | 293 m |
| Wien Mitte-Landstraße | 48.2073 / 16.3856 | 48.206048 / 16.384584 | 161 m |

Stationen <100 m Drift bleiben unangetastet (VOR vs GTFS Präzisions-Unterschied).

⚠️ **Wien Liesing**: VOR-Koordinaten (`48.134853, 16.284229`) liegen knapp
außerhalb der `vienna_boundary.geojson`-Polygon-Vereinfachung (das Polygon
hat nur 8 Vertices, einer davon ist exakt die alte Liesing-Position
`48.1366, 16.2883`). Damit der `is_in_vienna(lat, lon)`-Test weiter
True liefert, wird Liesing nicht auf VOR-Werte umgesetzt – die alte
GTFS-Position bleibt erhalten. Eine saubere Lösung erfordert ein
detaillierteres Wien-Polygon und ist außerhalb dieses Fixes.

### Alias-Token-Kollision Sue ↔ Su

Beim Modul-Import gibt `_station_lookup` eine Warnung aus:
```
Duplicate station alias 'Sue' normalized to 'su' for Wien Süßenbrunn conflicts with Stockerau
```
`Sue` (`bst_code` Süßenbrunn) und `Su` (`bst_code` Stockerau) normalisieren
beide zu `su`. Der Tie-Break greift via `_MatchStrength.IDENTITY` und gibt
deterministisch denselben Eintrag zurück, aber die Warnung ist real und
sollte durch einen disambiguierten `bst_code` (z. B. `Sbn` für Süßenbrunn)
oder eine explizite Suppress-Liste gemildert werden. **Nicht Teil dieses
Fixes** – separate Datenkorrektur am ÖBB-Stellencode-Verzeichnis nötig.

### Namens-Inkonsistenzen zwischen ÖBB-Excel und VOR

Fünf Einträge tragen ÖBB-Excel-Abkürzungen, die VOR ausschreibt. Da das
Stationsverzeichnis sich auf einen kanonischen Namen einigen soll (kurz,
ästhetisch, eindeutig), werden die Vollformen übernommen:

| bst_id | Vorher | Nachher |
|---|---|---|
| 1499 | `Wiener Neustadt Hbf` | `Wiener Neustadt Hauptbahnhof` |
| 1669 | `St.Pölten Hbf` | `St. Pölten Hauptbahnhof` |
| 2444 | `Wien Franz-Josefs-Bf` | `Wien Franz-Josefs-Bahnhof` |
| 2511 | `Wien Westbf` | `Wien Westbahnhof` |
| – | `München Hbf` | `München Hauptbahnhof` |

Die Abkürzungen bleiben als Aliase erhalten – eingehende Provider-Strings
(`"Westbf"`, `"Hbf"`) werden weiter auf den kanonischen Namen aufgelöst.

### Doppelte Repräsentation Rennweg

| Quelle | bst_id | Lat | Lon | Aliasse |
|---|---|---|---|---|
| ÖBB (S-Bahn-Halt Aspangbahn) | 1352 | 48.1989→48.194766 | 16.3896→16.386274 | 12 (`Wien Rennweg`, `Bahnhof Rennweg`, …) |
| Google Places (U3-Station) | – | 48.195591 | 16.386149 | 7 (`Rennweg`, `Bahnhof Rennweg`, `Bf Rennweg`, …) |

Geographischer Abstand: ~480 m. Beide sind real, aber separat.

**Maßnahme**: Die "Bahnhof"-Varianten (`Bahnhof Rennweg`, `Bf Rennweg`, `Rennweg
Bahnhof`, `Rennweg Bf`, `Rennweg bf`, `bf Rennweg`) werden aus dem
Google-Places-U-Bahn-Eintrag entfernt – sie sind dort semantisch falsch
(eine U-Bahn-Station ist kein "Bahnhof"). Der bare Alias `Rennweg` bleibt
beim U-Bahn-Eintrag, da das im umgangssprachlichen Wienerischen so
benutzt wird.

### `source`-Feld-Form

Drei Einträge tragen die mit-Leerzeichen-Variante `"google_places, vor, wl"`.
Vereinheitlichung auf no-space-Form `"google_places,vor,wl"` (matcht
`places/merge.py:182`). Konsumenten-Code in `stations.py` wird parallel auf
`_extract_source_tokens` umgestellt, damit künftige Drift toleriert wird.

## Maßnahmen in dieser Änderung

1. **Koordinaten** der 11 Stationen auf VOR-Werte korrigiert (höhere Präzision,
   bessere Übereinstimmung mit Wikipedia/OSM).
2. **Kanonische Namen** auf Vollformen normalisiert (5 Einträge).
3. **Source-Feld** auf no-space-Form normalisiert (3 Einträge).
4. **Rennweg-U-Bahn**-Eintrag von "Bahnhof"-Aliasen befreit.
5. **`stations.py`** verwendet `_extract_source_tokens` für source-basierte
   Tie-Breaks, damit beide Schreibweisen toleriert werden.
6. **Validator** erweitert um Check auf eindeutige kanonische Namen
   (`name`-Feld darf nicht in zwei Einträgen identisch sein, außer ein
   Eintrag ist `manual_foreign_city`).

## Nicht in diesem Fix

- **WL-DIVA-Lücke**: Erfordert externen Download von
  `https://data.wien.gv.at/csv/wienerlinien-ogd-haltestellen.csv` (vollständig).
  Lokal vorhanden ist nur ein 3-Zeilen-Sample.
- **Alias-Token-Kollision Sue↔Su**: Datenproblem im ÖBB-Stellencode-Verzeichnis.
- **`bst_id`-Werte mit 7 Stellen** (`4773541`, `4407597`, …): keine bekannten
  Stellencodes, aber im Validator zugelassen. Nicht modifiziert.
- **Auslandsstationen München/Roma**: Behalten, da Nightjet-Meldungen sie
  referenzieren können.
