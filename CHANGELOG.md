# CHANGELOG

## [Unreleased]

* **Fix**: VOR API Integration repariert (Umstellung auf `departureBoard` Endpunkt).
* **Refactor**: Migration auf HAFAS Long-IDs für Wien Hbf und Flughafen.
* **Security**: Strenge Rate-Limit-Guards (100req/day) implementiert.
* **Feat**: Verbessertes Deep-Parsing für Störungsmeldungen in Abfahrtsdaten.

## Quelle: PDF-Handbuch

- 2026-01-14 – Optimized feed deduplication logic to prioritize VOR provider events (API) over ÖBB provider events (Scraper). Conflicts are now resolved by retaining the VOR event as the master record while merging unique description details from the ÖBB event. This ensures higher data quality and stability.
- 2025-08-11 – Line Info Service ergänzt. (Kapitel 19)
- 2025-07-02 – Aktualisierung 5.9.2 zu Informationstexten bei Störungen.
- 2025-05-22 – Neuer Parameter `includeDrt` im Trip-Service.
- 2025-02-11 – Überarbeitung der Handbuchstruktur.
- 2024-12-10 – Kapitel 13.2 und 14.2 zu Scrolling in DepartureBoard und ArrivalBoard erweitert.
- 2024-11-27 – Kapitel 5 um neue Inhalte (5.4, 5.5, 5.11, 5.13, 5.16) und Meta-Parameter in `location.name` ergänzt.

Weitere Einträge und Detailbeschreibungen finden sich in der Änderungshistorie des PDFs (Kapitel 1.1).
