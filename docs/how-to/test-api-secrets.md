---
title: "Repository-Secrets sicher testen"
description: "So führen Sie API-Aufrufe mit in GitHub Actions hinterlegten Secrets aus, ohne die Zugangsdaten lokal preiszugeben."
---

# How to test an API call that uses repository secrets

Wenn der Endpunkt einer API und der zugehörige API-Key als Secrets im GitHub-Repository hinterlegt sind, können Sie den Abruf dennoch sicher testen, indem Sie die Ausführung innerhalb eines GitHub-Actions-Workflows vornehmen. Secrets werden ausschließlich in diesem Kontext aufgelöst – so behalten sie ihren Schutz, während Sie prüfen können, ob der Request erfolgreich ist.

## 1. Manuell startbaren Workflow anlegen

Legen Sie im Repository (z. B. in `.github/workflows/test-api.yml`) folgenden Workflow ab:

```yaml
name: Test API call

on:
  workflow_dispatch:
    inputs:
      resource:
        description: Optionaler Pfad oder Query-Parameter
        required: false

jobs:
  test-api:
    runs-on: ubuntu-latest
    env:
      API_URL: ${{ secrets.API_URL }}
      API_KEY: ${{ secrets.API_KEY }}
    steps:
      - name: Call API
        run: |
          curl --fail --silent --show-error \ 
            --header "Authorization: Bearer ${API_KEY}" \ 
            --header "Accept: application/json" \ 
            "${API_URL}${{ github.event.inputs.resource || '' }}"
```

* `workflow_dispatch` ermöglicht es, den Workflow jederzeit manuell über die GitHub-Oberfläche oder die GitHub CLI (`gh workflow run`) auszulösen.
* Die Secrets werden im Job als Umgebungsvariablen bereitgestellt und stehen dort sicher zur Verfügung.
* `curl --fail` sorgt dafür, dass der Schritt mit einem Fehler endet, falls der Serverstatuscode nicht im 2xx-Bereich liegt.

## 2. Ausführung auslösen und Ergebnis prüfen

1. Öffnen Sie den Reiter **Actions** Ihres Repositories.
2. Wählen Sie den Workflow **Test API call** aus und starten Sie ihn über **Run workflow**.
3. Verfolgen Sie das Log des Jobs. Eine erfolgreiche Ausführung bestätigt, dass die Secrets korrekt aufgelöst wurden und der API-Endpunkt erreichbar ist.

> Tipp: Wenn Sie zusätzliche Debug-Ausgaben benötigen, können Sie die Response mit `jq` formatieren oder den HTTP-Status separat ausgeben. Achten Sie jedoch darauf, niemals den Wert des Secrets zu loggen.

## 3. Optional: Tests über die GitHub CLI

Sie können denselben Workflow mit der GitHub CLI starten und den Status verfolgen:

```bash
# Workflow auslösen
gh workflow run "Test API call" --field resource="/status"

# Letzte Ausführung beobachten
gh run watch
```

Die CLI nutzt dabei ebenfalls die in GitHub gespeicherten Secrets. Auf diese Weise lässt sich der Abruf automatisiert und reproduzierbar testen, ohne die Geheimnisse lokal offenlegen zu müssen.

## 4. Beispiel: VOR API mit Secrets prüfen

Die VOR-Integration verwendet die Secrets `VOR_ACCESS_ID` (Access Token) und `VOR_BASE_URL` (Basis-URL inkl. Version). Das Repository enthält bereits einen Workflow `.github/workflows/test-vor-api.yml`, der diese Secrets verwendet, um einen Smoke-Test gegen die API durchzuführen (`location.name` Endpoint).

### Workflow ausführen

1. Öffnen Sie in GitHub den Reiter **Actions**.
2. Wählen Sie **Test VOR API** in der Liste der Workflows.
3. Klicken Sie auf **Run workflow**.

Alternativ über die CLI:
```bash
gh workflow run "Test VOR API"
gh run watch
```

### Ergebnis prüfen

Der Workflow führt `curl`-Abfragen sowie die vollständige Test-Suite (`pytest`) aus.

*   **Smoke test VOR endpoint**: Prüft, ob ein einfacher Abruf (z.B. nach "Wien Hauptbahnhof") mit den hinterlegten Secrets einen HTTP 200 Status liefert.
*   **Run pytest**: Validiert die gesamte Integrationslogik.

Schlägt der "Smoke test" fehl, sind meist die Secrets ungültig oder abgelaufen.
