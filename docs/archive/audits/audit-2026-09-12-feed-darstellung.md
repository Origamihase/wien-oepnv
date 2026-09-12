# Audit: Feed-Quellen, Verarbeitung und Darstellung

**Datum:** 2026-09-12
**Schwerpunkt:** Feed — Quellen, Pipeline, Darstellung in `docs/feed.xml` / `docs/feed.en.xml`
**Datenbasis:** Manual-Full-Refresh Run 93948230655 (Checkout `275ed8d586`, 08:02–08:05 UTC),
Repo-Stand `afc72b5f6c`, Live-Caches in `cache/`
**Status:** Befunde 1, 2, 3, 4, 5, 8 und 9 behoben (PR #1790–#1799). Offen: 7 und 6 — beide ohne Feed-Wirkung (s. Abschnitt 11)

---

## 1. Executive Summary

**Die Quellen funktionieren.** Alle fünf Datenquellen haben im auditierten Lauf
geantwortet, keine einzige Fehlermeldung, keine Netzwerk- oder Auth-Störung.
Die Pipeline ist stabil, die CI ist grün, der Feed ist wohlgeformt.

**Die Darstellung hat Mängel.** Neun Befunde — zwei davon kamen erst beim
Beheben der anderen dazu (8 beim Nachprüfen von 2, 9 beim Neupriorisieren).
**Sieben sind behoben.** Sechs davon wirkten auf den deutschen Feed, der
siebte (Befund 3) auf die englische Übersetzung:

| # | Befund | PR |
| --- | --- | --- |
| 1 | ÖBB-Titel nennt eine Station doppelt | [#1790](https://github.com/Origamihase/wien-oepnv/pull/1790) |
| 2 | Vier von 83 Meldungen als „Duplikat" verworfen | [#1791](https://github.com/Origamihase/wien-oepnv/pull/1791) |
| 8 | Dieselbe Störung zweimal im Feed (38A) | [#1792](https://github.com/Origamihase/wien-oepnv/pull/1792) |
| 9 | Ein Feuerwehreinsatz belegte zwei Feed-Plätze (64A) | [#1794](https://github.com/Origamihase/wien-oepnv/pull/1794) |
| 5 | ÖBB-Items trugen das Veröffentlichungsdatum statt des Bauzeitraums | [#1795](https://github.com/Origamihase/wien-oepnv/pull/1795) |
| 4 | Drei von 22 Baustellen-Titeln brachen mitten im Wort ab | [#1798](https://github.com/Origamihase/wien-oepnv/pull/1798) |
| 3 | Übersetzung erfand Liniennummern (nur EN) | [#1799](https://github.com/Origamihase/wien-oepnv/pull/1799) |

Zwei Muster ziehen sich durch:

**Befund 1 und 5 wurden zu klein notiert.** Bei 1 galt ein Fix als erledigt,
der den Live-Pfad nie erreichte — verifiziert war die Funktion, nicht die
Pipeline. Bei 5 war „drei gleiche Titel" nur die Spitze: Allen **elf**
ÖBB-Items fehlte der Zeitraum, und die Zeitzeile behauptete stattdessen ein
„Seit \<Veröffentlichungsdatum\>", das bei künftigen Sperren schlicht falsch
war.

**Befund 2 und 8 sind zwei Hälften derselben Sache.** Ein zu grober
Dedupe-Schlüssel hat gleichzeitig Verschiedenes weggeworfen **und** Gleiches
zusammenfallen lassen; weil das Ergebnis oft passabel aussah, fiel keine der
beiden Hälften auf. Von den vier ursprünglich verworfenen Meldungen war nur
das Paar `49A/50B` wirklich verschieden — die Linie-44-Trias ist ein Ereignis
aus drei Blickwinkeln (s. Korrektur in Abschnitt 4). Befund 9 zeigte dann, dass
die Wortliste aus 8 nur halb zu Ende gedacht war.

Befund 4 und 3 fügen dem ersten Muster zwei weitere Fälle hinzu. Bei 4 war
**ein** abgebrochener Titel notiert, tatsächlich waren es **drei von 22** — und
die Reparatur hätte beinahe die GUID mitverschoben (s. Abschnitt 6). Bei 3
waren drei Lückenklassen notiert, tatsächlich waren es fünf: `86AR` und `U6E`
fielen erst beim Abzählen der Live-Tokens auf (s. Abschnitt 5). **Dreimal von
sieben war der Befund beim Beheben größer als beim Notieren** — Nachmessen an
den Live-Daten lohnt sich vor jeder Korrektur.

**Offen bleiben die Befunde 7 und 6** — beide ohne jede Feed-Wirkung: 7 kostet
Laufzeit, 6 erzeugt Log-Rauschen. Beide sind belegt und mit Reproduktion
dokumentiert; jeder gehört in eine eigene, einzeln prüfbare Änderung.

---

## 2. Funktionieren die Quellen?

Ja. Aus dem Refresh-Log, Schritte 10–17:

| Quelle | Ergebnis | Laufzeit |
| --- | --- | --- |
| Stadt Wien OGD Baustellen (WFS) | 66 Meldungen geladen, 44 ohne ÖPNV-Bezug verworfen, **22** gecacht | 1,7 s |
| Wiener Linien (OGD) | **52** Items nach Filter/Dedupe | 2,2 s |
| ÖBB (RSS) | **11** Items nach Region/Titel-Kosmetik | 11 s |
| VOR/VAO (Stammstrecke) | `departureBoard` OK, 29 Abfahrten, 17 auf Nicht-Stammstrecke-Gleis verworfen | 1,3 s |
| HAFAS + OSM Overpass (Stationsverzeichnis) | beide HTTP 200, **2246** Stationen geschrieben | 20 s |

Der Feed-Bau selbst: `Status=success`, 83 Rohitems, 79 nach Dedupe, 10
veröffentlicht (`MaxItems=10`), 8,4 s gesamt. Alle Provider `ok` außer
`stammstrecke:empty`.

Ergänzend: `Health check` (Run 403) grün, `Update cycle` grün, CI auf `main`
grün. Die volle Testsuite: **8936 bestanden, 2 übersprungen**. Drei Fehlschläge
treten nur in dieser Sandbox auf (`test_client_ssrf_protection`,
`test_dns_rebinding_bypass_prevented`, `test_fetch_content_safe_charset`) — alle
drei laufen über die DNS-/SSRF-Prüfung in `request_safe`, die der `HTTPS_PROXY`
dieser Umgebung stört; der dritte ist einzeln grün und fällt nur im Volllauf um.
In CI sind alle drei grün.

**Eine Einschränkung:** `stammstrecke` liefert seit dem Umbau konsequent
0 Items (`cache/stammstrecke/events.json` hat nie existiert). Das ist korrektes
Verhalten — ohne Verspätung und ohne Ausfall gibt es nichts zu melden —, wird
aber pro Lauf als `WARNING` protokolliert und landet im Run-Report unter
„Warnungen". Eine funktionierende Stammstrecke sieht damit aus wie ein Defekt.
Siehe Befund 6.

---

## 3. Befund 1 — ÖBB-Titel nennt die Station doppelt (behoben)

**Schweregrad: hoch** (sichtbar im Feed) · **Status: behoben** ([PR #1790](https://github.com/Origamihase/wien-oepnv/pull/1790))

### Symptom

`cache/oebb_c40d21/events.json`, unverändert seit 2026-09-11 23:01:

```
S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach
```

Und in `data/first_seen.json` die englische Entsprechung:

```
S 4: construction works: no stop in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach
```

### Warum der Fix von heute früh nicht gegriffen hat

PR #1789 hat den Redundanz-Check in `_clean_title_keep_places` vom ersten auf
den letzten `": "` umgehängt. Ich habe im PR behauptet, der Cache heile sich
beim nächsten `update_oebb_cache`-Lauf. **Das war falsch.** Beleg:

- Der Merge (`f30b9d0ce3`) lief um 07:44 UTC.
- Der Refresh checkte `275ed8d586` aus — ein Commit **nach** dem Merge — und
  schrieb den Cache neu (`Updated ÖBB cache with 11 events.`).
- `cache/oebb_c40d21/events.json` wurde vom Auto-Commit (`add_options: -A`)
  **nicht** als geändert gemeldet: `fetch_events()` hat byte-identisch dasselbe
  produziert. Der defekte Titel stand danach unverändert im Repo.

Die Ursache liegt in der **Reihenfolge**. Der Redundanz-Check läuft auf dem
Rohtitel; die Normalisierung der Stationsnamen (`_clean_endpoint`, entfernt
„Bahnhst"/„Bahnhof") läuft erst danach, pro Segment. Upstream schreibt die
beiden Hälften aber in **unterschiedlicher Schreibweise**:

```
Bauarbeiten - kein Halt in Lind-Rosegg Bahnhst Föderlach Bahnhof: Lind-Rosegg Föderlach
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^
                          mit Bahnhofs-Zusätzen                   bereits ohne
```

Zum Prüfzeitpunkt sind die Hälften also **nicht** wörtlich gleich — der Check
findet keine Redundanz und lässt sie stehen. Erst `_clean_endpoint` macht aus
der linken Hälfte buchstäblich „Lind-Rosegg Föderlach", und der
Kategorie-Join-Zweig setzt am Ende `Bauarbeiten: <Rest>` wieder zusammen. Genau
dieser Zweig stand in PR #1789 unter „bewusst ausgeklammert" — er war nicht
Nebenschauplatz, sondern der Ort des Fehlers.

Der Rohtitel ließ sich nicht direkt abrufen (der Agent-Proxy blockiert
`fahrplan.oebb.at`), aber aus dem gecachten Ergebnis rekonstruieren: zwei
Kandidaten reproduzieren den Live-String zeichengenau durch die **aktuelle**
Pipeline.

### Korrektur

Der Redundanz-Check ist jetzt eine eigene Funktion `_drop_redundant_suffix` und
läuft **zweimal** — einmal auf dem Rohtitel wie bisher, einmal auf dem fertig
zusammengesetzten Titel, also nach der Normalisierung:

```
vorher:  S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach
nachher: S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach
```

Das Herausziehen in eine Funktion nimmt `_clean_title_keep_places` Zweige ab;
der zweite Aufruf fügt keine hinzu. Der C901-Gate meldet `increased: 0`, die
Baseline (26) bleibt unangetastet.

Geprüft: 12 Tests in `tests/test_oebb_title.py` (3 neu), 1131 Tests im
Umkreis `oebb|title|clean|feed`, `ruff` und `mypy --strict` sauber. Von den elf
gecachten ÖBB-Titeln ändert sich genau der fehlerhafte, die übrigen zehn sind
stabil.

### Nachtrag: keine Idempotenz

`_clean_title_keep_places` ist kein Fixpunkt auf der eigenen Ausgabe — ein
zweiter Durchlauf entfernt zusätzlich das Label (`Bauarbeiten: X` → `X`). Das
ist vorbestehend und ohne Folgen, weil `_build_item_from_xml` immer vom
Rohtitel startet, nie von einem früheren Ergebnis. Ein Test hält das jetzt
explizit fest, statt eine Idempotenz zu behaupten, die die Funktion nicht hat.

---

## 4. Befund 2 — Verschiedene Störungen werden als Duplikat verworfen

**Schweregrad: hoch** (Informationsverlust) · **Status: behoben (Nachtrag 2026-09-12)**

`python -m src.cli feed lint` meldet für den aktuellen Datenstand:

```
Nach Deduplizierung: 79 (entfernte Duplikate: 4)

- 3× Schlüssel wl|störung|L=44|D=2026-09-11:
    44: Veranstaltung Züge halten Rosensteingasse bei Linie 9 Richtung Westbahnhof
    44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz
    44: Fahrtbehinderung Veranstaltung
- 2× Schlüssel wl|hinweis|L=49A,50B|D=2026-08-25:
    49A/50B: Mondweg
    49A/50B: Hüttergasse
- 2× Schlüssel wl|störung|L=3A|D=2026-09-07:
    3A: Netzänderung Betrieb ab Riemergasse
    3A: Busse halten Kärntner Ring 5
```

Jeweils einer wird veröffentlicht, die anderen verschwinden — **4 von 83
Items, rund 5 %**.

> **Korrektur (Nachtrag 2026-09-12, nach dem Fix):** Der Satz „Das sind keine
> Duplikate" stand hier ursprünglich für alle vier Items und war zu
> selbstsicher. Belastbar ist er für **`49A/50B`** — „Mondweg" und
> „Hüttergasse" sind zwei verschiedene Straßen. Die **Linie-44-Trias** dagegen
> ist bei genauerem Hinsehen **ein** Ereignis, aus drei Blickwinkeln gemeldet;
> sie gehört zusammengeführt, nur eben qualitätsbewusst und nicht durch blindes
> Verwerfen. Das ist in Befund 8 nachgetragen. `3A` bleibt offen — dort gibt es
> kein gemeinsames Ursachenwort, an dem sich das entscheiden ließe.

### Ursache

`_wl_identity` (`src/providers/wl_fetch.py:228`) baut den Schlüssel aus
Linien-Set und Starttag, absichtlich titel- und beschreibungsunabhängig, damit
`first_seen` über Wortlaut-Änderungen stabil bleibt. Ein `topic_key` wird nur
dann eingemischt, wenn **kein** Linien-Set **oder** **kein** Startdatum
vorliegt. Der Docstring benennt das Risiko selbst:

> two genuinely distinct line-less Hinweise, or two distinct date-less
> Störungen sharing a line set, then collapse to the same key and
> `_dedupe_items` … silently drops one

Die Live-Daten zeigen: Das passiert auch im Normalfall — die Wiener Linien
veröffentlichen regelmäßig mehrere verschiedene Störungen für dieselbe Linie am
selben Tag. Die Absicherung deckt nur die Randfälle ab.

`_dedupe_key_for_item` (`src/build_feed.py:3273`) nimmt `_identity` und bricht
sofort ab — die feinere `guid` wird nie erreicht. Dabei hat **jedes** der
kollidierenden Items eine eigene, stabile SHA-256-`guid`.

### Korrektur (Nachtrag 2026-09-12)

Der `topic_key` wird jetzt **immer** eingemischt.

Die Sorge, das koste `first_seen`-Stabilität, hat sich beim Nachmessen
aufgelöst — der `topic_key` ist auf dieser Ebene kein neues Signal:

* Der **Bucket-Key** in `fetch_events` ist
  `make_guid("wl", category, topic_key, Linien-Set)`.
* Der **`guid`** jedes Items ist aus denselben drei Feldern gebaut.
* `_state_key_for_item` führt `first_seen` am **`guid`** — nicht an
  `_identity`. Der Schlüssel bewegt sich also ohnehin mit, sobald sich der
  `topic_key` bewegt.

Daraus folgt beides, was den Fix trägt: Echte Duplikate erreichen den
`_identity`-Vergleich gar nicht erst, weil das Bucketing sie vorher zu einem
Item verschmilzt; und `_identity` ist jetzt eine **Verfeinerung des
Bucket-Keys**, kann also nichts mehr verwerfen, was der Provider nicht schon
selbst zusammengeführt hat.

Was tatsächlich entfällt, ist der Legacy-`_identity`-Fallback in
`_lookup_state` für Einträge aus der Zeit vor der `guid`-Umstellung. Gemessen:
Von 53 gecachten WL-Items lösen **48** über den `guid` auf, **5** sind neu und
**0** hängen an diesem Fallback.

**Wirkung**, mit aufgefrischtem Cache gegengeprüft:

| | vorher | nachher |
| --- | --- | --- |
| entfernte Duplikate | 4 | **0** |
| Items im Lint | 79 von 83 | **84 von 84** |
| Lint-Befund | 3 Duplikat-Gruppen | „Keine strukturellen Probleme gefunden" |
| Run-Status | `error` | `success` |

Abgesichert durch `tests/test_wl_dedupe_distinct_disruptions.py`. Die Tests
fahren bewusst den **echten Pfad** — `fetch_events` (Identity-Bildung **und**
Bucketing) und danach `_dedupe_items` —, nicht `_wl_identity` allein: Ein
Unit-Test auf der Identity-Funktion wäre vor wie nach dem Fix grün gewesen,
weil der Defekt erst im Zusammenspiel der beiden Ebenen sichtbar wird. Genau
diese Lücke hat bei Befund 1 dazu geführt, dass ein Fix als erledigt galt, der
es nicht war. Gegenprobe: Die drei Regressionstests fallen gegen den
ungefixten Code um.

### Abgrenzung: die Ebene darunter bleibt

Tragen zwei Titel dasselbe Wort aus `TITLE_TOPIC_TOKENS` (etwa „Umleitung"),
reduziert `_topic_key_from_title` beide auf dieses Token, und das **Bucketing**
fasst sie zu einem Item zusammen — mit dem besser bewerteten Titel und der
Vereinigung von Haltestellen und Extras. Das ist eine gewollte Aggregation
(„ein Item pro Kategorie + Topic + Linienset") und passiert eine Ebene vor
`_identity`. Dieser Fix verursacht sie nicht und hebt sie nicht auf; ein Test
hält die Grenze fest, damit beide Ebenen nicht verwechselt werden.

Die echten 49A/50B-Titel sind davon nicht betroffen: „Mondweg" und
„Hüttergasse" enthalten kein Topic-Token, fallen auf den Titel-Kern zurück und
bleiben damit unterscheidbar.

---

## 5. Befund 3 — Übersetzung verstümmelt Liniennummern

**Wirkung auf den deutschen Feed: keine** (nur `feed.en.xml`) · **Status: behoben**
([PR #1799](https://github.com/Origamihase/wien-oepnv/pull/1799))

Live in `docs/feed.en.xml`, Item 10:

| | |
| --- | --- |
| DE | `43A/44A/844/N43/44B: Gleisbauarbeiten (Phase 2)` |
| EN | `43A/44A844/N43/44B: track construction works (phase 2)` |

Der Schrägstrich zwischen `44A` und `844` fehlt — zwei Linien sind zu einer
erfundenen Nummer `44A844` verschmolzen.

### Ursache

`_LINE_ENTITY_RE` (`src/build_feed.py:1048`) schützt Linien-Tokens vor dem
NMT-Modell:

```python
r"\b(U[1-6]|S[0-9]+|[1-9][0-9]?[A-Z]?)\b"
```

`[1-9][0-9]?[A-Z]?` deckt nur **ein- und zweistellige** Nummern ab. Praktisch
geprüft:

| Token | maskiert | Beispiel im Netz |
| --- | --- | --- |
| `43A`, `44A`, `44B`, `13A`, `U6`, `S40` | ja | — |
| `844`, `141` | **nein** | dreistellige Bus-/Regionallinien |
| `N43`, `N71`, `N31` | **nein** | Nachtbusse |
| `O`, `D` | **nein** | Straßenbahnlinien O und D |

Was nicht maskiert ist, geht ungeschützt durch das Modell. Der Masking-Test
zeigt es direkt:

```
XENT…X0X/XENT…X1X/844/N43/XENT…X2X: Gleisbauarbeiten (Phase XENT…X3X)
                  ^^^^ ^^^ ungeschützt
```

`N71` und `N31` sind diesmal heil geblieben (Items 8 und 9) — das ist Glück,
keine Garantie.

Nebenbefund: die `2` aus „(Phase 2)" wird als Linien-Token maskiert. Hier
folgenlos, aber ein Hinweis, dass das Muster auch zu breit greift.

### Korrektur

Beim Nachmessen an den Live-Caches war die Lücke größer als notiert. Von den
**71** Linien-Tokens, die dort als Titel-Präfix vorkommen, schützte das alte
Muster **58**. Die 13 Fehlstellen — vier davon standen nicht im ursprünglichen
Befund:

| Token | Was fehlte | im Befund notiert? |
| --- | --- | --- |
| `844` | dreistellige Nummern — nur zwei Stellen erlaubt | ja |
| `N8`, `N20`, `N29`, `N43`, `N46`, `N49`, `N65`, `N66`, `N71` | Nachtbusse — kein `N`-Präfix | ja |
| `D` | Straßenbahn D | ja |
| `86AR` | **zweibuchstabiges Suffix** — nur ein Buchstabe erlaubt | **nein** |
| `U6E` | **U-Bahn-Verstärker** — nach `U<n>` kein Suffix erlaubt | **nein** |

`86AR` und `U6E` standen am 2026-09-12 im Feed und kamen heil durch — wie
`N71` und `N31`. Das ist kein Schutz, sondern Glück: Ein unmaskiertes Token
überlebt nur so lange, wie das Modell es zufällig in Ruhe lässt. Die Tests
prüfen deshalb die **Maskierung**, nicht die Modellausgabe; ein Test auf das
Übersetzungsergebnis wäre an einem guten Tag grün geworden.

Jede Alternative des neuen Musters ist eine **echte Obermenge** der alten,
kein bisher geschütztes Token verliert seinen Schutz — ein Test pinnt das.
Insbesondere behält `S[0-9]+` seinen unbegrenzten Ziffernlauf, statt auf den
real verkehrenden Bereich S1–S80 verengt zu werden: Verengen ist die einzige
Richtung, die etwas ungeschützt lassen könnte.

`D` und `O` bekommen ein eigenes Muster mit Kontext-Gate
(`_TRAM_LETTER_LINE_RE`), weil ein einzelner Buchstabe keine erkennbare Form
hat. Maskiert wird nur am Titelanfang vor dem Doppelpunkt, neben einem
Schrägstrich oder nach „Linie"; „Vitamin D" und „Ausgang D" bleiben
unangetastet. Getrennt gehalten, weil `_LINE_ENTITY_RE` zusätzlich als
`fullmatch`-Prädikat dient, das linienförmige Stationsnamen aus dem
Stations-Schutzmuster hält — dort hat ein kontextabhängiges Muster keine
sinnvolle Bedeutung. Gegen das Live-Verzeichnis geprüft: Die Verbreiterung
filtert **keinen** zusätzlichen Stationsnamen heraus.

**Nicht mitbehoben:** Der Nebenbefund oben bleibt bestehen — die `2` aus
„(Phase 2)" wird weiterhin als Linien-Token maskiert. Folgenlos, und die
Gegenmaßnahme wäre ein Verengen des Musters, also genau die riskante
Richtung.

---

## 6. Befund 4 — Baustellen-Titel brechen mitten im Zitat ab

**Wirkung auf den deutschen Feed: verstümmelter Titel** · **Status: behoben**
([PR #1798](https://github.com/Origamihase/wien-oepnv/pull/1798))

Nicht ein Titel, sondern **drei von 22**:

| Länge | Titel (gekürzt dargestellt) |
| --- | --- |
| 100 | `… und Apostelgasse bis Schlachthausgas` |
| 100 | `… bis Unbenannte Verkehrsfläche und Rad` |
| 99 | `… auf Seite "Otto Wagner Hofpavillon` |

Die Stadt Wien kappt `BEZEICHNUNG` bei 100 Zeichen; das Projekt kürzt Titel an
dieser Stelle nicht selbst. Die Längenverteilung der 22 Live-Titel belegt die
Obergrenze: 100, 100, 99 — dann Sprung auf 94. Zwei der drei enden mitten im
Wort, der dritte mit einem Anführungszeichen, das nie schließt.

### Korrektur

Den fehlenden Text kann niemand zurückholen; kenntlich machen schon.
`_mark_upstream_truncation` hängt eine Ellipse an und entfernt ein unpaariges
Anführungszeichen.

Erkannt wird an **zwei unabhängigen Signalen** statt an der Länge allein:

1. `len >= 100` — die harte Obergrenze ist erreicht.
2. ein unpaariges `"` — ein vollständiger Titel hat keines.

Das zweite Signal fängt den 99-Zeichen-Fall. Upstream kappt bei 100 und
entfernt danach Leerraum, ein gekappter Titel kann also auch bei 99 landen —
über die Länge allein nicht von einem echten 99-Zeichen-Titel zu
unterscheiden, über das offene Anführungszeichen schon.

**Der Text selbst bleibt unangetastet.** „… bis Schlachthausgas…" sieht unschön
aus, ist aber die Wahrheit über das, was die Quelle liefert. Das abgeschnittene
Wort wegzukürzen würde auf Verdacht Information vernichten, die sich nicht
zurückholen lässt.

### Die Falle an dieser Stelle

`_feature_to_event` nutzt den Titel als **GUID-Fallback**, wenn die WFS-Ebene
keine `OGD_ID` liefert — der Kommentar dort warnt ausdrücklich davor, die
Identität an etwas Volatiles zu hängen. Wäre die Kosmetik in die GUID
geflossen, hätte **jede gekappte Baustelle beim Deploy schlagartig neu
ausgesehen** und ihr `first_seen` wäre zurückgesetzt worden — mit dem Effekt,
dass sie den first_seen-sortierten Feed dominiert.

Die GUID leitet sich deshalb weiterhin vom **Rohtitel** ab, der reparierte
Titel geht ausschließlich in die Anzeige. Ein Test pinnt das und schlägt gegen
die naive Umsetzung fehl.

Von 22 Live-Titeln ändern sich genau die drei gekappten; der längste
unbeschädigte (94 Zeichen) bleibt unberührt.

Im Englischen kommt erschwerend hinzu, dass „auf Seite" im Sinne von
*Brückenseite* als „on page" übersetzt wird. Ein Glossareintrag wäre hier
angebracht — allerdings ist „Seite" kontextabhängig, deshalb nur mit Overlay
für die Quelle „Stadt Wien".

---

## 7. Befund 5 — Drei verschiedene Sperren, ein identischer Titel

**Wirkung auf den deutschen Feed: verdrängt Meldungen** · **Status: behoben**
([PR #1795](https://github.com/Origamihase/wien-oepnv/pull/1795))

Im ÖBB-Cache stehen drei Items mit exakt demselben Titel
`Wien Hauptbahnhof ↔ Gramatneusiedl`. Es sind drei verschiedene Bauzeiträume:

| GUID-Ende | Zeitraum |
| --- | --- |
| `894473` | 03.10.–05.10.2026 |
| `906928` | 31.10.–30.11.2026 |
| `910806` | 05.12.–07.12.2026 |

### Die Ursache war größer als der Befund

Ursprünglich als reines Titel-Problem notiert (`_apply_route_title` verwirft
Kategorie und Zeitraum). Beim Beheben zeigte sich: Der Zeitraum ist nicht nur
im Titel abwesend, sondern **nirgends im Item**. ÖBB stellt ihn jeder
Beschreibung voran —

```
03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten können …
```

— und `build_feed` hat dieses Präfix als Metadatum erkannt, aber ausschließlich
**verworfen** (`_DATE_RANGE_PREFIX_RE` / `_DATE_SINGLE_PREFIX_RE`). Also blieb
`starts_at` das **Veröffentlichungsdatum** und `ends_at` leer. Das traf nicht
drei Items, sondern **alle elf**:

| Titel | Zeitzeile vorher | tatsächlicher Zeitraum |
| --- | --- | --- |
| Wien Hauptbahnhof ↔ Felixdorf | `Seit 19.12.2025` | 10.02.–10.11.2026 |
| Wien Hauptbahnhof ↔ Gramatneusiedl | `Seit 24.08.2026` | 03.10.–05.10.2026 |
| Wien Hauptbahnhof ↔ Gramatneusiedl | `Seit 10.09.2026` | 05.12.–07.12.2026 |
| … | … | … |

Die Klammer behauptete „seit August laufend" für eine Sperre, die erst im
Dezember beginnt. Auf einem Info-Display, das genau diese Klammer zeigt, ist
das nicht bloß uninformativ, sondern **falsch**.

Dritte Folge: `_drop_old_items` Regel 1 („`ends_at` in der Vergangenheit →
sofort raus") konnte nie greifen, weil `ends_at` immer `None` war. Erledigte
Bauarbeiten verschwanden erst über die FIFO-Alterung.

### Korrektur

`src/providers/oebb.py` liest den Zeitraum über `_parse_period` in
`starts_at`/`ends_at` (Beginn 00:00, Ende 23:59:59 Europe/Vienna, damit ein
Item am letzten Tag nicht um Mitternacht verschwindet).

**Neue Renderlogik brauchte es keine** — `format_local_times` konnte das längst:

```
vorher (3x)                    nachher
  Seit 24.08.2026                03.10.2026 – 05.10.2026
  Seit 24.08.2026                31.10.2026 – 30.11.2026
  Seit 10.09.2026                05.12.2026 – 07.12.2026
```

Ein einzelnes Datum wird zu `Am 01.11.2026`, ein künftiger Beginn ohne Ende zu
`Ab …`. Fehlt das Präfix, bleibt das bisherige Verhalten; unmögliche
(`31.02.`) und verdrehte Zeiträume werden abgewiesen, statt den Abruf
abzubrechen. `pubDate` behält seine RSS-Bedeutung.

Geprüft: Sortierung (`_recency_sort_key`) und Altersfilter richten sich nach
`first_seen`, nicht nach `starts_at` — ein künftiges Startdatum wirft also
nichts aus dem Feed. Gegenprobe am echten Pfad: ohne den Fix **eine**
unterscheidbare Zeitzeile für drei Sperren, mit ihm **drei**.

---

## 8. Befund 6 — Warnungen ohne Aussagekraft

**Schweregrad: niedrig (Beobachtbarkeit)** · **Status: offen**

Zwei Muster verrauschen die Logs so stark, dass echte Warnungen darin untergehen:

**Stationsalias-Konflikte.** `src/utils/stations.py:790` protokolliert pro
Prozess 111 `WARNING`-Zeilen; im auditierten Lauf viermal, also **444 Zeilen**
für **83** eindeutige Konflikte — überwiegend „Wien Bhf. X (WL)" gegen
„Wien X". Der jeweils spätere Alias wird verworfen.

Bemerkenswert ist der Widerspruch: `python -m src.cli stations validate` meldet
im selben Lauf

```
2244 stations analysed, 1 geographic duplicates, 0 alias issues, …,
0 cross-name alias collisions
```

Der Validator sieht also nichts, während der Loader 83-mal warnt. Entweder ist
die Kollision harmlos — dann gehört sie auf `DEBUG` und der Validator hat recht
— oder sie ist es nicht, dann sollte der Validator sie melden. Beides
gleichzeitig ist irreführend.

**Leerer Stammstrecke-Provider.** Siehe Abschnitt 2: kein Vorfall wird als
Warnung dargestellt. Sinnvoller wäre `INFO` mit einer eigenen Kennzeichnung
(etwa `ok-empty`), damit „läuft normal" nicht wie „defekt" aussieht.

---

## 9. Befund 7 — Übersetzung, die nie konvergiert

**Schweregrad: niedrig (Kosten)** · **Status: offen**

Im Build-Log, jedes Mal:

```
Cached EN translation for …TRACKINFO&910806/title equals source; retrying.
```

`src/build_feed.py:2206` behandelt „zwischengespeicherte Übersetzung ist
identisch mit der Quelle" als Hinweis auf einen früheren Pipeline-Ausfall und
übersetzt neu. Für Titel, die **nur aus Stationsnamen** bestehen — etwa
`Wien Hauptbahnhof ↔ Gramatneusiedl` —, ist die korrekte englische Fassung aber
identisch mit der deutschen. Die Bedingung ist damit dauerhaft erfüllt: Das
Modell läuft für diese Items bei **jedem** Build erneut, ohne je ein anderes
Ergebnis zu erzeugen.

Bei einem Build alle 30 Minuten summiert sich das. Ein Marker „geprüft,
identisch ist korrekt" (etwa ein Flag neben `epoch`) würde die Schleife
schließen.

Der Epoch-Mechanismus selbst arbeitet korrekt: `_TRANSLATION_CACHE_EPOCH = 5`,
veraltete Einträge werden bei Verwendung verworfen. Von den 2531 gespeicherten
Übersetzungen in `first_seen.json` stehen nur 19 auf der aktuellen Epoch; 2152
liegen auf Epoch 1–4, weitere 360 tragen gar keine. Das ist folgenlos — es sind
Items, die nicht mehr im Feed erscheinen und deren Eintrag bei Wiederauftauchen
ohnehin verworfen würde. Die Datei liegt mit 920 KB weit unter dem Cap
von 50 MB, Retention 600 Tage.

---

## 9a. Befund 8 — Dieselbe Störung zweimal im Feed (Nachtrag)

**Schweregrad: mittel** (sichtbar im Feed) · **Status: behoben (Nachtrag)**

Nach dem Fix zu Befund 2 standen im Refresh vom 2026-09-12, 10:30 UTC zwei
Meldungen zur selben Sperre im Feed:

```
38A: Demonstration
38A: Demonstration Haltestelle Kahlenberg wird nicht eingehalten
```

Beide beschreiben, dass die Haltestelle Kahlenberg nicht bedient wird.

### Ursache

Eine Lücke in `TITLE_TOPIC_TOKENS` (`src/providers/wl_text.py`). Steht das
Ursachen-Wort nicht in dieser Menge, fällt `_topic_key_from_title` auf den
ganzen Titel-Kern zurück — dann ist jede Formulierungsvariante ein eigenes
Topic, ein eigener Bucket, am Ende ein eigenes Item. `demonstration` und
`veranstaltung` fehlten, obwohl sie in dieselbe Klasse gehören wie die bereits
gelisteten `polizeieinsatz` und `rettungseinsatz`.

Vor dem Fix zu Befund 2 fiel das nicht auf: `_dedupe_items` warf solche Paare
über den groben `_identity` (Linie + Tag) blind zusammen. Das war **keine
Zusammenführung, sondern eine Maskierung** — sie traf genauso Meldungen, die
wirklich verschieden waren. Der Fix hat die Maskierung entfernt und damit
sichtbar gemacht, was darunter lag.

### Korrektur

`demonstration` und `veranstaltung` ergänzt — nur diese beiden, beide durch
Live-Daten belegt. Zusammengeführt wird damit an der richtigen Stelle: im
Bucketing von `fetch_events`, wo der bessere Titel **und** die bessere
Beschreibung gewinnen (`_title_quality_key` / `_description_info_score`) und
Haltestellen wie Extras vereinigt werden.

Das Ergebnis schlägt beide Eingaben:

| | Titel | Text |
| --- | --- | --- |
| Meldung A | `38A: Demonstration` | ausführlich, gut lesbar |
| Meldung B | `38A: Demonstration Haltestelle Kahlenberg …` | knapp |
| **zusammengeführt** | **wie B** | **wie A** |

Wirkung auf die vier Gruppen aus Befund 2:

| Gruppe | vorher | nachher | richtig? |
| --- | --- | --- | --- |
| `38A: Demonstration` ×2 | 2 Items | **1** | ja — dieselbe Sperre |
| `44: Veranstaltung` ×3 | 3 Items | **1** | ja — ein Ereignis, drei Facetten |
| `49A/50B: Mondweg`/`Hüttergasse` | 2 Items | **2** | ja — zwei Straßen |
| `3A: Netzänderung`/`Busse halten` | 2 Items | **2** | kein gemeinsames Ursachenwort |

### Was daran zu lernen ist

Befund 2 und Befund 8 sind zwei Hälften derselben Sache. Der grobe
`_identity`-Schlüssel hat **beide** Fehler gleichzeitig verdeckt: Er warf
Verschiedenes weg und ließ Gleiches zusammenfallen — und weil das Ergebnis
zufällig oft passabel aussah, fiel keiner der beiden auf. Erst das Entfernen
der Maskierung hat die zweite Hälfte sichtbar gemacht.

Die Tests halten jetzt beide Richtungen fest: Verschiedenes muss überleben,
Gleiches muss zusammengeführt werden — und zwar im Bucketing, wo der Merge
qualitätsbewusst ist, nicht weiter unten durch blindes Verwerfen.

---

## 9b. Befund 9 — Ein Feuerwehreinsatz belegt zwei Feed-Plätze (Nachtrag)

**Wirkung auf den deutschen Feed: verdrängt Meldungen** · **Status: behoben**

Beim Neupriorisieren der Befunde nach der Feed-Rangfolge (s. `AGENTS.md`) fiel
im **live ausgelieferten** `docs/feed.xml` auf:

```
 3. 64A: Fahrtbehinderung wegen Feuerwehreinsatz
 4. 64A: Feuerwehreinsatz Betrieb ab Gregorygasse
```

Zwei der zehn Plätze für **einen** Einsatz. Auf einem Display mit fester
Item-Zahl heißt das: Eine andere Störung erscheint gar nicht.

### Ursache

Dieselbe wie bei Befund 8 — eine Lücke in `TITLE_TOPIC_TOKENS`. `feuerwehr‑
einsatz` fehlte, obwohl seine Geschwister `polizeieinsatz` und
`rettungseinsatz` längst dort standen. Der Fix zu Befund 8 hatte nur die zwei
damals belegten Wörter ergänzt (`demonstration`, `veranstaltung`) und die
Reihe nicht zu Ende gedacht.

### Korrektur

`feuerwehreinsatz` ergänzt. Wirkung auf die live vorhandenen Meldungen:

```
vorher (5 Items)                              nachher (4 Items)
  60A: Feuerwehreinsatz ab Carlbergergasse      60A: … Carlbergergasse
  66A: Feuerwehreinsatz ab Atzgersdorf          66A: … Atzgersdorf
  64A: Fahrtbehinderung wegen Feuerwehreinsatz  64A: … Betrieb ab Gregorygasse
  64A: Feuerwehreinsatz ab Gregorygasse         66A: Busse halten Salvatorianerplatz
  66A: Busse halten Salvatorianerplatz
```

Nur die beiden 64A-Meldungen verschmelzen — der informative Titel gewinnt.
Gleiche Einsätze auf **anderen** Linien bleiben getrennt, weil das Linien-Set
Teil des Bucket-Keys ist; ein Test hält das fest.

### Was offen bleibt

Zwei Gruppen im aktuellen Cache bleiben getrennt, und das ist bewusst so:

| Gruppe | warum getrennt |
| --- | --- |
| `49A/50B: Mondweg` / `Hüttergasse` | zwei verschiedene Straßen — richtig so |
| `66A: Busse halten Salvatorianerplatz` / `66A: Feuerwehreinsatz …` | könnte derselbe Einsatz sein, aber nur eine der beiden Meldungen nennt eine Ursache. Ohne gemeinsames Wort gibt es nichts, woran sich das belegen ließe — Raten wäre hier teurer als Nichtstun. |
| `3A: Netzänderung …` / `3A: Busse halten …` | dasselbe Muster |

Das ist die Grenze dieses Mechanismus: Er führt zusammen, was ein gemeinsames
Ursachen-Wort trägt. Meldungen, die dieselbe Störung aus reiner
Betriebsperspektive beschreiben („Busse halten X"), erreicht er nicht. Ob das
den Aufwand einer stärkeren Heuristik wert ist, entscheidet sich daran, wie oft
es vorkommt — derzeit zweimal von 56 Items.

---

## 10. Befundübersicht

Priorisiert nach der Rangfolge in `AGENTS.md` → „Priorität der Ausgaben":
**Was den deutschen Feed betrifft, kommt zuerst.** Er läuft über EasySignage
auf Full-HD-Displays mit hart begrenzter Item-Zahl — ein doppelter oder
verstümmelter Eintrag verdrängt dort eine andere Störung vollständig.

| # | Befund | Wirkung auf `docs/feed.xml` (DE) | Prio | Status |
| --- | --- | --- | --- | --- |
| 2 | Verschiedene Störungen als Duplikat verworfen (4/83) | verdrängt Meldungen | — | **behoben** |
| 8 | Dieselbe Störung zweimal im Feed (38A Demonstration) | verdrängt Meldungen | — | **behoben** |
| 9 | Ein Feuerwehreinsatz belegt zwei Feed-Plätze (64A) | verdrängt Meldungen | — | **behoben** |
| 1 | ÖBB-Titel nennt Station doppelt | verstümmelter Titel | — | **behoben** |
| 5 | Drei Sperren, ein identischer Titel | verdrängt Meldungen | — | **behoben** |
| 4 | Baustellen-Titel bricht mitten im Zitat ab (3 von 22) | verstümmelter Titel | — | **behoben** |
| 3 | Übersetzung erfand Liniennummern (13 von 71 Tokens ungeschützt) | keine — nur `feed.en.xml` | — | **behoben** |
| **7** | **Übersetzung läuft für Stationstitel endlos neu** | **keine — nur Laufzeitkosten** | **1** | offen |
| 6 | 444 Warnzeilen/Lauf; Validator meldet 0 | keine — nur Logs | 2 | offen |

---

## 11. Empfohlene Reihenfolge

**Neu geordnet am 2026-09-12** nach der in `AGENTS.md` festgehaltenen
Rangfolge der Ausgaben. Maßgeblich ist nicht mehr, welcher Befund technisch
schwerer wiegt, sondern **ob er den deutschen Feed betrifft**.

1. ~~**Befund 5**~~ — erledigt. Beim Beheben zeigte sich, dass die Ursache
   größer war als der Befund: Nicht nur die drei Titel waren gleich, **allen
   elf** ÖBB-Items fehlte der Zeitraum, und die Zeitzeile behauptete
   stattdessen ein „Seit \<Veröffentlichungsdatum\>", das für künftige Sperren
   schlicht falsch war (s. Abschnitt 7).

2. ~~**Befund 4**~~ — erledigt. Es waren drei von 22 Titeln, nicht einer. Die
   Kappung bleibt upstream; wir machen sie jetzt kenntlich, statt sie
   wortwörtlich als vermeintlich eigenen Fehler auszuliefern (s. Abschnitt 6).

3. ~~**Befund 3**~~ — erledigt. Betraf ausschließlich `docs/feed.en.xml` und
   stand deshalb hinten an; nach Befund 4 war er der nächste. Die Lücke war
   größer als notiert: 13 von 71 Live-Tokens ungeschützt, darunter zwei
   Formen, die im Befund fehlten (s. Abschnitt 5).

**Damit ist kein offener Befund mehr übrig, der einen der beiden Feeds
berührt.** Die verbleibenden zwei kosten Laufzeit und erzeugen Log-Rauschen:

1. **Befund 7** — die Übersetzung läuft für Stationstitel bei jedem Lauf neu,
   weil `cached == text` als Fehlschlag gewertet wird und einen erneuten
   Versuch auslöst. Keine Feed-Wirkung, nur Laufzeit.
2. **Befund 6** — 444 Warnzeilen pro Lauf über doppelte Stations-Aliase,
   während der Validator „0 alias issues" meldet. Bleibt sinnvoll, weil ruhige
   Logs die nächste echte Warnung sichtbar machen, aber es steht keine Anzeige
   daran.

Unverändert offen und außerhalb jedes PRs: In den Branch-Protection-Regeln für
`main` ist **„Allow force pushes" weiterhin aktiv** — die Ursache des
History-Verlusts vom 11.09., dokumentiert in
`audit-2026-09-12-force-push-history-loss.md`.

---

## 12. Methodische Anmerkung

Befund 1 ist zugleich eine Korrektur meiner eigenen Arbeit: PR #1789 wurde mit
der Behauptung gemergt, der Cache heile sich beim nächsten Lauf. Verifiziert
hatte ich nur, dass die *geänderte Funktion* den *gecachten* Titel repariert —
nicht, dass die *vollständige Pipeline* den *Rohtitel* repariert. Der
Unterschied ist genau der Fehler.

Die Lehre für kommende Titel-Änderungen: Ein Test gegen den bereits
verarbeiteten Titel beweist nichts über den Live-Pfad. Maßgeblich ist
`_build_item_from_xml` von Anfang bis Ende, und die Gegenprobe ist der Cache
nach dem nächsten Refresh — ändert er sich nicht, hat der Fix nicht gegriffen.
