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
