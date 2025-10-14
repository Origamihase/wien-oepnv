# VOR API Umgebungs-Setup (2025-10-17)

- **Ziel:** Sicherstellen, dass die Secrets (`VOR_ACCESS_ID`, `VOR_BASE_URL` etc.) beim Start der Tools automatisch in der Umgebung landen.
- **Maßnahme:** Neue Helper laden `.env`-Dateien wie `.env`, `data/secrets.env` oder `config/secrets.env` automatisch und respektieren zusätzlich den Pfad in `WIEN_OEPNV_ENV_FILES`.
- **Resultat:** Sowohl `src/providers/vor` als auch `scripts/test_vor_api.py` lesen die Dateien beim Start ein; vorhandene Umgebungswerte behalten Priorität. Damit stehen Secrets ohne manuelles `export` bereit.
- **Validierung:** `pytest` deckt den neuen Loader mit `tests/test_env_loader.py` ab und bestätigt, dass Werte gesetzt, respektive nicht überschrieben werden (`tests/test_env_loader.py`).
