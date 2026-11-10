[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 7.

### Die Platzhalter

Zwei Sorten, bewusst unterscheidbar:

* `XENT<nonce>X<n>X` — **Entitäten** (Marken, Stationsnamen, Kalenderdaten
  wie `27.09.2026`, Linienkennungen, Straßennamen, geschützte Symbole).
  Seit dem EN-Audit vom 2026-10-03 auch freistehende Straßennamen mit einem
  Ortsadjektiv auf „-er“ („Südtiroler Platz“, vorher „South Tyrolean
  square“; Determinative wie „Dieser Platz“ ausgenommen), Hauptbahnhöfe in
  ÖBB-Kurzform („Wien Hbf“, vorher „Vienna Hbf“) und WL-Haltestellen in der
  Komma-Form der Echtzeittexte („Burggasse, Stadthalle“, vorher „city hall“).
  Seit 15.10. gehört „Wien“ direkt vor einem Namen oder hinter ÖBBs „b.“
  (bei) zum Namen und wird mit ihm geschützt (`_WIEN_NAME_RE`, letzter
  Durchgang): „Wien Schedifkaplatz“ (vorher „Vienna Schedifkaplatz“ neben
  „Wien Matzleinsdorfer Platz“), „Wien St.Marx“, „Purkersdorf b.Wien
  Zentrum“, „Gerasdorf b. Wien“. Ein Name bleibt im EN-Feed ein Name, wie
  auf dem Schild; „Wien“ allein („Fahrtrichtung Wien“, „Großraum Wien“) ist
  die Stadt und wird weiter „Vienna“, vor einer Marke („Wien
  Stammstrecke“), Linie oder einem Symbol bleibt es frei. Fehlt vor einem
  Satzanfang der Punkt („Richtung Wien Ab 20 Uhr“), nähme die Regel das
  erste Wort als Namen; das kam in 9.965 echten Texten seit 01.07. nie vor
  (alle 48 Formen „Wien + Großwort“ sind Stationsnamen) und bleibt so,
  Betreiberentscheidung 2026-10-16 „So lassen“.
  Seit den Jules-Audits vom 26. und 27.10. auch Straßennamen im Genitiv
  („Streckensperre des Algenwegs“, vorher „closure of the algae path“;
  ebenso „des Margaretengürtels“, „des Aumannplatzes“). Der englische Text
  nennt sie in der Grundform, wie am Schild: „closure of Algenweg“
  (`_street_names_nominative`). Gattungswörter mit Straßensuffix im
  Genitiv („des Bahnsteigs“, „eines Flohmarktes“, seit der Zweitprüfung
  vom 27.10. auch „des Einstiegs“, „des Spielplatzes“, „des Bahndamms“)
  bleiben übersetzbar (geschlossene Liste `_STREET_COMMON_NOUNS`; geschützt
  stand sonst „closure of Einstieg“ im englischen Text).
  Seit den Jules-Audits vom 28. und 29.10. auch Straßennamen mit
  Präposition, wenn diese mitten im Satz großgeschrieben steht („im Bereich
  An den Eisteichen“, vorher „At the ice ponds“; „über Am
  Kaisermühlendamm“, `_PREPOSITION_NAME_RE`); am Satz-, Feld- oder
  Listenanfang („Am Samstag …“, „Zeitraum: Am 1. November“) bleibt die
  Präposition übersetzbar, ebenso vor einem Namen auf -straße/-gasse. Ein
  per Bindestrich vorangestelltes Großwort gehört zum Namen dahinter
  („Unter-Meidlinger Straße“, vorher „Under-Meidlinger Straße“;
  „Neu-Leopoldau“, `_absorb_hyphen_prefixes`, letzter Durchgang); vor einer
  Linie („U6-Station“) nicht.
  Seit dem KI-Wächter vom 02.11. (Feed-Stand vom 30.10.) auch die zweite
  Hälfte eines Haltestellenpaars mit Schrägstrich: WL schrieb „Betrieb ab
  Parlament/ Volkstheter“ (Linie 2), das Verzeichnis kannte „Parlament“,
  nicht den Tippfehler, und der englische Text las „service from Parlament/
  Folktheter“. Ein großgeschriebenes Wort, das per Schrägstrich (mit oder
  ohne Leerzeichen) an einem geschützten Namen hängt, gehört zu diesem
  Haltestellennamen und bleibt, wie die Quelle es schreibt, Tippfehler
  eingeschlossen („Laxenburger/Gudrunstraße“, „Währinger Str./ Volksoper“;
  `_shield_slash_partners`, nach dem Bindestrich-Durchgang). Das gilt nur für
  ein einzelnes Wort am äußeren Ende des Paars: Geht der Name dahinter
  weiter („Hauptbahnhof/Sankt Marx“) oder steht davor ein weiteres Namenswort
  („Wien Spittelau Bahnhst/Wien Mitte-Landstraße Bahnhof“), bleibt das Wort
  beim Modell. Eine erste, breitere Fassung schützte dort „Sankt“ und
  „Bahnhst“ allein, und das echte Modell schrieb „between Praterstern and
  Hauptbahnhof/Sankt is Marx closed“ und „Wien Mitte-Landstraße Train
  Station“. Neben einer Linie oder Marke („U2/U3“, „ÖBB/Westbahn“) gilt die
  Regel nicht. Sie ist die Stellung im Text, keine Wortliste; über alle
  11.222 Quelltexte seit Juli ändert sie die Modelleingabe von acht (vier
  Meldungen, je Titel und Text), jedes Mal an einer Haltestelle oder Straße;
  das echte Modell übersetzt sieben davon wie vorher, die achte ist die
  Linie 2 mit „Volkstheter“.
  Seit dem KI-Wächter vom 01.11. auch Haltestellennamen in gebeugter Form:
  WL schrieb „Weiterfahrt über die Linie 37 bis Hohen Warte“ (Linie 38,
  31.10.), das Verzeichnis kennt „Hohe Warte“, und der englische Text las
  „Continue via line 37 to High Wait“. Vor dem Maskieren
  bringt `_normalise_for_translation` einen solchen Namen in die Form des
  Verzeichnisses (`_station_base_forms`): Beginnt ein Name mit einem Wort auf
  -e, -er oder -es („Hohe Warte“, „Hoher Markt“, „Altes Landgut“), steht die
  schwache Form auf -en („Hohen Warte“, „Hohen Markt“, „Alten Landgut“) für
  ihn, außer das Verzeichnis kennt diese Form als eigenen Namen. Die Regel
  ist die Grammatik, keine Wortliste; Ortsadjektive („Döblinger
  Hauptstraße“) bekommen eine Form, die kein Text schreibt. Der englische
  Text nennt den Namen wie am Schild („to Hohe Warte“, mit dem echten Modell
  geprüft), der deutsche Feed behält WLs Wortlaut. In 14.447 Quelltexten
  seit Juli änderte die Regel nur die vier mit „Hohen Warte“.
  Seit dem KI-Wächter vom 03.11. (Feed-Stand vom 02.11. 14:30) kennt jeder
  Namensdurchgang alle lateinischen Buchstaben, nicht nur die deutschen: WL
  schrieb „Grund: Verkehrsstörung im Bereich Biróstraße 17“ (Linie 17A), das
  „ó“ beendete den Namen vor dem Suffix, und der englische Text las „in the
  area of Biró Street 17“. Die Namensmuster in `entities`, `masking` und
  `placeholders` stehen weiter in deutscher Schreibweise (`[A-ZÄÖÜ]`,
  `[a-zäöüß]`); `latin_names` (`src/utils/name_letters.py`) weitet sie beim
  Kompilieren auf Basic Latin und U+00C0 bis U+024F („Bertégasse“,
  „Esterházygasse“, „Dvořákgasse“, „Győr“). Dieselbe Regel gilt für WLs
  Adressmaske vor der Linienerkennung (`ADDRESS_NO_RE`), wo die Hausnummer
  einer solchen Straße sonst als Linie galt („Biróstraße 17“ → Linie 17).
  In 23.302 Texten aller 17.035 Stände des deutschen Feeds änderte die
  Regel die Modelleingabe von sechs (Biróstraße, Bertégasse,
  Fertöszentmiklós), jedes Mal nur den Namen; das echte Modell schreibt jetzt „Biróstraße 17“ und
  übersetzt die übrigen wie vorher.
  Seit dem KI-Wächter vom 06.11. auch WLs „Bahnhof“-Namen ausgeschrieben:
  Das Verzeichnis kürzt „Wien Bhf. Meidling (WL)“, „Wien Bhf. Hütteldorf
  (WL)“, „Wien Bhf. Meidling, Schedifkaplatz (WL)“ (zehn Namen), WLs
  Hinweise schreiben „Haltestellenverlegung der Linie 15A in Richtung
  Bahnhof Meidling S U, Schedifkaplatz“ (05.11.). Geschützt war nur
  „Meidling“, und der englische Text las „Station Meidling“. Zu jedem
  „Bhf. X“ kennt der Namensdurchgang jetzt „Bahnhof X“ und „Bhf X“
  (`_station_written_out_forms`), längste Form zuerst, sodass „Bahnhof
  Meidling, Schedifkaplatz“ vor „Meidling“ greift. Ein „Bahnhof“ vor einer
  Haltestelle, die das Verzeichnis ohne ihn führt („Bahnhof Heiligenstadt“),
  bleibt Fließtext und wird übersetzt; der deutsche Feed ändert sich nicht
  (`tests/test_station_written_out_names.py`).
  Seit dem KI-Wächter vom 08.11. auch WLs eigene zweiteilige
  Haltestellennamen (Ort und Haltestelle, `wl_stops` im Verzeichnis):
  „Laaer Berg, Kurpark Nordosteingang“, „Atzgersdorf, Friedhof“, „Liesing,
  Feuerwache“. Das Verzeichnis führt den Eintrag unter einem anderen Namen
  („Wien Kurpark Nordosteingang (WL)“), geschützt war nur der Ort, und der
  englische Feed las „Stop relocation of line 68A towards Laaer Berg,
  northeast entrance“. Eine Richtung darf vorn Wörter der Haltestelle
  weglassen („Laaer Berg, Nordosteingang“); diese Kurzform greift nur hinter
  „Laaer Berg, “ (`_wl_stop_names`, 79 bisher ungeschützte Namen von
  Wiener Einträgen). In 6.136 Texten des deutschen Feeds seit Juli ändert
  sich die Modelleingabe von 68, fast immer nur so, dass zwei schon
  geschützte Teile ein Platzhalter werden; der echte Build des Stands vom
  08.11. 14:01 mit dem echten Modell ändert nur den 68A-Namen. Epoche 32
  (`tests/test_wl_stop_names_masking.py`).
  Werden nach dem Modelllauf **wortgleich** zurückgesetzt. Ein Datum wird
  **vor** den Linienkennungen maskiert: sonst wäre nur sein Tag ein
  Platzhalter, der am Punkt klebt, und das Modell verliert den Punkt
  (`on 2709.2026`, 2026-09-24).
* `XGLO<nonce>X<n>X` — **Glossar-Treffer**. Werden durch die *englische*
  Entsprechung ersetzt; die Auswahl ist nach `(source, category)` geschichtet
  (`_GLOSSARY_BASE`, `_GLOSSARY_BY_SOURCE`, `_GLOSSARY_BY_CATEGORY`, aufgelöst
  von `_resolve_glossary`), damit betreiberspezifisches Vokabular nur bei
  Items dieses Betreibers greift. Die Tabellen (samt `_FIELD_LABEL_EN`)
  stehen seit 2026-10-15 als reine Daten in `src/feed/glossary.py`;
  `src/build_feed.py` und die Teile `placeholders`, `en_repairs` und
  `pretranslate` unter `src/feed_build/` importieren sie unter denselben
  Namen, die Funktionen, die sie anwenden, stehen seit 2026-11-03 in diesen
  Teilen (§1, „Eine Feed-Kette, ein Modul“).

Die Reihenfolge ist kein Zufall: **Glossar zuerst, Entitäten danach.** Nach
dem Glossar-Durchlauf stehen dort `XGLO…`-Platzhalter, die der Entity-Masker
als undurchsichtige Eigennamen sieht und in Ruhe lässt. Umgekehrt würde das
Glossar in bereits maskiertem Text nach deutschen Wörtern suchen, die nicht
mehr da sind.

Beide tragen einen **prozess-zufälligen Nonce** (`_PLACEHOLDER_NONCE`). Ohne
ihn könnte ein Quelltext, der selbst `XENT0X` enthält, die Rückersetzung
kapern oder zerstören. Das Format ist alphanumerisch und beginnt mit einem
Buchstaben, weil der SentencePiece-Tokenizer sonst dazu neigt, es zu zerlegen.
Jede Nonce, die des Laufs wie die des zweiten Versuchs, zieht
`_placeholder_nonce`: 16 Hex-Zeichen, neu gezogen, bis keine sechs Ziffern und
keine sechs Buchstaben aufeinander folgen (seit 2026-10-25). So trägt jedes
Bruchstück ab sechs Zeichen eine Ziffer und einen Buchstaben, und die
Nachkontrolle erkennt es als Nonce-Rest (siehe unten). Vorher kam etwa jede
1 800. Ziehung nur aus Ziffern; verlor das Modell im zweiten Versuch Präfix
und Index eines Platzhalters, blieb eine nackte Zahl stehen, die keine Regel
sah.

**Das Modell sieht die Platzhalter ohne Nonce (seit 2026-10-06).** Der erste
Durchlauf zeigt dem Modell `XENT3X` und `XGLO0X` (`_to_short_placeholders`),
die Antwort wird vor jeder Prüfung auf die Nonce des Laufs zurückgesetzt
(`_from_short_placeholders`). Mit Nonce ist ein Platzhalter rund zehn
SentencePiece-Stücke Zufalls-Hex, und WLs kurze Texte bestehen fast nur aus
Platzhaltern („XGLO…X0X der Linien XENT…X1X in Richtung XENT…X0X U bzw.
XENT…X2X XENT…X3X“). Gemessen mit dem echten Modell in GitHub Actions über
alle 814 verschiedenen Modell-Eingaben des Feeds seit 01.07.2026: unter einer
zufälligen Nonce verstümmelte oder verlor das Modell in 167 Durchläufen einen
Platzhalter (20,5 %), in der Kurzform in 8 (1,0 %). Über die 243 Eingaben ab
20.09. mit je vier Nonces scheiterten 227 von 972 Durchläufen, in der
Kurzform keiner. Jeder Text, der in beiden Durchläufen scheiterte, stand für
einen Lauf deutsch im EN-Feed, seit 04.10. neun Meldungen, zuletzt 66A/N66 am
06.10. um 17:23 (Log: Rest-Platzhalter unter beiden Nonces). Der Wortlaut
bleibt sonst gleichwertig; nur „in Richtung X … umgeleitet“ kam öfter als
„redirected to X“ zurück und wird danach zu „redirected towards X“
(`_redirected_towards`, ein Glossar-Eintrag für „in Richtung“ machte es in der
Probe schlechter). Der Schutz der Nonce bleibt: Masken, Zuordnung und
Rückersetzung tragen sie weiter, und ein Quelltext, der die Kurzform selbst
enthält (`_has_short_placeholder_shape`, ohne Rücksicht auf Groß- und
Kleinschreibung), geht wie bisher unter der Nonce ans Modell.

### Nachkontrollen

Nach dem Modelllauf wird nicht blind vertraut:

* `_normalise_placeholder_debris` repariert Platzhalter, die das Modell
  verdoppelt hat (`X4XX` → `X4X`), und seit 2026-10-01 auch einen
  wiederholten eigenen Index (`X0X0X` → `X0X`). Der hatte vom 30.09. bis
  01.10. „Wien Franz-Josefs-Bahnhof0X“ und „service obstruction0X“ in den
  EN-Feed gebracht, an keinem Wächter vorbei sichtbar.
* Seit 2026-10-02 auch ein `X`, das das Modell **vor** einen Platzhalter
  setzt (`_PLACEHOLDER_LEADING_X_RE`): Es wird zum Leerzeichen, das es
  ersetzt hat. Es hatte „WipplingerstrX39; service fromXAugasse“ auf Platz 2
  des EN-Feeds gebracht; Epoche 19 räumt den Cache.
* `_entities_dropped_by_translation` verwirft die Übersetzung, wenn das
  Modell eine wortgleiche Entität **verloren** hat — lieber deutsch als eine
  englische Meldung, in der eine Haltestelle fehlt.
* `_fix_glossary_articles` gleicht `a`/`an` an das Wort an, das das Glossar
  eingesetzt hat. Das Modell macht dabei nichts falsch: es sah den
  Platzhalter, nicht das Ergebnis.
* `_capitalise_sentence_start` hebt den ersten Buchstaben von Titelrumpf
  und Beschreibung an: Glossar-Substantive sind klein, weil sie auch
  mitten im Satz stehen; am Satzanfang liest sich das als Tippfehler.
* `_capitalise_glossary_after_stop` tut dasselbe für einen Glossar-Begriff,
  der mitten im Text einen Satz beginnt („track 2. Expected duration:
  10:10“, Audit vom 25.09., A.4). Nur Glossar-Werte, nie nach einer
  Abkürzung („Hauptstr. opp 197“, „ca.“, „approx.“). Wirkt beim Rendern,
  also auch auf gecachte Werte, ohne Epochensprung.
* Ein gecachter EN-Wert wird vor dem Ausliefern geprüft
  (`_cached_translation_defect`): ein Rest-Platzhalter oder ein Datum des
  deutschen Quelltexts, das im Englischen fehlt, macht den Treffer zum
  Miss — gezielt für dieses Feld, ohne Epochensprung. Seit 2026-10-02 zählt
  auch ein Rest, den die Rückersetzung hinterlassen hat
  (`_CACHED_DEBRIS_RE`): ein `X` oder ein Index, der an einem Wort klebt
  („Bahnhof0X“, „accidentX“, „Line 18X“), oder ein verstümmeltes Präfix
  („XGLAB…X0X“), sofern der deutsche Text das Wort nicht selbst enthält.
  Die Reparatur vom 01.10. griff nur für neue Übersetzungen; am 02.10.
  zeigte der EN-Feed noch „Wien Franz-Josefs-Bahnhof0X“ aus dem Cache, der
  44 solche Felder hielt.
* Übrig gebliebene Platzhalter (`_RESIDUAL_PLACEHOLDER_RE`) lassen das Feld
  scheitern — und damit das Item deutsch bleiben. Das schließt eine
  Platzhalter-Form ein, der das Modell das führende `X` genommen hat
  (`ENT<nonce>X4X` statt `XENT<nonce>X4X`) — beobachtet, als zwei
  Platzhalter ohne Leerzeichen an einem Bindestrich aneinanderstießen
  (`_LINE_ENTITY_RE` maskiert auch bloße Hausnummern als Linienkennung).
  Verankert auf der Form der Nonce selbst (8–32 Hex-Zeichen), nicht auf dem
  laxeren Muster der `XENT`/`XGLO`-Varianten — ein nacktes `ENT`/`GLO` ist
  sonst ein gewöhnliches Wortfragment.
* **Zusammengeklebte Platzhalter bekommen ihr Leerzeichen zurück (seit
  2026-10-16).** Unter einer Zufalls-Nonce ließ das Modell das Leerzeichen
  zwischen zwei Platzhaltern weg, und der Satz kam als „the trains of line
  U6do not stop at“ oder „line 13Adoes not stop atNeubaugasse“ heraus
  (EN-Probe vom 16.10.). `_respace_glued_placeholders` setzt es wieder
  ein, wo der maskierte Quelltext die beiden Platzhalter getrennt hatte;
  stehen sie dort zusammen („47–51“ sind drei Platzhalter in Folge),
  bleibt es so.
* **Eine Regel für frische Übersetzung und Cache (seit 2026-10-10).**
  Jeder Modelldurchlauf wird mit derselben Prüfung beurteilt wie ein
  gecachter Wert (`_leftover_placeholder`: `_RESIDUAL_PLACEHOLDER_RE` plus
  `_CACHED_DEBRIS_RE` gegen den deutschen Text). Vorher sah der Durchlauf
  nur `_RESIDUAL_PLACEHOLDER_RE`: Verlor das Modell das Präfix eines
  Glossar-Platzhalters (`XGLO`), blieb ein Rest stehen, den kein Muster
  kannte und den auch `_entities_dropped_by_translation` nicht zählt (es
  zählt nur `XENT`). So ein Wert wurde veröffentlicht, gecacht und erst
  im nächsten Lauf vom Cache verworfen. Das galt in beiden Versuchen, in
  der Kurzform („NordGLO0X“, „XGLO0“) wie unter der Nonce
  („Nord8d74459316abX5X“, „Nord8d74459316ab“). `_CACHED_DEBRIS_RE` kennt
  dafür zwei weitere Formen: Präfix-Buchstaben, die am Index kleben
  (`ent`/`glo` vor einer Ziffer), und ein Nonce-Rest (mindestens sechs
  kleine Hex-Zeichen mit Ziffer und Buchstabe). Ein intakter Platzhalter,
  den der Quelltext nicht hat (verschobener Index), lässt den Durchlauf
  ebenfalls scheitern, statt beim Demaskieren still ein Wort zu löschen.
  Jeder erkannte Rest nimmt den Weg jedes verstümmelten Platzhalters:
  zweiter Versuch, danach bleibt das Feld deutsch. Replay: In den 3 003
  vom Juli bis 08.10. veröffentlichten DE/EN-Paaren und den 5 151
  gecachten EN-Feldern vom 10.10. findet die neue Regel genau die Werte,
  die der Cache schon verwarf (27 bzw. 42, kein Wort fälschlich); 24 der
  27 veröffentlichten hätte der Durchlauf schon abgewiesen, der letzte
  stand am 30.09. im Feed. Der Live-Feed vom 10.10. ändert sich nicht.
* **Zweiter Versuch mit frischer Nonce (seit 2026-09-27, Audit A.5).**
  Scheitert ein Feld an einem verstümmelten oder verlorenen Platzhalter,
  läuft das Modell noch einmal, diesmal unter einer frisch gewürfelten
  Nonce (`_model_pass`). Seit 2026-10-06 ist das der Versuch nach der
  Kurzform, also eine andere Zerlegung jedes Platzhalters. Vor der Kurzform
  hing das Scheitern an der Nonce, und eine schlechte Nonce traf mehrere
  Texte eines Laufs: Am 26.09. um 17:01 scheiterten so drei
  Zusammenfassungen, darunter WLs Standardsatz, den andere Läufe übersetzt
  hatten. Die frische
  Nonce sieht nur das Modell: Masken und Zuordnung behalten die Nonce des
  Laufs, und intakte Platzhalter der Antwort werden zurückgesetzt, bevor
  geprüft und demaskiert wird. Ein Fehler der Pipeline selbst löst keinen
  zweiten Versuch aus. Nach zwei gescheiterten Durchläufen bleibt das Feld
  deutsch wie bisher. Die Logzeilen nennen die Nonce jedes Durchlaufs,
  `<kurz>` für die Kurzform.

### Betrieb

Das Übersetzungsmodell wird **lazy** geladen
(`_get_translation_pipeline`), damit CLI-Kommandos ohne Übersetzungsbedarf
kein PyTorch anfassen.

Seit 2026-11-01 ruft der Feed das Marian-Modell direkt auf
(`_MarianTranslator`: Tokenizer, `generate`, Dekodieren), nicht mehr über
`pipeline("translation_de_to_en")`. `transformers` 5 hat diese Aufgabe
entfernt (Dependabot #2114: Mit 5.22.1 lud die Pipeline nicht mehr, der
EN-Feed wäre still auf Deutsch zurückgefallen). Der direkte Aufruf liefert
für alle 792 Modelleingaben der 3.003 EN-Texte seit Juli dieselbe Ausgabe
wie die Pipeline unter 4.57.6, Zeichen für Zeichen. Deshalb bleibt die
Epoche des Übersetzungs-Caches. Die Parameter (`num_beams` 4,
`max_length` 512) kommen aus `generation_config.json` der festen Revision;
`transformers` 5 liest sie nur noch dort.

Jeder Build, der das Modell braucht, schreibt nach
`data/translation_status.json`, ob es geladen wurde
(`_record_translation_status`). Der Health check zeigt das als Punkt
„Übersetzung“: Ein gescheiterter Build ist ein Hinweis, ab dem zweiten in
Folge wird der Punkt rot (Betreiberentscheidung 2026-11-01, „Ja, Umstieg
bauen“). Bis dahin stand ein Ladefehler nur als WARNING im Log; der Check
„build“ am PR und die Tests blieben grün.

Die Logzeile des Ladens nennt seit 2026-10-01 den Commit der geladenen
Modell-Revision (`_model_revision`). Anlass war CVE-2026-80047:
`transformers` 4.49 bis 5.8.1 schreibt bei jedem `from_pretrained` eine
`custom_generate/generate.py` aus dem Modell-Repository in den Cache, bevor
`trust_remote_code` geprüft wird. Mit `transformers` 5.22.1 ist das behoben;
`pip-audit` läuft seit 2026-11-01 ohne Ausnahmeliste. Der Feed lädt das
Modell weiter fest in der Revision, die der Update-Zyklus am 2026-10-01 um
17:01 geloggt hat (`_TRANSLATION_MODEL_REVISION`). Ein späterer Push in das
Repository erreicht den Build nicht, die Übersetzungen bleiben gleich. Wer
auf eine neuere Revision wechselt, ändert die Konstante und erhöht die
Epoche.

`torch` steht **absichtlich nicht** in `requirements.txt` — es ist ein
mehrere hundert MB schweres Backend, das nur der EN-Pfad braucht. Alle
drei feed-schreibenden Workflows installieren es deshalb getrennt und
CPU-only, auf zwei verschiedenen Wegen:

* `build-feed.yml` und `manual-full-refresh.yml` als eigener Schritt
  *Install PyTorch (CPU-only)*,
* `update-cycle.yml` innerhalb des gecachten venv (`.venv/bin/pip install
  torch --index-url https://download.pytorch.org/whl/cpu`) — der
  Cache-Schlüssel dort bezieht die Workflow-Datei bewusst mit ein, weil ein
  früherer Schlüssel die `torch`-Zeile nicht abdeckte und der EN-Feed nach
  einem Cache-Treffer ohne Backend dastand.

Alle drei cachen zusätzlich den Hugging-Face-Hub, damit das Modell nicht bei
jedem Lauf neu geladen wird. Ist die Pipeline nicht verfügbar, liefert
`_get_translation_pipeline` `None`, jedes Feld scheitert, und der EN-Feed
enthält deutsche Items — sichtbar, aber nicht kaputt.

---

## 9. Querverweise

Die laufende Audit- und Refactoring-Historie liegt in den Tagesdateien
unter `changelog/` (Übersicht: `CHANGELOG.md`) und unter
`docs/archive/audits/` (abgeschlossene Audit-Berichte). Architektur-
relevante Änderungen sollten zusätzlich diese Karte und gegebenenfalls
das jeweilige Mermaid-Diagramm aktualisieren.

**Sicherheitsmechanismen** sind bewusst über mehrere Dokumente verteilt –
diese Karte beschreibt nur die laufzeit-architektonischen Schichten:

* `request_safe`-SSRF-Abwehr (DNS-Rebinding-Schutz, Host-Pinning,
  privater-Adressraum-Block): § 2 in diesem Dokument.
* Resilienz-Stack (Circuit-Breaker, Retry, Soft-Fail auf Caches): § 3.
* Path-Guard (`ALLOWED_ROOTS` auf `docs/`/`data/`/`log/`), atomare
  Schreiboperationen (`atomic_write`) und der **Secret-Scanner**
  (`src/utils/secret_scanner.py`, im CI- und pre-commit-Lauf) sind als
  Entwicklungs-/CI-Werkzeuge in [`development.md`](development.md)
  dokumentiert.
* Eine konsolidierte Gesamtübersicht steht in [`AGENTS.md`](https://github.com/Origamihase/wien-oepnv/blob/main/AGENTS.md);
  die Meldewege für Schwachstellen in [`SECURITY.md`](https://github.com/Origamihase/wien-oepnv/blob/main/SECURITY.md).

---

## 10. Robustheit und Laufzeit des Feed-Baus (seit 2026-11-10)

Aus dem Code-Audit vom 10.11.2026. Betreiberentscheidung (Michael,
2026-11-10, Karte „Soll ich den PR Robustheit und Laufzeit aus dem Audit
bauen?“, Antwort „Ja, alle vier Teile“), zu seinem Ziel vom 04.11.: ein
Feed, der ohne Handgriffe läuft und Rechenzeit schont.

**Datum am Rand des Kalenders.** Ein Zeitstempel, dessen Jahr außerhalb von
2000 bis 2100 liegt, gilt als nicht gesetzt (`PLAUSIBLE_YEARS` und
`plausible_moment` in `src/utils/vienna_clock.py`). Anlass, im Audit
nachgestellt: Eine einzige WL-Störung mit dem Ende
`9999-12-31T23:59:59.000+0100` kam in den Cache, und der Feed-Bau brach
danach in jedem Lauf ab (`OverflowError` beim Rechnen mit dem Datum), solange
die Quelle die Meldung führte; ein Beginn im Jahr 1 beendete schon den
WL-Abruf. Solche Werte stehen in Quellen für „offen“ oder „unbekannt“; mit
ihnen lässt sich weder rechnen noch eine Zeitzeile schreiben. Die Regel
greift dort, wo ein Zeitstempel gelesen wird: bei WL (`_iso` in
`src/providers/wl_item_fields.py`, `_moment` in
`src/providers/wl_resolved.py`, die Daten aus Titel und „Zeitraum:“-Text in
`src/providers/wl_text.py`), bei den ÖBB (`_parse_dt_rfc2822` und
`_parse_period` in `src/providers/oebb.py`, jede Seite eines Zeitraums für
sich), bei den Baustellen (`_parse_datetime` in
`src/providers/baustellen_events.py`), beim Lesen der Caches
(`_parse_datetime` in `src/feed_build/base.py`), beim Lesen des Zustands
(`_state_stamp` in `src/feed_build/base.py`: ein solcher Wert in
`data/first_seen.json` gilt als unlesbar, wie bisher ein Wert, der kein
Datum ist: Warnung im Protokoll, als erste Sichtung gilt der Zeitpunkt des
Baus) und im Stammstrecken-Monitor (`_parse_vao_dt` in
`src/stammstrecke/legs.py`). Die Liste führt `READERS` in
`tests/test_plausible_years.py`. Die Meldung bleibt
im Feed; ihr fehlt nur dieses Feld, wie bei einer Meldung, die kein Ende
nennt. Das gilt für jedes Jahr außerhalb der Grenzen, nicht nur für die,
mit denen Python nicht rechnen kann: Ein Ende im Jahr 1970 beendet eine
Meldung nicht mehr, ein Beginn im Jahr 2101 hält sie nicht mehr zurück; sie
gilt wie eine Meldung ohne Ende, bis die Quelle sie nicht mehr führt oder
der Altersfilter greift. Die aufgezeichneten Daten nennen nur die Jahre 2019
bis 2031, keine Meldung ändert sich (Nachspiel im Changelog vom
10.11.2026).

**Eine Meldung hält den Bau nicht an.** Scheitert die Feed-Kette oder das
Erzeugen des deutschen Feeds, sucht `build_isolating`
(`src/feed_build/isolation.py`) die Ursache: Jede gesammelte Meldung läuft
einzeln durch Kette und Ausgabe, auch eine, die es nicht unter die zehn
Plätze geschafft hätte. Scheitert dabei höchstens die Hälfte der Meldungen,
wird der Zustand neu von der Platte gelesen und der Feed ohne diese
Meldungen gebaut; Zähler und Zustand stammen aus diesem zweiten Durchgang.
Der normale Lauf ist unverändert: Die Kette läuft einmal, nur eine Kopie
der Meldungen wird vorher angelegt, 0,3 ms bei den 88 Meldungen vom
10.11.2026 (lassen sie sich nicht kopieren, baut
der Lauf wie vor dem 10.11. ohne Einzelprüfung und schreibt die Warnung
„Meldungen nicht kopierbar“). Scheitert keine Meldung allein,
scheitert mehr als die Hälfte (dann liegt der Fehler im Code, nicht an
einer Meldung, und der Feed käme fast leer heraus) oder scheitert auch der
zweite Durchgang, endet der Bau wie bisher mit dem ersten Fehler, und der
veröffentlichte Feed bleibt, wie er ist; das Protokoll nennt dann, wie
viele Meldungen einzeln scheiterten. Das Schreiben der Feed-Dateien und der
englische Feed sind wie bisher eigene Schritte. Eine übersprungene Meldung
wird gemeldet: als Fehlerzeile im Protokoll des Laufs, im Gesundheitsbericht
des Baus (Abschnitt „Übersprungene Meldungen“ und Fehlerliste; im JSON der
Schlüssel `skipped_items` mit Quelle, Titel und Fehlerklasse samt Stelle im
Code, ohne den Wortlaut des Fehlers) und von dort im Update-Lauf über die
Feed-Wächter-Regel `meldung-uebersprungen` als Issue, also am selben Tag
per Mail ([`reference/feed_watch.md`](reference/feed_watch.md)). Die Läufe
„Build feed“ und „Manual full refresh“ haben keinen Wächter-Schritt: Dort
steht eine übersprungene Meldung nur im Protokoll, das Issue kommt mit dem
nächsten Update-Lauf. Im Gesundheitsbericht zählt die Meldung bei
„Rohdaten“ mit und fehlt ab „Nach Altersfilter“. Der Bericht selbst besteht
im Update-Lauf nur während des Laufs (`.gitignore`; der Wächter-Schritt
liest ihn nach dem Schritt „Build feed“ desselben Jobs,
`tests/test_feed_watch/test_skipped_items_workflow.py`); was der Betreiber
liest, sind das Issue, die Annotation des Laufs und
`data/feed_watch.json`. Der Lauf bleibt grün („Alarmwege: rot, Issue,
Hinweis“, §7): Die Technik arbeitet, dem Feed fehlt eine Meldung. Die
Zeilen der Störungsstatistik (`data/stats/stoerungen_JJJJ.csv`) hält der
Bau zurück, bis der deutsche Feed erzeugt ist (`rows_held_back` in
`src/utils/stats.py`), und schreibt sie vor den Feed-Dateien: Ein
gescheiterter Durchgang und die Einzelproben zählen nichts. Scheitert
danach das Schreiben der Feed-Datei, stehen die Zeilen wie bisher schon in
der Statistik. `lint` prüft nur die Feed-Kette, ohne diese Einzelproben und
ohne die Ausgabe: Scheitert die Kette an einer Meldung, endet `lint` mit
dem Fehler (Exit-Code 2); eine Meldung, an der erst die Ausgabe scheitert,
findet nur der Bau.

**Stationssuche über einen Index.** Ob ein ÖBB-Text eine Wiener Station
nennt, prüfte `text_has_vienna_connection` (`src/utils/stations.py`) mit
einem einzigen Muster aus 159.754 Namen und Aliasen: 7 s zum Kompilieren und
im Mittel rund 1 s je ÖBB-Text, der ÖBB-Abruf brauchte damit 13 bis 16 s.
`WholeTermSearch` (`src/utils/term_search.py`) gibt für die Begriffe des
Verzeichnisses dieselbe Antwort über einen Index nach den ersten vier
Zeichen: Aufbau 0,5 s, ein Text 0,1 bis 0,2 ms, Arbeitsspeicher des
Prozesses 143 statt 583 MB. Welches Zeichen welches trifft und was ein Wortzeichen ist,
fragt der Index Zeichen für Zeichen das Modul `re` selbst, statt dessen
Regeln nachzubauen; käme ein Zeichen vor, das sich so nicht einordnen
lässt, fiele er auf das alte Muster zurück (bei keinem Unicode-Zeichen der
Fall). Die Normalisierung der Stationsnamen (`_normalize_token` in
`src/utils/stations_normalize.py`) merkt sich ihre Ergebnisse, weil die zwei
Indexe des Verzeichnisses dieselben 250.000 Namen nacheinander fragen: der
zweite Index braucht 0,5 statt 1,8 s. Zusammen, gemessen am 10.11.2026 mit
den Daten des Tages, erster Aufruf im Prozess: ÖBB-Abruf 13 bis 17 s →
2,7 s, Feed-Bau 4,2 bis 4,9 s → 2,7 bis 2,9 s.

## 11. Ein Vorfall, ein Platz: gekürzte Ursache und vertauschte Rollen (seit 2026-11-10)

Betreiberentscheidung vom 2026-11-10 (Karte „Zusammenfassen prüfen“), Ergänzung zu
`_join_part_line_tickers` (Teil 1, „Eine zweite Ausnahme“). Am 09.11. von 12:30 bis 14:30 Uhr belegte ein
Oberleitungsschaden vier der zehn Plätze, „2/12/31: Beschädigte
Oberleitung durch einen LKW“ neben „12: Fahrleitungsgebr.“, „31:
Fahrleitungsgebr.“ und „2: Fahrleitungsgebr“. Der Vergleich nahm den
Urheber „durch einen LKW“ mit, und die von der Tafel gekürzte Ursache
kannte er nicht. Jetzt zählt die Ursache so, wie der zusammengefasste
Titel sie nennt (`_title_cause`), und eine gekürzte Ursache wird
ausgeschrieben, wenn sie ohne Schlusspunkt und mit mindestens sechs
Buchstaben genau eine bekannte Ursachengruppe beginnt
(`_cause_key`, „Fahrleitungsgebr.“ → Oberleitungsstörung); beginnt sie
mehrere, bleibt sie, wie WL sie schrieb. Am 23.10. („40/41/42:
Polizeieinsatz“ neben „40/41: Polizeieinsatz“) und 10.11.
(„37/40/41/42: Verkehrsunfall“ neben „41: Verkehrsunfall“) waren die
Rollen vertauscht: Die Tafeln nannten alle Linien, WLs Meldung mit
Nummer nur einen Teil, mit nichts als dem Standardsatz. Jetzt nimmt der
Eintrag mit mehr Linien den mit weniger auf, wenn genau einer der beiden
eine WL-Nummer trägt; zwei Meldungen mit Nummer und zwei reine
Tafel-Kurzmeldungen bleiben getrennt. WLs Standardsatz „Nach einer
Fahrtbehinderung …“ fügt einem Eintrag mit eigenem Text nichts hinzu.
Andere oder keine Ursache behält weiter ihren Platz („60: Falschparker“
neben „10/60: Polizeieinsatz“, 10.11.). Nachgerechnet über alle 2.207
Feed-Stände vom 29.09. bis 10.11.: 11 ändern sich, alle an diesen
Vorfällen und am 05.11. („12: Fahrleitungsgebr, Beschädigte Oberleitung“
wird „12: Beschädigte Oberleitung“); 0 Fehler.

## 12. Jahresarchiv der Statistik (geprüft 2026-11-10)

Der Statistik-Schritt lief noch nie über einen Jahreswechsel. Nachgestellt mit
dem echten Ledger: die 100 Ticks von Mittwoch, 04.11.2026, 23:01 bis Samstag,
07.11.2026, 00:31 um 56 Tage verschoben, sodass Donnerstag, 05.11., auf
Donnerstag, 31.12.2026, fällt (Wiener Uhrzeit, Zeilen nach Wiener Jahr auf
die Jahresdateien verteilt), und das ganze Ledger bis zu 13 Wochen voraus. Jahr, Zeitzone und Dateiwahl stimmen: Der Lauf um 00:01
am 01.01. wertet 2027 aus und schreibt `docs/statistik-2026.md` mit allen
Zeilen bis 31.12. 23:59; die Testsuite, die Sitemap und die Liquid-Prüfung
laufen mit der neuen Seite durch. Gefunden und behoben
(`src/markdown_stats/year_archive.py`):

* **Die Archivseite nannte keine andere Seite.** Das Dashboard zeigte am
  01.01. ein fast leeres Jahr und keinen Weg zum eben abgeschlossenen. Es
  führt jetzt unter dem Kopf zu den Archivseiten (`Frühere Jahre:
  [2026](statistik-2026.md)`, neueste zuerst), jede Archivseite zurück
  („Abgeschlossenes Jahr · Zur laufenden Statistik“). Ohne Archivseite
  lautet das Dashboard wie bisher.
* **Die Archivseite trug keinen Abdeckungshinweis.** Das Dashboard sagte
  bis 31.12., dass die Jahreszahlen Richtung Praterstern nur bis
  14.08.2026 enthalten; die Archivseite desselben Jahres, einen Tag später
  geschrieben, zeigte Praterstern mit halb so vielen Zeilen wie Meidling
  und kein Wort dazu. Sie sagt es jetzt in der Vergangenheit („Von Wien Hbf
  Richtung **Praterstern** sind auf der Stammstrecke Fahrten nur bis
  14.08.2026 erfasst. Die Jahreszahlen unten enthalten Richtung
  **Praterstern** daher nur bis dahin.“). Beurteilt wird am Jahresende und
  nur an den Zeilen des Jahres, damit die Seite an jedem Schreibtag gleich
  lautet: Es gilt die Regel des Dashboards (die andere Richtung fuhr
  weiter, oder der ganze Korridor war länger still als jede normale
  Pause), und die letzte Fahrt liegt vor dem 31.12.
  (`render_finished_year_coverage_note`).
* **Der Abdeckungshinweis endete am 31.01.** README und Website lesen aus
  den geladenen Zeilen, wann eine stille Richtung zuletzt fuhr. Geladen
  waren das laufende Jahr und, solange das 30-Tage-Fenster hineinreicht,
  das Vorjahr. Ab 31.01. galt eine Richtung, die zuletzt im Vorjahr fuhr,
  als nie gemessen: Die beiden 30-Tage-Blöcke im README und das Banner der
  Website verloren den Hinweis, und Meidling allein stand für den
  Korridor (nachgestellt für 31.01. und 09.02.2027). Fehlt den geladenen
  Zeilen eine Richtung, liest der Lauf jetzt die früheren Jahresdateien,
  die neueste zuerst, bis jede Richtung eine Zeile hat oder ein Jahr keine
  Datei (`earlier_rows_until_every_direction`); im Normalbetrieb wird
  nichts zusätzlich gelesen.
* **Die Archivseite folgt ihrem Ledger** (Betreiberentscheidung
  2026-11-10). Das Ledger eines Jahres ändert sich auch nach dem ersten
  Dashboard-Lauf des neuen Jahres noch. Einen Zug vom 31.12. trägt der
  Tick nach seiner Abfahrt ein; in 88 Nächten seit 15.08.2026 stand jede
  Zeile des Vortags im Lauf um 00:01 schon im Ledger, ein Tick ohne Abruf
  trägt sie aber erst später ein. Und Zeilen werden von Hand berichtigt
  (26.10.2026: neun Zwillingszeilen gestrichen; 28.10.2026: 1.060 Zeilen
  für 33 vergangene Tage nachgetragen). Die einmal geschriebene
  Seite hätte dann auf Dauer Zahlen gezeigt, die ihr Ledger nicht mehr
  hat. Jeder Dashboard-Lauf rendert jetzt jedes abgeschlossene Jahr, das
  eine Ledger-Datei hat, und schreibt `docs/statistik-<Jahr>.md`, wenn die
  Seite fehlt oder anders lauten würde; die Zeile „Automatisch erzeugt
  am“ zählt dabei nicht, eine Seite ohne Änderung wird nicht angefasst
  (`write_year_archives`; je Jahr rund 70 ms am Tag).

Nachspiel alt gegen neu vor dem Merge: `changelog/2026-11-10.md`. Tests:
`tests/test_year_archive.py`, `tests/test_calendar_years.py`.
