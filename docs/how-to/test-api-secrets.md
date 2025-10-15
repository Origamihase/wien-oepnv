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

Die VOR-Integration verwendet die Secrets `VOR_ACCESS_ID` (Access Token) und `VOR_BASE_URL` (Basis-URL inkl. Version). Beide Werte werden beim Laden der Provider-Konfiguration direkt aus der Umgebung gelesen und für Requests ergänzt.【F:src/providers/vor.py†L274-L301】【F:src/providers/vor.py†L358-L378】

Legen Sie im Repository zusätzlich einen aufrufbaren Workflow wie `.github/workflows/test-vor-api.yml` an:

```yaml
name: Test VOR API

on:
  workflow_dispatch:
    inputs:
      station_id:
        description: Optionale StationBoard-ID (Standard siehe Skript)
        required: false

jobs:
  vor-auth:
    runs-on: ubuntu-latest
    env:
      VOR_ACCESS_ID: ${{ secrets.VOR_ACCESS_ID }}
      VOR_BASE_URL: ${{ secrets.VOR_BASE_URL }}
      VOR_AUTH_TEST_STATION: ${{ github.event.inputs.station_id }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Check VOR authentication
        run: python scripts/check_vor_auth.py
```

Das Skript `scripts/check_vor_auth.py` führt einen einzelnen `departureboard`-Request aus, hängt das Secret automatisch als `accessId` bzw. `Authorization`-Header an und gibt ein JSON-Ergebnis mit Statuscode und `authenticated`-Flag aus.【F:scripts/check_vor_auth.py†L1-L145】 Über `station_id` können Sie optional die getestete Station über die Umgebungsvariable `VOR_AUTH_TEST_STATION` überschreiben; ohne Eingabe greift das Skript auf die Standard-ID zurück.【F:scripts/check_vor_auth.py†L97-L105】

### Workflow ausführen und Ergebnis interpretieren

1. Öffnen Sie in GitHub den Reiter **Actions**, wählen Sie **Test VOR API** und klicken Sie auf **Run workflow**. Optional geben Sie eine `station_id` ein.
2. Prüfen Sie im Log den Schritt **Check VOR authentication**. Eine erfolgreiche Authentifizierung erkennen Sie daran, dass `authenticated` auf `true` steht und der HTTP-Status < 400 ist.【F:scripts/check_vor_auth.py†L126-L145】
3. Bei Fehlern zeigt das JSON `error_code`/`error_text` sowie den HTTP-Status – diese Informationen helfen beim Nachschärfen der Secrets.

### Was bedeutet eine erfolgreiche Ausführung?

Wenn der Workflow – wie im Screenshot zu sehen – ohne Fehler endet und der JSON-Block unter **Check VOR authentication** in etwa wie folgt aussieht, sind mehrere Punkte gleichzeitig bestätigt:

```json
{
  "authenticated": true,
  "status_code": 200,
  "url": "https://.../departureboard?accessId=***&format=json&id=430470800",
  "payload": {
    "stopLocationOrCoordLocation": [...]
  }
}
```

* **Secrets werden aufgelöst:** Sowohl `VOR_ACCESS_ID` als auch `VOR_BASE_URL` wurden vom Runner entschlüsselt und in das Skript injiziert. Andernfalls könnte keine Anfrage gestellt werden.【F:scripts/check_vor_auth.py†L72-L123】
* **Anmeldung bei der VOR API funktioniert:** Der HTTP-Status ist < 400, das Skript meldet `authenticated: true` und es werden keine Auth-Fehlercodes gemeldet. Damit ist der Access Token gültig und hat Zugriff auf den gewünschten Endpunkt.【F:scripts/check_vor_auth.py†L126-L145】
* **Netzwerkpfad ist offen:** Der Runner konnte die VOR-API über das Internet erreichen. Wäre die API blockiert oder die Basis-URL falsch, würde der Request scheitern und `error_text` gefüllt sein.【F:scripts/check_vor_auth.py†L103-L145】

Die Response im Feld `payload` zeigt außerdem bereits Live-Daten (z. B. Abfahrts-Events), sodass Sie auf einen Blick sehen, ob die erwarteten Informationen zurückgeliefert werden.

Alternativ lässt sich der Workflow auch per CLI starten:

```bash
# Optional andere Station testen
gh workflow run "Test VOR API" --field station_id=430470800

gh run watch
```

Mit dieser Vorgehensweise verifizieren Sie die hinterlegten VOR-Secrets ohne lokalen Zugriff auf die Klarwerte und sehen unmittelbar, ob die API-Anmeldung funktioniert.
