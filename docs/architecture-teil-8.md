[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 8.

## 13. Wo ein Satz endet: der Punkt einer Abkürzung (seit 2026-11-12)

WL und ÖBB kürzen mitten im Text ab: „Bhf. Meidling“, „Hlst. Li. 95A“, „prov.
Haltestelle“, „bzw. Lainzer Tor“, „Josefstädter Str.“, „Fahrleitungsgebr.“,
„Johann-N.-B.-Pl.“, „Zentralfriedhof, 4. Tor“, „bis 03. Juni“. Jeder Schritt,
der das Ende eines Satzes sucht oder einem Satz den Schlusspunkt abnimmt, muss
diese Punkte vom Satzende unterscheiden („… und S80. Die …“, „… durch einen
PKW. Grund: …“, „… Fahrzeit ein.“). Bis 2026-11-11 hatte jeder Schritt dafür
eine eigene Regel: Die Kürzung hinter einem ganzen Satz hielt „Bhf.“ für ein
Satzende und „U.“ nie, der Titel ließ „2: Fahrleitungsgebr“ ohne seinen Punkt
stehen, und die Kürzung auf 180 Zeichen warf „ein.“ und „D.“ weg, als wären es
Abkürzungen. Seit 2026-11-12 fragen diese Schritte dieselbe Regel
(`src/feed_build/abbreviations.py`; Auftrag des Betreibers vom 11.11.2026,
Klasse 15 der Qualitätsprüfung der Wächter-Funde vom 06.11.). Klasse 16
derselben Prüfung, „am Bahnhof“ als Haltestelle maskiert, steht in Teil 6 unter
[Was am Modell vorbeigeht](architecture-teil-6.md#was-am-modell-vorbeigeht)
(„Ein Haltestellenname beginnt groß“).

**Die Regel** (`is_abbreviation`, `is_abbreviation_period`) liest die Form des
Wortes vor dem Punkt, keine Liste aller Abkürzungen:

* Ein Wort aus Buchstaben ohne Vokal ist eine Abkürzung („Bhf“, „Hbf“, „Hlst“,
  „Str“, „St“, „Nr“, „Wr“, „bzw“, „zw“, „b“); ein deutsches Wort hat einen
  („Uhr“, „aus“, „ein“).
* Kein deutsches Wort endet auf einen Konsonanten und „r“, außer auf „hr“
  („Uhr“, „Verkehr“) und „rr“ („Herr“): „Fahrleitungsgebr“, „Hauptstr“ und
  „Hausnr“ sind gekürzt.
* Ein Wort aus Großbuchstaben oder mit einem Großbuchstaben im Inneren ist ein
  Name, eine Linie oder ein Kurzwort und beendet einen Satz wie jedes Wort
  („PKW“, „KG“, „WLB“, „GmbH“, die Zeichen „S“ und „U“ der Haltestellen, die
  Linie „D“), ebenso eine Linie mit Ziffern („S80“, „N20“). Ein einzelner
  Großbuchstabe direkt hinter einem Bindestrich oder Punkt ist die Initiale
  eines Namens („J-Baumann-G.“, „V.I.C.“).
* Einige Abkürzungen haben einen Vokal oder einen Großbuchstaben im Inneren.
  `_WITH_VOWEL` hält die, die WL, ÖBB und Stadt Wien von April bis November
  2026 in die Caches schrieben („ca.“, „ggü.“, „prov.“, „geg.“, „Li.“, „Ri.“,
  „Haltest.“, „gem.“, „mind.“, „etc.“, „ONr.“), dazu „usw.“, „sog.“, „evtl.“
  und „inkl.“ aus der früheren Liste der Kürzung hinter einem Satz. „Pkw“,
  „Lkw“ und „Kfz“ haben keinen Vokal, stehen aber für ein Hauptwort und beenden
  einen Satz wie eines.
* Eine Zahl mit einer oder zwei Ziffern ist eine Ordnungszahl und kein
  Satzende, wenn ein Monat, „Tor“, „Bezirk“ oder die „Haidequerstraße“ folgt
  („bis 03. Juni“, „Zentralfriedhof, 4. Tor“, „1. Bezirk“, „1.
  Haidequerstraße“), eine Abkürzung mit ihrem Punkt (WLs „Li. 6. Ri.
  Geiereckstr.“ vom 05.04.2026), oder wenn sie in einer offenen Klammer steht
  („(Simmeringer Platz, Busbahnhof, 3. und 4. Haltestelle)“, „(Nacht von 6.
  auf 7.)“). „die Linie 49. Voraussichtliche Dauer“, „über Gleis 2.
  Voraussichtliche …“ und „der Linien 43 und 9. Haltestelle Mayssengasse“
  (Linie 2, 06.11.2026) enden dort.

**Wo sie gilt.** Jede Stelle des deutschen Feed-Baus, die Sätze trennt oder
einen Schlusspunkt abnimmt, außer den unter „Grenzen“ genannten; der englische
Feed übersetzt das Ergebnis.

* Die Kürzung hinter einem ganzen Satz, wenn die Folge eines WL-Tickers vor der
  Beschreibung steht (`_last_sentence_end` und `_lead_with_fragment` in
  `src/feed_build/title_words.py`, Teil 1, „Ticker-Titel: Ursache oben, Folge
  darunter“). Seither zählt dort nur ein Satz der Beschreibung, und er endet an
  jedem Wort, das die Regel nicht als Abkürzung liest, auch an „U.“, „D.“ und
  „Gleis 2.“ (bis 2026-11-11 nie an einem Wort unter drei Buchstaben oder einer
  Zahl). Passt keiner, wird an einer Wortgrenze gekürzt wie jeder andere Text,
  statt nur die Folge zu zeigen. Ein Satz, der genau am 180. Zeichen endet,
  zählt („… 52 und 48A aus.“ bei 13A am 26.09.2026; der Großbuchstabe danach
  liegt hinter der Grenze). Fünf Beschreibungen vom 29.09. bis 06.11.2026
  bestanden nur aus der Folge („13A: Feuerwehreinsatz“ am 05.11.: „Betrieb ab
  Neubaugasse, Burggasse.“), und für N8 hätte WLs Text vom 30.10. „Betrieb ab
  Bhf. Meidling. Die Linie N8 wird in beiden Richtungen zwischen Bhf.“
  ergeben. Für Linie 6 am 21.08. endet der Text jetzt „… über Hauptbahnhof.“
  vor „Grund: Polizeieinsatz“ (WL: „Hauptbahnhof S U.“).
* Die Kürzung auf 180 Zeichen (`_should_drop_trailing_tail` und
  `_trim_truncation_tail` in `src/feed_build/summary_text.py`) nimmt ein
  angeschnittenes Wort mit Punkt nur weg, wenn es eine Abkürzung ist, deren
  Namen der Schnitt abtrennt (bleibt von „gegenüber Bhf. Meidling“ nur
  „gegenüber Bhf.“, endet der Text „… gegenüber …“), und eine Zahl mit Punkt
  nur, wenn sie im ungekürzten Text eine Ordnungszahl ist („bis 03.“ vor
  „Juni“). Ein Wort, das seinen Satz beendet, bleibt: „Planen Sie ca. 15
  Minuten mehr Fahrzeit ein.“ verlor am 19.08.2026 sein „ein.“
  (25/26/27/N20), „… die baustellenbedingte Streckensperre der Linien D.“
  endete am 22.10. auf „Streckensperre …“, „Voraussichtlich bis 11:15 Uhr.“
  am 13.10. auf „bis 11:15 …“ (38); ein WL-Text der Linie 71 vom Mai 2026 („…
  im Bereich Simmeringer Hauptstraße 26. Das Ende der Störung …“) endete in
  der Kürzung auf „Hauptstraße …“.
* Die ausführliche Meldung eines Vorfalls (`_is_long_message` in
  `src/feed_build/wl_tickers.py`, Teil 1, „Ein Vorfall, ein Platz“): Ein
  Ticker, der mit einer Abkürzung endet („Züge halten bei Linie 12 Richtung
  Josefstädter Str.“), ist keine, solange davor kein Satz endet. Am 23.10.2026
  galt er als eine, behielt seine Ursache, und der Feed las „12: Fremdunfall“
  über „Fremdunfall Züge halten bei Linie 12 Richtung Josefstädter Str. Fremder
  Verkehrsunfall.“. Unter allen WL-Titeln und -Texten seit April enden 46 so;
  keiner davon ist eine ausführliche Meldung.
* Der Titel einer Tafel-Meldung (`_first_title_sentence` in
  `src/feed_build/display_titles.py`) behält den Punkt einer Abkürzung und
  endet an jedem anderen Satzende („Streckensperre der Linie D. Betrieb ab …“;
  bis 2026-11-11 erst hinter drei Kleinbuchstaben). „Umleitung ab
  Arbeitergasse über Gürtel - Flurschützstr.“ verlor den Punkt am 30.10.2026,
  der Titel passte nicht mehr zu WLs Beschreibung, und der Feed las „59A:
  Feuerwehreinsatz“ über „Feuerwehreinsatz Umleitung ab Arbeitergasse über
  Gürtel - Flurschützstr.“; ebenso stand am 09.11. „2: Fahrleitungsgebr“ ohne
  Punkt im Feed. Verliert ein Titel nur sein Schlusszeichen, wiederholt der Text
  ihn nicht (`_summary_duplicates_title` in `src/feed_build/item_format.py`):
  WLs „18: Gasrohrgebr. Kein Halt!!!“ vom 04.05.2026 ergäbe sonst „18:
  Gasrohrgebr. Kein Halt“ über „Gasrohrgebr. Kein Halt!!!“.
* Der erste Satz eines Abschnitts, wenn wiederholte Linienabschnitte
  zusammengelegt werden (`_lead_end` in `src/feed_build/wl_measures.py`): Er
  endet nicht an „4.“. WLs Text vom 08.07.2026 („Linie 11: Kein Betrieb
  zwischen Fickeysstraße und Zentralfriedhof, 4. Tor. Linie 71: …“) ergab sonst
  „4. Tor. Tor.“; der Feed zeigte an diesem Tag die Ticker, nicht diesen Text.
* Das Verbinden mit „; “ und der Pfeil einer Verlegung (`without_full_stop`,
  genutzt in `wl_ticker_text.py`, `wl_joins.py` mit `_with_unowned` und
  `_with_line_tickers`, `relocation.py` und `wl_notices.py`): Der Schlusspunkt
  fällt weg, der Punkt einer Abkürzung bleibt („Betrieb nur bis Bhf. Meidling
  Dörfelstr.; Betrieb ab …“, „13A: Umleitung über Josefstädter Str.; 57A: …“,
  „Josefstädter Str. → Lange Gasse 5“), und ein Satz, den WL mit „!“ schloss,
  bekommt keinen zweiten Punkt („Busse halten bei 13A!.“ bei N46 am
  28.10.2026).

**Haltestellenzeichen hinter einem Namen mit Punkten.** WL schreibt
„Kaisermühlen, V.I.C. U“, das Verzeichnis „Kaisermühlen, VIC“. Die Kürzung auf
180 Zeichen warf das „U.“ am Ende eines 92B-Texts vom 18.08.2026 bisher als
Abkürzung weg; seit es als Satzende bleibt, muss das Zeichen dort wie überall
fallen. `without_stop_markers` in `src/utils/wl_stop_markers.py` erkennt einen
Namen deshalb auch mit den Punkten, die das Verzeichnis weglässt, und der
letzte Punkt des Namens beendet dann zugleich den Satz: „… Kaisermühlen, V.I.C.
U. …“ wird „… Kaisermühlen, V.I.C. …“, wie die Kürzung es vorher zeigte (Teil
5, [Haltestellen ohne „S“ und „U“](architecture-teil-5.md#haltestellen-ohne-s-und-u-seit-2026-11-04)).

**Gemessen.** Über alle Titel und Beschreibungen der WL-, ÖBB- und
Baustellen-Caches seit April 2026 und den veröffentlichten deutschen Feed liest
die Regel jedes Wort, das sie Abkürzung nennt, als gekürzt. Nachspiel alt
gegen neu durch die ganze Kette (`main()` mit Cache und Zustand jedes Stands,
die Übersetzung durch ein Echo ersetzt): 4.244 Feed-Stände vom 15.08.2026,
03:01 Wien, bis 12.11.2026; kein Stand scheitert, 597 Stände des deutschen
Feeds ändern sich, alle auf die beschriebenen Arten. Am häufigsten endet eine
Verlegung mit ihrem Satz, wo die Kürzung hinter „Alterlaa U.“ fiel (WLs
Überschrift „… beziehungsweise Alterlaa U“, als Satz geschlossen; jetzt „…
beziehungsweise Alterlaa. …“ statt „… Alterlaa …“, 64A/N64R in 350 Ständen,
35A/N35R/N35 in 108, 12A/14A in 85); WLs „40A. aus.“ bleibt (D, 51); „54B:
Stock im Weg“ zeigt vom 11.11. an „Ghelengasse 30 → Ghelengasse 28“ mit
beiden Zielen, weil die Kürzung „32.“ vor „Haltestellenverlegung“ als
Satzende behält und die Ziele damit ganz zeigt (29; Teil 2, Verlegung);
„57A: Derzeit keine Echtzeitinformation“ wiederholt den Titel nicht (27);
„13A: Veranstaltung“ am 26.09. endet hinter „… 52 und 48A aus.“ statt mitten
in der Linienliste (16).

**Grenzen.** Die Form verrät kein gekürztes Wort, das einen Vokal behält und
anders endet („Quartier Belv.“, „Matzleinsd. Platz“, „Schwarzenbergpl.“, je
ein- bis dreimal seit April), keine Abkürzung am Satzende („Oberleitung def.
Betrieb ab Kagran“), deren Punkt beides ist, und keine Ordnungszahl vor einem
verschriebenen Monat („31. Auust“). WLs eigener Tippfehler „… 71 und 40A.
aus.“ (30.09.2026, so auch veröffentlicht) steht jetzt auch am Ende einer
Kürzung, wie WL ihn schrieb. Eigene Regeln für das Satzende haben noch: die
Satztrennung in `base.py` (`_SENTENCE_SPLIT_RE`, vier Buchstaben vor dem Punkt;
sie sieht hinter „N81.“ oder „ein.“ kein Satzende), die ÖBB-Sätze
(`_oebb_first_two_sentences`, die Strecken in `oebb_routes.py` und
`_sentence_around` in `oebb_route_title.py`), die Sätze der Baustellen
(`_split_into_sentences` mit eigener Liste in `baustellen.py`), das
Zusammenlegen von Beschreibungen (`_collapse_description_prefix` in
`merge_texts.py`), der englische Feed (`_WL_SENTENCE_SPLIT_RE`,
`_OEBB_SENTENCE_SPLIT_RE`, `_capitalise_glossary_after_stop`) und die Prüfer
(`_ends_its_sentence` des KI-Wächters, `feed_rules.py`, `count_sentences` des
Jahresrückblicks). Sie verwechseln vor allem die andere Richtung, ein echtes
Satzende, das nicht als eines gilt; das ist eine eigene Klasse. Tests:
`tests/test_abbreviation_periods.py`, `tests/test_bhf_cut_and_phrase_stops.py`,
`tests/test_wl_stop_markers.py`, `tests/test_ticker_title_split.py`.

## 14. Gemini entscheidet, wo die Regeln eine Störung offen lassen (seit 2026-11-12)

Betreiberentscheidung vom 12.11.2026 (Michael): „Erstelle ein neues
Entscheidungs-Modul mit Gemini. Verwende den vorhandenen, modularen
Gemini-Konnektor … Wenn bei einer Meldung nicht klar ist, ob sie eine Störung
oder ein Hinweis ist, soll Gemini dies über ein neues Modul entscheiden können.
Plötzliche Störungen haben Priorität. Geplante Hinweise kommen danach. Neue
Störungen sind an den ersten Plätzen. Alte (aber noch gültige) Hinweise an den
letzten Plätzen.“ Zur Karte „Soll ich die Gemini-Entscheidung für unklare Fälle
so bauen?“: „Ja, so bauen“. Es ist seine zweite ausdrückliche Ausnahme von
„eine KI liest nur mit und meldet“ (Teil 3, „KI-Wächter“); einen Text des Feeds
schreibt weiter keine KI.

**Was offen bleibt.** Ob eine Störung plötzlich kam, liest `_incident_since`
(`src/feed_build/time_line.py`) aus ihren Wörtern: Ein Planwort irgendwo in
Titel oder Text (`PLANNED_DISRUPTION_RE`) macht sie geplant, eine WL-Störung
ohne Ursache gilt als geplant, wenn sie zur vollen Stunde begann. Eine
plötzliche Störung zeigt „[Seit hh:mm]“ und steht in der ersten Gruppe
(`_is_current_incident`), eine geplante zeigt ihren Tag und steht in der
normalen Reihenfolge. Im Nachspiel der 2.169 Cache-Stände vom 20.09. bis
12.11.2026 entscheidet das bei 22 von 1.026 Störungen kein klares Wort. Sie
fallen in drei Klassen (`unclear_class` in
`src/ki_entscheidung/candidates.py`):

- `ohne-ursache`: eine WL-Störung ohne Ursache und ohne Planwort, über die nur
  die Uhr entscheidet. „26: Züge halten in Tokiostraße“ (Anzeigetext der
  Gleisbauarbeiten in der Donaufelder Straße) stand am 21.09.2026 von 09:01
  bis 23:01 mit „[Seit 08:48]“ in der Gruppe der aktuellen Störungen (28
  Feed-Stände, mit „27: Linie 25 in Tokiostraße benützen“ und „26E: Busse
  halten Satzingerweg 41“); „9: Züge halten bei Linie 52“ (18.10.,
  Rettungseinsatz auf den Linien 6/9/18/52) unterscheidet davon nur die Minute.
- `bauwort`: eine WL-Störung, deren Ursache ein Störungswort ist, während ein
  Bauwort an anderer Stelle im Text steht. „48A: Gasrohrgebrechen“ (30.09.,
  „Aufgrund der Bauarbeiten werden die Busse … umgeleitet“) zeigte „[Bis Do
  01.10.]“, „U6: Weichenstörung“ (22.09., „Die Station Neue Donau kann … wegen
  Bauarbeiten nicht eingehalten werden“) „[Heute]“.
- `oebb-tag`: eine ÖBB-Meldung mit Bauwort, die ÖBB am Tag ihres Beginns
  erstmals veröffentlicht und die an diesem Tag endet. „Wegen Verlängerung der
  Bauarbeiten sind zwischen Wien Franz-Josefs-Bahnhof und Wien Heiligenstadt
  Bahnhof bis voraussichtlich 09:00 Uhr keine Fahrten möglich“ stand am
  01.11.2026 um 06:01 als „[Heute]“ auf Platz 7. Geplante Sperren
  veröffentlicht ÖBB Tage vorher.

Bei den Regeln bleibt, was der Betreiber entschieden hat: Ein Text, der eine
Verlegung, eine Demonstration, eine Veranstaltung, einen Staatsbesuch oder
eine Übung nennt, ist geplant (03.11.2026) und kein Kandidat; `bauwort` und
`oebb-tag` gelten nur, wenn jedes Planwort des Textes ein Bauwort ist. Eine
ÖBB-Meldung über eine beendete Störung bleibt, was sie ist. Ein Vorfall, der
in Arbeiten übergeht („1: Gleisschaden“, 20.10., danach „1:
Gleisbauarbeiten“), ist auf beiden Seiten klar.

**Was Gemini darf.** Eine Antwort je Meldung: `ploetzlich`, `geplant` oder
`unklar`, dazu ein Satz Begründung. Die Antwort setzt nur das Feld `_ki_art`
der Meldung; `_incident_since` wählt damit zwischen den zwei Fassungen, die
die Regeln ohnehin bauen. Bei `unklar` und ohne Antwort entscheiden die Regeln
wie bisher. Gemini schreibt keinen Titel, keinen Text und keine Zeitzeile; die
Begründung steht nur im Speicher. Das Modell liest von der Meldung Quelle,
Titel, Text, Beginn, Ende und Veröffentlichung und bis zu vier andere
Meldungen derselben Linie oder Strecke (`describe`, `neighbours_of`): den
Hinweis über Gleisbauarbeiten neben „26: Züge halten in Tokiostraße“, den
Rettungseinsatz neben „9: Züge halten bei Linie 52“. Die Texte der Betreiber
stehen als Daten zwischen festen Marken; Werkzeuge hat das Modell keine.

**Wann gefragt wird.** Nur der Feed-Build des Update-Laufs fragt
(`Decider.decide` in `src/ki_entscheidung/run.py`, aufgerufen in
`_assemble_feed_items` nach `_carry_item_identity` und vor der Sortierung),
höchstens einmal je Build und mit höchstens acht Meldungen. `feed lint`, die
Einzelproben des Builds, Nachspiele und Tests lesen nur den Speicher
(`apply_stored`). Gefragt wird nach einer Meldung nur, solange sie neu ist:
höchstens 2 h 45 min im Feed, und der Beginn, den ein „plötzlich“ zeigen würde,
liegt ebenso kurz zurück (`ASK_WINDOW`). Der Feed-Wächter meldet ein „[Seit
hh:mm]“, das mehr als drei Stunden nach seiner Uhrzeit zum ersten Mal
erscheint (`seit-zu-frueh`); eine späte Antwort würde genau das erzeugen. Je
Meldung gibt es höchstens drei Anfragen, je Wiener Tag höchstens sechs (gelesen
aus den gespeicherten Zeitpunkten der Anfragen, kein eigener Zähler); ein
geänderter Text wird in diesem Rahmen neu gefragt, die wechselnde Prognose von
WL („Voraussichtliche Dauer: …“) zählt nicht als Änderung. Gefragt werden die
Lite-Modelle der Wächter-Kette zuerst (`MODELS` in `ask.py`), damit die beiden
Flash-Modelle ihr Tageskontingent für den KI-Wächter behalten; nach 20
Sekunden startet keine weitere Anfrage. Die Anfrage läuft über den Konnektor
des Jahresrückblicks (`ask_chain` in `src/markdown_stats/review_client.py`,
Schlüssel im Kopf `x-goog-api-key`).

**Ohne Gemini.** Fehlt der Schlüssel, ist ein Modell überlastet, das
Kontingent erreicht oder die Antwort unbrauchbar, gilt für die Meldung die
Regel; der Build wartet nie länger als die 20 Sekunden und scheitert daran
nie. Der Build meldet sein Ergebnis als Nebenschritt „KI-Entscheidung“
(Ausgabe `ki_outcome`, Teil 5, „Überwachung: Nebenschritte“): Ein leerer oder
von Google abgelehnter Schlüssel ist sofort rot („ein Fehler sollte
auffallen“, 28.10.2026). Bekam eine neue Meldung auf keine ihrer drei Anfragen
eine Antwort oder sind die sechs Anfragen des Tages verbraucht, ist das wie
beim KI-Wächter ein Hinweis (04.11.2026), der endet, sobald die Meldung nicht
mehr neu ist. `KI_ENTSCHEIDUNG=0` schaltet die Anfragen ab. Builds ohne
Schlüssel (`build-feed.yml`, `manual-full-refresh.yml`, lokal) lesen den
Speicher und schreiben ihn nie.

**Speicher.** `data/ki_entscheidungen.json` fährt im Commit des Update-Laufs
mit wie `data/first_seen.json`: je Meldung (Schlüssel wie im Zustand, die
GUID) die Antwort `art`, ihr `grund`, das `modell`, der Abdruck des Textes,
die Zeitpunkte der Anfragen und der Tag, an dem die Meldung zuletzt in einem
Build stand. Ein Eintrag fällt eine Woche danach weg. Ein Nachbau desselben
Commits liest dieselben Antworten und baut denselben Feed.
