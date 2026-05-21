# Audits — Index

Chronologische Übersicht aller archivierten Audits dieses Projekts.

## Stationsverzeichnis (`data/stations.json`)

| Datum | Bericht | Begleitender PR | Schwerpunkt |
|---|---|---|---|
| 2026-05-05 | [Erst-Audit](stations_data_audit_2026-05-05.md) | [#1188](https://github.com/Origamihase/wien-oepnv/pull/1188) | Koordinaten-Fixes (9 Stationen), kanonische Namen, source-Format, Rennweg-Doublette, NamingIssue-Validator |
| 2026-05-05 | [Follow-up](stations_data_audit_2026-05-05_followup.md) | [#1189](https://github.com/Origamihase/wien-oepnv/pull/1189) | 31-Vertex-Polygon (vorher 8-Vertex-Konvex-Hülle), Liesing-VOR-Coords, Sue↔Su-Token-Fix, WL-OGD-Auto-Download |
| 2026-05-05 | [Offizielles Polygon](stations_data_audit_2026-05-05_official_polygon.md) | [#1190](https://github.com/Origamihase/wien-oepnv/pull/1190) | Hand-kuratiertes Polygon ersetzt durch offizielle `LANDESGRENZEOGD`-Quelle der MA 41 (5.637 Vertices, ~1–2 m Genauigkeit) |
| 2026-05 | [Vollständigkeit / Pendler-Coverage](stations_coverage_2026-05.md) | (offen) | 12 kritische + 57 wichtige Pendlerstationen identifiziert; name-basierte Wishlist `data/pendler_candidates.json` + Updater-Erweiterung statt manuelle ID-Raterei |
| 2026-05-21 | [GeoNetz-Reconciliation](stations_geonetz_reconciliation_2026-05-21.md) | (dieser PR) | ÖBB-GeoNetz-Quervalidierung deckt drei falsche hand-kurierte Koordinaten aus PR #1224 auf (Laxenburg-Biedermannsdorf ~10 km off); Weigelsdorf operativ stillgelegt 2023, aus allen Datenpfaden entfernt |

## Code & System

| Datum | Bericht | Schwerpunkt |
|---|---|---|
| 2025-07 | [audit-2025-07-08.md](audit-2025-07-08.md) | Manueller Review der Feed-Builder-Robustheit (keine Findings) |
| 2025-06 | [audit-2025-06-02.md](audit-2025-06-02.md) | `ruff check` als statisches Lint-Gate aufgenommen |
| 2025-05 | [audit-2025-05-29.md](audit-2025-05-29.md), [audit-2025-05-22.md](audit-2025-05-22.md), [system_audit.md](system_audit.md) | Codebasis-Audits + allgemeiner System-Audit |
| 2025-04 | [audit-2025-04-05.md](audit-2025-04-05.md) | Periodischer Quartals-Audit |
| 2025-03 | [audit-2025-03-17.md](audit-2025-03-17.md) | Periodischer Quartals-Audit |
| 2025-02 | [audit-2025-02-14.md](audit-2025-02-14.md), [audit_report.md](audit_report.md), [code_quality_audit_2025_02.md](code_quality_audit_2025_02.md) | Quartals-Audit + Code Quality + Februar-Audit-Bericht |
| 2025-01 | [audit-2025-01.md](audit-2025-01.md), [audit-2025-01-04.md](audit-2025-01-04.md) | Monats-Audits |
| 2024-12 | [audit-2024-12-31.md](audit-2024-12-31.md) | Monats-Audit |

## Provider-spezifisch

| Bereich | Berichte |
|---|---|
| VOR/VAO API | [vor_api_review.md](vor_api_review.md), [vor_api_test.md](vor_api_test.md) |
| ÖBB Stammstrecke | [oebb_stammstrecke_audit.md](oebb_stammstrecke_audit.md) (2026-05-09: PRs #1365 – #1368, `max_journeys=5`-Anpassung, vollständige Audit-Abnahme); [stammstrecke_vor_migration_qa_2026-05-09.md](stammstrecke_vor_migration_qa_2026-05-09.md) (Senior-Architect-QA der `pyhafas`→VOR/VAO-Migration) |
| Security | [security_report.md](security_report.md) |
| Performance | [performance_report.md](performance_report.md) |
| Deduplication | [deduplication_report.md](deduplication_report.md) |
| System Health | [system_health_review.md](system_health_review.md), [system_review.md](system_review.md), [code_audit.md](code_audit.md), [code_quality_review.md](code_quality_review.md), [code_review_summary.md](code_review_summary.md) |

## Stations-Validation-Report

Der periodisch regenerierte Validator-Report liegt nicht hier im Archiv,
sondern direkt unter
[`docs/stations_validation_report.md`](../../stations_validation_report.md)
(wird vom wöchentlichen `update-stations.yml`-Workflow überschrieben,
Cron `0 1 * * 0`).
