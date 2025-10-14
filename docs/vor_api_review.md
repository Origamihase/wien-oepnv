# VOR API Implementation Review

## Ergebnis
- Die Zugangsdaten werden ausschließlich aus den Secrets `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` geladen; fehlt eine Belegung, greift automatisch der von der VAO-Dokumentation empfohlene Standardwert `VAO`.【F:src/providers/vor.py†L268-L282】
- Die Basis-URL der API wird priorisiert aus dem Secret `VOR_BASE_URL` eingelesen; Version und Pfad werden dabei automatisch normalisiert, sodass sowohl versionierte als auch unversionierte Werte korrekt funktionieren.【F:src/providers/vor.py†L315-L360】
- Bei allen API-Aufrufen (z. B. `DepartureBoard` und `location.name`) werden die Secrets angehängt und der konfigurierte Basis-Pfad verwendet, wodurch die Requests die geschützten Zugangsdaten nutzen.【F:src/providers/vor.py†L399-L508】【F:src/providers/vor.py†L626-L720】

## Tests
- `pytest tests/test_vor_env.py tests/test_vor_default_version.py tests/test_vor_location_name.py`
  - Bestätigt das erwartete Verhalten beim Laden der Secrets, beim Normalisieren der Basis-URL sowie beim Zusammensetzen der Request-Parameter.【d744d3†L1-L9】
