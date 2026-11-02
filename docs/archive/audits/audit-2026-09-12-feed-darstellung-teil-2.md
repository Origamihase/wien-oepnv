[Audit: Feed-Quellen, Verarbeitung und Darstellung](audit-2026-09-12-feed-darstellung.md), Teil 2.

### Korrektur

Ein gespeicherter String allein kann die beiden Fälle nicht trennen — „ein
kaputter Build hat hier die Quelle hinterlegt" sieht aus wie „das Modell lief
und seine Ausgabe entspricht der Quelle". Ein Marker kann es, weil er **nur
dort** geschrieben wird, wo sich die Fälle unterscheiden: nachdem
`_translate_text_attempt` ein Ergebnis ungleich `None` geliefert hat. Ein
fehlgeschlagener Versuch liefert `None` und speichert nichts; ein Build, der
nicht übersetzen konnte, kann ein Feld also nie als „identisch ist korrekt"
markieren.

**Der Drift-Schutz wird eingegrenzt, nicht entfernt.** Bestandseinträge ohne
Marker — genau der kaputte Altbestand, für den der Guard existiert — werden
weiterhin einmal neu übersetzt und dabei markiert. Ein Test pinnt beide
Richtungen.

Der Marker liegt neben `en` statt darin, damit die `en`-Teilstruktur eine
homogene Feld-zu-String-Abbildung bleibt. Er ist epoch-gebunden und wird bei
der Eviction zusammen mit `en` verworfen: Eine spätere Epoch mit besserem
Glossar kann denselben Text sehr wohl übersetzen. Er wird zurückgenommen,
sobald ein Lauf doch eine echte Übersetzung liefert, und der Self-Heal für
Rest-Platzhalter (s. Audit 2026-09-05 §5) schlägt ihn weiterhin — ein rohes
Sentinel darf nie aus dem Cache kommen.

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
| 7 | Übersetzung lief für Stationstitel endlos neu | keine — nur Laufzeitkosten | — | **behoben** |
| 6a | 444 Warnzeilen/Lauf über Alias-Kollisionen | keine — nur Logs | — | **behoben** |
| 6b | Leerer Stammstrecke-Provider galt als Warnung | keine — nur Logs | — | **behoben** |

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
berührt.** Was blieb, kostete Laufzeit oder erzeugte Log-Rauschen:

4. ~~**Befund 7**~~ — erledigt. `cached == text` galt als Beleg für einen
   kaputten Build; für Titel aus reinen Stationsnamen ist die Gleichheit aber
   die richtige Antwort, und die Schleife schloss sich nie (s. Abschnitt 9).

5. ~~**Befund 6, Alias-Teil**~~ — erledigt. Der vermutete Widerspruch war
   keiner: Der Validator prüfte nie, was der Loader meldete. Von den 111
   Zeilen waren 69 harmlos, 42 eine echte Kollision zwischen verschiedenen
   Orten — heute folgenlos, aber ungesichert. Loader beruhigt, Prüfung
   nachgerüstet (s. Abschnitt 8).

6. ~~**Befund 6, Stammstrecke-Teil**~~ — erledigt. Ein Provider ohne Vorfälle
   wurde als Warnung dargestellt, sodass „läuft normal" wie „defekt" aussah
   (s. Abschnitt 8).

**Damit ist kein Befund aus diesem Audit mehr offen.**

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
