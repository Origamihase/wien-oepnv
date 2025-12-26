# Mitwirken am Wien ÖPNV Feed (CONTRIBUTING.md)

Danke für dein Interesse am Projekt! Wir freuen uns über Bug Reports, Feature Requests, Dokumentationsverbesserungen und Pull Requests.

## Entwicklungsumgebung aufsetzen

Das Projekt benötigt **Python 3.11+**.

1. **Repository klonen:**
   ```bash
   git clone https://github.com/origamihase/wien-oepnv.git
   cd wien-oepnv
   ```

2. **Virtuelles Environment erstellen und aktivieren:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   # Windows: .venv\Scripts\activate
   ```

3. **Abhängigkeiten installieren:**
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   python -m pip install -r requirements-dev.txt
   ```

## Workflow für Entwickler

Wir nutzen eine vereinheitlichte CLI (`src.cli`) für die wichtigsten Aufgaben.

### Tests ausführen
Vor jedem Commit sollten die Tests durchlaufen:
```bash
python -m pytest
```

### Code-Style und Typprüfung
Wir nutzen `ruff` für Linting und `mypy` für Typprüfung.
```bash
python -m src.cli checks --fix
```

### Feed lokal bauen
Um Änderungen am Feed-Generator zu testen:
```bash
# Beispiel: Nur Wiener Linien und ÖBB aktivieren
export WL_ENABLE=true
export OEBB_ENABLE=true
export VOR_ENABLE=false
python -m src.cli feed build
```
Der Output liegt dann unter `docs/feed.xml`.

## Pull Requests (PRs)

1. **Feature-Branch erstellen:**
   Arbeite nie direkt auf `main`. Erstelle einen Branch wie `feature/mein-neues-feature` oder `fix/issue-123`.

2. **Kleine Commits:**
   Zerlege Änderungen in logische, atomare Commits mit aussagekräftigen Nachrichten.

3. **Tests schreiben/anpassen:**
   Jede Änderung an der Logik benötigt einen entsprechenden Test in `tests/`. Wenn du einen Bug fixest, schreibe zuerst einen Test, der den Bug reproduziert.

4. **CI-Checks beachten:**
   Beim Erstellen des PRs laufen GitHub Actions (`test.yml`). Stelle sicher, dass sie grün sind.

## Fehlermeldungen (Issues)

- Beschreibe das Problem präzise (Was hast du erwartet? Was ist passiert?).
- Füge Logs hinzu (siehe `log/errors.log` oder `log/diagnostics.log`).
- Nenne die verwendete Python-Version und das Betriebssystem.

## Dokumentation

Die Dokumentation lebt in `docs/` und in den Docstrings.
- Neue Features müssen in `README.md` oder entsprechenden `docs/`-Dateien dokumentiert werden.
- Nutze Google-Style Docstrings für Python-Funktionen.

## Lizenz

Mit dem Einreichen von Code erklärst du dich einverstanden, dass deine Beiträge unter der MIT-Lizenz des Projekts veröffentlicht werden.
