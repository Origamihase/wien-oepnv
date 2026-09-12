# Audit: Feed-Quellen, Verarbeitung und Darstellung

**Datum:** 2026-09-12
**Schwerpunkt:** Feed — Quellen, Pipeline, Darstellung in `docs/feed.xml` / `docs/feed.en.xml`
**Datenbasis:** Manual-Full-Refresh Run 93948230655 (Checkout `275ed8d586`, 08:02–08:05 UTC),
Repo-Stand `afc72b5f6c`, Live-Caches in `cache/`
**Status:** Befund 1 in diesem PR behoben, Befunde 2–7 dokumentiert, nicht behoben

---

## 1. Executive Summary

**Die Quellen funktionieren.** Alle fünf Datenquellen haben im auditierten Lauf
geantwortet, keine einzige Fehlermeldung, keine Netzwerk- oder Auth-Störung.
Die Pipeline ist stabil, die CI ist grün, der Feed ist wohlgeformt.

**Die Darstellung hat Mängel.** Sieben Befunde, davon zwei mit sichtbarer
Auswirkung auf das, was Abonnenten lesen:

- ein ÖBB-Titel, der eine Station doppelt nennt — **derselbe Titel, den
  [PR #1789](https://github.com/Origamihase/wien-oepnv/pull/1789) heute früh
  beheben sollte** und nachweislich nicht behoben hat;
- vier von 83 Meldungen (≈ 5 %), die als „Duplikat" verworfen werden, obwohl es
  verschiedene Störungen sind;
- Liniennummern, die die englische Übersetzung verstümmelt (`44A/844` → `44A844`).

Die Korrektur von Befund 1 ist Teil dieses PRs. Die übrigen sind belegt und mit
Reproduktion dokumentiert, aber bewusst nicht mitbehoben — sie berühren
Dedupe-Identität und Übersetzungs-Masking und gehören in eigene, einzeln
prüfbare Änderungen.

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

**Schweregrad: hoch** (sichtbar im Feed) · **Status: in diesem PR behoben**

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

**Schweregrad: hoch** (Informationsverlust) · **Status: offen**

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

Das sind keine Duplikate. „Mondweg" und „Hüttergasse" sind zwei verschiedene
Orte; „Netzänderung ab Riemergasse" und „Busse halten Kärntner Ring 5" zwei
verschiedene Sachverhalte. Jeweils einer wird veröffentlicht, die anderen
verschwinden — **4 von 83 Items, rund 5 %**.

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

### Vorschlag

Den `topic_key` immer einmischen, nicht nur in den Randfällen — oder in
`_dedupe_items` den Inhalts-Hash als Tiebreaker nutzen, während `_identity` für
`first_seen` grob bleibt. Beides trennt die zwei Aufgaben sauber, die der
Schlüssel derzeit gleichzeitig erfüllen soll (stabile Alterung **und**
Duplikaterkennung). Wichtig: `first_seen` darf dabei nicht zurückgesetzt
werden.

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

## 10. Befundübersicht

| # | Befund | Schwere | Sichtbar im Feed | Status |
| --- | --- | --- | --- | --- |
| 1 | ÖBB-Titel nennt Station doppelt | hoch | ja | **behoben** |
| 2 | Verschiedene Störungen als Duplikat verworfen (4/83) | hoch | ja (fehlend) | offen |
| 3 | Übersetzung verstümmelt Liniennummern | mittel | ja (EN) | offen |
| 4 | Baustellen-Titel bricht mitten im Zitat ab | niedrig | ja | offen (upstream) |
| 5 | Drei Sperren, ein Titel | niedrig | latent | offen |
| 6 | 444 Warnzeilen/Lauf; Validator meldet 0 | niedrig | nein | offen |
| 7 | Übersetzung läuft für Stationstitel endlos neu | niedrig | nein | offen |

---

## 11. Empfohlene Reihenfolge

1. **Befund 2** zuerst — es ist der einzige, bei dem Abonnenten Meldungen
   **gar nicht** zu sehen bekommen. Fehlende Information wiegt schwerer als
   falsch formatierte.
2. **Befund 3** danach — klar abgegrenzt, eine Regex plus Tests.
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
