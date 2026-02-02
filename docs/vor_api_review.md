# VOR API Implementation Review

## Ergebnis
- Die aktuelle Implementierung erfüllt die im Handbuch dokumentierten Anforderungen: Secrets werden geladen, Endpunkte korrekt zusammengesetzt und die Authentifizierung wird automatisch an Requests angehängt, wie durch die begleitenden Unit-Tests abgesichert wird.【F:src/providers/vor.py†L274-L378】【F:src/providers/vor.py†L526-L680】【F:tests/test_vor_env.py†L9-L118】【F:tests/test_vor_location_name.py†L9-L78】
- Die Zugangsdaten werden ausschließlich aus den Secrets `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` geladen.【F:src/providers/vor.py†L268-L286】
- Der frühere Fallback-Wert `VAO` wird vom Produktivsystem inzwischen konsequent mit `API_AUTH` abgewiesen.
- Der erneute Test vom 14.10.2025 bestätigt den Fehlerstatus `API_AUTH`, weil im aktuellen Laufzeitkontext kein Secret gesetzt war und deshalb kein Token übertragen wurde.【F:log/2025-10-14_vor_auth_check.md†L1-L23】【F:src/providers/vor.py†L268-L286】
- Die Basis-URL der API wird priorisiert aus dem Secret `VOR_BASE_URL` eingelesen; Version und Pfad werden dabei automatisch normalisiert, sodass sowohl versionierte als auch unversionierte Werte korrekt funktionieren.【F:src/providers/vor.py†L315-L360】
- Bei allen API-Aufrufen (z. B. `DepartureBoard` und `location.name`) werden die Secrets angehängt und der konfigurierte Basis-Pfad verwendet, wodurch die Requests die geschützten Zugangsdaten nutzen.【F:src/providers/vor.py†L399-L508】【F:src/providers/vor.py†L626-L720】
- Zusätzlich wird der Zugangsschlüssel als `Authorization: Bearer …` an die Sitzungen angehängt, damit aktualisierte Sicherheitsrichtlinien der VAO-Backend-Systeme eingehalten werden, ohne dass Logs den Klartext enthalten.【F:src/providers/vor.py†L398-L433】【F:tests/test_vor_accessid_not_logged.py†L1-L46】

## Tests
- `pytest tests/test_vor_env.py tests/test_vor_default_version.py tests/test_vor_location_name.py`
  - Bestätigt das erwartete Verhalten beim Laden der Secrets, beim Normalisieren der Basis-URL sowie beim Zusammensetzen der Request-Parameter.【d744d3†L1-L9】

## Lösung & Status Quo (Februar 2026)

* **Endpoint**: Wir nutzen final `departureBoard` (da `trafficInfo` html/fehlerhaft war).
* **IDs**: Es sind zwingend **HAFAS Long-IDs** (Format `A=1@O=...`) erforderlich. Einfache numerische IDs funktionieren für diesen Endpunkt nicht.
* **Parameter-Falle**: Der Parameter `type` darf NICHT gesendet werden (führte zu Fehler 400 "No enum constant").
* **Rate Limit Architektur**: Drei Schutzebenen gegen die 100-Request-Sperre:
    1.  Workflow-Lock (nur stündlich).
    2.  Pre-Flight Check (Skript bricht ab, wenn Config > 100 Requests/Tag erzeugt).
    3.  Runtime Circuit Breaker (Notaus bei >10 Requests/Run).
