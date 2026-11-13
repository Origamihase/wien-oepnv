[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 13.

## 8. Der zweisprachige Feed (DE → EN)

`docs/feed.en.xml` ist kein eigener Feed, sondern ein **Spiegel** von
`docs/feed.xml`: dieselben Items, dieselbe Reihenfolge, dieselben Zeiträume
— nur Titel und Rumpf auf Englisch. Beide Dateien entstehen im selben
Build-Lauf (`build_feed.main`, Ende von `src/build_feed.py`), der den
fertigen deutschen Feed schreibt und danach denselben Item-Satz ein zweites
Mal durch `_make_rss(..., lang="en")` schickt.

Der Code der Übersetzung steht seit 2026-11-03 in Teilen unter
`src/feed_build/` (Etappe 8, [Modularisierung](modularisierung.md));
`src/build_feed.py` reicht die Namen weiter. Die Schritte des Diagramms
unten liegen so:

| Teil | Schritte |
| --- | --- |
| `translation_cache` | `_apply_lang_overlay`, `_cached_translation`, `_cached_translation_defect` |
| `translation_epoch` | `_TRANSLATION_CACHE_EPOCH`, `_evict_stale_translations`, `_stamp_translation_epoch`, `_record_source_digest` |
| `pretranslate` | `_split_label_record`, `_normalise_for_translation` |
| `translate_run` | `_translate_text_attempt`, `_render_label_record` |
| `translate_titles` | `_translate_text`, `_translate_title_attempt` |
| `placeholders` | `_PLACEHOLDER_NONCE`, `_placeholder_nonce`, `_apply_domain_glossary`, `_normalise_placeholder_debris` |
| `entities`, `masking` | Entitätsmuster, `_mask_entities`, `_unmask_entities` |
| `model_pass` | `_model_pass`, Prüfung auf Platzhalterreste |
| `en_repairs`, `en_templates` | `_entities_dropped_by_translation`, `_fix_glossary_articles`, EN-Vorlagen |
| `translator` | `_get_translation_pipeline`, `_MarianTranslator`, `_TRANSLATION_STATE`, `_record_translation_status` |

Ein Test ersetzt einen dieser Namen in dem Teil, dessen Code ihn liest;
`_get_translation_pipeline` lesen mehrere Teile, dort ersetzt `setattr_all`
ihn in jedem.

Übersetzt wird **auf Item-Ebene, nach der deutschen Formatierung**. Das ist
wichtig für das Verständnis der ganzen Kette: Kürzung auf 180 Zeichen,
Satz-Auswahl, Dedupe gegen den Titel und `MaxItems` sind zu diesem Zeitpunkt
längst passiert. Die Übersetzung sieht genau den Text, den auch ein deutscher
Leser sieht — nicht den Rohtext des Providers.

```mermaid
flowchart TD
    DE["Fertiger DE-Item-Text<br/>(title_out, summary, time_line)"]
    Overlay["_apply_lang_overlay<br/>(pro Item, lang='en')"]
    Evict["_evict_stale_translations<br/>Epoche veraltet? → verwerfen"]
    Cache{"_cached_translation<br/>Treffer?"}
    Digest["Quell-Fingerprint geprüft<br/>(_SOURCE_DIGEST_KEY)"]
    Split["_split_label_record<br/>Prosa | Label-Record"]
    Record["_render_label_record<br/>Tabelle OHNE Modell,<br/>Grund nur bei unbekanntem Wort"]
    Norm["_normalise_for_translation"]
    Glo["_apply_domain_glossary<br/>XGLO-Platzhalter"]
    Mask["_mask_entities<br/>XENT-Platzhalter"]
    Marian["Helsinki-NLP/opus-mt-de-en<br/>(transformers, direkter Aufruf, lazy)"]
    Unmask["_unmask_entities<br/>+ _normalise_placeholder_debris"]
    Guards["Nachkontrollen:<br/>_entities_dropped_by_translation<br/>_fix_glossary_articles<br/>Rest-Platzhalter"]
    OK{"Alle Felder<br/>übersetzt?"}
    EN["EN-Item"]
    Fallback["Item bleibt DEUTSCH<br/>(byte-identisch zum DE-Feed)"]
    Stamp["_stamp_translation_epoch<br/>+ _record_source_digest"]

    DE --> Overlay --> Evict --> Cache
    Cache -- ja --> Digest --> OK
    Cache -- nein --> Split
    Split -- Record --> Record --> Guards
    Split -- Prosa --> Norm --> Glo --> Mask --> Marian --> Unmask --> Guards
    Guards --> OK
    OK -- ja --> EN --> Stamp
    OK -- nein --> Fallback
```

### Die beiden Invarianten

**1. Ein Item ist ganz englisch oder ganz deutsch.** `_apply_lang_overlay`
gibt bei einem Fehlschlag in *irgendeinem* Feld (`title`, `summary`,
`time_line`) das unveränderte `base` zurück. Ein gemischtsprachiges Item oder
ein „Partially translated"-Marker würde bedeuten, dass der englische Abonnent
etwas anderes liest als der deutsche — genau das verbietet der
Content-Parity-Vertrag. Wer hier eine „wenigstens teilweise"-Logik einbaut,
bricht ihn.

**2. Der Cache wird pro Identität geführt, nicht pro Text.** Die EN-Strings
liegen in `state[ident]["translations"]["en"]` und überleben Builds — bei
einer mehrjährigen Baustelle also Jahre. Zwei Mechanismen brechen diese
Persistenz auf, und sie haben **getrennte Zuständigkeiten**:

| Mechanismus | Invalidiert bei | Wer ihn zieht |
| --- | --- | --- |
| `_TRANSLATION_CACHE_EPOCH` | Änderungen auf **unserer** Seite (Masking, Glossar, Entity-Muster) | du, von Hand, im selben PR |
| `_SOURCE_DIGEST_KEY` | Änderungen **upstreams** (WL formuliert dieselbe Störung um) | automatisch, pro Feld |

Die „Sticky-German"-Bremse erzwingt einen neuen Versuch nur, wenn der
gecachte Wert *gleich dem deutschen Quelltext* ist. Eine **falsche, aber
nicht-deutsche** Übersetzung — `Schlachthausgasse` als „slaughterhouse gas",
bevor der Straßen-Suffix-Masker existierte — wird ohne Epochen-Bump für die
Lebensdauer des Items weiter ausgeliefert. Das ist die praktische Regel:
**wer Masking oder Glossar verbessert, erhöht die Epoche im selben PR**,
sonst sieht niemand die Verbesserung. Die Epochen-Historie steht als
Kommentar bei der Konstante in `src/feed_build/translation_epoch.py` und ist
dort zu ergänzen.

### Was am Modell vorbeigeht

Nicht jeder Text gehört in ein NMT-Modell:

* **Label-Records.** Endet der Text auf mindestens
  `_MIN_LABELS_FOR_RECORD` (= 2) `Label: Wert`-Paare — bis 2026-10-05 vor
  allem die WL-Haltestellenverlegungen mit `Haltestelle:` / `Von:` /
  `Nach:` / `Dauer:`, seither der WL-Störungsschluss „Voraussichtliche
  Dauer: … Grund: …“ —, trennt
  `_split_label_record` sie ab und `_render_label_record` setzt sie **ohne
  Modell** aus dem Glossar zusammen. Nur die Prosa davor geht durch Marian.
  Ein Modell, das eine Tabelle als Satz liest, erfindet Zusammenhänge.
  Die *Werte* setzt `_gloss_record_values` um — eine eigene, nur hier
  wirkende Wortliste für die kleine Grammatik hinter `Dauer:` und
  `Nach:` (Datumspräposition an eine Ziffer gebunden, Monatsnamen, „etwa",
  „bis auf Widerruf", „Meter in Richtung"). Nicht im Glossar, weil „ab",
  „bis", „vor", „nach" in Prosa gewöhnliches Deutsch sind. „bis
  voraussichtlich“ wird im Record vorher zu „bis etwa“ und damit „until
  approx.“, am Wertanfang groß („Duration: Until approx. 22:00“); in Prosa
  greift der Glossar-Eintrag „bis voraussichtlich“ → „until approx.“
  (seit 2026-09-26, Audit vom 25.09., A.3; vorher „until expected“).
  Seit 2026-10-06 steht im deutschen Text „Voraussichtlich bis 14:10 Uhr.“
  statt „Voraussichtliche Dauer: 14:10 Uhr.“ (§1, „Titel und Beschreibung“). Der Satz zählt wie ein
  Label und beginnt den Record (`_EXPECTED_UNTIL_RE`); als letzter Satz ist
  er auch allein ein Record. `_render_record_table` setzt ihn als „bis …“
  zusammen und stellt „Expected“ davor: „Expected until 14:10.“, „Expected
  until approx. 22:00.“, „Expected until Sunday end of service.“ Die
  Wertliste kennt dafür die Wochentage, „circa“ und „Anfang“/„Mitte“ vor
  einem Monat („Wien Mitte“ bleibt ein Name), das Glossar
  „Betriebsschluß“ und „Betriebschluss“. Eine Hausnummernspanne beginnt
  nicht mehr an der Ziffer eines Label-Platzhalters („Duration:-19
  September“ wurde „Duration: Until 19 September“). Kleingeschriebenes
  „voraussichtlich bis“ in Prosa (ÖBB, WLs „Die Störung dauert …“) bleibt
  Prosa.
  Seit dem EN-Audit vom 2026-10-03 gehört ein „Voraussichtliche“ vor dem
  ersten Label zum Record („Expected duration:“; vorher blieb es als letztes
  Wort der Prosa stehen und wurde verschluckt oder einzeln übersetzt), und
  ein abschließendes `Grund:` wird eigens behandelt: Kennt das Glossar jedes
  Wort, entsteht es ohne Modell („Reason: third-party traffic accident in the
  stop area of Broßmannplatz.“), sonst geht nur dieser kurze Satz durchs
  Modell. Vorher blieb jedes unbekannte Ursachenwort deutsch, weil der Record
  das Modell nie sah („Reason: Tiere im Gleis im Haltestellenbereich …“).
  Seit 2026-10-05 stehen „Von:“ und „Nach:“ einer Verlegung als
  „Donaufelder Straße 48 → Donaufelder Straße 40-42“ im deutschen Text
  (`_relocation_first`). Der Pfeil wird wie jeder Pfeil maskiert
  (`_PRESERVED_SYMBOLS_RE`). Durchs Modell kamen die Orte verklebt und mit
  Platzhalter-Resten heraus (erster Lauf 05.10. 17:45: „Wienerbergstraße27b-27c
  →Wienerbergstraße27a. … 15A5X …“); seit Epoche 23 setzt
  `_render_wl_sentence` jeden Satz mit Pfeil wie früher den Record ohne
  Modell zusammen (`_render_record_table`: „Hartäckerstraße 65 → approx. 50
  metres towards Borkowskigasse.“). Die Lage am Ort bekommt dabei ihr
  englisches Wort, für jedes Wort, das von Juli bis 23.10.2026 in den 101
  Verlegungen deutsch blieb (Epoche 26, `_RECORD_VALUE_RULES`: „Gleiskörper“
  → „track area“, „Haltestelle Straßenbahn“ → „tram stop“, „Parkanlage“,
  „Einfahrt“, „Stiege“, „Richtung Ampel“, „unter“, „in Fahrtrichtung
  Kagran“ → „in the direction of Kagran“). Seit Epoche 33 auch der Eingang
  eines Parks oder Gebäudes, gebunden an den Genitiv oder „zum“/„zur“
  dahinter (`_ENTRANCE_OF_RE`): „Filmteichstraße vor Nordeingang des
  Kurparks“ (68A, 05.11.2026) las „Filmteichstraße before Nordeingang des
  Kurparks“ und liest nun „Filmteichstraße before the north entrance of
  Kurpark“; der Name dahinter steht in der Grundform wie jeder Name im
  englischen Feed. Eine Haltestelle mit dem Wort im Namen („Kurpark
  Nordeingang“, „Laaer Berg, Nordosteingang“) bleibt ihr Name. Unter den
  188 Verlegungs-Records von WL seit Februar 2026 ändert sich nur dieser.
  Seit Epoche 34 gilt das für jeden Weg hinein oder hinaus, nicht nur den
  Eingang (`_ACCESS_NOUNS_EN`: Eingang, Zugang, Einfahrt, Ausfahrt,
  Zufahrt, Hauseinfahrt, Zusammensetzungen wie „Seitenausgang“): „Würzburggasse nach Einfahrt zum
  Friedhof“ (8A, 13.11.2026, KI-Wächter-Issue #2209) las „Würzburggasse
  after the driveway zum Friedhof“ und liest nun „Würzburggasse after the
  driveway to Friedhof“. „Zufahrt zum Campingplatz“ behält seinen festen
  Wortlaut. Unter den 256 Verlegungswerten von WL seit 28.09.2026 ändert
  sich nur dieser.
  Ein Satz endet dabei nicht hinter einer
  Ordnungszahl am Adressanfang („1. Haidequerstraße“) oder einer Abkürzung
  („ggü. 40“, `_WL_SENTENCE_SPLIT_RE`).
* **ÖBB-Sätze nach Vorlage.** ÖBB schreibt Störungen fast immer in einem
  Satz mit festen Feldern: „Wegen <Ursache> sind|waren zwischen <A> und
  <B>|in <A> [Zugfahrten] [bis [voraussichtlich] <Zeit> Uhr|derzeit] keine
  Fahrten|nur eingeschränkt möglich.“ (407 der 455 ÖBB-„Wegen“-Sätze seit
  Mai). `_translate_oebb_templates` setzt solche Sätze aus den Feldern
  zusammen („Due to a police operation, no trains can run between …
  until approx. 20:30.“); nur „Wegen <Ursache>“ geht als kurze Phrase durchs
  Modell, Stationsnamen bleiben wie ÖBB sie schreibt, nur ein nachgestelltes
  „Bahnhof“/„Bahnhst“ wird „station“. Vorher verlor das Modell regelmäßig das
  Verb am Satzende („… to 23:48 were not possible.“). Sätze ohne Vorlage
  nehmen den normalen Weg. Die Vorlage kennt auch ein Datum vor der Uhrzeit
  („bis voraussichtlich 27.08.2026, 23:59 Uhr“), die Wortfolge
  „voraussichtlich bis“ und die Gemeinde in eckigen Klammern hinter dem
  Bahnhof („Hinterstoder Bahnhof [in St.Pankraz]“). Ein Stationsfeld enthält
  nur einen Namen: Steht darin eine Ziffer oder ein Wort wie „Zugfahrten“,
  „bis“, „bzw“ oder „am“ (`_OEBB_SLOT_NOT_A_NAME_RE`), weicht der Satz von
  der Vorlage ab und nimmt ebenfalls den normalen Weg. Vorher landete jede
  Abweichung nach der zweiten Station im Stationsfeld, Deutsch mitten im
  englischen Satz („… and Gramatneusiedl Bahnhof Zugfahrten bis
  voraussichtlich 27.08.2026, 23:59 Uhr.“; vierte Nachprüfung 2026-10-03).
  Seit 2026-10-09 (Epoche 25) darf der Ort fehlen, weil der deutsche Text
  ihn weglässt, wenn der Titel ihn nennt („…, no trains could run until
  20:18.“, siehe §1, „ÖBB: der zweite Satz“). ÖBBs feste Folgesätze setzt
  `_render_oebb_follow_up` ohne Modell zusammen („Planen Sie derzeit noch
  bis zu 10 Minuten mehr Reisezeit ein.“ → „For now, allow up to 10
  minutes of extra travel time.“; ebenso „Es kommt noch zu vereinzelten
  Zugausfällen und Verzögerungen.“, „Über die Dauer der Unterbrechung kann
  derzeit noch keine Angabe gemacht werden.“, „Die Züge warten die Sperre
  vorerst ab.“, „Ihre Reisezeit verlängert sich um bis zu N Minuten.“):
  249 der 261 zweiten Sätze, die der deutsche Text seither behält; die
  übrigen („Fernverkehrszüge und CJX Züge werden umgeleitet.“) nehmen den
  normalen Weg. Der Satz endet nicht mehr hinter „St.“ („in St. Pölten
  Hbf“): 42 der 443 ÖBB-Texte gingen vorher in zwei Hälften durchs Modell.
* **WL-Sätze mit „Wegen …“ (seit 2026-10-05).** WL schreibt Ursachen wie
  ÖBB vor den Hauptsatz („Wegen Bauarbeiten im Bereich Wildbadgasse wird die
  Linie 20A umgeleitet.“), und das Modell verlor daran den Sinn
  („Construction works in the area of Wildbadgasse is redirected to line
  20A.“, 3 sinnentstellte Sätze seit Juli; nach der Textänderung bei U6 Neue
  Donau fiel der Grund ganz weg). Die Hauptsätze sind freie Prosa, Felder
  gibt es nicht: `_render_wl_because_sentence` übersetzt die Ursache als
  kurze Phrase (wie bei ÖBB) und den Hauptsatz als eigenen Satz, eröffnet
  mit „Deshalb“, damit das Verb an zweiter Stelle bleibt. Das „Therefore,“
  des Modells fällt weg, die Ursache tritt an seine Stelle („Due to
  construction works in the area of Wildbadgasse, line 20A is diverted.“).
  Beginnt das Modell den Hauptsatz anders oder enthält die Ursache ein Komma
  (244 von 255 WL-„Wegen“-Sätzen seit Juli passen), geht der Satz wie bisher
  als Ganzes durchs Modell. Epoche 22. Gibt das Modell die Ursache als
  bloßes Hauptwort zurück („Renovation of the platform“, erster echter Lauf
  05.10. 17:45), setzt `_oebb_cause_en` „Due to“ davor (Epoche 23, gilt auch
  für ÖBB).
* **Ursachen heißen immer „Due to“ (seit 2026-10-08).** Betreiberentscheidung
  vom 05.10. Das Modell gibt „Wegen“ mal als „Due to“, mal als „Because of“
  wieder: 72 der 309 verschiedenen Sätze, die der EN-Feed seit Juli mit
  einer Ursache eröffnete, begannen mit „Because of“ (63A, 29B), drei
  weitere sagten „because of“ mitten im Satz, und die Ursachen-Bausteine
  von ÖBB und WL nahmen beides an. `_cause_due_to` macht nach jedem
  Modelldurchlauf (`_model_pass`) aus „because of“, „owing to“, „on account
  of“ und „as a result of“ ein „due to“, wo immer es steht; ein großer
  Anfangsbuchstabe bleibt. Wörter ohne Ursachen-Bedeutung („following“,
  „As a result,“ ohne „of“, „Because the …“) bleiben unberührt. Werte aus
  dem Übersetzungs-Cache bekommen dieselbe Behandlung beim Ausliefern
  (`_cached_translation`), darum ohne Epochenwechsel und ohne neue
  Übersetzung. Nachgespielt über alle 4.820 EN-Feed-Stände seit Juli: 134
  der 4.205 verschiedenen Titel und Beschreibungen ändern sich, und zwar
  nur in diesem einen Wort. Seit der EN-Prüfung vom 2026-10-08 gehört auch
  „in view of“ dazu (kam nie vor, aber `_CAUSE_EN_RE` nimmt es als Ursache).
* **EN-Sinnfehler als Klassen (seit 2026-10-08, Epoche 24).** Alle 3.003
  verschiedenen deutschen Texte des Feeds seit Juli liefen durch das echte
  Modell; was den Sinn verfehlte, ist nach Klassen behoben:
  * WLs „Wegen <Ursache> kommt es zu (folgenden) Verkehrsmaßnahmen (für die
    Linie X).“ wird ohne Modell geschrieben (`_WL_SERVICE_CHANGES_RE`):
    „Due to <cause>, there are service changes.“ bzw. „…, the following
    service changes apply (to line X).“ (Wortwahl des Betreibers vom
    08.10.; vorher „transport is taking place“). Ebenso WLs zwei Sätze zu
    unregelmäßigen Intervallen (`_WL_IRREGULAR_INTERVALS_RE`): „Currently,
    there are irregular intervals on line N31 in both directions.“ und
    „After a service obstruction, there are irregular intervals.“
  * `_normalise_for_translation` schreibt WLs Kürzel aus („Li.“, „Ri.“,
    „Hlst.“/„Haltest.“, „Fahrleitungsgebr“, „Steig A“, „ONr.“), macht
    „Einstieg bei Linie 44“ zu „bei Haltestelle der Linie“, eine
    Querstraße hinter „nach“ in Halte- und Einstiegsmeldungen zu „just
    after“ und „kann nicht eingehalten werden“ / „hält … die Station … ein“
    zu „not served“ / „stops at“; „kann/können … die Haltestelle(n)/Station(en)
    … nicht einhalten“ mit dem Subjekt davor („Die Linie 66A kann die
    Haltestelle Atzgersdorf, Kirchenplatz in Richtung Reumannplatz nicht
    einhalten“, vorher „cannot follow the stop“) wird zu „hält nicht an der
    Haltestelle …“, das Glossar sagt „does not stop at“ (15.10.). Steht das
    Subjekt hinter dem Verb („Derzeit können die Züge der Linie U6 die Station
    Alser Straße in beiden Richtungen nicht einhalten“, U1, U2 und U6 seit Juli),
    kommt es vor das Verb und die Richtung hinter die Station („Derzeit die
    Züge der Linie U6 halten nicht in der Station Alser Straße in beiden
    Richtungen“), damit das Glossar greift. Vorher blieb der Satz ungeglost,
    und das Modell schrieb je nach Platzhalter-Nonce des zweiten Durchlaufs
    etwas anderes („stop towards Floridsdorf do not stop at the station“,
    „are unable to meet the Stadlau station“). Michael wählte die Behebung am
    16.10. (Karte „Englischer U6-Text“): Der Satz soll bei jedem Durchlauf
    gleich und sauber herauskommen. Mit dem Glossar besteht schon der erste
    Durchlauf mit den kurzen Platzhaltern; der Zufalls-Nonce des zweiten wird
    nicht mehr gebraucht, und das Ergebnis ist bei jedem Build dasselbe.
    Seit der Prüfung vom 16.10. gilt das auch für die Einzahl „die Station“
    mit dem Subjekt davor oder einer Uhrzeit hinter der Station („Die Linie
    U6 kann die Station Alser Straße nicht einhalten“; das Muster
    `Stationen?` traf nur „Statione(n)“) und für ein Subjekt mit „Linien“
    hinter dem Verb („die Züge der Linien U2 und U3“, „die Busse der Linien
    54A und 54B“, `_STOP_RULE_SUBJECT`). Ein Partizip, das das Glossar übersetzt
    („kurzgeführt“, „gesperrt“, „nicht angefahren“, „aufrecht gehalten“),
    kommt mit seinem Hilfsverb zusammen (`_GLOSSED_PARTICIPLE_RE`), damit das
    Modell es nicht abtrennt („line 5A curtailed“, „can be be maintained“).
  * Neue Glossar-Einträge: „unterschiedlichen Intervallen“, „über Gleis
    1/2“, „kurzgeführt“ → „curtailed“, „Ausgang“, „Sperre“, „gesperrt“,
    „Weichentausch“, „betriebslose Zeit“, „Nebenfahrbahn“, „Umleitung
    nach“, „verlegt nach“, „im Bereich der/des“ (vorher „in the area of
    of“).
  * Namen bleiben Namen: Wiener Orte, die gewöhnliche Nomen sind („Ring“,
    „Gürtel“, „Oper“, „Innere Stadt“, „Kurpark“, `_PLACE_NAME_ENTITIES`),
    Straßen mit Leerzeichen („Julius Raab Platz“, `_SPACED_STREET_NAME_RE`),
    Stationsnamen, deren Bindestrich zwischen zwei Leerzeichen steht
    („Burggasse - Stadthalle“ für „Burggasse-Stadthalle“, „Michelbeuern -
    AKH“, „Julius - Raab - Platz“; vorher „Burggasse - City Hall“, „Julius -
    Raab - square“, 16.10.; ein Leerzeichen nur auf einer Seite wie in
    „Lorenz-Müller- Gasse“ oder „Josef- Baumann- Gasse“ ist ein Tippfehler
    der Quelle, den das Modell in der Probe vom 16.10. stehen ließ, wie er
    geschrieben ist: der Name bleibt ein Name; ein Name mit diesem Tippfehler
    in einem ÖBB-Text, „Wien Erzherzog- Karl Straße“ (03.07.2026, nur im
    dritten Satz, den der Feed nicht zeigt), wird bewusst nicht eigens
    geschützt, Betreiberentscheidung 2026-10-16 „So lassen“: er kam nie ins
    Modell),
    „Erlaaer Schleife“ und das eine Wort nach „Richtung“
    (`_DIRECTION_NAME_RE`, außer Himmelsrichtungen und „Zentrum“). „in
    Richtung Bahnhof Heiligenstadt S U“ (43 Texte seit Juli) bleibt dem
    Modell überlassen, Betreiberentscheidung 2026-10-16 „So lassen“: meist
    kommt „towards the station Heiligenstadt S U“ heraus, selten „direction
    Heiligenstadt S U“ (05.10.); beides ist verständlich und richtig, und nur
    der englische Feed ist betroffen.
  Nachgespielt mit dem echten Modell über alle 3.003 Texte: Nur Texte mit
  einer dieser Formen ändern sich; die übrigen Unterschiede sind das
  bekannte Rauschen der Wiederholung unter zufälliger Nonce.
* **Ein Haltestellenname beginnt groß (seit 2026-11-12).** Die Suche nach
  Stationsnamen (`_station_entity_pattern`) ignoriert Groß- und
  Kleinschreibung, damit WLs Listen in Großbuchstaben („STADION U“) geschützt
  bleiben. Dadurch traf die Haltestelle „Am Bahnhof“ auch die Wendung „am
  Bahnhof“ im Fließtext: ÖBBs „Wegen einer Stellwerkstörung am Bahnhof …“
  stand im englischen Feed als „Due to an interlocking malfunction am
  Bahnhof, …“ (31.05. bis 02.10.2026; „interlocking fault am Bahnhof“ ab
  19.06.). Seit dem Glossareintrag „am Bahnhof“ → „at the station“ vom
  03.10.2026 verschwindet die Wendung vor der Maskierung, die Maskierung
  blieb aber falsch. Jetzt darf ein Treffer nicht mit der Kleinschreibung
  eines Anfangsbuchstabens aus dem Verzeichnis beginnen („a“, „ö“, „š“ von
  „Šatov“); jeder Name dort beginnt mit einem Großbuchstaben oder einer
  Ziffer. Gemessen an allen 10.309 Quelltexten seit Juni (jeder Cache-Stand
  von WL, ÖBB und Baustellen): Nur die 90 Texte mit „am Bahnhof“, alle von
  ÖBB, werden anders maskiert; alle großgeschriebenen Treffer („Am Platz“,
  „Am Schöpfwerk“, „Unter St. Veit“, „STADION“) bleiben Namen.
* **ÖBBs Abschnittsnamen auf Englisch (seit 2026-10-23).** Der
  deutsche Feed schreibt ÖBBs Abschnittsnamen normal („Teilausfall:“, §1,
  `_oebb_section_labels_cased`); das Modell machte daraus „Partial
  failure:“ (RJ 263, 18.–19.10.2026). Michael wählte am 23.10. feste Namen
  für alle Abschnittsnamen statt der Modellübersetzung. Steht ein solcher
  Name am Satzanfang (`_OEBB_LABELLED_SENTENCE_RE`: großgeschriebene Wörter,
  durch Leerzeichen oder „/“ verbunden, dann der Doppelpunkt), schreibt
  `_render_oebb_sentence` ihn aus `_OEBB_SECTION_LABELS_EN`, und nur der Rest
  des Satzes geht durch seine Vorlage oder das Modell. Die Liste enthält jeden
  Abschnittsnamen der 926 ÖBB-Meldungen in der Cache-Geschichte: „Achtung“ →
  „Please note“, „Ausfall“ → „Cancellation“, „Teilausfall“ → „Partial
  cancellation“, „Haltausfall“ → „Cancelled stops“, „Ersatzbeförderung“ →
  „Replacement service“, „Schienenersatzverkehr“ → „Rail replacement
  service“, „Geänderte Fahrzeiten“ → „Timetable changes“, „Hinweis(e)“ →
  „Note(s)“. Zwei Namen mit „/“ („Teilausfall/Schienenersatzverkehr“) werden
  Teil für Teil nachgeschlagen („Partial cancellation/rail replacement
  service“). Hat der Rest des Satzes eine ÖBB-Vorlage, wird er nach ihr
  geschrieben; sonst bekommt das Modell den Satz wie bisher samt Namen, und
  nur die Wörter vor seinem Doppelpunkt werden ersetzt. So liest das Modell
  den Rest im selben Zusammenhang, und im englischen Text ändert sich nur
  der Name. Ein unbekannter Name bleibt beim Modell wie bisher; andere
  Quellen sind nicht betroffen. Dabei kam ein Fehler der Satztrennung heraus:
  `_OEBB_SENTENCE_SPLIT_RE` trennte nach „bzw.“, „Wr.“ und „b.“ wie nach
  einem Satzende („zwischen Budapest-Keleti und Wien Hbf bzw. Salzburg Hbf
  fahren …“); diese Kürzel stehen vor einem Namen und trennen jetzt so wenig
  wie „St.“. Nachgespielt mit allen 926 ÖBB-Meldungen der Cache-Geschichte:
  die deutsche Fassung ist gleich; mit einem festen Echo-Modell ändern sich
  im englischen Feed vier Meldungsfassungen (RJ 263 dreimal, „Geänderte
  Fahrzeiten“ Budapest–Salzburg einmal), und zwar nur im Namen; mit dem
  echten Modell ebenso („Partial failure:“ → „Partial cancellation:“,
  „Modified journey times:“ → „Timetable changes:“). Von den 25 Sätzen mit
  Abschnittsnamen in den vollen Texten behalten 22 ihren englischen Rest
  wörtlich; zwei „Achtung: Ihre Reisezeit verlängert sich …“ bekommen die
  feste Folgesatz-Vorlage („Your journey takes up to 15 minutes longer.“),
  und der Budapest-Satz ist wieder ganz. Ein Wert im Übersetzungs-Cache, dem
  der feste Name fehlt („Partial failure:“), gilt als Fehler und wird neu
  übersetzt (`_oebb_section_label_defect` in `_cached_translation`, nur für
  ÖBB); eine neue Epoche hätte jede Meldung neu übersetzt.
* **ÖBBs „kann … nicht fahren“ auf Englisch (seit 2026-10-25).** Das Modell
  verlor das Verb hinter Strecken und Tagen (Jules-Audit 2026-10-25, beide
  aus dem EN-Feed): „… the S2 train can't drive 23360.“ und „Construction
  works allows between … on individual days of the S1 train …“, als führe
  der Zug. Über alle ÖBB-Texte seit Dezember 2025 las es diesen Satzbau oft
  falsch („… can be used by 10.08.2026“, „… cannot drive R95 trains“). Der
  Satz wird jetzt aus seinen Teilen geschrieben, in ÖBBs Reihenfolge wie in
  der mit den Zügen vorn (§1): „Due to construction works, the S2 train 23360
  cannot run between Wien Leopoldau station (U) and Mistelbach/Zaya station on
  25.10.2026 and 01.11.2026.“ (`_oebb_cannot_run_parts`). Nur die Ursache geht
  durch das Modell. Züge: „der S1-Zug 23626“ → „the S1 train 23626“, „der Zug
  RJ 650“ → „train RJ 650“, „keine Nahverkehrszüge … fahren“ → „no local
  trains can run“, „einige …“ → „some …“, „Fernverkehrszüge“ → „long-distance
  trains“. Strecken und Tage aus einer festen Menge: „zwischen A und B“ (auch
  „A bzw. B“ → „A or B“), „am …“ → „on …“, „von … bis …“ → „from … to …“,
  „bis voraussichtlich Ende 2027“ → „until probably the end of 2027“, „an
  einzelnen Tagen“ → „on certain days“, „(jeweils 08:45 Uhr - 14:45 Uhr)“ →
  „(each day 08:45 - 14:45)“. Steht etwas anderes da („in der Nacht von …“,
  „(jeweils Samstag, Sonntag und Feiertag)“), bleiben Züge und Verb fest, und
  nur Strecken und Tage gehen hinter einem Doppelpunkt durch das Modell. Ein
  vom Schnitt gekürzter Satz behält sein „…“. Ein Satz mit „Teilausfall:“
  vorn oder einem anderen Satzbau bleibt beim bisherigen Weg. Ein Wert im
  Übersetzungs-Cache ohne die feste Form gilt als Fehler und wird neu
  übersetzt (`_oebb_cannot_run_defect`, nur ÖBB, keine neue Epoche).
  Nachgespielt mit dem echten Modell über die 102 betroffenen ÖBB-Texte:
  98 ändern sich, alle lesen jetzt „cannot run“ oder „can run“; der
  Feed-Neubau vom 25.10. ändert nur S1 und S2.
* **„<Anlass>verkehr“ bei WL und ÖBB (seit 2026-10-30).** Das Modell kennt
  solche Komposita nicht und verliert den Kopf oder den Sinn: „38B/39B:
  Allerheiligenverkehr 01.11.2026“ stand als „38B/39B: All Saints' Worship
  01.11.2026“ im EN-Feed (Jules-Audit 2026-10-30), „Feiertagsverkehr“ wird
  „Public holidays“, „Konzertverkehr“ „Concertation“. Den Anlass allein
  übersetzt es richtig („Allerheiligen“ → „All Saints“, „Fronleichnam“ →
  „Corpus Christi“, „Silvester“ → „New Year's Eve“). Schreibt ein Betreiber
  über seine Linien, ist „Verkehr“ am Ende eines Kompositums sein Angebot.
  Darum ersetzt `_mask_service_compounds` (nach dem Glossar, vor dem
  Maskieren) in WL- und ÖBB-Texten jedes „<Anlass>verkehr“ (auch mit
  Fugen-s, Endung „-s“/„-es“ oder „-Verkehr“) durch einen Glossar-Platzhalter:
  den Anlass, allein durch das Modell übersetzt (`_service_modifier_en`, pro
  Lauf einmal je Anlass), plus „service“, also „All Saints service“. Ein
  gebeugtes Glossar-Kompositum („des Schienenersatzverkehres“) bekommt das
  Englisch seiner Grundform. Beim Modell wie bisher bleiben Komposita, deren
  erster Teil Straßenverkehr, ein Verkehrsmittel, ein Gebiet oder eine
  Tageszeit nennt, die das Modell allein falsch liest
  (`_SERVICE_COMPOUND_KEEP`: „Kreisverkehr“ → „roundabout“,
  „Gesamtverkehr“, „Individualverkehr“, „Pendelbusverkehr“ → „shuttle bus
  service“, „Straßenbahnverkehr“, „Regionalverkehr“, „Morgenverkehr“, weil
  „Morgen“ allein „Tomorrow“ wird). Ebenso ein Anlass, dessen Übersetzung
  leer, länger als vier Wörter oder noch deutsch ist. Baustellen-Texte
  handeln vom Straßenverkehr und bleiben unberührt. Ein Wert im
  Übersetzungs-Cache, dem das feste Englisch eines Anlasses fehlt, gilt als
  Fehler und wird neu übersetzt (`_service_compound_defect`, keine neue
  Epoche).
  Nachgespielt mit dem echten Modell über alle 281 WL- und ÖBB-Meldungen
  der Cache-Geschichte mit einem solchen Kompositum: Im Feed ändert sich nur
  der Titel „38B/39B: Allerheiligenverkehr 01.11.2026“ („All Saints'
  Worship“ → „All Saints service“); der Feed-Neubau vom 30.10. mit dem
  Übersetzungs-Cache ebenso, der deutsche Feed ist gleich.
* **Halte-Meldungen der WL.** Nach „Busse/Züge halten“ und „Ersatzbus hält“
  wählte das Modell die Präposition in jedem Lauf neu („Buses stop
  Salvatorianerplatz“, „Trains stop for lines 6 and 18“). Seit 2026-10-04
  (Jules-Audits vom 02. und 04.10., Epoche 21) schreibt
  `_normalise_stop_verbs` den deutschen Text vorher um: Ein Ort direkt nach
  dem Verb oder nach „bei“ bekommt „an“, das Glossar macht daraus „… stop
  at“; die Haltestelle einer anderen Linie („bei [der] Linie 18“, „Linien 6
  und 18“) wird „bei Haltestelle der Linie“ bzw. „bei den Haltestellen der
  Linien“ („at the stop(s) of line(s)“). Eine eigene Präposition („in“,
  „auf“, „am“, „vor“, „nach“, „gegenüber“, „Richtung“) bleibt dem Modell.
  „Züge/Busse halten nicht“ ist „… do not stop“. Seit 2026-10-09 (Epoche
  25) gilt das auch, wenn ein einzelnes Fahrzeug seine Linie trägt
  („Ersatzbus 26E hält Karl-Waldbrunner-Platz“, „Linie 26E hält …“,
  „Ersatzverkehr hält …“): Das Modell las „hält“ dort als „holds“
  („replacement bus 26E holds Karl-Waldbrunner-Platz ahead of …“, offener
  Punkt r). „hält an“ am Satzende ist das andere Verb („Die Störung hält
  an.“) und wird vorher zu „dauert an“ (`_PERSISTS_RE`), damit es nicht
  „stops at“ wird.
* **Nicht-übersetzbarer Inhalt.** `_is_non_translatable_content` erkennt
  maskierte Texte, in denen nach dem Maskieren nichts mehr steht, was ein
  Modell übersetzen könnte. Das sind reine Linien- und Stationsfolgen, seit
  2026-09-25 auch Linie plus Glossar-Begriff: „94A: Verkehrsunfall“ →
  `XENT…: XGLO…` → „94A: traffic accident“. Ein Glossar-Platzhalter steht
  bereits für den englischen Begriff. Solche Texte gehen nicht mehr ans
  Modell, das sie je nach Nonce verstümmelte. Die Umsteige-Kennungen hinter
  einem Haltestellennamen („Meidling Hauptstraße U“, „S U“) zählen dabei
  nicht als Wort (seit 2026-10-03).
* **Der Gedankenstrich der Ticker-Titel.** `_separate_reason_word` schreibt
  `31: Demonstration – Betrieb ab Wallensteinstraße`. Der Strich ist ein
  geschütztes Zeichen und stünde im Modell als Platzhalter zwischen zwei
  Wörtern — eine Form, die Marian mit Anhängsel oder verstümmelt zurückgibt
  (EN-Feed 19.09.2026: „Demonstration –Xservice from …"). Darum übersetzt
  `_translate_title_attempt` solche Titel in zwei Hälften (`_split_reason_title`:
  Linienpräfix mit Ursachenwort, dann das Fragment) und setzt den Strich
  wörtlich wieder ein. Nur der Titel-Pfad des Caches nimmt diesen Weg.
  Trenner und Zerlegung lesen dieselbe Wortmenge `_TITLE_REASON_WORDS`
  (geplante Arbeiten und Veranstaltungen plus Störungsursachen wie
  `Fremdunfall`); ein Wort, das nur der Trenner kennt, schickte den Strich
  wieder ins Modell.
Wie Titel und Beschreibung im deutschen Feed entstehen (Ticker-Titel,
„Ein Vorfall, ein Platz“, „Fahrtbehinderung <Ursache>“, „ÖBB-Ersatzbus“),
steht in §1 unter „Titel und Beschreibung im deutschen Feed“. Der EN-Feed
übersetzt das Ergebnis.

Weiter in [Teil 14](architecture-teil-14.md).
