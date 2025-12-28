# Feed Health Report

- **Status:** ❌ Fehlerhaft
- **Run-ID:** `20251228T173107Z`
- **Start:** 2025-12-28 18:31:07 CET
- **Ende:** 2025-12-28 18:31:07 CET

## Pipeline-Kennzahlen

| Schritt | Anzahl |
| --- | ---: |
| Rohdaten | 0 |
| Nach Altersfilter | 0 |
| Nach Deduplizierung | 0 |
| Neue Items seit letztem State | 0 |
| Entfernte Duplikate | 0 |

## Providerübersicht

| Provider | Status | Items | Dauer (s) | Details |
| --- | --- | ---: | ---: | --- |
| baustellen | pending | — | — |  |
| oebb | pending | — | — |  |
| vor | pending | — | — |  |
| wl | pending | — | — |  |

## Fehler

- build_feed: Feed-Bau fehlgeschlagen: OUT_PATH outside allowed directories Traceback (most recent call last): File &quot;/app/src/build_feed.py&quot;, line 1773, in main out_path = _validate_path(Path(OUT_PATH), &quot;OUT_PATH&quot;) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File &quot;/app/src/feed/config.py&quot;, line 79, in validate_path raise InvalidPathError(f&quot;{name} outside allowed directories&quot;) feed.config.InvalidPathError: OUT_PATH outside allowed directories
- Ausnahme: InvalidPathError: OUT_PATH outside allowed directories
- InvalidPathError: OUT_PATH outside allowed directories
