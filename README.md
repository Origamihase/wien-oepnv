# Wien ÖPNV Feed – Projektdokumentation

### Status Badges
[![Update VOR Cache](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-vor-cache.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-vor-cache.yml)
[![Update ÖBB Cache](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-oebb-cache.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-oebb-cache.yml)
[![Test VOR API](https://github.com/Origamihase/wien-oepnv/actions/workflows/test-vor-api.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/test-vor-api.yml)

[![Feed Build](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml/badge.svg?branch=main)](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml)
[![Tests](https://github.com/Origamihase/wien-oepnv/actions/workflows/test.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/test.yml)
[![SEO-Checks](https://github.com/Origamihase/wien-oepnv/actions/workflows/seo-guard.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/seo-guard.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Subscribe to Feed](https://img.shields.io/badge/RSS-Subscribe_to_Feed-orange?style=flat&logo=rss)](https://origamihase.github.io/wien-oepnv/feed.xml)

---

## Was ist der Wien ÖPNV Feed?

**Ein Feed. Alle Meldungen. Eine Quelle der Wahrheit für den Wiener ÖPNV.**

Dieses Projekt bündelt offizielle Störungs-, Baustellen- und Hinweismeldungen
der **Wiener Linien (WL)**, der **ÖBB** und des **Verkehrsverbund Ost-Region (VOR)**
zu einem konsolidierten, dedupliziertem RSS-Dokument für Wien sowie das
niederösterreichisch-burgenländische Umland. Ergänzt wird der Feed durch den
Open-Government-Data-Baustellenkatalog der Stadt Wien.

### Kernfunktionen auf einen Blick

| Funktion | Beschreibung |
| -------- | ------------ |
| **Konsolidierter RSS-Feed** | Vereinigte, deduplizierte Meldungen aller relevanten Verkehrsträger – sofort einsetzbar in jedem Reader, Widget oder Display. |
| **Statistik-Dashboard** | Tägliche Aggregation der Verspätungen auf der **S-Bahn-Stammstrecke** sowie der gemeldeten Störungen aus den CSV-Ledgern unter `data/stats/`. |
| **Baustellen-Layer** | OGD-Baustellendaten der Stadt Wien als zusätzlicher Provider mit Bezirk, Zeitraum und Geo-Information. |
| **Stabile JSON-API** | Versionierte Cache-Dateien (`cache/<provider>/events.json`) für Drittprojekte – ohne Build-Schritt nutzbar. |
| **Reproduzierbar & sicher** | Atomare Schreibvorgänge, SSRF-Schutz, Path-Traversal-Schutz und ein vollständiges Stationsverzeichnis (`data/stations.json`). |

> **Direkt zum Feed:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)

---

## Stammstrecke – Störungen & Verspätungen

Die S-Bahn-Stammstrecke ist das Rückgrat des Wiener Pendlerverkehrs.
Halbstündlich misst der Workflow [`update-stammstrecke-status.yml`](.github/workflows/update-stammstrecke-status.yml)
den HAFAS-Median der Verspätungen und schreibt das Ergebnis in das
Append-only-Ledger [`data/stats/stammstrecke_YYYY.csv`](data/stats/).
Parallel protokolliert der Feed-Builder neu erkannte Störungen unter
[`data/stats/stoerungen_YYYY.csv`](data/stats/). Der nächtliche Workflow
[`generate-stats.yml`](.github/workflows/generate-stats.yml) aggregiert diese
Rohdaten zu einem Markdown-Dashboard.

> **Live-Dashboard:** Die vollständige Auswertung mit Wochentags- und Stunden-Heatmaps,
> Top-Stationen und Trend-Linien wird unter [`docs/statistik.md`](docs/statistik.md) regeneriert.

### Aktueller Schnappschuss

<!-- STATS:STAMMSTRECKE:BEGIN -->
> _Dieser Block wird vom Workflow [`generate-stats.yml`](.github/workflows/generate-stats.yml) automatisch befüllt.
> Bis zum ersten Lauf bleibt er als Platzhalter sichtbar._

| Kennzahl | Wert |
| -------- | ---- |
| Beobachtungen (gesamt) | _wird beim nächsten Stats-Lauf gesetzt_ |
| Median-Verspätung | _wird beim nächsten Stats-Lauf gesetzt_ |
| Schwellwert-Überschreitungen (> 9 min) | _wird beim nächsten Stats-Lauf gesetzt_ |
| Stand | _wird beim nächsten Stats-Lauf gesetzt_ |
<!-- STATS:STAMMSTRECKE:END -->

### Top-Störungsorte

<!-- STATS:DISRUPTIONS:BEGIN -->
> _Dieser Block wird vom Workflow [`generate-stats.yml`](.github/workflows/generate-stats.yml) automatisch befüllt.
> Bis zum ersten Lauf bleibt er als Platzhalter sichtbar._

| Rang | Ort | Anzahl |
| ---- | --- | ------ |
| 1.   | _wird beim nächsten Stats-Lauf gesetzt_ | – |
| 2.   | _wird beim nächsten Stats-Lauf gesetzt_ | – |
| 3.   | _wird beim nächsten Stats-Lauf gesetzt_ | – |
<!-- STATS:DISRUPTIONS:END -->

> Die Roh-Ledger findest du unter [`data/stats/`](data/stats/) – alle Zeitstempel sind in `Europe/Vienna` normalisiert.

---

## Weiterführende Links

### Feed konsumieren

- **RSS-Feed (live):** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)
- **Feed-Health-Report:** [`docs/feed-health.md`](docs/feed-health.md) _(nach jedem Build regeneriert)_
- **JSON-Schema der Events:** [`docs/schema/events.schema.json`](docs/schema/events.schema.json)
- **Projekt-Website:** <https://origamihase.github.io/wien-oepnv/>

### Mitwirken & Weiterentwickeln

- **Entwicklerdokumentation (Setup, CLI, Konfiguration, Provider-Logik):** [`docs/development.md`](docs/development.md)
- **Beitragen (Branches, PRs, Pre-Commit):** [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Architektur-Karte mit Mermaid-Diagrammen:** [`docs/architecture.md`](docs/architecture.md)
- **Sicherheits-Policy & Reporting:** [`SECURITY.md`](SECURITY.md)
- **Verhaltenskodex:** [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

### Hilfe & Community

- **Issue-Tracker:** <https://github.com/Origamihase/wien-oepnv/issues>
- **Feature-Requests & Diskussionen:** <https://github.com/Origamihase/wien-oepnv/issues/new/choose>
- **Changelog:** [`CHANGELOG.md`](CHANGELOG.md)

### Referenz

- **Stationsverzeichnis:** [`data/stations.json`](data/stations.json) · [Schema](docs/schema/stations.schema.json) · [Validation-Report](docs/stations_validation_report.md)
- **VOR / VAO ReST API:** [`docs/reference/`](docs/reference/) · [How-to-Guides](docs/how-to/) · [Beispiele](docs/examples/)
- **Statistik-Dashboard:** [`docs/statistik.md`](docs/statistik.md) _(generiert von [`generate-stats.yml`](.github/workflows/generate-stats.yml))_
- **Audit-Index:** [`docs/archive/audits/INDEX.md`](docs/archive/audits/INDEX.md)

---

<sub>Veröffentlicht unter der [MIT-Lizenz](LICENSE) · Datenquellen behalten ihre jeweiligen Lizenzen
(siehe [Entwicklerdokumentation › Datenquellen und Lizenzen](docs/development.md#datenquellen-und-lizenzen)).</sub>
