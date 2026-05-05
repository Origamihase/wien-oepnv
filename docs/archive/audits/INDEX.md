# Audits — Index

Chronologische Übersicht aller archivierten Audits dieses Projekts.

## Stationsverzeichnis (`data/stations.json`)

| Datum | Bericht | Begleitender PR | Schwerpunkt |
|---|---|---|---|
| 2026-05-05 | [Erst-Audit](stations_data_audit_2026-05-05.md) | [#1188](https://github.com/Origamihase/wien-oepnv/pull/1188) | Koordinaten-Fixes (9 Stationen), kanonische Namen, source-Format, Rennweg-Doublette, NamingIssue-Validator |
| 2026-05-05 | [Follow-up](stations_data_audit_2026-05-05_followup.md) | [#1189](https://github.com/Origamihase/wien-oepnv/pull/1189) | 31-Vertex-Polygon (vorher 8-Vertex-Konvex-Hülle), Liesing-VOR-Coords, Sue↔Su-Token-Fix, WL-OGD-Auto-Download |
| 2026-05-05 | [Offizielles Polygon](stations_data_audit_2026-05-05_official_polygon.md) | [#1190](https://github.com/Origamihase/wien-oepnv/pull/1190) | Hand-kuratiertes Polygon ersetzt durch offizielle `LANDESGRENZEOGD`-Quelle der MA 41 (5.637 Vertices, ~1–2 m Genauigkeit) |

## Code & System

| Datum | Bericht | Schwerpunkt |
|---|---|---|
| 2025-05 | [system_audit.md](system_audit.md) | Allgemeiner System-Audit |
| 2025-04 | [audit-2025-04-05.md](audit-2025-04-05.md) | Periodischer Quartals-Audit |
| 2025-03 | [audit-2025-03-17.md](audit-2025-03-17.md) | Periodischer Quartals-Audit |
| 2025-02 | [audit-2025-02-14.md](audit-2025-02-14.md), [code_quality_audit_2025_02.md](code_quality_audit_2025_02.md) | Quartals-Audit + Code Quality |
| 2025-01 | [audit-2025-01.md](audit-2025-01.md), [audit-2025-01-04.md](audit-2025-01-04.md) | Monats-Audits |
| 2024-12 | [audit-2024-12-31.md](audit-2024-12-31.md) | Monats-Audit |

## Provider-spezifisch

| Bereich | Berichte |
|---|---|
| VOR/VAO API | [vor_api_review.md](vor_api_review.md), [vor_api_test.md](vor_api_test.md) |
| Security | [security_report.md](security_report.md) |
| Performance | [performance_report.md](performance_report.md) |
| Deduplication | [deduplication_report.md](deduplication_report.md) |
| System Health | [system_health_review.md](system_health_review.md), [system_review.md](system_review.md), [code_audit.md](code_audit.md), [code_quality_review.md](code_quality_review.md), [code_review_summary.md](code_review_summary.md) |

## Stations-Validation-Report

Der periodisch regenerierte Validator-Report liegt nicht hier im Archiv,
sondern direkt unter
[`docs/stations_validation_report.md`](../../stations_validation_report.md)
(wird vom monatlichen `update-stations.yml`-Workflow überschrieben).
