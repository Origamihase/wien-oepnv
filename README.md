# Wien ÖPNV Feed

### Status Badges

[![Feed Build](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml/badge.svg?branch=main)](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml)
[![Health Check](https://github.com/Origamihase/wien-oepnv/actions/workflows/health-check.yml/badge.svg?branch=main)](https://github.com/Origamihase/wien-oepnv/actions/workflows/health-check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Subscribe to Feed](https://img.shields.io/badge/RSS-Subscribe_to_Feed-orange?style=flat&logo=rss)](https://origamihase.github.io/wien-oepnv/feed.xml)
[![Website](https://img.shields.io/badge/Website-Wien_ÖPNV-0F1736?style=flat&logo=githubpages&logoColor=white)](https://origamihase.github.io/wien-oepnv/site.html)

---

## 🚇 Was ist der Wien ÖPNV Feed?

**Ein Feed, alle Meldungen: Die zentrale Info-Quelle für den Wiener ÖPNV.**

Dieses Projekt bündelt Störungs- und Baustellenmeldungen der Wiener Linien (WL) und ÖBB in einem deduplizierten RSS-Feed für Wien und das Umland, ergänzt um offizielle OGD-Baustellendaten der Stadt Wien sowie einen dedizierten S-Bahn-Stammstrecken-Monitor (Verspätungen & Ausfälle) auf Basis der VOR/VAO ReST API.

### 🚀 Kernfunktionen auf einen Blick

| Funktion | Beschreibung |
| -------- | ------------ |
| **Konsolidierter RSS-Feed** | Bereinigte Meldungen aller Verkehrsträger – ideal für Feed-Reader, Dashboards oder Widgets. |
| **Statistik-Dashboard** | Tägliche Auswertung von Verspätungen und Störungen (Fokus: S-Bahn-Stammstrecke). |
| **OGD-Baustellen-Layer** | Integration städtischer Baustellendaten (inkl. Bezirk, Zeitraum, Geo-Infos). |
| **Stabile JSON-API** | Sofort nutzbare Cache-Dateien (`events.json`) für eigene Projekte und Analysen. |

> 📡 **Direkt zum Feed:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)

---

## 📈 Stammstrecke – Störungen, Verspätungen & Ausfälle

Die S-Bahn-Stammstrecke ist das Rückgrat des Pendlerverkehrs. Wir erfassen und werten reale Verspätungen, Ausfälle und Störungen kontinuierlich aus. Ausfälle werden aus den bestehenden VAO-Abfahrtsmonitor-Abfragen extrahiert und im 30-Min-Zyklus deduplizierungssicher gezählt.

Mehr Statistiken findest du hier:
[**📊 Zum detaillierten Dashboard**](docs/statistik.md)

### Verspätungen auf der S-Bahn-Stammstrecke

<!-- STATS:STAMMSTRECKE_LIVE:BEGIN -->
> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Beobachtungen (gesamt) | 4 |
| Durchschnittliche Verspätung | 1.2 min |
| Kritische Verspätungen (> 9 min) | 0 |
| Letzte Aktualisierung | 2026-07-09 16:31 CEST |
<!-- STATS:STAMMSTRECKE_LIVE:END -->

<!-- STATS:STAMMSTRECKE:BEGIN -->
> _Letzte 30 Tage – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Beobachtungen (gesamt) | 2.621 |
| Durchschnittliche Verspätung | 1.2 min |
| Kritische Verspätungen (> 9 min) | 25 |
| Letzte Aktualisierung | 2026-07-09 16:31 CEST |
<!-- STATS:STAMMSTRECKE:END -->

### Ausfälle auf der S-Bahn-Stammstrecke

<!-- STATS:AUSFAELLE_LIVE:BEGIN -->
> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Ausfälle (gesamt) | 1 |
| Letzte Aktualisierung | 2026-07-09 16:31 CEST |
<!-- STATS:AUSFAELLE_LIVE:END -->

<!-- STATS:AUSFAELLE:BEGIN -->
> _Letzte 30 Tage – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Ausfälle (gesamt) | 783 |
| Häufigste Linien | S1 (237), S3 (160), REX3 (124) |
| Letzte Aktualisierung | 2026-07-09 16:31 CEST |
<!-- STATS:AUSFAELLE:END -->

> **Hinweis:** Die zugrunde liegenden Roh-Ledger im CSV-Format liegen unter [`data/stats/`](data/stats/) (Zeitstempel in `Europe/Vienna`).

---

## 🗺️ Datenquellen

Das Projekt unterscheidet konsequent zwischen *Live-Verkehrsmeldungen* (treiben den RSS-Feed) und *Stationsverzeichnis-Anreicherung* (befüllt `data/stations.json`). Beide Pfade greifen auf disjunkte Upstream-Sets zu — die folgende Tabelle ist die transparente Inventarisierung.

### Verkehrsmeldungen (Live-Feed)

| Quelle | Zweck |
| --- | --- |
| **Wiener Linien (WL)** | Störungs- und Baustellenmeldungen für U-Bahn, Straßenbahn und Bus innerhalb des WL-Netzes. |
| **ÖBB** | Bundesweite Bahnmeldungen mit Wien-Filter (RSS-Feed, `OEBB_RSS_URL`-konfigurierbar). |
| **VOR/VAO** | Regionalverkehr Wien/Niederösterreich/Burgenland. Wird seit 2026-05-11 ausschließlich für den Stammstrecken-Monitor genutzt (siehe `docs/architecture.md` §7). |
| **OGD Stadt Wien** | Offizielle Baustellendaten der Stadt Wien (Bezirk, Zeitraum, Geo-Infos). |

### Stationsverzeichnis (Geokoordinaten & Metadaten)

Die Koordinaten-Anreicherung läuft als geordnete Fallback-Kette mit drei Tiers, damit das Monatskontingent der kommerziellen Quelle (Google Places) nur als letzter Notausgang in Anspruch genommen wird.

| Tier | Quelle | Zweck |
| --- | --- | --- |
| **1 (Primär)** | **OpenStreetMap (Overpass API)** | Offene Daten ohne API-Limit. Liefert Koordinaten, passagierfreundliche Namen und Klassifizierungs-Tags für jeden Stop innerhalb der Wiener Bounding-Box. |
| **2 (Fallback)** | **HAFAS (ÖBB Scotty)** | Nativer Mgate-`LocMatch`-Client. Liefert hochpräzise Koordinaten und Metadaten (insb. EVA-Nummer / `extId`) für Stationen, die OSM nicht auflösen konnte. Kein Tagesbudget — schont das Google-Kontingent. |
| **3 (Letzter Ausweg)** | **Google Places** | Wird nur für die strikte Restmenge aktiviert, die weder OSM noch HAFAS abdecken konnten. Persistenter Monats-Quota-Manager (`data/places_quota.json`). |

Begleitende Stamm-/Identifier-Quellen: das **ÖBB-Excel** „Verzeichnis der Verkehrsstationen" (Pflicht-`bst_id`/`bst_code`), das **ÖBB-GeoNetz** (EVA-Nummer & IFOPT-ID, Source-Token `oebb_geonetz`), die **Wiener Linien OGD-CSVs** für DIVA-Subcodes und Bahnsteig-Stops sowie die gepinnte **VOR-Stop-Liste** (`data/vor-haltestellen.csv`). Details in [`docs/architecture.md`](docs/architecture.md) §5 und in [`docs/how-to/google_places_stations.md`](docs/how-to/google_places_stations.md).

---

## 🔗 Weiterführende Links

### 📥 Feed & Daten nutzen

- **RSS-Feed abonnieren:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)
- **Projekt-Website:** <https://origamihase.github.io/wien-oepnv/>
- **JSON-Schema der Events:** [`docs/schema/events.schema.json`](docs/schema/events.schema.json)
- **Feed-Health-Report:** `docs/feed-health.md` (+ `docs/feed-health.json` für maschinelle Konsumenten) _(beide werden lokal nach jedem Feed-Build erzeugt; nicht im Repository versioniert)_

### 💻 Für Entwickler & Mitwirkende

Infos zu Installation, Architektur und Provider-Logik finden sich gebündelt in den Entwickler-Docs:

- **Entwicklerdokumentation:** [`docs/development.md`](docs/development.md) _(Setup, CLI, Architektur)_
- **Mitwirken (Contributing):** [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Referenzen & API:** [`docs/reference/`](docs/reference/)
- **Architektur & Diagramme:** [`docs/architecture.md`](docs/architecture.md)

### 🙋 Hilfe & Community

- **Fehler melden (Issues):** [GitHub Issue Tracker](https://github.com/Origamihase/wien-oepnv/issues)
- **Feature-Requests:** [Neues Feature vorschlagen](https://github.com/Origamihase/wien-oepnv/issues/new/choose)
- **Changelog:** [`CHANGELOG.md`](CHANGELOG.md)
- **Sicherheits-Policy:** [`SECURITY.md`](SECURITY.md)
