# VOR API Implementation Review

## Ergebnis
- Die Zugangsdaten werden ausschließlich aus den Secrets `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` geladen.【F:src/providers/vor.py†L268-L286】
- Der frühere Fallback-Wert `VAO` wird vom Produktivsystem inzwischen konsequent mit `API_AUTH` abgewiesen. Verwende das neue Hilfsskript `scripts/check_vor_auth.py`, um die eigene Konfiguration zu validieren.【F:scripts/check_vor_auth.py†L1-L117】
- Der erneute Test vom 14.10.2025 bestätigt den Fehlerstatus `API_AUTH`, weil `VOR_ACCESS_ID` im aktuellen Laufzeitkontext weiterhin auf den Fallback `VAO` fällt.【F:log/2025-10-14_vor_auth_check.md†L1-L23】【F:src/providers/vor.py†L268-L286】
- Die Basis-URL der API wird priorisiert aus dem Secret `VOR_BASE_URL` eingelesen; Version und Pfad werden dabei automatisch normalisiert, sodass sowohl versionierte als auch unversionierte Werte korrekt funktionieren.【F:src/providers/vor.py†L315-L360】
- Bei allen API-Aufrufen (z. B. `DepartureBoard` und `location.name`) werden die Secrets angehängt und der konfigurierte Basis-Pfad verwendet, wodurch die Requests die geschützten Zugangsdaten nutzen.【F:src/providers/vor.py†L399-L508】【F:src/providers/vor.py†L626-L720】
- Zusätzlich wird der Zugangsschlüssel als `Authorization: Bearer …` an die Sitzungen angehängt, damit aktualisierte Sicherheitsrichtlinien der VAO-Backend-Systeme eingehalten werden, ohne dass Logs den Klartext enthalten.【F:src/providers/vor.py†L398-L433】【F:tests/test_vor_accessid_not_logged.py†L1-L46】

## Tests
- `pytest tests/test_vor_env.py tests/test_vor_default_version.py tests/test_vor_location_name.py`
  - Bestätigt das erwartete Verhalten beim Laden der Secrets, beim Normalisieren der Basis-URL sowie beim Zusammensetzen der Request-Parameter.【d744d3†L1-L9】
