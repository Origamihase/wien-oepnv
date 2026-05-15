# Tests im Wien ÖPNV Feed Projekt

Dieses Verzeichnis enthält die automatisierte Test-Suite, basierend auf `pytest`.

## Struktur

- **`conftest.py`**: Enthält globale Fixtures (z. B. Mocks für `requests.Session`, das `isolate_stats_writes`-Autouse-Fixture und das `reset_circuit_breakers`-Autouse-Fixture für deterministische Test-Reihenfolge).
- **`test_*.py`**: Die eigentlichen Testdateien. Sie sind meist nach dem Modul benannt, das sie testen (`test_build_feed_atom.py`, `test_build_feed_cache.py`, … decken jeweils einen Aspekt von `src/build_feed.py` ab; das Pendant zu `src/build_feed.py` ist also kein einzelnes Modul, sondern eine Familie themenspezifischer Dateien).
- **Unterverzeichnisse** für strukturierte Test-Suites:
  - `tests/places/` — Tier-1/2/3-Stationsverzeichnis-Pipeline (OSM, HAFAS, Google Places).
  - `tests/providers/` — WL- und ÖBB-Provider-Adapter.
  - `tests/scripts/` — Wartungsskripte (`update_stammstrecke_*`, `update_station_directory`, `generate_markdown_stats`, …).

## Tests ausführen

### Alle Tests
Führe im Hauptverzeichnis des Projekts folgenden Befehl aus:
```bash
python -m pytest
```

### Spezifische Tests
```bash
# Einzelne Datei
python -m pytest tests/test_build_feed_atom.py

# Alle Build-Feed-Tests via Glob
python -m pytest tests/test_build_feed_*.py
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
