# Tests im Wien ÖPNV Feed Projekt

Dieses Verzeichnis enthält die automatisierte Test-Suite, basierend auf `pytest`.

## Struktur

- **`conftest.py`**: Enthält globale Fixtures, z.B. Mocks für `requests.Session` oder temporäre Verzeichnisse.
- **`test_*.py`**: Die eigentlichen Testdateien. Sie sind meist nach dem Modul benannt, das sie testen (z.B. `test_build_feed.py` testet `src/build_feed.py`).

## Tests ausführen

### Alle Tests
Führe im Hauptverzeichnis des Projekts folgenden Befehl aus:
```bash
python -m pytest
```

### Spezifische Tests
```bash
python -m pytest tests/test_build_feed.py
```
Oder filtere nach Testnamen:
```bash
python -m pytest -k "dedupe"
```

## Wichtige Test-Konzepte

### 1. Mocking von Netzwerkzugriffen
Um externe Abhängigkeiten zu vermeiden und Tests deterministisch zu halten, werden Netzwerkaufrufe (`requests.get`) gemockt.
- Wir nutzen `requests-mock` oder `unittest.mock`.
- **Regel:** Kein Test darf echte HTTP-Anfragen an externe APIs senden (außer explizite Integrationstests, die gesondert markiert sind).

### 2. Pfad-Isolation
Viele Tests schreiben temporäre Dateien. Dafür nutzen wir das `tmp_path` Fixture von pytest.
Damit der `Path Guard` (`validate_path`) diese Pfade akzeptiert, müssen sie oft innerhalb der erlaubten Verzeichnisse (`data`, `docs`, `log`) simuliert werden.
Siehe `conftest.py` oder `tests/test_path_guard.py` für Beispiele.

### 3. Environment Variables
Tests, die Konfigurationen prüfen, nutzen das `monkeypatch` Fixture, um Umgebungsvariablen (`os.environ`) temporär zu überschreiben.

## Abdeckung (Coverage)
Wir streben eine hohe Testabdeckung an. Kritische Logik (Deduplizierung, Path-Guards, SSRF-Schutz) muss vollständig getestet sein.
