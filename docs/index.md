---
title: "Wien ÖPNV Feed – Aktuelle Verkehrsmeldungen & Open-Data-API"
description: "Open-Source-Projekt für Verkehrsinformationen in Wien und der Ostregion: RSS-Feed, JSON-Daten, Dokumentation & Entwickler-Tools."
lang: de
layout: default
---

<meta name="keywords" content="Wien ÖPNV, Verkehr Wien, Störungen Wiener Linien, Verkehrsverbund Ost-Region, ÖPNV API, Verkehrsmeldungen Wien, Öffentlicher Verkehr Österreich, Echtzeit Verkehr Wien, Wien Linien Störungen, VOR Meldungen">

# Wien ÖPNV Feed – Verkehrsmeldungen & API für Wien und die Ostregion

Der **Wien ÖPNV Feed** bündelt alle relevanten Störungs- und Baustellenmeldungen für den öffentlichen Verkehr in Wien, Niederösterreich und dem Burgenland. Das Projekt richtet sich an Nahverkehrs-Apps, Informationsdisplays, Medienhäuser, Data-Science-Teams und engagierte Open-Data-Communities, die aktuelle Informationen zu U-Bahn, Straßenbahn, Bus und Bahn automatisiert weiterverarbeiten möchten.

Der gesamte Code steht als Open Source zur Verfügung, sodass du jederzeit nachvollziehen kannst, wie der Feed erstellt wird. Dank ausführlicher Dokumentation, reproduzierbaren Build-Skripten und transparent gepflegten Cache-Dateien lässt sich der Feed nahtlos in bestehende Datenpipelines und Mobilitätsplattformen integrieren.

## Warum der Wien ÖPNV Feed?

- **Zentrale Verkehrsinformationen**: Vereinheitlichte Meldungen der Wiener Linien (WL), der ÖBB und des Verkehrsverbund Ost-Region (VOR) inklusive neuer Baustellen-Datenquellen.
- **Suchmaschinenfreundlich**: Optimierte Seitentitel, strukturierte Daten und interne Verlinkungen helfen, dass Entwickler:innen, Journalist:innen und Mobilitätsplaner:innen das Projekt schnell finden.
- **Fokus auf Reproduzierbarkeit**: Vom Cache-Update über den Feed-Build bis hin zu Tests und Audits – jeder Schritt ist dokumentiert und automatisierbar.
- **Flexible Nutzung**: Konsumiere den RSS-Feed, greife direkt auf JSON-Caches zu oder verwende die Python-Helfer aus `src/`, um deine Anwendung mit Echtzeitdaten zu versorgen.

## Schnellstart für Entwickler:innen

1. **Repository klonen** und ein virtuelles Environment anlegen (`python -m venv .venv`).
2. **Abhängigkeiten installieren** mit `pip install -r requirements.txt`.
3. **Caches aktualisieren** via `python -m src.cli cache update`.
4. **Feed bauen** mit `python -m src.cli feed build`, um `docs/feed.xml` zu generieren.
5. **Statische Analysen** optional mit `scripts/run_static_checks.py` ausführen.

Weitere Details findest du in der [ausführlichen Projektdokumentation](../README.md) sowie in den [How-to-Anleitungen](how-to/) für spezielle Workflows.

## Datengrundlage und Lizenzierung

| Datenquelle | Inhalt | Aktualisierung |
|-------------|--------|----------------|
| Wiener Linien (WL) | Störungs- und Echtzeitmeldungen für U-Bahn, Straßenbahn und Bus | Mehrmals täglich | 
| ÖBB | Informationen zum regionalen und nationalen Bahnverkehr im Großraum Wien | Nach Fahrplanänderungen und Ereignissen |
| Verkehrsverbund Ost-Region (VOR) | Verbundweite Meldungen und VAO/VAO-API-Dokumentation | Kontinuierlich |
| Stadt Wien (OGD) | Baustellen- und Ereignisdaten als Fallback | Täglich |

Alle Datenquellen werden revisionssicher versioniert, inklusive Lizenzhinweisen. Informiere dich vor der Weiterverwendung über die jeweiligen Nutzungsbedingungen.

## Integrationsszenarien

- **Mobilitäts-Apps & Widgets**: Binde den RSS-Feed direkt ein, um Nutzer:innen mit aktuellen Verkehrsinfos zu versorgen.
- **Fahrgastinformationssysteme**: Nutze die JSON-Caches, um Displays in Stationen oder Fahrzeugen zu aktualisieren.
- **Datenjournalismus & Forschung**: Analysiere historische Meldungen, entdecke Muster in Störungsdaten und visualisiere Trends.
- **Unternehmensinterne Dashboards**: Überwache wichtige Linien, verknüpfe Daten mit eigenen KPIs und setze Benachrichtigungen auf.

## Vorteile für SEO & Auffindbarkeit

- Aussagekräftige Titel, Meta-Beschreibungen und Keywords in der Projektdokumentation.
- Strukturierte JSON-LD-Metadaten für Organisation und Software-Anwendung.
- Sitemap und Robots.txt erleichtern das Crawling durch Suchmaschinen.
- Häufige Fragen und thematische Zwischenüberschriften decken relevante Suchanfragen wie „Wien Linien Störungen“, „ÖPNV API Wien“ oder „Echtzeit Verkehrsmeldungen Wien“ ab.

## Häufige Fragen (FAQ)

### Was ist der Funktionsumfang des Wien ÖPNV Feed?
Der Feed konsolidiert Meldungen, dedupliziert identische Ereignisse, versieht sie mit konsistenten Metadaten und stellt sie als RSS- und JSON-Daten bereit.

### Kann ich eigene Provider oder Filter hinzufügen?
Ja. Die Architektur erlaubt es, Provider über Umgebungsvariablen zu deaktivieren oder neue Adapter in `src/providers/` hinzuzufügen. Tests und statische Analysen unterstützen bei der Integration.

### Unter welcher Lizenz steht das Projekt?
Der Code steht unter der MIT-Lizenz. Prüfe bei externen Datenquellen die individuellen Lizenzbedingungen.

### Wie bleibe ich über Änderungen informiert?
Überwache das Repository, abonniere Releases oder integriere die Cache-Updates in deine CI/CD-Pipeline. Audit-Berichte und Changelogs dokumentieren wichtige Änderungen.

## Weiterführende Ressourcen

- [Projektübersicht im README](../README.md)
- [API-Referenzen und Audits](reference/)
- [Stationsvalidierung & Reports](stations_validation_report.md)
- [System-Health-Reviews](system_health_review.md)
- [Feed als RSS-Dokument](feed.xml)

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Wien ÖPNV Feed",
  "applicationCategory": "DataFeed",
  "operatingSystem": "Cross-platform",
  "offers": {
    "@type": "Offer",
    "price": "0",
    "priceCurrency": "EUR"
  },
  "creator": {
    "@type": "Organization",
    "name": "Wien ÖPNV Projektteam"
  },
  "description": "Open-Source-Projekt zur Aggregation und Bereitstellung von Verkehrsmeldungen für Wien, Niederösterreich und das Burgenland via RSS und JSON.",
  "url": "https://wien-oepnv.github.io/",
  "softwareVersion": "1.0",
  "keywords": [
    "Wien Linien Störungen",
    "ÖPNV Wien",
    "Verkehrsmeldungen Wien",
    "VOR API",
    "ÖBB Verkehr"
  ]
}
</script>

<footer class="page-footer">
  <p><strong>Hinweis:</strong> Passe bei Bedarf die Projekt-URL in der Sitemap und in den strukturierten Daten an deine produktive Domain an.</p>
</footer>
