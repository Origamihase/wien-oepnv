---
title: "Eigene Provider-Plugins anbinden"
description: "Schritt-für-Schritt-Anleitung, um neue Datenquellen als Provider-Plugin in die Feed-Pipeline einzubinden und lokal zu testen."
---

# Eigene Provider-Plugins anbinden

Die Feed-Pipeline kann über zusätzliche Provider erweitert werden, ohne den
Kerncode anzupassen. Dieses Dokument zeigt, wie ein Plugin erstellt, geladen und
getestet wird.

## 1. Plugin-Skelett erzeugen

Verwende den neuen Scaffold-Befehl, um ein startfertiges Modul zu erzeugen:

```bash
python scripts/scaffold_provider_plugin.py plugins/custom_provider.py
```

Die Datei enthält eine `register_providers`-Funktion, die einen Loader
registriert. Der Loader muss eine Liste von Ereignisdictionaries liefern, die
dem [Eventschema](../schema/events.schema.json) entsprechen.

## 2. Provider implementieren

Ersetze den Platzhalter durch deine Datenquelle. Beispiel mit einem einfachen
Cache-Leser:

```python
from pathlib import Path
import json


def register_providers(register_provider):
    def load_custom_events():
        cache_path = Path("data/custom/events.json")
        if not cache_path.exists():
            return []
        return json.loads(cache_path.read_text(encoding="utf-8"))

    register_provider("CUSTOM_PROVIDER_ENABLE", load_custom_events, cache_key="custom")
```

## 3. Plugin laden

Aktiviere das Plugin über die Umgebungsvariable
`WIEN_OEPNV_PROVIDER_PLUGINS`. Mehrere Module werden kommasepariert angegeben:

```bash
export WIEN_OEPNV_PROVIDER_PLUGINS=plugins.custom_provider
export CUSTOM_PROVIDER_ENABLE=1
python -m src.cli feed build
```

Während des Builds erscheinen der Providerstatus und mögliche Warnungen im
Feed-Health-Report (`docs/feed-health.md`).

## 4. Tests

Erstelle eine kleine Testdatei, die den Loader direkt aufruft oder den Feed-Build
mit aktivem Plugin über `python -m pytest` ausführt. Die neuen End-to-End-Tests
unter `tests/test_provider_plugins.py` zeigen, wie Plugins isoliert getestet
werden können.
