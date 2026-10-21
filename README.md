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
| **GeoRSS, wo der Ort sicher ist** | ÖBB-Abschnitte als `<georss:line>`, einzelne Bahnhöfe, Baustellen und WL-Haltestellenverlegungen als `<georss:point>`; alle anderen Items bleiben bewusst ohne Geodaten ([`docs/architecture.md`](docs/architecture.md) §1). |
| **Statistik-Dashboard** | Tägliche Auswertung von Verspätungen und Störungen (Fokus: S-Bahn-Stammstrecke). |
| **OGD-Baustellen-Layer** | Integration städtischer Baustellendaten (inkl. Bezirk, Zeitraum, Geo-Infos). |
| **Stabile JSON-API** | Sofort nutzbare Cache-Dateien (`events.json`) für eigene Projekte und Analysen. |

> 📡 **Direkt zum Feed:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)
>
> 🇬🇧 **Englischer Spiegel:** [`https://origamihase.github.io/wien-oepnv/feed.en.xml`](https://origamihase.github.io/wien-oepnv/feed.en.xml) — dieselben Items, dieselbe Reihenfolge, nur übersetzt. Wie die Übersetzung arbeitet, steht in [`docs/architecture.md`](docs/architecture.md) §8.

---

## 📈 Stammstrecke – Störungen, Verspätungen & Ausfälle

Die S-Bahn-Stammstrecke ist das Rückgrat des Pendlerverkehrs. Wir erfassen und werten reale Verspätungen, Ausfälle und Störungen kontinuierlich aus. Ausfälle werden aus den bestehenden VAO-Abfahrtsmonitor-Abfragen extrahiert und im 30-Min-Zyklus deduplizierungssicher gezählt.

> Stammstrecken-Daten: Grundlage ist der VAO-„Start“-Zugang der [Verkehrsauskunft Österreich VAO GmbH](https://www.verkehrsauskunft.at/start) (kostenlos, bis zu 100 Abfragen pro Tag; Hinweis nach Punkt 5.3 des Vertrags, siehe [`NOTICE.md`](NOTICE.md)).

Mehr Statistiken findest du hier:
[**📊 Zum detaillierten Dashboard**](docs/statistik.md)

### Verspätungen auf der S-Bahn-Stammstrecke

<!-- STATS:STAMMSTRECKE_LIVE:BEGIN -->
> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Beobachtungen (gesamt) | 2 |
| Durchschnittliche Verspätung | 0.0 min |
| Kritische Verspätungen (> 9 min) | 0 |
| Letzte Aktualisierung | 2026-10-21 07:30 CEST |
<!-- STATS:STAMMSTRECKE_LIVE:END -->

<!-- STATS:STAMMSTRECKE:BEGIN -->
> ⚠️ **Eingeschränkte Abdeckung:** Aktuell keine Fahrten von Wien Hbf Richtung **Praterstern** auf der Stammstrecke (zuletzt am 14.08.2026). Die Zahlen unten decken daher nur Richtung **Meidling** ab — **kein Korridor-Gesamtwert**.

> _Letzte 30 Tage – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Beobachtungen (gesamt) | 1.182 |
| Durchschnittliche Verspätung | 0.3 min |
| Kritische Verspätungen (> 9 min) | 15 |
| Letzte Aktualisierung | 2026-10-21 07:30 CEST |
<!-- STATS:STAMMSTRECKE:END -->

### Ausfälle auf der S-Bahn-Stammstrecke

<!-- STATS:AUSFAELLE_LIVE:BEGIN -->
> _Letzte 60 Minuten – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Ausfälle (gesamt) | 0 |
| Letzte Aktualisierung | 2026-10-21 07:30 CEST |
<!-- STATS:AUSFAELLE_LIVE:END -->

<!-- STATS:AUSFAELLE:BEGIN -->
> ⚠️ **Eingeschränkte Abdeckung:** Aktuell keine Fahrten von Wien Hbf Richtung **Praterstern** auf der Stammstrecke (zuletzt am 14.08.2026). Die Zahlen unten decken daher nur Richtung **Meidling** ab — **kein Korridor-Gesamtwert**.

> _Letzte 30 Tage – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Ausfälle (gesamt) | 64 |
| Häufigste Linien | S1 (39), CJX9 (24), S3 (1) |
| Letzte Aktualisierung | 2026-10-21 07:30 CEST |
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

Bestehende Koordinaten bleiben erhalten. Einer Station ohne Koordinate gibt der wöchentliche Lauf zuerst den Punkt aus dem ÖBB-GeoNetz (über die `bst_id`), danach fragt er HAFAS. Eine Station, die keine der beiden auflöst, bleibt ohne Koordinate und wird mit Namen gewarnt. (Google Places hat Michael am 16.10.2026 entfernt, OpenStreetMap am 17.10.2026: Das Verzeichnis führt nur Koordinaten mit bekannter Herkunft und passenden Bedingungen, siehe [`NOTICE.md`](NOTICE.md).)

| Tier | Quelle | Zweck |
| --- | --- | --- |
| **1 (Primär)** | **ÖBB-Infrastruktur GeoNetz** | Offene Daten (CC BY 3.0 AT). Liefert den Punkt jeder ÖBB-Station über ihre `bst_id`; nie über den Namen, weil gleichnamige Orte verwechselt würden. |
| **2 (Fallback)** | **HAFAS (ÖBB Scotty)** | Nativer Mgate-`LocMatch`-Client. Liefert Koordinaten und Metadaten (insb. EVA-Nummer / `extId`) für Stationen, die GeoNetz nicht abdeckt. Kein Tagesbudget. |

Begleitende Stamm-/Identifier-Quellen: das **ÖBB-Excel** „Verzeichnis der Verkehrsstationen" (Pflicht-`bst_id`/`bst_code`), das **ÖBB-GeoNetz** (EVA-Nummer & IFOPT-ID, Source-Token `oebb_geonetz`), die **Wiener Linien OGD-CSVs** für DIVA-Subcodes und Bahnsteig-Stops sowie die gepinnte **VOR-Stop-Liste** (`data/vor-haltestellen.csv`). Details in [`docs/architecture.md`](docs/architecture.md) §5.

### ⚖️ Lizenz

**Inoffizielles Projekt** ohne Verbindung zu Wiener Linien, ÖBB, Stadt Wien und
VOR/VAO; maßgeblich sind die verlinkten Originalmeldungen.

Der **Code** steht unter der [MIT-Lizenz](LICENSE). Die **Meldungstexte** in
`docs/feed.xml` und `docs/feed.en.xml` und die Datensätze unter `data/` gehören
den jeweiligen Quellen und stehen unter deren Lizenzen (Wiener Linien und Stadt
Wien: CC BY 4.0; ÖBB-Meldungen: © ÖBB). Die vollständige Aufstellung mit den
Pflicht-Quellenangaben steht in [`NOTICE.md`](NOTICE.md).

---

## 🔗 Weiterführende Links

### 📥 Feed & Daten nutzen

- **RSS-Feed abonnieren:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)
- **Englischer Feed:** [`https://origamihase.github.io/wien-oepnv/feed.en.xml`](https://origamihase.github.io/wien-oepnv/feed.en.xml) _(Spiegel des deutschen Feeds; bei fehlgeschlagener Übersetzung bleibt das betroffene Item deutsch)_
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
