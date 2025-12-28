# Feed Health Report

- **Status:** ❌ Fehlerhaft
- **Run-ID:** `20251228T133516Z`
- **Start:** 2025-12-28 14:35:16 CET
- **Ende:** 2025-12-28 14:35:16 CET

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

- build\_feed: Feed-Bau fehlgeschlagen: OUT\_PATH outside allowed directories Traceback \(most recent call last\): File &quot;/app/src/build\_feed.py&quot;, line 1763, in main out\_path = \_validate\_path\(Path\(OUT\_PATH\), &quot;OUT\_PATH&quot;\) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File &quot;/app/tests/../src/feed/config.py&quot;, line 79, in validate\_path raise InvalidPathError\(f&quot;{name} outside allowed directories&quot;\) feed.config.InvalidPathError: OUT\_PATH outside allowed directories
- Ausnahme: InvalidPathError: OUT\_PATH outside allowed directories
- InvalidPathError: OUT\_PATH outside allowed directories
