# Feed Health Report

- **Status:** ✅ Erfolgreich
- **Run-ID:** `20251202T151503Z`
- **Start:** 2025-12-02 16:15:03 CET
- **Ende:** 2025-12-02 16:15:03 CET
- **RSS-Datei:** `/workspace/wien-oepnv/docs/feed.xml`

## Pipeline-Kennzahlen

| Schritt | Anzahl |
| --- | ---: |
| Rohdaten | 43 |
| Nach Altersfilter | 43 |
| Nach Deduplizierung | 38 |
| Neue Items seit letztem State | 38 |
| Entfernte Duplikate | 5 |

### Laufzeiten

| Schritt | Dauer (s) |
| --- | ---: |
| collect | 0.00 |
| dedupe | 0.00 |
| filter | 0.00 |
| normalize | 0.00 |
| rss | 0.05 |
| total | 0.05 |

## Providerübersicht

| Provider | Status | Items | Dauer (s) | Details |
| --- | --- | ---: | ---: | --- |
| baustellen | empty | 0 | 0.00 | Cache-Datei fehlt (cache/baustellen/events.json) |
| oebb | ok | 1 | 0.00 |  |
| vor | empty | 0 | 0.00 | Keine aktuellen Daten |
| wl | ok | 42 | 0.00 |  |

### Entfernte Duplikate im Detail

- **4×** Schlüssel `wl|störung|L=1|D=2025-12-02` – Beispiele: `1: Schadhafter Zug Einstieg bei Linie 18 Richtung St Marx`, `1: Fahrtbehinderung Schadhafter Zug`, `1: Schadhafter Zug Betrieb ab Matzleinsdorfer Platz`
- **2×** Schlüssel `wl|störung|L=5A|D=2025-12-01` – Beispiele: `5A: Busse halten Dammstraße 2`, `5A: Busse halten Wallensteinstraße 51-53`
- **2×** Schlüssel `wl|störung|L=6|D=2025-12-02` – Beispiele: `6: Fahrtbehinderung Schadhafter Zug`, `6: Schadhafter Zug Einstieg bei LInie 1 Richtung Prater Hauptallee`

## Warnungen

- Provider vor: Keine aktuellen Daten
- Cache baustellen: Cache-Datei fehlt (cache/baustellen/events.json)
- Provider baustellen: Cache-Datei fehlt (cache/baustellen/events.json)
