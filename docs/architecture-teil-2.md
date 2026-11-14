[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 2.

### Titel und Beschreibung im deutschen Feed

Diese Regeln entscheiden, was ein Eintrag im deutschen Feed zeigt. Sie
laufen vor jeder Übersetzung; der EN-Feed übersetzt ihr Ergebnis (§8). Der
Feed läuft auf Info-Displays mit zehn Plätzen (`AGENTS.md`, „Priorität der
Ausgaben“): Ein Titel muss für sich stehen, und ein doppelter Eintrag
verdrängt eine andere Störung.

* **Linien im Titel in der Reihenfolge der Wiener Linien (seit 2026-10-25,
  Betreiberentscheidung).** Jedes Linienpräfix folgt der Spalte
  `SortingHelp` der offenen WL-Liniendaten
  (`data/wienerlinien-ogd-linien.csv`, vom wöchentlichen Stationslauf
  aktualisiert): U-Bahn, Straßenbahn (D, O und LB nach den Nummern), Bus A,
  Bus B, dann Nachtbus und S-Bahn gemeinsam nach der Nummer (S1 bis S4, N6,
  S7, …); ein Rufbus steht, wo WL ihn einordnet (25BR nach den
  Straßenbahnen, 41AR bei 41A). Teilen sich zwei Linien einen Rang (41A und
  41AR, N60 und S60), entscheidet ihr Code, damit dieselben Linien immer
  gleich dastehen (`line_order_key` in `src/providers/wl_lines.py`).
  Das gilt für alle vier Stellen, die ein Präfix bauen: den WL-Abruf
  (`_ensure_line_prefix`), den WL-Cache vor dem Bau (`_post_filter_wl`,
  auch für einen Titel aus der Zeit davor), die unscharfe Zusammenführung
  (`deduplicate_fuzzy`) und den Vorfall mit seinen Kurzmeldungen
  (`_union_prefix`, `_twin_labels`). Eine Linie, die die Daten (noch) nicht
  kennen, steht nach ihrer Form an ihrem Platz („U5“ bei der U-Bahn, „3“
  bei den Straßenbahnen); ÖBB-Züge und Ersatzbusse („REX 51“, „U6E“) stehen
  am Ende. Anlass: Am 25.10. stand „1/2/71/D: Veranstaltung“ (Vorfall,
  nach Nummer sortiert) neben „1/2/D/71: Nationalfeiertag“ (Hinweis in der
  Reihenfolge, die WL in `relatedLines` schickte); seit 26.09. ändert sich
  so das Präfix von 22 Titeln des WL-Abrufs, darunter „12/5: Albertgasse“, „56B/56A/N62:
  Strampfergasse“ und dieselbe Meldung einmal als „56A/56B/58A/58B“, einmal
  als „56B/58B/56A/58A“. Nachbau des WL-Abrufs aus 80 Rohdaten-Ständen seit
  26.09. und der Feed-Kette aus 182 Ständen: Es ändert sich nur die
  Reihenfolge im Präfix, Linien, Meldungen, GUIDs und Plätze bleiben gleich.
* **Ticker-Titel: Ursache oben, Folge darunter (seit 2026-09-25).** Bei einer
  WL-Störung behält der Titel nur Linie und Ursache, die Folge wandert an den
  Anfang der Beschreibung (`_finish_reason_title`): `14A: Rettungseinsatz` über
  `Betrieb ab Laxenburger Straße / Gudrunstraße [Am 25.09.2026]`. Die Ursache
  findet `_reason_and_fragment` auf zwei Wegen: über `_TITLE_REASON_WORDS` oder,
  für Ursachen außerhalb der Liste („Schadhafter Zug“, „Signalstörung“, „PKW im
  Gleis“), über den Anfang der Folge (`_CONSEQUENCE_START_RE`: „Betrieb
  ab/nur/über“, „Kein Betrieb“, „Züge/Busse halten“, „Umleitung“). Davor dürfen
  höchstens drei Wörter ohne Ziffer stehen. Nennt die Beschreibung die Folge
  schon, bleibt sie, wie sie ist. Wiederholt sie nur Ursache und Folge, ersetzt
  die Folge sie. Sonst steht die Folge vorn, und gekürzt wird hinter dem letzten
  passenden Satz der Beschreibung, sonst an einer Wortgrenze
  (`_last_sentence_end`, §13). Den Strich behalten Hinweise, deren zweite Hälfte
  ein Ort ist (`D: Gleisbauarbeiten – Althanstraße`), sowie Ticker, deren kurzer
  Titel unter den sichtbaren Items doppelt wäre. WL schickt zu einem Vorfall oft
  mehrere Ticker (`49: Gleisschaden`, `… Betrieb ab Urban-Loritz-Platz`, `…
  Betrieb ab Hütteldorfer Straße`), und drei gleiche Zeilen auf dem Display wären
  schlechter als drei lange (`_short_title_collisions`, entschieden in
  `_make_rss` für DE und EN gemeinsam). Trägt ein anderes sichtbares Item den
  kurzen Titel schon als eigenen, bleiben alle lang. Sonst wird der am höchsten
  platzierte Ticker kurz, die übrigen bleiben lang. Seit 2026-09-26 kommen solche
  Gruppen meist gar nicht mehr so weit (nächster Punkt).
* **Ein Vorfall, ein Platz (seit 2026-09-26, Betreiberentscheidung).** Am
  26.09. belegte die Linie 62 drei der zehn Plätze mit drei Tickern aus 86
  Sekunden („ÖBB Bauarbeiten Betrieb ab Kliebergasse“, „Züge halte bei
  Linie 18, Richtung Burggasse“, „ÖBB Bauarbeiten Kein Betrieb“). Rückschau
  über 693 Feed-Stände: 242 mit einer solchen Gruppe, zusammen 485 Plätze.
  `_merge_wl_ticker_clusters` legt WL-Störungen derselben Linien, die
  innerhalb von `WL_TICKER_CLUSTER_SECONDS` (600 s) nach der ersten
  erscheinen, zu einem Eintrag zusammen, nach der Duplikatprüfung und vor
  Sortierung und Platzvergabe (`main` und `lint`):
  - Ausführliche Meldung (seit 2026-09-27): Sagt eine ausführliche WL-Meldung der
    Gruppe mehr als den Standardsatz, steht sie mit Titel und Text für die
    Gruppe; die Kurzmeldungen belegen keinen eigenen Platz. Was Kurzmeldungen
    ankündigen und ihr Text nicht nennt, bleibt (`_with_consequences`, seit
    2026-10-01, Regeln unten bei „Eine Linie, ein Platz“): „Betrieb ab
    Mühlbreiten“ der 64A vom 01.10. stand sonst nirgends. Eine Tafel-Meldung
    anderer Ursache gibt nur ihre Maßnahme dazu, ihr Ursachenwort bleibt draußen
    (`_announced`, seit 2026-11-06; vorher fiel sie ganz weg: „12:
    Polizeieinsatz“ verlor am 05.11. „Betrieb ab Spitalgasse“ der Tafel
    „Verkehrsunfall“). Erkannt wird die ausführliche Meldung an ihren Sätzen
    (`_is_long_message`: Der Text endet mit einem Punkt, der nicht nur eine
    Abkürzung schließt, §13; im WL-Cache vom September gilt das für alle 504
    Texte in Sätzen und für keine der 247 Kurzmeldungen). Das Präfix „Linie 48A:“
    taugt dafür nicht, weil `_post_filter_wl` es beim Lesen des Caches entfernt
    (seit 2026-11-06 auch „Linie Badner Bahn:“, WLs Name der Linie LB, mit dem
    der Satz am 05.11. seine englische Vorlage verfehlte;
    `tests/test_wl_badner_bahn_prefix.py`) und manche Meldungen es nie hatten.
    Anlass: Am 27.09. stand „48A: Falschparker“ über „Grund: Fremder
    Verkehrsunfall“, und Kurzmeldungen schoben den zweiten Satz über 180 Zeichen.
  - Kurzmeldungen außerhalb des Fensters (seit 2026-10-01): Sie kommen
    über „Eine Linie, ein Platz“ (unten) zu ihrer ausführlichen Meldung
    und behalten, was sie zusätzlich sagen. Anlass: Am 30.09. kamen die
    Kurzmeldungen der Linie D um 04:00, die ausführliche Meldung um 04:30;
    „D: Gleisbauarbeiten“ stand bis zum 01.10. zweimal im Feed. Jetzt
    steht dort einmal „Kein Betrieb zwischen Börse und Augasse; Züge
    halten in Schleife, Wipplingerstr 39. Weichen Sie …“. Bei der 48A kam
    die Kurzmeldung um Mitternacht neu, die ausführliche Meldung um 21:47;
    jetzt steht „Shuttlebus eingerichtet, Abfahrtsstelle: Haltestelle
    Linie 46!“ hinter ihrer Maßnahme. Bis zum Abend des 01.10. verwarf
    eine eigene Zuordnung zur ausführlichen Meldung diese Kurzmeldungen
    samt ihrem Inhalt.
  - Kurzmeldungen einer erledigten Störung (seit 2026-10-05) kommen gar
    nicht erst hier an. WL schickt die erledigte Störung einen Abruf lang
    mit Status „resolved“, ihre Kurzmeldungen ohne Status bis zu deren
    eigenem Ende. Ohne die Störung zogen sie hier die erledigte Störung
    weiter in den Feed: „15A: Feuerwehreinsatz“ stand am 04.10. noch
    viereinhalb Stunden nach dem Abschluss, die Kurzmeldungen von „36B:
    Fremder Verkehrsunfall“ wurden am 05.10. ein neuer Eintrag auf Platz 1.
    `src/providers/wl_resolved.py` verwirft sie schon beim Abruf. Eine
    Kurzmeldung gehört zur jüngsten WL-Meldung ihrer Art auf ihren Linien:
    deren Linien umfassen die der Kurzmeldung (sonst aus dem Titel, die
    U6-Kurzmeldungen nennen keine), sie begann höchstens zehn Minuten nach
    der Kurzmeldung und von allen solchen Meldungen zuletzt, und beide sind
    geplante Maßnahmen oder beide nicht (`PLANNED_DISRUPTION_RE` im Titel,
    dieselbe Regel wie für „[Heute]“). Ist diese Meldung erledigt und begann
    die Kurzmeldung vor dem Ende der Störung, geht sie mit. Was die
    Kurzmeldung sagt, zählt seit 2026-10-08 nicht mehr: Vorher musste jedes
    ihrer Wörter in der Störung stehen, und „6: Rettungseinsatz züge halten
    Favoritenstraße 113“ stand am 08.10. noch vier Stunden nach dem
    Abschluss auf Platz 2, weil der Nachlauf keine Straße nennt. Eine
    jüngere laufende Störung einer anderen WL-Nummer auf denselben Linien
    behält ihre Kurzmeldungen („11, O: Fremder Verkehrsunfall“ ab 19:00
    neben der erledigten „74A, O: Verkehrsüberlastung“, 06.10.), eine
    Bauarbeiten-Kurzmeldung neben einer erledigten Störung bleibt („N49:
    Gleisbauarbeiten“ ab 01:00 während „N49: Verspätungen“, 06.10.; „66A:
    Bauarbeiten / Busse halten Salvatorianerplatz“ neben „66A: Störung an
    einem Bahnübergang“, 04.10.). Ist die erledigte Meldung eine
    Folgemeldung „Nach einer Fahrtbehinderung …“ (`…-F01`), endete die
    Störung, als WL sie anlegte; WL schließt sie erst 20 bis 90 Minuten
    später. Eine Kurzmeldung, die mehr als zwei Minuten nach der Anlage
    beginnt, gehört deshalb zu einer neuen Störung und bleibt („38A:
    Falschparker“, 05.10.: Nachlauf von 11:30 bis 12:59 Uhr).
    `scripts/update_wl_cache.py` merkt sich die Kurzmeldungen (Name, Beginn,
    Titel) in `data/wl_resolved_tickers.json`, bis WL sie entfernt.
  - Sonst ist der Titel Linie und die häufigste Ursache der Gruppe; ohne
    Ursache der Titel der ersten Meldung. Die Ursache kommt aus dem Titel
    (`_reason_and_fragment`), aus einer ausführlichen Meldung mit dem
    Standardsatz oder aus der ersten Zeile der Tafel („Gleisbauarbeiten /
    Betrieb ab Johnstraße U“). „Häufigste“ entscheidet nur noch zwischen
    WLs Namen derselben Ursache (`_CAUSE_SYNONYMS`): **Zwei Ursachen in
    einem Zeitfenster nennt der Titel beide (seit 2026-11-02,
    Betreiberentscheidung „Titel nennt beide Ursachen“).** Ein Zeitfenster
    ohne eigene ausführliche Meldung, das verschiedene Ursachen meldet,
    wird vor den Linien-Läufen nach Ursache geteilt (`_split_by_cause`);
    jede Ursache ist ein Vorfall des Laufs, und der Eintrag nennt jede, die
    neueste zuerst, wie zwei Vorfälle zehn Minuten auseinander (unten,
    „66A: Rettungseinsatz, Bauarbeiten“). Eine Meldung ohne Ursache bleibt
    bei der Ursache, die das Fenster bisher nannte; ist eine Ursache ein
    Satz (mehr als `_MAX_LISTED_WORDS` Wörter), bleibt das Fenster ganz.
    Anlass (Fund des KI-Wächters): Am 29.10. hatte die 71 zwei Vorfälle
    neun Minuten auseinander (I20261029-0032 Rettungseinsatz, -0034
    Polizeieinsatz), und der Feed las von 18:31 bis 18:59 Uhr „71:
    Rettungseinsatz“ über „Fahrtbehinderung wegen Polizeieinsatz.“, als wäre
    der Polizeieinsatz eine Folge. Jetzt: „71: Polizeieinsatz, Rettungseinsatz“
    über „Fahrtbehinderung.“ Sagen alle Vorfälle dieselbe Folge, steht sie
    einmal und ohne Ursache davor (`_incident_sentences`).
    Wer eine Ursache verursacht hat („durch einen LKW“), steht im Text, nicht
    im Titel (`_title_cause`, Betreiberentscheidung 2026-11-02): „27:
    Stromstörung, Signalstörung, Beschädigte Oberleitung“ über „Beschädigte
    Oberleitung durch einen LKW: Fahrtbehinderung.“ (27.10.). Ein Ort gehört
    zur Ursache und bleibt („PKW im Gleis“, „Störung an einem
    Bahnübergang“). Ein solcher Titel darf länger als 50 Zeichen sein: Jede
    Ursache bleibt im Titel.
    Ein Zeitfenster mit eigener ausführlicher Meldung bleibt ganz: WLs Text
    steht, die Tafel gibt ihre Maßnahme dazu (oben). Nur eine zweite ausführliche
    Meldung oder eine geplante Maßnahme neben einer Störung teilt es (`_another_incident`,
    seit 2026-11-06; am 30.10. fiel so die Baustellen-Haltestelle der N8 neben „N8: Feuerwehreinsatz“ weg).
  - Beschreibung: alle Folgen in der Reihenfolge, in der WL sie
    veröffentlicht hat, mit „;“ zu einem Satz verbunden, weil die
    Beschreibung höchstens zwei Sätze übernimmt. Was der Titel
    oder eine andere Meldung der Gruppe schon sagt, fällt weg; WLs
    Standardsatz „Nach einer Fahrtbehinderung …“ weicht allem Konkreten.
    Folgen mit denselben zwei ersten Wörtern nennen diese einmal
    (`_shared_openings`, seit 2026-10-01): „Busse halten Bessemerstraße 1-3,
    auf Hauptfahrbahn, Hoßplatz 11“. Eine Folge mit eigenem Komma bleibt für
    sich, sonst läse sich ihr zweiter Teil als weitere Haltestelle.
  - Der Eintrag behält GUID, Identität und Beginn der zuerst
    veröffentlichten Meldung und bekommt das späteste Ende; ein offenes Ende
    bleibt offen. Nicht der früheste Beginn: WL verwendet manchmal eine alte
    Tafel-Meldung für einen neuen Vorfall, und am 27.09. stand deshalb
    „23.09.2026 – 27.09.2026“ über einem Schaden vom selben Morgen.
  - `62: ÖBB Bauarbeiten` über „Betrieb ab Kliebergasse; Züge halte bei
    Linie 18, Richtung Burggasse; Kein Betrieb.“
  - Grenzen: Andere Linienmengen („62/18“, Ausnahmen unten), Hinweise und andere Quellen
    bleiben getrennt. Die Beschreibung ist auf 180 Zeichen begrenzt und wird
    an der Wortgrenze gekürzt, nicht am Satzende (Betreiberentscheidung
    2026-11-05, `changelog/2026-11-05.md`). Die Störungsstatistik zählt
    einen zusammengelegten Vorfall einmal.
  - Eine Ausnahme bei den Linienmengen (seit 2026-10-02): Zwei
    Kurzmeldungen verschiedener Linien mit gleichem Titeltext und gleichem
    Text im selben Fenster verbinden ihre Gruppen (`_join_twin_groups`).
    Der Eintrag steht unter allen Linien; eine Folge, die nur ein Teil der
    Linien meldet, behält deren Kürzel. Anlass: Am 02.10. um 01:00:12 kam
    „Busse halten Laxenburger Straße 66“ für N65 und für N66 und belegte
    zwei der zehn Plätze. Jetzt steht dort „N65/N66: Bauarbeiten“ über
    „Busse halten Laxenburger Straße 66; N66: Busse halten
    Salvatorianerplatz.“ Eine Gruppe mit ausführlicher Meldung verbindet
    sich nur mit einer Gruppe, die Meldung für Meldung dasselbe sagt (seit
    2026-10-03): WL schickt auch die ausführliche Meldung einmal je Linie,
    etwa „6: Schadhaftes Fahrzeug“ und „18: Schadhaftes Fahrzeug“, beide mit
    „Nach einer Fahrtbehinderung kommt es zu unterschiedlichen
    Intervallen.“, oder viermal „Störung an einem Bahnübergang“ für 16A,
    17A, 67A und 67B. Nachgerechnet über 3.875 Feed-Stände seit 15.07.
    standen solche Zwillinge in 540 Ständen und nahmen 808 Plätze, die
    anderen Störungen fehlten; nachher sind es 9. Jetzt steht
    „6/18: Schadhaftes Fahrzeug“ da. Unterscheidet sich eine Meldung,
    bleiben die Gruppen getrennt.
  - Eine zweite Ausnahme (seit 2026-10-15): Kurzmeldungen einzelner Linien
    eines Vorfalls mehrerer Linien gehen in dessen ausführliche Meldung auf
    (`_join_part_line_tickers`). WL schreibt eine ausführliche Meldung für
    alle Linien, aber eine Tafel-Kurzmeldung je Linie, und die Gruppen
    oben trafen sich nie. Am 11.10. von 11:30 bis 23:30 Uhr belegte ein
    Gasrohrbruch in der Brünner Straße so vier der zehn Plätze:
    „30A/31A/36B: Gasrohrgebrechen“ und daneben „30A: Gasrohrbruch“,
    „36B: Gasrohrbruch“ und „31A: Gasrohrbruch“ mit „Kein Betrieb.“, während
    die ausführliche Meldung sagt, dass die 31A umgeleitet wird. Der
    Provider legt eine Kurzmeldung nur dann in die ausführliche Meldung,
    wenn diese jedes ihrer Wörter wiederholt
    (`wl_fetch._fold_display_tickers`), und die Tafel sagt „Gasrohrbruch“,
    die Meldung „Gasrohrgebrechen“. Eine Kurzmeldung gehört zur
    ausführlichen Meldung, wenn deren Linien ihre umfassen, beide dieselbe
    Ursache nennen (mit WLs Synonymen, `_CAUSE_SYNONYMS`, jetzt auch
    „Gasrohrbruch“ = „Gasrohrgebrechen“), sie höchstens zehn Minuten vor ihr
    begann (wie `wl_resolved.TICKER_LEAD`) und sich die Gültigkeiten
    überschneiden; von mehreren gehört sie der zuletzt begonnenen. Eine
    Kurzmeldung anderer Ursache behält ihren Platz („43: Falschparker“ neben
    „43/44: Rettungseinsatz“, 09.10.), eine ohne Ursache ebenfalls. Spricht
    der Text der ausführlichen Meldung von der Linie („Die Linie 31A wird …
    umgeleitet“), steht dort WLs eigene Aussage, und die Kurzmeldung einer
    einzelnen Haltestellentafel fügt nichts hinzu; „31A: Kein Betrieb“
    hätte ihr auf dem Bildschirm widersprochen. Das gilt auch für die Linie,
    deren „Linie 2:“ das Einlesen vor der ersten Maßnahme gestrichen hat
    (`_stripped_label_line`, seit 2026-10-25, Nachprüfung: am 19.10. hängte
    „2/10: Fremder Verkehrsunfall“ hinter die Umleitung der 2 noch „2:
    Betrieb ab Mayssengasse; Züge halten bei Linie 9.“). Was die
    Kurzmeldung einer Linie ankündigt, die der Text nicht nennt, bleibt mit dem Linienkürzel stehen, platziert wie bei `_with_consequences` („14A: Umleitung über
    Gumpendorfer Str., Linke Wienzeile.“ vor WLs Standardsatz zu
    „13A/14A: Falschparker“, 06.10.). Die Kurzmeldungen werden Mitglieder
    des Eintrags (`_members`), damit GUID und Beginn bleiben, wenn die
    ausführliche Meldung zuerst endet. Nachgerechnet über alle Feed-Stände
    seit 15.07. (4.510 Stände): In 26 ändert sich etwas, alle am 06.10. und 11.10.; 24 davon sind der Gasrohrbruch (drei Plätze frei), je einer „37/38/40/41/42: Schadhaftes Fahrzeug“ (zwei Plätze frei) und „13A/14A: Falschparker“ (einer). Sonst bleibt jeder Stand gleich. Zwei Lücken geschlossen am 2026-11-10: Teil 15, §11.

Weiter in [Teil 3](architecture-teil-3.md).
