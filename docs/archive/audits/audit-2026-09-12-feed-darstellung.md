# Audit: Feed-Quellen, Verarbeitung und Darstellung

**Datum:** 2026-09-12
**Schwerpunkt:** Feed — Quellen, Pipeline, Darstellung in `docs/feed.xml` / `docs/feed.en.xml`
**Datenbasis:** Manual-Full-Refresh Run 93948230655 (Checkout `275ed8d586`, 08:02–08:05 UTC),
Repo-Stand `afc72b5f6c`, Live-Caches in `cache/`
**Status:** Befunde 1, 2 und 8 behoben (PR #1790, #1791 und Nachtrag), Befunde 3–7 dokumentiert, nicht behoben

---

## 1. Executive Summary

**Die Quellen funktionieren.** Alle fünf Datenquellen haben im auditierten Lauf
geantwortet, keine einzige Fehlermeldung, keine Netzwerk- oder Auth-Störung.
Die Pipeline ist stabil, die CI ist grün, der Feed ist wohlgeformt.

**Die Darstellung hat Mängel.** Acht Befunde — Befund 8 kam beim Nachprüfen
der Korrektur zu Befund 2 dazu. Drei mit sichtbarer Auswirkung auf das, was
Abonnenten lesen, sind behoben:

- **Befund 1** — ein ÖBB-Titel, der eine Station doppelt nennt. Derselbe Titel,
  den [PR #1789](https://github.com/Origamihase/wien-oepnv/pull/1789)
  beheben sollte und nachweislich nicht behoben hat; erledigt in
  [PR #1790](https://github.com/Origamihase/wien-oepnv/pull/1790).
- **Befund 2** — vier von 83 Meldungen (≈ 5 %) wurden als „Duplikat" verworfen,
  weil der Dedupe-Schlüssel nur Linie und Tag kannte
  ([PR #1791](https://github.com/Origamihase/wien-oepnv/pull/1791)).
- **Befund 8** — die Kehrseite davon: Ohne die grobe Maskierung stand dieselbe
  Störung zweimal im Feed, weil zwei Ursachen-Wörter in
  `TITLE_TOPIC_TOKENS` fehlten
  ([PR #1792](https://github.com/Origamihase/wien-oepnv/pull/1792)).

Befunde 2 und 8 sind zwei Hälften derselben Sache: Ein Schlüssel, der zu grob
war, hat gleichzeitig Verschiedenes weggeworfen **und** Gleiches zusammenfallen
lassen. Weil das Ergebnis oft passabel aussah, fiel keine der beiden Hälften
auf. Von den vier ursprünglich verworfenen Meldungen war nur das Paar
`49A/50B` wirklich verschieden — die Linie-44-Trias ist ein Ereignis aus drei
Blickwinkeln (s. Korrektur in Abschnitt 4).

Offen bleiben die Befunde 3–7. Der gewichtigste davon: Liniennummern, die die
englische Übersetzung verstümmelt (`44A/844` → `44A844`). Sie sind belegt und
mit Reproduktion dokumentiert, aber bewusst nicht mitbehoben — jeder gehört in
eine eigene, einzeln prüfbare Änderung.

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

**Schweregrad: mittel** (sichtbar im EN-Feed) · **Status: offen**

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

### Vorschlag

`N\d{1,3}[A-Z]?` und dreistellige Nummern ergänzen, die Straßenbahn-Buchstaben
`O`/`D` nur in Linienkontext (mit Präfix oder vor `:`), damit nicht jedes
alleinstehende „D" maskiert wird.

---

## 6. Befund 4 — Baustellen-Titel brechen mitten im Zitat ab

**Schweregrad: niedrig** · **Status: offen, Ursache upstream**

```
Kennedybrücke zwischen Schönbrunner Schloßstraße und Hadikgasse, auf Seite "Otto Wagner Hofpavillon
```

99 Zeichen, öffnendes Anführungszeichen ohne schließendes. Der Titel kommt
unverändert aus dem Feld `BEZEICHNUNG` der Stadt-Wien-WFS-Daten
(`scripts/update_baustellen_cache.py:770`), das dort offenbar bei 100 Zeichen
abgeschnitten wird. Im Projekt gibt es keine Titel-Kappung an dieser Stelle.

Der Feed übernimmt den Abbruch wortwörtlich, sodass er wie ein Darstellungs-
fehler aussieht. Ein Ellipsen-Marker und das Entfernen eines unpaarigen
Anführungszeichens würden den Titel als „gekürzt" kenntlich machen, statt ihn
kaputt aussehen zu lassen.

Im Englischen kommt erschwerend hinzu, dass „auf Seite" im Sinne von
*Brückenseite* als „on page" übersetzt wird. Ein Glossareintrag wäre hier
angebracht — allerdings ist „Seite" kontextabhängig, deshalb nur mit Overlay
für die Quelle „Stadt Wien".

---

## 7. Befund 5 — Drei verschiedene Sperren, ein identischer Titel

**Schweregrad: niedrig** · **Status: offen**

Im ÖBB-Cache stehen drei Items mit exakt demselben Titel
`Wien Hauptbahnhof ↔ Gramatneusiedl`. Es sind drei verschiedene Bauzeiträume:

| GUID-Ende | Zeitraum |
| --- | --- |
| `894473` | 03.10.–05.10.2026 |
| `906928` | 31.10.–30.11.2026 |
| `910806` | 05.12.–07.12.2026 |

`_apply_route_title` rekonstruiert den Titel aus den Endpunkten und verwirft
dabei Kategorie und Zeitraum. Im Feed-Reader stehen dann drei ununterscheidbare
Einträge; der Unterschied steht nur in der Beschreibung. Aktuell fällt es nicht
auf, weil `MaxItems=10` derzeit nur einen davon durchlässt.

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

## 10. Befundübersicht

| # | Befund | Schwere | Sichtbar im Feed | Status |
| --- | --- | --- | --- | --- |
| 1 | ÖBB-Titel nennt Station doppelt | hoch | ja | **behoben** |
| 2 | Verschiedene Störungen als Duplikat verworfen (4/83) | hoch | ja (fehlend) | **behoben** |
| 3 | Übersetzung verstümmelt Liniennummern | mittel | ja (EN) | offen |
| 4 | Baustellen-Titel bricht mitten im Zitat ab | niedrig | ja | offen (upstream) |
| 5 | Drei Sperren, ein Titel | niedrig | latent | offen |
| 6 | 444 Warnzeilen/Lauf; Validator meldet 0 | niedrig | nein | offen |
| 7 | Übersetzung läuft für Stationstitel endlos neu | niedrig | nein | offen |
| 8 | Dieselbe Störung zweimal im Feed (38A Demonstration) | mittel | ja | **behoben** |

---

## 11. Empfohlene Reihenfolge

1. ~~**Befund 2**~~ — erledigt (Nachtrag 2026-09-12). Es war der einzige, bei
   dem Abonnenten Meldungen **gar nicht** zu sehen bekamen.
2. **Befund 3** ist damit der nächste — klar abgegrenzt, eine Regex plus Tests.
3. **Befund 6** als Aufräumarbeit: erst wenn die Logs ruhig sind, fällt die
   nächste echte Warnung auf.
4. Befunde 4, 5, 7 nach Bedarf.

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
