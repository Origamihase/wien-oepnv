# Feed Health Report

- **Status:** ❌ Fehlerhaft
- **Run-ID:** `20251226T205321Z`
- **Start:** 2025-12-26 21:53:21 CET
- **Ende:** 2025-12-26 21:53:21 CET

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

- build_feed: Feed-Bau fehlgeschlagen: OUT_PATH outside allowed directories Traceback (most recent call last): File "/app/src/build_feed.py", line 1724, in main out_path = _validate_path(Path(OUT_PATH), "OUT_PATH") ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/app/src/feed/config.py", line 75, in validate_path raise InvalidPathError(f"{name} outside allowed directories") feed.config.InvalidPathError: OUT_PATH outside allowed directories
- Ausnahme: InvalidPathError: OUT_PATH outside allowed directories
- InvalidPathError: OUT_PATH outside allowed directories
