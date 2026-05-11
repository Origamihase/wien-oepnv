# Wien ÖPNV Feed

### Status Badges

[![Feed Build](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml/badge.svg?branch=main)](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Subscribe to Feed](https://img.shields.io/badge/RSS-Subscribe_to_Feed-orange?style=flat&logo=rss)](https://origamihase.github.io/wien-oepnv/feed.xml)

---

## 🚇 Was ist der Wien ÖPNV Feed?

**Ein Feed, alle Meldungen: Die zentrale Info-Quelle für den Wiener ÖPNV.**

Dieses Projekt bündelt Störungs- und Baustellenmeldungen der Wiener Linien (WL), ÖBB und des VOR in einem deduplizierten RSS-Feed für Wien und das Umland. Ergänzt wird dies durch offizielle OGD-Baustellendaten der Stadt Wien.

### 🚀 Kernfunktionen auf einen Blick

| Funktion | Beschreibung |
| -------- | ------------ |
| **Konsolidierter RSS-Feed** | Bereinigte Meldungen aller Verkehrsträger – ideal für Feed-Reader, Dashboards oder Widgets. |
| **Statistik-Dashboard** | Tägliche Auswertung von Verspätungen und Störungen (Fokus: S-Bahn-Stammstrecke). |
| **OGD-Baustellen-Layer** | Integration städtischer Baustellendaten (inkl. Bezirk, Zeitraum, Geo-Infos). |
| **Stabile JSON-API** | Sofort nutzbare Cache-Dateien (`events.json`) für eigene Projekte und Analysen. |

> 📡 **Direkt zum Feed:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)

---

## 📈 Stammstrecke – Störungen & Verspätungen

Die S-Bahn-Stammstrecke ist das Rückgrat des Pendlerverkehrs. Wir erfassen und werten reale Verspätungen und Störungen kontinuierlich aus.

Mehr Statistiken findest du hier:
[**📊 Zum detaillierten Dashboard**](docs/statistik.md)

### Verspätungen auf der S-Bahn Stammstrecke

<!-- STATS:STAMMSTRECKE:BEGIN -->
> _Letzte 30 Tage – automatisch aktualisiert vom Workflow_ [`update-cycle.yml`](.github/workflows/update-cycle.yml).

| Kennzahl | Wert |
| -------- | ---- |
| Beobachtungen (gesamt) | 105 |
| Durchschnittliche Verspätung | 0.2 min |
| Kritische Verspätungen (> 9 min) | 0 |
| Letzte Aktualisierung | 2026-05-11 23:30 CEST |
<!-- STATS:STAMMSTRECKE:END -->

> **Hinweis:** Die zugrunde liegenden Roh-Ledger im CSV-Format liegen unter [`data/stats/`](data/stats/) (Zeitstempel in `Europe/Vienna`).

---

## 🔗 Weiterführende Links

### 📥 Feed & Daten nutzen

- **RSS-Feed abonnieren:** [`https://origamihase.github.io/wien-oepnv/feed.xml`](https://origamihase.github.io/wien-oepnv/feed.xml)
- **Projekt-Website:** <https://origamihase.github.io/wien-oepnv/>
- **JSON-Schema der Events:** [`docs/schema/events.schema.json`](docs/schema/events.schema.json)
- **Feed-Health-Report:** `docs/feed-health.md` _(wird lokal nach jedem Feed-Build erzeugt; nicht im Repository versioniert)_

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
