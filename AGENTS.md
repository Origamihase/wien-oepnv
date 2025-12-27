# Informationen für KI-Agenten (AGENTS.md)

Dieses Dokument dient als Leitfaden für KI-Agenten, die an diesem Repository arbeiten. Es fasst die wichtigsten Architektur-Entscheidungen, Werkzeuge und Sicherheitsrichtlinien zusammen.

## Projektüberblick

Das Projekt `wien-oepnv` aggregiert Verkehrsmeldungen (Wiener Linien, ÖBB, VOR, Baustellen) und stellt sie als RSS-Feed sowie JSON-Daten bereit. Es legt höchsten Wert auf **Reproduzierbarkeit**, **Sicherheit** und **Datenintegrität**.

### Wichtige Verzeichnisse
- `src/`: Der gesamte Quellcode des Pakets.
  - `src/build_feed.py`: Hauptlogik zur Feed-Generierung.
  - `src/providers/`: Adapter für die verschiedenen Datenquellen.
  - `src/utils/`: Hilfsmodule (HTTP, Caching, Environment, Textverarbeitung).
- `scripts/`: Wartungsskripte (Cache-Updates, Stations-Validierung). Viele werden über die CLI gekapselt.
- `tests/`: Umfangreiche Pytest-Suite.
- `docs/`: Dokumentation, Audit-Logs und Referenzen.
- `data/`: Stationsverzeichnis (`stations.json`) und Mapping-Dateien.

## Entwicklungsumgebung & Werkzeuge

### Unified CLI
Das Projekt nutzt einen zentralen Einstiegspunkt für fast alle Aufgaben:
```bash
python -m src.cli [command]
```
Wichtige Befehle:
- `python -m src.cli feed build`: Erzeugt den Feed lokal.
- `python -m src.cli checks`: Führt `ruff` (Linter) und `mypy` (Type-Checker) aus.
- `python -m src.cli tests`: (Falls implementiert, sonst direkt `pytest` nutzen).

### Testing
- Framework: `pytest`
- Ausführung: `pytest` (im Root-Verzeichnis).
- **Regel:** Vor jedem Commit müssen alle Tests bestehen. Neue Features oder Bugfixes müssen durch Tests abgedeckt sein.

### Statische Analyse
- **Ruff**: Für Linting und Code-Formatierung.
- **Mypy**: Für statische Typprüfung.
- Befehl: `python -m src.cli checks` (oder `scripts/run_static_checks.py`).

## Coding-Conventions & Sicherheit

1.  **Secrets & Konfiguration**:
    - **Niemals** Secrets (API-Keys, Token) im Code hardcoden.
    - Nutze `src.utils.env` oder `os.getenv`.
    - Secrets werden über Umgebungsvariablen oder `.env`-Dateien (nicht eingecheckt!) geladen.

2.  **Dateisystem & Pfade**:
    - Verwende **immer** `validate_path` oder `resolve_env_path` aus `src.feed.config` (bzw. Import via `src.build_feed`), wenn Dateipfade aus Konfigurationen verarbeitet werden.
    - Schreiben ist nur in `docs/`, `data/` und `log/` erlaubt (Path-Traversal-Schutz).
    - Verwende `src.utils.files.atomic_write` für alle Dateischreibvorgänge, um Atomizität und korrekte Berechtigungen sicherzustellen.
    - Tests, die temporäre Dateien benötigen, sollten diese in `data/` erstellen, um Path-Traversal-Checks zu bestehen.

3.  **Netzwerkzugriffe (SSRF-Schutz)**:
    - Verwende für externe Requests **ausschließlich** `src.utils.http.session_with_retries` und `fetch_content_safe`.
    - `fetch_content_safe` implementiert DNS-Rebinding-Schutz (Überprüfung der verbundenen IP) und Limits für die Antwortgröße (DoS-Schutz).
    - `validate_http_url` muss vor Redirects oder Request-Initiierung aufgerufen werden.

4.  **Logging**:
    - Sensible Daten (Token, Auth-Header) müssen vor dem Logging maskiert werden (siehe `_sanitize_message` in Providern).

5.  **Typisierung**:
    - Python 3.11+ Syntax.
    - Type-Hints sind **verpflichtend** (`from __future__ import annotations`).

## Dokumentation
- Änderungen am Code müssen in der Dokumentation (z. B. `README.md` oder `docs/`) reflektiert werden, falls sich das Verhalten ändert.
- Halte Docstrings aktuell.
