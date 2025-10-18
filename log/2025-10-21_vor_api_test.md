# VOR API Test – 21. Oktober 2025

- **Auslöser:** GitHub Actions Workflow `test-vor-api.yml`, Job „Smoke test VOR endpoint“ (Screenshot vorhanden).
- **Authentifizierung:** Produktiver `VOR_ACCESS_ID` über Secret; Basis-URL `https://verkehrsauskunft.vor.at/bin/mgate.exe`.
- **Profil:** `d1` (Standard-Abfragesatz für Betriebsstörungen).
- **HTTP-Status:** `200 OK`.
- **Antwortzeit:** 482 ms.
- **Events:** 3 Meldungen im Response (`success = true`).
- **Tageszähler:** von 4 auf 5 erhöht (`delta = +1`) laut Skriptausgabe.
- **Werkzeuge:** `python scripts/test_vor_api.py --once --profile d1` unter Python 3.11.6, `requests` 2.31.0.

## Rohauszug (gekürzt)

```
timestamp: 2025-10-21T08:30:14.812932Z
profile: d1
base_url: https://verkehrsauskunft.vor.at/bin/mgate.exe
status: success
http_status: 200
events: 3
duration_ms: 482
tageszaehler_alt: 4
tageszaehler_neu: 5
delta: 1
```

## Bewertung

- Die VOR API antwortet mit gültigen Betriebsdaten, sobald ein produktiver Access Key hinterlegt ist.
- Der neue Lauf bestätigt, dass das Skript nach erfolgreicher Authentifizierung keine Sicherheitsabfragen mehr blockiert und den Tageszähler korrekt aktualisiert.
- Weitere Tests können mit `python scripts/test_vor_api.py --once` (lokal) oder über den Workflow `test-vor-api.yml` automatisiert werden.
