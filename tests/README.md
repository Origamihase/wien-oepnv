# Tests im Wien ÖPNV Feed Projekt

Dieses Verzeichnis enthält die automatisierte Test-Suite, basierend auf `pytest`.

## Struktur

- **`conftest.py`**: Enthält globale Fixtures, darunter 15 autouse-Fixtures, die jeden Test von echten Dateien, dem Netz und dem Zustand früherer Tests fernhalten (umgeleitete Ledger, Caches und Zähler, zurückgesetzter Modulzustand, wiederhergestellte Umgebungsvariablen, kein echtes DNS). Die vollständige Liste mit Begründung steht in [`docs/development.md`](../docs/development.md) unter „Test-Isolation“.
- **`test_*.py`**: Die eigentlichen Testdateien. Sie sind meist nach dem Modul benannt, das sie testen (`test_build_feed_atom.py`, `test_build_feed_cache.py`, … decken jeweils einen Aspekt von `src/build_feed.py` ab; das Pendant zu `src/build_feed.py` ist also kein einzelnes Modul, sondern eine Familie themenspezifischer Dateien).
- **Unterverzeichnisse** für strukturierte Test-Suites:
  - `tests/places/` — Stationsverzeichnis-Pipeline (HAFAS; der OSM-Client hat seit 2026-10-17 keinen Aufruf im Produktivlauf mehr, die Google-Places-Stufe wurde am 2026-10-16 entfernt).
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
Um externe Abhängigkeiten zu vermeiden und Tests deterministisch zu halten, werden Netzwerkaufrufe gemockt.
- Wir nutzen die [`responses`](https://github.com/getsentry/responses)-Library (`requirements-dev.txt`) für HTTP-Stubs sowie `unittest.mock` / `pytest`-Fixtures für feinere Patches.
- **Regel:** Kein Test darf echte HTTP-Anfragen an externe APIs senden (außer explizite Integrationstests, die gesondert markiert sind).

### 2. Pfad-Isolation
Viele Tests schreiben temporäre Dateien. Dafür nutzen wir das `tmp_path` Fixture von pytest.
Damit der `Path Guard` (`validate_path`) diese Pfade akzeptiert, müssen sie oft innerhalb der erlaubten Verzeichnisse (`data`, `docs`, `log`) simuliert werden.
Siehe `conftest.py` oder `tests/test_path_guard.py` für Beispiele.

### 3. Environment Variables
Tests, die Konfigurationen prüfen, nutzen das `monkeypatch` Fixture, um Umgebungsvariablen (`os.environ`) temporär zu überschreiben.

## Abdeckung (Coverage)
Wir streben eine hohe Testabdeckung an. Kritische Logik (Deduplizierung, Path-Guards, SSRF-Schutz) muss vollständig getestet sein.
