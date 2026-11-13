[Entwicklerdokumentation – Wien ÖPNV Feed](development.md), Teil 3.

## Provider-spezifische Workflows

Der Meldungsfeed sammelt offizielle Störungs- und Hinweisinformationen der Wiener Linien (WL) und der ÖBB sowie ergänzende Baustelleninformationen der Stadt Wien. Die VOR/VAO-API liefert seit 2026-05-11 keine Meldungen mehr, sondern nur noch die Messwerte des Stammstrecken-Monitors (Abschnitt unten).

### Wiener Linien (WL)

- **Anforderung**: "Alle Meldungen sind interessant." (Die Wiener Linien sind per Definition Wien-fokussiert).
- **Umsetzung**: Der Provider verarbeitet sämtliche Meldungen der Realtime-Schnittstelle (`trafficInfoList` mit `stoerunglang`/`stoerungkurz`, dazu `newsList`). Verworfen werden beim Abruf nur: inaktive Meldungen (seit 2026-10-05 auch Status „resolved“: so liefert WL eine erledigte Störung noch einen Abruf lang mit, vorher stand ihr alter Text bis zu 30 Minuten im Feed und verdeckte, wenn es eine gab, die aktive Folgemeldung `…-F01`), die Anzeigetafel-Kurzmeldungen (`stoerungkurz`) einer solchen erledigten Störung, solange WL sie noch schickt (seit 2026-10-05, `src/providers/wl_resolved.py`: die Kurzmeldung gehört zur jüngsten Meldung ihrer Art auf ihren Linien, die höchstens zehn Minuten nach ihr begann – geplante Maßnahme oder nicht nach `PLANNED_DISRUPTION_RE` im Titel –, und begann vor dem Ende der Störung; ihre Wörter zählen seit 2026-10-08 nicht mehr, vorher blieb etwa „6: Rettungseinsatz züge halten Favoritenstraße 113“ vier Stunden nach dem Abschluss im Feed; eine jüngere laufende Störung einer anderen WL-Nummer auf denselben Linien behält ihre Kurzmeldungen; ist die erledigte Meldung eine Folgemeldung „Nach einer Fahrtbehinderung …“ (`…-F01`), endete die Störung, als WL sie anlegte, und eine Kurzmeldung, die mehr als zwei Minuten danach beginnt, gehört zu einer neuen Störung; Kurzmeldungen haben keinen Status und liefen bis zu viereinhalb Stunden weiter; gemerkt über die Läufe in `data/wl_resolved_tickers.json`), Meldungen mit reinem Aufzug-/Fahrtreppen-Titel (seit 2026-10-09 zählt auch die Klapprampe der Züge dazu, Betreiberentscheidung „Auch verwerfen“: „U1: Klapprampensperre am 10.10.2026“, WLs einzige Schreibweise seit Juli; die Züge fahren normal), Meldungen über einen gesperrten Aufgang, Ausgang oder Zugang einer Station (seit 2026-10-09, Betreiberentscheidung „wie ein defekter Aufzug“; `is_station_access_only` in `src/utils/text.py`: der Titel nennt einen Aufgang, Ausgang, Zugang, Eingang oder Stiegen-/Treppenabgang, auch im Wortinneren wie „Aufgangssperre“, und keine Maßnahme für die Linie selbst wie „kein Halt“, „fahren durch“, „Ersatzverkehr“, seit der Prüfung vom 2026-10-09 auch die Zugwörter der ÖBB wie „Zugausfall“, „Verspätung“, „Zugangebot“, „eingleisig“, „Streckensperre“; vorher stand „U1: Nestroyplatz, Aufgangssperre“ ab 2026-10-09 im Feed, oft auf Platz 1 oder 2; ÖBB verwirft solche Titel ebenso, bisher schickte ÖBB keine; der Titel einer Haltestellenverlegung oder -auflassung zählt seit 2026-11-07 weder hier noch bei den Aufzügen, denn er nennt die Haltestelle und kein Thema – `is_stop_notice` in `src/providers/wl_text.py`, der Text beginnt mit „Haltestellenverlegung“ oder „Haltestellenauflassung“: „68A: Laaer Berg, Kurpark Nordeingang“, Verlegung ab 05.11.2026 für etwa zwei Wochen, fiel vom 04.11. 14:01 bis 07.11. 18:01 Uhr Wiener Zeit in jedem Abruf (148 gespeicherte Rohdaten-Stände) als „nur Aufgang/Ausgang“; WLs Aufzugs- und Fahrtreppen-Einträge der `newsList` fallen seit demselben Tag am Feld `elevatorId` (`_is_lift_record` in `src/providers/wl_title_context.py`; Status „geplante Wartung“, Titel ist der Stationsname wie „Praterstern“, Text der Standort des Aufzugs) und nicht erst am fehlenden Einschränkungs-Stichwort: 115 solche Titel seit 04.10., keiner kam durch, aber nur, weil kein Text ein Stichwort trug), Meldungen außerhalb ihres Zeitraums, Störungen mit Ausschluss-Stichwort (`KW_EXCLUDE`: „Gewinnspiel“, „Eröffnung“, „Info“ …) ohne Einschränkungs-Stichwort, und Hinweise (`newsList`), deren Text kein Einschränkungs-Stichwort trägt (`KW_RESTRICTION` in `src/providers/wl_text.py`: Wortstämme wie „sperr“, „umleitung“, „kurzführung“ und seit 2026-10-04 auch die Maßnahme als Verb: „kurz geführt“, „umgeleitet“, „durchfahren“, „kein Halt“, „kein Betrieb“, „eingestellt“, „entfällt“; seit 2026-10-04 außerdem „verleg“ und „auflass“/„aufgelassen“: jede Haltestellenverlegung und -auflassung kommt in den Feed, Betreiberentscheidung „Alle aufnehmen“ vom 2026-10-04 – vorher kam sie nur durch, wenn ihr Grund „…arbeiten“ hieß, 13 von 37; ausgenommen seit 2026-11-07 eine Netzänderung, Betreiberentscheidung „Als Hinweis aufnehmen“: WL setzt die Art einer Langmeldung als Überschrift an den Textanfang, und `is_network_change` in `src/providers/wl_news_kind.py` liest „Netzänderung“ dort – „9A: Errichtung einer Haltestelle“ (neue Haltestelle ab 07.09.2026) und „57A: Errichtung einer Haltestelle“ (ab 05.09.) fielen in jedem Abruf vom 04. bis 07.10. am fehlenden Stichwort, denn eine Netzänderung schränkt nichts ein; sie steht ohne Ende im Feed (`_notice_end` in `wl_fetch.py`, auch wenn ihr Text ein Stichwort trägt wie „N67: Geänderte Linienführung ab 05.12.2026“ mit „entfällt“: WLs `time.end` ist das Ende des Hinweises, Wochen nach der Änderung – 9A ein Monat, N67 zehn Tage –, nicht der Maßnahme; wann der Hinweis den Feed verlässt, bestimmt weiter WLs Ende), und ihren Beginn nennt WL unter „Zeitpunkt:“ statt „Zeitraum:“ (`_PERIOD_HEADING_RE`), sonst hätte sie ab dem Tag der Veröffentlichung gegolten). Die Stichwörter prüfen den Text ohne HTML (`_gate_text` in `src/providers/wl_ticker_fold.py`): WL liefert die Beschreibungen der Hinweise als HTML mit Umlauten als Entities („gef&uuml;hrt“), vorher traf darin kein Stichwort mit Umlaut. Beides zusammen hielt „18: LCC-Herbstmarathon am 11.10.2026“ und „U6: Neue Donau, kein Halt Richtung Floridsdorf“ aus dem Feed (Rohdaten vom 2026-10-04). Welche Meldung warum verworfen wurde, steht in `data/raw/wl/verworfen.json`; eine Klapprampe steht dort seit 2026-10-10 mit dem Grund „nur Klapprampe“, vorher als „nur Aufzug/Fahrtreppe“. Eine explizite Geo-Filterung ist nicht notwendig und findet nicht statt.
- **Quelle**: Realtime-Störungs-Endpoint (`WL_RSS_URL`, Default: `https://www.wienerlinien.at/ogd_realtime`).
- **Item-Link**: Jede WL-Meldung verlinkt seit 2026-10-17 auf die Betriebsinfo-Seite `https://www.wienerlinien.at/betriebsinfo` (`WL_INFO_URL` in `src/providers/wl_api.py`). Vorher stand dort die Adresse der Schnittstelle, die ein Browser mit 404 beantwortet (Live-Prüfung vom 17.10.). Eine eigene Seite je Meldung gibt es bei den Wiener Linien nicht; GUID, `first_seen` und die Reihenfolge ändern sich nicht. Seit demselben Tag prüft der Feed für jede Quelle, dass der Link beim Betreiber bleibt, und nennt die Quelle im Element `<source>` (`_ITEM_SOURCES`, [Architektur](architecture.md)).
- **Cache**: `cache/wl/events.json`.
- **Titel-Präfix**: Der Feed-Build parst gecachte Titel bei jedem Lauf neu (`_post_filter_wl`) und setzt die Linien aus `relatedLines` als `L1/L2:`-Präfix davor (`src/providers/wl_lines.py`). Wiederholt der Titeltext die Linienliste selbst (`4A. 80A, N29: …`, `N66, Rufbus N68: …`), wird sie in das Präfix gefaltet — Trenner `/`, `+`, `,` und `.` mit folgendem Leerraum, damit `13.10:` keine Linienliste ist; `Rufbus N68` im Text und `N68R` aus `relatedLines` gelten als eine Linie.
- **Störung ohne Linie**: `_post_filter_wl` verwirft eine Störung, deren Titel keine erkennbare Linie trägt (`Sperre Bahnsteig Richtung Siebenhirten`). Als Linie gilt ein Code mit Ziffer (`13A`, `N66`, auch die Rufbus-Form `44BR`), ein einzelner Buchstabe (`D`) oder seit 2026-10-03 die Badner Bahn `LB` (bis Juni 2026 `WLB`). Vorher fiel jede Badner-Bahn-Störung samt den Straßenbahnen desselben Vorfalls aus dem Feed: 15 verschiedene Störungstitel seit 15.07., darunter `1/18/62/LB: Signalstörung` am 10.09.
- **Hinweis ohne Text**: `_post_filter_wl` hält einen WL-Hinweis zurück, dessen Text leer ist (`<p></p>`), bis WL ihn füllt; eine Störung ohne Text bleibt (Betreiberentscheidung 2026-11-07, `docs/architecture-teil-5.md`, „WL-Hinweis ohne Text“).
- **Titel beim Bündeln**: Meldungen gleicher Kategorie, gleichen Themas und gleicher Linien bündelt der Abruf zu einem Eintrag; den Titel stellt die informativste Fassung (`_title_quality_key` in `src/providers/wl_title_context.py`). Ein von der Anzeigetafel abgeschnittener Titel – er endet auf eine Präposition ohne Objekt („Züge halten gegenüber“, `INCOMPLETE_TITLE_TAIL_RE`) – verliert seit 2026-11-07 gegen jeden vollständigen Titel. Vorher gewann am 24.10.2026 um 20:31 Uhr die Kurzmeldung „Demonstration Züge halten gegenüber“ (R45-101) über die ausführliche Meldung „Demonstration“ der Linie 1, und `_post_filter_wl` verwarf den ganzen Eintrag wegen des hängenden „gegenüber“: Die Demonstration auf der Linie 1 fehlte in diesem Feed-Stand. Im Replay über 1 591 Rohdatenstände seit 04.10. ändert sich nur dieser Stand (Titel „1: Demonstration Betrieb ab Resselgasse“ mit dem Text der ausführlichen Meldung); eine abgeschnittene Kurzmeldung ohne vollständige Meldung im Bündel fällt wie bisher in `_post_filter_wl` weg („2: Verkehrsunfall Züge halten gegenüber“, 31.10. 15:01).
- **Zusammenlegen im Build**: `deduplicate_fuzzy` legt im Build Meldungen überlappender Linien mit ähnlichem Namen zusammen; der Überlebende ist der Eintrag mit der kleinsten Identität, bei WL stets der Hinweis („wl|hinweis|…“ vor „wl|störung|…“). Seit 2026-11-07 hält `_may_merge` (`src/feed/merge_items.py`) drei Paare auseinander: Angekündigtes (Beginn nach dem Vorschautag von `_defer_upcoming_items`) nur mit Angekündigtem; eine Haltestellenverlegung oder -auflassung (`is_stop_notice`) nie mit einer Störung; ein Hinweis nie mit einer laufenden Störung, deren Titel und Text keine geplante Maßnahme nennen (`names_planned_measure`). Geplante Maßnahmen (Bautafeln, Demonstrationen, Veranstaltungen) gehen weiter im Hinweis ihrer Arbeiten auf (Betreiberentscheidung 2026-11-03). Fälle und Replay-Zahlen: [Architektur](architecture.md), „Nach der Dedupe“, und `changelog/2026-11-07.md`.
- **Sammel- und Teilmeldungen**: Nach der Bündelung entfernt der Provider eine Meldung für mehrere Linien, wenn jede ihrer Linien eine eigene Meldung derselben Kategorie mit überlappendem Zeitraum hat (E), und eine Meldung, deren Linien in einer solchen Meldung für mehr Linien stecken (F) — seit 2026-10-03 nur noch, wenn jedes Wort ihres Titels (ohne die Liniennummern) im Text der anderen steht oder sie (nur bei F) in Titel bzw. erster Zeile deren Thema nennt („37: Betrieb ab Nußdorfer Straße“ mit „Gleisbauarbeiten“ neben „5/12/37/…: Gleisbauarbeiten“). Vorher zählten nur Linien, Kategorie und Zeitraum: „2A: Bauarbeiten Renngasse“ fehlte zehn Tage, solange die Regenbogenparade auf 2A angekündigt war; seit April traf das 108 Meldungen. Eine Haltestellenverlegung oder -auflassung entfernen E und F nur, wenn die andere Meldung dieselbe Haltestelle betrifft (`_covers_stop_notice`, seit 2026-10-04): Ihr Titel ist nur die Haltestelle, und andere Meldungen nennen sie als Richtung („in Richtung Oper, Karlsplatz“) – so nahm die Verlegung Schellinggasse die von „3A: Oper, Karlsplatz“ mit. Eine Anzeigetafel-Kurzmeldung deckt nie eine ausführliche Störungsmeldung (mit WL-Nummer `I…`) ab (`_may_cover`, seit 2026-10-07): Ihr Titel ist die Ursache, die auch im Titel der ausführlichen steht, und so entfernte E „10, 60: Polizeieinsatz“ samt Maßnahmen und Dauer, weil zwei Kurzmeldungen „Polizeieinsatz“ nannten; der Feed zeigte 10 und 60 auf zwei Plätzen.

### ÖBB

- **Anforderung**:
  1. Pendlerbahnhöfe mit gestörter Verbindung nach Wien.
  2. Wien nach Pendlerbahnhof.
  3. Innerhalb von Wien (alle Störungen).
- **Umsetzung**: Der Provider implementiert einen **strengen Geo-Filter** (`_is_relevant`):
  - Nennt die Meldung Strecken („zwischen A und B“, „A ↔ B“), muss mindestens eine davon Wien ↔ Wien oder Wien ↔ Pendlerbahnhof sein; Wien ↔ fern, Pendler ↔ Pendler und Strecken mit unbekanntem Endpunkt fallen weg. Ein Endpunkt, der zwei Bahnhöfe nennt („Wien Hbf/Wien St. Marx ↔ Wien Praterstern“, „zwischen Wien Hbf bzw. Wien St.Marx und Wien Praterstern“), zählt seit 2026-11-06 als eine Strecke je Bahnhof (`_endpoint_alternatives` in `src/providers/oebb_routes.py`) – nur wenn der Name als Ganzes nicht auflöst und jeder Teil ein Bahnhof der Eisenbahn ist, nicht nur eine Haltestelle der Wiener Linien oder des VOR („bzw.“ und „oder“ trennen immer, der Schrägstrich nur einen Teil, der als Ganzes nicht auflöst: „Tulln/Donau bzw. Krems/Donau“ sind zwei Bahnhöfe, „Bruck/Leitha“ bleibt einer; überlange Endpunkte aus den Alternativ-Texten und Straßenbahnhaltestellen werden nicht geteilt). Vorher galt die Strecke als Wien ↔ unbekannt, und die Sperre der Stammstrecke Wien Hbf/Wien St. Marx ↔ Wien Praterstern (07.09.2026 bis Oktober 2027) fehlte zwei Monate im Feed.
  - Ohne erkennbare Strecke reicht ein Wiener oder Pendler-Bahnhof im Text, solange kein ferner Bahnhof mitgenannt ist (so kommen „Polizeieinsatz in Mödling Bahnhof“ oder „Weichenstörung in Hollabrunn Bahnhof“ in den Feed, Anforderung 1); zuletzt prüft eine Text-Heuristik auf Wien-Bezug (U-Bahn usw.).
  - Bahnhöfe im Text sucht ein gleitendes Fenster über bis zu vier Wörter (`_find_stations_in_text`). Ein solches Bruchstück zählt seit 2026-10-05 nur über einen Stationsnamen oder Text-Alias mit mindestens drei Buchstaben, nie über einen Code oder eine ID (`station_named_in_text` in `src/utils/stations_lookup.py`). Vorher wurde „Stellwerkstörung am Bahnhof“ zur WL-Haltestelle „Am Bahnhof“, die Zugnummer „1730“ zur WL-Haltestellen-ID von „Klinik Hietzing“ und „Update 2 (…)“ zur Haltestelle „Venediger Au“; so kamen Störungen in Wolfurt, Ebensee, Hinterstoder, Telfs-Pfaffenhofen und Lind-Rosegg als Wien-Meldungen in den Feed (sechs seit Juli). Dieselbe Regel gilt für den Titel-Check `_title_has_unknown_endpoint` und für die Ortssuche der Statistik.
  - Bahnhofsnamen löst `station_info` (`src/utils/stations_lookup.py`) auch in ÖBB-Schreibweise auf: „Bruck/Leitha“ als „Bruck an der Leitha“, und seit 2026-10-04 einen Ortsnamen mit Fluss-, Regions- oder Landeszusatz („Mistelbach/Zaya“, „Wolkersdorf im Weinviertel“, „Traisen NÖ“) als den bloßen Ort, sofern dieser außerhalb Wiens liegt. Vorher galt „Wien Leopoldau ↔ Mistelbach/Zaya“ (S2) als Wien ↔ unbekannt und fiel weg.
  - Meldungen, die *nur* ferne Bahnhöfe erwähnen, werden verworfen, ebenso eine Strecke zwischen zwei Pendlerbahnhöfen („Pfaffstätten ↔ Bad Vöslau“, siehe [data/stations.json](https://github.com/Origamihase/wien-oepnv/blob/main/data/stations.json) für Definitionen von `in_vienna` und `pendler`). Ein einzelner Pendlerbahnhof ohne Strecke genügt dagegen (oben).
  - Vor dem Geo-Filter fallen Meldungen weg, deren Titel nur eine Anlage der Station betrifft (`_is_facility_or_weather_only` in `src/providers/oebb_scope.py`): jeder Titel mit Aufzug-/Lift-/Fahrtreppen-Wort, seit 2026-10-09 auch ein gesperrter Aufgang, Ausgang, Zugang oder Eingang (`is_station_access_only`, wie bei WL), außer der Titel nennt zugleich, was die Züge tun („Zugausfall“, „Verspätungen“, „kein Halt“, „Schienenersatzverkehr“ …). Reine Wetterwarnungen ohne Störungswort im Titel fallen ebenso weg; „Sturmschaden: Strecke Wien-Mödling gesperrt“ bleibt. Den Grund jeder verworfenen ÖBB-Meldung hält `data/raw/oebb/verworfen.json` fest (`_drop_reason`), seit 2026-10-10 mit denselben Etiketten wie bei WL: „nur Aufzug/Fahrtreppe“, „nur Klapprampe“, „nur Aufgang/Ausgang“, dazu „nur Wetterwarnung“ und für den Geo-Filter „nicht Wien-relevant“; vorher stand dort für jede verworfene Meldung „nicht Wien-relevant“, auch für den defekten Personenlift in Wien Leopoldau.
  - Eine Meldung über einzelne Züge bleibt seit 2026-11-11 aus dem Feed (Betreiberentscheidung vom 2026-11-11: „Einzelzüge sollen nicht in den Feed.“, zur Karte „Sollen auch einzelne ausfallende ÖBB-Züge wie Sperren vor den WL-Hinweisen stehen?“ mit der Antwort „Nein, nur echte Sperren“; `_oebb_single_trains_only` in `src/feed_build/oebb_single_trains.py`, beim Lesen des Caches in `_post_filter_oebb` in `src/feed_build/cache_filters.py`). Ihr Leitsatz, der Satz, den der Feed zuerst zeigt (`_oebb_shown_sentences`), nennt die Züge mit Nummer („der S1-Zug 23626“, „der Zug RJ 263“, „die S80-Züge 2590, 25080, …“, „die Züge REX6(7777), S60(26085) …“, „die Züge REX 1 (2334, 2338 und 2342)“) oder als „einzelne“ Züge, und kein Satz der Meldung sagt, dass keine Züge fahren oder halten (`_oebb_closes_line`): „keine REX50-Züge fahren“ mit einer Liste von REX51- und S50-Zügen dahinter bleibt. „Einige“ Züge ohne Nummer bleiben im Feed (Betreiberentscheidung vom 2026-11-12 zur Karte „Zählen ÖBB-Meldungen über „einige Züge“ ohne Zugnummer auch als Einzelzüge?“: „Nein, sie bleiben im Feed“; „einige Nahverkehrszüge … nicht fahren“ zwischen Wien Hbf und Gramatneusiedl, Götzendorf und Bruck an der Leitha vom 08.03. bis 22.06.2026, „einige REX 1-Züge … können in Wien Mitte-Landstraße, Wien Praterstern … nicht halten“): Sie sagen, dass auf dem Abschnitt weniger Züge fahren, oft wochenlang; eine Zugnummer sagt dem Zuschauer nichts. Eine Linie in der Mehrzahl ist keine Zugnummer („Für die Züge REX 63 und REX 64 haben wir … einen Autobus organisiert“): Regionallinien haben ein- oder zweistellige Nummern, ihre Züge drei- bis fünfstellige; Fernzüge haben hinter ihrer Gattung auch zwei Stellen („RJX 62“). Jeder ÖBB-Leitsatz mit Zugnummern vom 01.05. bis 11.11.2026 nannte Tage, er kündigte Bauarbeiten an; eine ungeplante Störung nennt ihren Abschnitt („Zugfahrten nur eingeschränkt möglich“) und bleibt. Die Meldung bleibt im ÖBB-Cache und in den Rohdaten; `verworfen.json` nennt sie nicht, denn der Abruf verwirft sie nicht. Von den 688 ÖBB-Texten vom 01.05. bis 11.11.2026, die der Wien-Filter heute durchlässt (Cache und heutiger Abruf über die Rohdaten seit 04.10.), trifft die Regel 22, Fassungen von 13 Meldungen: S1 23626, S2 23360, S60 26093 (06.09.), RJ 263 Hütteldorf ↔ Hbf, RJ 820, RJ 12265, D 347, die IC- und D-Züge Westbahnhof ↔ Hütteldorf vom 03. bis 08.06., REX 1 (2334, 2338 und 2342), vier Züge St. Pölten ↔ Hütteldorf (25./26.07.), elf S-Bahn-Züge Liesing ↔ Meidling (02.08.), REX6 7799 mit S60 26093 (02.08.) und die Züge Hbf ↔ Gramatneusiedl vom 03. bis 05.10.; die 24 Texte über „einige“ Züge bleiben, der letzte verließ den Cache am 31.08. Im Nachspielen der echten Kette über 1.220 Feed-Stände vom 01.10. bis 11.11.2026 (Rohdaten ab 04.10., davor die Caches der Stände) ändern sich gegen die Regel „nur echte Sperren“ allein 292: S1 23626 fehlt in 202 Ständen, S2 23360 in 245, RJ 263 in 44, an ihre Stelle rücken WL-Hinweise („64A/N64R: Erlaaer Spitz“, „11A/78A: Straßenbauarbeiten“, „35A/N35R/N35: Obersteinergasse“ …); keine aktuelle Störung und keine echte Sperre verlässt die ersten zehn.
  - Mit `OEBB_ONLY_VIENNA=1` lässt sich der Fallback auf reine Pendler-Bahnhof-Routen abschalten — siehe [`docs/reference/oebb_provider_logic.md`](reference/oebb_provider_logic.md).
- **Quelle**: Offizielle ÖBB-Störungsinformationen (RSS-Feed; Default-URL via `OEBB_RSS_URL` überschreibbar, validiert gegen die `fahrplan.oebb.at`-Allow-List).
- **Cache**: `cache/oebb/events.json`.
- **Routentitel**: Aus „zwischen A und B" bzw. „von A nach B" leitet der Provider `A ↔ B`-Titel ab. Ein großgeschriebener Ortszusatz mit „im"/„am" direkt vor `Bahnhof`/`Bf`/`Hbf` („Baumgarten im Bgld-Schattendorf Bahnhof") bleibt Teil des Endpunkts (`_with_place_qualifier`), sonst schrumpft der Endpunkt auf ein Wort und löst auf eine falsche Wiener Haltestelle auf. Mehrere Strecken einer Meldung werden zur Kette „A ↔ B ↔ C“, wenn sie sich zu einer Linie verbinden und jede Zwischenstation auf dem Weg zwischen ihren Nachbarn liegt (Umweg höchstens 20 %, Koordinaten aus `data/stations.json`; seit 2026-11-06, `_chain_is_corridor` in `src/providers/oebb_route_title.py`); sonst steht die Station, an der alle Strecken enden, einmal vorn („Wien Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx“, `_try_star_routes`, seit 2026-11-06 schon bei zwei Strecken); Strecken ohne gemeinsame Station stehen einzeln mit „ / “. Vorher reichte die Form des Graphen, und zwei Strecken mit gemeinsamem Ende lasen sich als Weg durch dieses Ende („Wien Hauptbahnhof ↔ Wien Praterstern ↔ Wien St. Marx“). Unbekannte Koordinaten lassen die Kette stehen. Statt einer Haltestelle der Wiener Linien vor einem Bahnhof („Wien Bhf. Kaiserebersdorf (WL)“) nennt der Titel den Bahnhof („Wien Kaiserebersdorf“), auch wenn er beide Namen fand (seit 2026-11-08, `_same_place_names`; welche Haltestelle zu welchem Bahnhof gehört, sagt die Alias-Tabelle des Verzeichnisses, `railway_station_of` in `src/utils/stations_lookup.py`).
- **Update-Meldungen**: ÖBB veröffentlicht laufende Störungen als „Update N (TT.MM.JJJJ hh:mm) Kategorie: Ort". Das Präfix wird entfernt (`_strip_update_prefix`), die Kategorie wie bei jedem ÖBB-Titel verworfen — außer bei Entwarnungen: „Aufhebung …: Ort" bleibt vollständig stehen.

### Verkehrsverbund Ost-Region (VOR)

- **Anforderung**: VAO-Tagesbudget (100 Requests/Tag) wird seit 2026-05-11 ausschließlich vom S-Bahn-Stammstrecken-Monitor verbraucht. Seit der 2026-05-15-Migration auf `/departureBoard` am Wien Hauptbahnhof sind das **48 Calls/Tag** (1 Hbf-Call × ~48 Cycles statt vorher 2 `/trip`-Calls × 48 Cycles). Ein automatisiertes Disruption-Polling existiert nicht mehr (Operator-Policy "VOR nur für die Stammstrecke").
- **Quelle**: VOR/VAO-ReST-API (`/departureBoard`-Endpunkt am Wien Hauptbahnhof), authentifiziert über Access Token.
- **Persistenz**: keine eigene JSON-Cache-Datei mehr; der Stammstrecken-Monitor schreibt Beobachtungen direkt in zwei CSV-Ledgers: `data/stats/stammstrecke_<YYYY>.csv` (aggregierte Verspätungen pro Richtung und Tick) und `data/stats/ausfaelle_<YYYY>.csv` (eine Zeile pro entdecktem Ausfall, dedupliziert via Pending-Trip-Ledger unter `cache/stammstrecke/` und je Abfahrt: ein Eintrag mit Zugnummer zur Minute eines Linieneintrags derselben Richtung ist derselbe Zug). Siehe [`docs/reference/stammstrecke_provider_logic.md`](reference/stammstrecke_provider_logic.md).

### Stadt Wien – Baustellen

- **Quelle**: Open-Government-Data-Baustellenfeed der Stadt Wien (`BAUSTELLEN_DATA_URL`, Default: offizieller WFS-Endpoint).
- **Cache**: `cache/baustellen/events.json`, gepflegt via `scripts/update_baustellen_cache.py`.
- **Ausfall**: Schlägt der Abruf fehl (z. B. wegen Rate-Limits), bleibt der bestehende Cache stehen (Exit-Code 1). Fällt nur einer der beiden WFS-Layer aus, kommt er aus seiner letzten guten Antwort unter `data/raw/baustellen/` (Exit-Code 3). Nur in einem Checkout ohne Cache nutzt das Skript `data/samples/baustellen_sample.geojson` als Grunddatensatz (Exit-Code 2). Details: `docs/architecture.md`, „Ausfall einer Quelle“.
- **Kontext**: Die Meldungen enthalten Metadaten zu Bezirk, Maßnahme, Zeitraum sowie geokodierte Adressen und ergänzen damit ÖPNV-Störungsmeldungen um bauzeitliche Einschränkungen.
- **Auswahl**: In den Feed kommt eine Baustelle nur, wenn ihr eigener Text sagt, was sich für Öffis ändert: eine Haltestelle verlegt oder aufgelassen, eine Linie umgeleitet oder kurzgeführt, der Straßenbahnbetrieb eingestellt, ein Schienenersatzverkehr (`mentions_oepnv` in `src/providers/baustellen.py`, geprüft beim Abruf und noch einmal beim Bauen des Feeds; Betreiberentscheidung 2026-10-06, „Nur mit Öffi-Folgen“). Nicht zählen: die Nähe zu einem Bahnhof (bis 2026-10-06 reichte eine Baustelle bis 150 m neben einem Bahnhof, etwa eine nächtliche Fahrstreifensperre am Rennweg); die U-Bahn als Bauherr („Im Zuge des U-Bahnbaus“, „Bauvorhaben U2 / U5“; in dieser Quelle seit Mai nie eine Folge für die U-Bahn selbst); Sätze, die Öffis als unberührt nennen („in der betriebslosen Zeit der Straßenbahn“, „nicht beeinträchtigt“, „einschließlich des öffentlichen Verkehrs … aufrecht gehalten“; seit 2026-11-06 „aufrecht gehalten/erhalten“ und „bleibt … aufrecht“, sofern im Satz davor kein „nicht“, „nur“, „kein“, „eingeschränkt“ oder „teilweise“ steht (`_keeps_running`) – die Salztorbrücke stand mit „die Buslinie 2A kann aufrecht gehalten werden“ im Cache, obwohl nur Autos umgeleitet werden); der Verweis „Nähere Informationen … sind/ist der Auskunft der Wiener Linien … zu entnehmen“; der Firmenname „Wiener Linien“ und die „öffentliche Verkehrsfläche“ (die Straße). Gemessen an der Feed-Historie vom 23.05. bis 06.10.2026: Von 10.027 Plätzen gingen rund 4.800 an Baustellen ohne solche Folge; in einem Nachbau von 110 Ständen fielen 17 von 30 Baustellen-Plätzen weg, an ihre Stelle kamen WL-Meldungen und Baustellen mit echter Folge.
- **WL-Zwilling**: Meldet ein WL-Hinweis, der bereits gilt, dieselbe Maßnahme, fällt die Baustelle beim Bauen des Feeds weg (`_drop_baustellen_twins`, Betreiberentscheidung 2026-10-09, „WL-Meldung genügt“). Zwilling ist ein WL-Hinweis, dessen Ursachensatz („Wegen Bauarbeiten im Bereich Neilreichgasse # Davidgasse …“, „Aufgrund von Bauarbeiten in der Maxingstraße …“) die Straße der Baustelle nennt (Kopf des Titels, `site_street`; `names_site_street`), oder der dieselbe Adresse nennt wie der Baustellentext („nach Burggasse ONr. 69“ / „Nach: Burggasse 69“, `shares_address`). Eine Straße nur in einer Umleitungsstrecke, einer Haltestellenliste oder einem Haltestellennamen zählt nicht: Lange Straßen tragen mehrere Maßnahmen zugleich (Burggasse im Sommer: 48A St.-Ulrichs-Platz, Burggasse 25 → 27, und eine Flohmarkt-Umleitung des 13A). Anlass: „Burggasse 67“ (Haltestelle „von Burggasse ONr.67 nach Burggasse ONr. 69“) stand am 08./09.10. auf Platz 9; WL meldete dieselbe Verlegung als „48A: Neubaugasse, Burggasse“ mit Linie und Richtung. Ohne Zwilling bleibt eine Baustelle im Feed.
- **Verlängerung**: Die GUID enthält das Enddatum. Eine verlängerte Baustelle kommt deshalb mit neuer GUID und ohne State-Eintrag zurück. Für die Reihenfolge zählt sie trotzdem nie später als an ihrem Starttag als neu (`_baustelle_start_cap`, seit 2026-10-09).
- **Beginn**: `OBJEKT_BEGINN` und `OBJEKT_ENDE` kommen als UTC-Datum eines Wiener Zeitpunkts (`2026-06-22Z`). Der Beginn (Wiener Mitternacht) fällt dabei auf den Vortag, das Ende (Ende des letzten Tages) bleibt. Belegt an den Baustellentexten selbst: 13 von 13 nennen als ersten Tag den Tag nach `OBJEKT_BEGINN`, 8 von 9 als letzten Tag genau `OBJEKT_ENDE`. Das Cache-Skript setzt den Beginn deshalb auf den Folgetag (`_first_day`, seit 2026-10-06); die GUID bleibt am gelieferten Datum, damit keine Baustelle neu erscheint. Liefert die Stadt das Datum eines Tages korrigiert, stünde der Beginn einen Tag zu spät: Dann nennen die Texte („Bauphase 1 (23.06.2026 …)“) wieder das Datum aus `OBJEKT_BEGINN`.
- **Jahres-Ledger** (seit 2026-11-10, Betreiberentscheidung: Gemini fasst die Baustellen eines abgeschlossenen Jahres auf dessen Statistikseite zusammen): Beim Schreiben des Caches bekommt jede Baustelle, die die Auswahl besteht, eine Zeile je guid und Wiener Jahr in `data/stats/baustellen_<YYYY>.csv` (`append_baustellen_rows` in `src/utils/stats_baustellen.py`), mit dem Text der ersten Sichtung im Jahr, ohne die vom Provider angehängten Zeilen und ohne den Platzhalter „Baustelle ohne weitere Angaben“. Eine Baustelle, die vor dem 1. Jänner des Schreibjahres endete, bekommt keine Zeile. Daraus entstehen Liste, Anzahl und Rückblick auf `docs/statistik-<Jahr>.md`; Regeln: [`data/stats/README.md`](https://github.com/Origamihase/wien-oepnv/blob/main/data/stats/README.md).
- **Leerer Cache**: Nach der Auswahl bleiben oft nur wenige oder gar keine Baustellen. Ein Live-Abruf schreibt das Ergebnis trotzdem, auch leer (`write_cache(..., allow_shrink=True)`, Exit 0); ob die Antwort der Stadt brauchbar war, prüft das Skript vorher je Layer (`_usable_layer`, letzte gute Antwort). Liefert die Stadt gar keine Baustelle, ist das ein Ausfall, kein ruhiger Tag (beide Layer führen immer Langläufer): Der Cache bleibt stehen, Exit 1. Leer werden darf er nur, wenn die Stadt Baustellen liefert und keine eine Folge für Öffis hat. Die 20-%-Sperre von `write_cache` gilt weiter für das Demo-Beispiel. Im Feed-Bericht steht ein leerer Baustellen-Cache als `ok-empty` (`register_provider(..., empty_is_normal=True)`), es sei denn, die Cache-Datei fehlt oder ist unlesbar.
- **Titel**: Die Stadt kappt `BEZEICHNUNG` bei 100 Zeichen; das Cache-Skript markiert den Schnitt mit „…" und lässt den Text unangetastet (die GUID leitet sich vom Rohtitel ab). Beim Bauen des Feeds vervollständigt `_repair_baustellen_title` (`src/feed_build/display_titles.py`) ein abgeschnittenes Wort aus der Beschreibung desselben Items, wenn es dort eindeutig ist („… bis Rad…" → „… bis Radetzkybrücke"), und streicht den Platzhalter „Unbenannte Verkehrsfläche" aus einer Endpunkt-Liste, die auch einen echten Namen nennt. Danach stellt `_post_filter_baustellen` den Bahnhof, neben dem die Baustelle liegt (150 m), als Präfix voran; ein U-Bahn-Präfix („U2/U5: …“) gibt es seit 2026-10-06 nicht mehr, es las sich wie eine U-Bahn-Störung. Hausnummern stehen als Adresse („Rennweg 33A–37“, „Altmannsdorfer Straße 76–76A“ statt „ONr.76 bis ONr.76A“). Im Text (und in WL-Titeln und -Texten) wird „ONr.“, „O.Nr.“ und „ON“ vor einer Hausnummer zu „Nr.“ („von Burggasse Nr. 67 nach Burggasse Nr. 69“, „von der Nr. 125 bis zur Nr. 141“; `_ordnungsnummer_as_nr`, seit 2026-10-09).

### Eigene Provider-Plugins

Zusätzliche Datenquellen lassen sich ohne Änderungen am Kerncode anbinden. Das
How-to [eigene Provider-Plugins anbinden](how-to/provider_plugins.md)
erläutert den Workflow und verweist auf das Skript
`scripts/scaffold_provider_plugin.py`, das ein lauffähiges Modul-Skelett
erzeugt. Aktivierte Plugins erscheinen automatisch im Feed-Health-Report und
können über `WIEN_OEPNV_PROVIDER_PLUGINS` gesteuert werden.

## Nutzung als Datenquelle in Drittprojekten

Das Repository stellt die aufbereiteten Meldungen nicht nur als RSS-Feed bereit, sondern bietet auch stabile JSON-Datensätze und
wiederverwendbare Python-Helfer für die Integration in andere Anwendungen.

### Schnellstart für Datenkonsumenten

1. Repository klonen und in ein virtuelles Environment wechseln (`python -m venv .venv && source .venv/bin/activate`).
2. Projektabhängigkeiten installieren (`python -m pip install -r requirements.txt`).
3. Die gewünschten Cache-Dateien unter `cache/<provider>/events.json` konsumieren oder die Python-Helfer aus `src/` nutzen.

Die Cache-Dateien werden von den GitHub-Actions regelmäßig aktualisiert und enthalten ausschließlich strukturierte JSON-Listen.
Sie sind damit ohne zusätzlichen Build-Schritt sofort für externe Automationen verwendbar.

### Programmgesteuerter Zugriff via Python

Für Python-Anwendungen existieren zwei bequeme Zugriffspfade:

- **Direkter Cache-Zugriff** – `src.utils.cache.read_cache()` liest die zwischengespeicherten Provider-Events als Python-Liste
  von Dictionaries ein (Wrapper wie `src.feed_build.providers_setup.read_cache_wl()` sind bereits vorkonfiguriert für „wl", „oebb" und
  „baustellen"; zusätzlich erzeugt `src.feed_build.providers_setup.read_cache_stammstrecke()` die Stammstrecke-Events on-the-fly aus dem
  CSV-Ledger statt aus einer Cache-Datei). VOR hat seit 2026-05-11 keine eigene JSON-Cache-Datei mehr (siehe `cache/`-Hinweis oben).
- **Live-Abruf der Provider** – Die Module `src.providers.wl_fetch` und `src.providers.oebb` stellen jeweils eine Funktion
  `fetch_events()` bereit, die die Rohdaten der Wiener Linien bzw. ÖBB direkt normalisiert. `src.providers.vor` ist seit der
  2026-05-11-Konsolidierung **kein Disruption-Provider** mehr — das Modul exportiert nur noch
  Authentifizierungs- und Quota-Helfer (`VorAuth`, `apply_authentication`, `load_request_count`, `save_request_count`,
  `refresh_access_credentials`, `refresh_base_configuration`) für den Stammstrecken-Monitor. Eine eigenständige
  `fetch_events`-Funktion gibt es nicht mehr.

Minimalbeispiel für den Cache-Zugriff:

```python
from src.utils.cache import read_cache

wl_events = read_cache("wl")
for event in wl_events:
    print(event["title"], event["starts_at"])
```

### Datenformat der Ereignisse

Unabhängig vom Provider folgen alle Ereignisse derselben Struktur, die auch im Feed verwendet wird. Die Zeitfelder liegen
im JSON-Cache als ISO-8601-Strings (bzw. bei direkter Python-Nutzung als `datetime`-Objekte) vor und dürfen `null` sein.
Die wichtigsten Felder sind:

| Feld        | Beschreibung                                                                                  |
| ----------- | --------------------------------------------------------------------------------------------- |
| `source`    | Ursprungsquelle der Meldung (`"Wiener Linien"`, `"ÖBB"`, `"VOR/VAO"`, …).                      |
| `category`  | Typ der Meldung, z. B. „Störung“, „Hinweis“, „Baustelle“.                                       |
| `title`     | Bereinigter, menschenlesbarer Titel mit Linienkürzeln.                                         |
| `description` | Ausführliche Beschreibung inkl. Zusatzinfos wie Umleitungen, betroffene Haltestellen usw.     |
| `link`      | Referenz-URL zur Originalmeldung oder weiterführenden Infos.                                   |
| `guid`      | Stabile eindeutige Kennung, geeignet als Primärschlüssel.                                      |
| `pubDate`   | Veröffentlichungszeitpunkt der Meldung; `null`, wenn die Quelle keinen parsebaren Zeitstempel liefert (z. B. WL-Items ohne Zeitfeld). |
| `starts_at` | Technischer Startzeitpunkt der Maßnahme (häufig identisch mit `pubDate`); `null`, wenn nicht ermittelbar.                    |
| `ends_at`   | Optionales Ende der Maßnahme; `null`, wenn unbekannt oder bereits vergangen.                   |
| `first_seen` | Zeitpunkt, an dem die Meldung erstmals im Feed erschien (projektintern via `data/first_seen.json` gepflegt). Bei einer wiederkehrenden WL-Meldung zählt der Beginn des aktuellen Auftretens (siehe „Reihenfolge im Feed“). |
| `_identity` | Projektinterner Schlüssel zur Nachverfolgung des „first seen“-Zeitpunkts (optional vorhanden). |

Eine formale Beschreibung steht als [JSON-Schema](schema/events.schema.json)
bereit und eignet sich für Validierungen in Drittprojekten. Pflichtfelder
sind `source`, `category`, `title`, `description`, `link`, `guid`, `pubDate`
und `starts_at`; die Zeitfelder `pubDate`, `starts_at` und `ends_at` dürfen
`null` sein. Alle übrigen Werte sind Unicode-Strings; zusätzliche
provider-spezifische Hilfsfelder werden vor dem JSON-Export entfernt, sodass
die Datensätze stabil und schema-konform bleiben.

## Stationsverzeichnis

`data/stations.json` vereint ÖBB-, Wiener-Linien-, VOR- und manuell
gepflegte Auslandsknoten in einer Datei. Das Format ist als JSON Schema
unter [`docs/schema/stations.schema.json`](schema/stations.schema.json)
formal definiert; ein Pin-Test (`tests/test_stations_schema.py`, braucht
`jsonschema` aus `requirements-dev.txt`) verhindert Drift.

### Felder pro Eintrag

| Feld | Pflicht | Beschreibung |
| ---- | ------- | ------------ |
| `name` | ✓ | Operator-facing Anzeige-Name (wird im Feed verwendet). **Nicht unique**: zwischen mehreren Einträgen kann derselbe Name vorkommen, etwa bei multi-modalen Knoten, wo Bus- und Tram-Bahnsteige unter zwei DIVAs am selben Knoten geführt werden. Die strukturelle Eindeutigkeits-Garantie tragen `bst_id`/`bst_code`/`vor_id`/`wl_diva`; siehe PR #1452. |
| `in_vienna` | ✓ | `true` wenn die Koordinaten innerhalb des LANDESGRENZEOGD-Polygons liegen. |
| `pendler` | ✓ | `true` für Pendler-Knoten **außerhalb** Wiens (siehe `data/pendler_bst_ids.json`). **Exklusiv zu `in_vienna`**: jede Station ist entweder Wien-Station ODER Pendler, niemals beides. Ausnahmen sind manuell gepflegte Knoten außerhalb des Pendlergürtels: `type: manual_foreign_city` (z. B. München Hauptbahnhof, Roma Termini, Bratislava hl.st.) und `type: manual_distant_at` (z. B. Salzburg Hbf, Graz Hbf, Linz Hbf, Innsbruck Hbf) — bei beiden Sondertypen sind beide Flags `false`. Verstöße werden vom Validator als NamingIssue gemeldet und vom Updater automatisch korrigiert (in_vienna gewinnt). |
| `aliases` | ✓ | Schreibvarianten und IDs zur Erkennung in Provider-Texten. |
| `latitude` / `longitude` | ✓ | WGS84-Koordinaten (validiert gegen das Wien-Polygon für `in_vienna`-Einträge). |
| `source` | ✓ | Komma-getrennte Provider-Tokens (kein Whitespace, alphabetisch): `hafas`, `manual`, `oebb`, `oebb_geonetz`, `vor`, `wl`. Das Feld nennt alle beteiligten Quellen eines Eintrags, nicht nur die seiner Koordinate. `google_places` ist nur noch als Altwert zulässig; seit 2026-10-16 trägt kein Eintrag ihn, die Google-Stufe ist entfernt. Für `osm` gilt dasselbe seit 2026-10-17 (OSM-Stufe entfernt, `docs/architecture.md` §5). |
| `bst_id`, `bst_code` | ÖBB | ÖBB-Stellen-ID und -Stellencode aus dem Excel-Verzeichnis (data.oebb.at). |
| `vor_id` | ÖBB/VOR | VOR/VAO-Stop-ID (numerisch oder volles HAFAS-Token); entspricht typischerweise GTFS-`stop_id`. |
| `wl_diva` | WL | Wiener-Linien-DIVA aus `wienerlinien-ogd-haltestellen.csv`. |
| `wl_stops` | WL | Einzelhaltepunkte (Bahnsteige/Richtungen) inkl. eigener `stop_id`. |
| `wl_lines` | WL | Linien, die an mindestens einem der `wl_stops` halten (z. B. `["47A", "N49", "U4"]`), aus `wienerlinien-ogd-linien.csv` und `wienerlinien-ogd-fahrwegverlaeufe.csv`. Fehlt, wenn keine Liniendaten vorliegen. |
| `type` | – | Sondertyp für manuell gepflegte Knoten außerhalb des Pendlergürtels: `manual_foreign_city` für Auslandsknoten (München, Roma, Bratislava) und `manual_distant_at` für distante österreichische Hauptbahnhöfe (Salzburg, Graz, Linz, Innsbruck etc.). Bei beiden ist die Coordinate-Bounds-Prüfung tolerant. |

Lookups laufen über `src/utils/stations_lookup.py:station_info(name)` mit
diakritik-tolerantem Token-Normalizer (Umlaut-Faltung erst ab Token-Länge 4,
damit kurze Stellencodes wie `Sue`/`Su` distinkt bleiben).

### Datenquellen und Lizenzen

Die MIT-Lizenz gilt nur für den Code. Welche Lizenz für Feed-Texte, Statistik und
die Dateien unter `data/` gilt, fasst [`NOTICE.md`](https://github.com/Origamihase/wien-oepnv/blob/main/NOTICE.md) zusammen; beide
Feeds nennen die Quellen im `<copyright>`-Element des Kanals (Entscheidung
Michael, 16.10.2026).

| Quelle | Datei(en) | Lizenz | Pflicht-Attribution |
|---|---|---|---|
| **ÖBB-Verkehrsstationen** (`data.oebb.at`) | extrahiert aus dem Excel „Verzeichnis der Verkehrsstationen"; eine atomar-geschriebene Cache-Kopie liegt unter `data/oebb-verkehrsstationen.xlsx` (Soft-Fail-Snapshot seit PR #1450 — wird bei `data.oebb.at`-Outage automatisch verwendet). | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) (Datensatzseite auf `data.oebb.at`) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)" (der Name als Link, so verlangt es die Datensatzseite) |
| **ÖBB GeoNetz** (`data.oebb.at`) | `data/oebb_geonetz_stops.json` — kompakte Stops-Projektion (EVA-Nummer, IFOPT-ID), extrahiert von `scripts/extract_oebb_geonetz_stops.py` aus dem `GeoNetz_*.zip`. Source-Token `oebb_geonetz`. | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) (Datensatzseite auf `data.oebb.at`) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)" |
| **Wiener Linien OGD** | `data/wienerlinien-ogd-haltestellen.csv`, `data/wienerlinien-ogd-haltepunkte.csv`, `data/wienerlinien-ogd-linien.csv`, `data/wienerlinien-ogd-fahrwegverlaeufe.csv` (Quelle: `www.wienerlinien.at/ogd_realtime/doku/ogd/`, seit PR #1442; der vorherige `data.wien.gv.at/csv/`-Proxy wurde in der 60. OGD-Phase im September 2025 retired) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: Wiener Linien" |
| **Wiener Linien GTFS** (data.gv.at „Wiener Linien – Fahrplandaten GTFS Wien“) | `data/wienerlinien-gtfs-stops.csv` = die Datei `stops.txt` des Fahrplans (`www.wienerlinien.at/ogd_realtime/doku/ogd/gtfs/stops.txt`), wöchentlich von `update_wl_stations.py` geladen. Nur Ersatz, wenn die OGD-Haltepunkte einer DIVA außerhalb des WL-Netzes liegen (seit 2026-10-25, siehe „Manuelle Station-Overrides“). | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: Wiener Linien" |
| **VOR (Verkehrsverbund Ost-Region)** | `data/vor-haltestellen.csv`, `data/vor-haltestellen.mapping.json` und der daraus abgeleitete Auszug `data/gtfs/stops.txt`. Bis 11.05.2026 vom gelöschten `fetch_vor_haltestellen.py` beim Auskunftssystem der AnachB-Webapp (`anachb.vor.at/hamm/gate`) abgefragt, seither eingefroren; seit 17.10.2026 nur noch ID und Name, ohne Koordinaten (Michaels Entscheidung, `NOTICE.md`). | **ungeklärt**, keine offene Lizenz bekannt (bis 17.10.2026 hier fälschlich CC BY 4.0; ein offener VOR-Datensatz dafür existiert auf `data.gv.at` nicht) | „Haltestellendaten: VOR" |
| **Wien-Stadtgrenzen-Polygon** | `data/LANDESGRENZEOGD.json` (Layer `ogdwien:LANDESGRENZEOGD` der MA 41 – Stadtvermessung, WFS-API von data.wien.gv.at, `srsName=EPSG:4326`, `outputFormat=json`) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | **„Datenquelle: Stadt Wien – data.wien.gv.at"** |
| **OpenStreetMap Overpass** | **kein Eintrag.** Seit 2026-10-17 läuft keine OSM-Stufe mehr; die 150 früheren OSM-Koordinaten sind durch ÖBB-GeoNetz oder WL-OGD ersetzt (`docs/architecture.md` §5). `src/places/osm_client.py` bleibt ohne Aufruf im Produktivlauf | – | – |
| **HAFAS (ÖBB Scotty)** (Stations-Fallback) | Profil-Sidecar `data/hafas_profile.json` (extrahiert von `scripts/sync_hafas_profile.py`), Live-Anreicherung via `src/places/hafas_client.py`. Liefert hochpräzise Koordinaten und die EVA-Nummer (`hafas_extId`) für Stationen, die weder Bestand noch GeoNetz abdecken. Seit 2026-09-25 auch die Bahnlinien je ÖBB-Bahnhof (`data/oebb_station_lines.json`, `scripts/update_oebb_station_lines.py`). | **ungeklärt**: öffentlich erreichbares Mgate-Backend der ÖBB ohne offene Lizenz (Anfrage bei der ÖBB läuft, siehe `NOTICE.md`); Profil aus dem ISC-lizenzierten Open-Source-Projekt [`public-transport/hafas-client`](https://github.com/public-transport/hafas-client) | „Fahrplandaten: ÖBB" |

### Aktualisierungsskripte

| Skript | Funktion |
| ------ | -------- |
| `python -m src.cli stations update all --verbose` | Führt alle Teilaktualisierungen (ÖBB, WL) in einem Lauf aus. |
| `python -m src.cli stations update directory --verbose` | Aktualisiert das ÖBB-Basisverzeichnis und setzt `in_vienna`/`pendler`. |
| `python scripts/update_wl_stations.py [--no-download] -v` | Lädt die WL-OGD-CSVs vom kanonischen Wiener-Linien-OGD-Endpoint `www.wienerlinien.at/ogd_realtime/doku/ogd/` und führt sie mit `data/stations.json` zusammen. Dazu lädt es die GTFS-Haltestellen der Wiener Linien (`data/wienerlinien-gtfs-stops.csv`), die nur einspringen, wenn alle OGD-Haltepunkte einer DIVA außerhalb des WL-Netzes liegen. `--no-download` nutzt die lokal gepinnten CSVs (Sandbox/Offline-Modus). Beim Download-Erfolg wird die jeweilige CSV atomar geschrieben; bei Netzwerk-Fehlern wird das gepinnte Snapshot beibehalten. |


Die GitHub Action `.github/workflows/update-stations.yml` aktualisiert
`data/stations.json` wöchentlich automatisch (Cron `0 1 * * 0`, Sonntag 01:00 UTC). Pipeline-Schritte:

1. **VOR-Stop-Liste**: gepinnt in `data/vor-haltestellen.csv`. Seit
   2026-05-11 existiert kein automatisiertes VOR-Stop-Refresh-Skript
   mehr; die CSV wird redaktionell gepflegt. Ihre Werte stammen aus dem
   Auskunftssystem der AnachB-Webapp und stehen unter keiner bekannten
   offenen Lizenz (`NOTICE.md`, „VOR-Haltestellendaten“); seit 17.10.2026
   enthält sie deshalb keine Koordinaten mehr, und der lokale
   Koordinatenindex (`_build_location_index`) bekommt aus ihr und aus
   `data/gtfs/stops.txt` keine Werte mehr. VOR-Stop-IDs ändern sich
   nur selten (Jahre).
2. **Sub-Skripte** (`scripts/update_all_stations.py`) – `update_station_directory.py` →
   `update_wl_stations.py` → `enrich_station_aliases.py`,
   → `apply_station_overrides.py`, alle gegen ein Temp-File. Erst nach der
   Validierung wird per `atomic_write` ins Repo zurückkopiert. Ein VOR-Stations-Sub-Skript
   gibt es seit 2026-05-11 nicht mehr.
3. **Validation-Gate** – die Sub-Skript-Ausgabe wird vom selben Wrapper
   validiert. Vier Kategorien lösen die **Auto-Quarantäne** aus: Die
   betroffenen Einträge werden aus dem Ergebnis herausgenommen und mit
   Begründung in `data/quarantine.json` zur Prüfung abgelegt, der Lauf
   schreibt die übrigen Stationen weiter (ein Teilfehler der Quelle hält
   den Refresh nicht auf): `provider_issues`, `cross_station_id_issues`,
   `naming_issues` (Vienna/Pendler-Mutual-Exclusivity + no-space-
   Source-Format; die kanonische Namens-Eindeutigkeit wurde pre-
   2026-05-12 entfernt — siehe Code-Kommentar in
   `src/utils/station_checks/identity.py:_find_naming_issues`) und
   `security_issues`. Andere Kategorien
   (`alias_issues`, `coordinate_issues` mit Exemption für die
   Sondertypen `manual_foreign_city` und `manual_distant_at`) sind
   tolerant.
4. **Beobachtbarkeit** – nach erfolgreichem Atomic-Write schreibt der
   Wrapper zwei Artefakte:
   - `data/stations_last_run.json` – Heartbeat mit Timestamp,
     Sub-Skript-Laufzeiten und Exit-Codes, Validation-Summary nach
     Kategorie, Diff-Summary und aktuelle Polygon-Vertex-Zahl.
   - `docs/stations_diff.md` – menschenlesbarer Diff (added / removed /
     renamed / Koordinaten-Drift ≥ 100 m) gegen den Pre-Update-Snapshot.
     Ein leerer Bericht bestätigt den No-Change-Lauf (Heartbeat-Funktion).
5. **Validation-Report regenerieren** – `python -m src.cli stations validate
   --output docs/stations_validation_report.md` schreibt die Markdown-
   Variante des Validation-Reports (alle 12 Kategorien) für Review-Zwecke.

Der Workflow ruft dazu vor dem Wrapper `scripts/sync_hafas_profile.py`
(Mgate-Profil-Sidecar) und danach `scripts/update_oebb_station_lines.py`
(Linien je ÖBB-Station über HAFAS) als eigene Schritte auf.

#### Automatisierte Qualitätsberichte

`python -m src.cli stations validate` erzeugt einen Markdown-Bericht mit
zwölf Issue-Kategorien: **geographic duplicates**, **alias issues**,
**coordinate anomalies**, **GTFS mismatches**, **security warnings**,
**provider issues** (VOR-/OEBB-Konsistenz), **cross-station ID
collisions**, **identity field conflicts** (raw `wl_diva`/`bst_id`/
`vor_id`/`bst_code`-Kollisionen zwischen Stationen, ergänzt 2026-05-16
in PR #1539), **naming issues** (no-space-Source-Format +
Vienna/Pendler Mutual-Exclusivity; die pre-2026-05-12 vorhandene
kanonische Namens-Eindeutigkeits-Prüfung wurde entfernt, weil
`name` ein operator-facing Display-Label ist, dessen
strukturelle Eindeutigkeit von `wl_diva`/`bst_id`/`vor_id`/
`bst_code` getragen wird), **cross-name alias collisions**
(Namens-Alias-Kollisionen zwischen Stationen) und **alias-key
collisions** (mehrere Stationen beanspruchen denselben normalisierten
Alias und sind sich über den Ort uneins — abweichendes `in_vienna` oder
mehr als 2 km Abstand; ergänzt 2026-09-12 in PR #1801, siehe
`docs/architecture.md` §5) und **Namens-Zuordnungskonflikte** (zwei
Einträge tragen denselben Namen, und der Lookup löst ihn auf den Eintrag
auf, der keine Haltestelle dieses Namens hat; ergänzt 2026-09-18).
Über `--output docs/stations_validation_report.md` wird der Bericht
persistiert; mit `--fail-on-issues` bricht die CLI bei jedem Befund mit
einem Fehlercode ab. In CI läuft der Validator als Pflicht-Gate (siehe
`.github/workflows/test.yml`); zusätzlich regeneriert
`update-stations.yml` den persistenten Report im wöchentlichen Daten-Refresh.

### Pendler-Whitelist

Zwei komplementäre Dateien legen fest, welche Bahnhöfe außerhalb der
Stadtgrenze als Pendler-Knoten ins Verzeichnis aufgenommen werden:

- **`data/pendler_bst_ids.json`** – Liste von ÖBB-`bst_id`-Werten.
  Eintrag wirkt sofort: ist die ID im ÖBB-Excel-Verzeichnis vorhanden,
  wird die Station mit `pendler=true` übernommen.
- **`data/pendler_candidates.json`** – name-basierte Wishlist (siehe
  [`docs/schema/pendler_candidates.schema.json`](schema/pendler_candidates.schema.json)).
  Sinnvoll, wenn der `bst_id` der gewünschten Station unbekannt ist —
  der Updater matcht den Stationsnamen aus dem ÖBB-Excel gegen diese
  Liste und ergänzt die fehlende ID automatisch.

Die Auswahl ist in beiden Dateien **redaktionell kuratiert** und
priorisiert die für Wien-Pendler:innen relevantesten Bahnhöfe.
Änderungen wirken beim nächsten Lauf von
`python -m src.cli stations update directory`. Die Mutual-Exclusivity
zu `in_vienna` (Vienna-Station vs. Pendler) wird sowohl vom Updater als
auch vom Validator und JSON-Schema erzwungen — Verstöße führen zu einer
WARNING bzw. nehmen den Eintrag per Auto-Quarantäne aus dem Ergebnis (`data/quarantine.json`).

### Manuelle Station-Overrides

`data/stations_overrides.json` ist eine schmal definierte
Korrekturschicht, die `scripts/apply_station_overrides.py` zwischen
WL-Merge und Validator-Gate auf `data/stations.json` anwendet.
Sie behebt Upstream-Defekte der Wiener-Linien-OGD, die sich nicht
durch Tuning der Merge-Logik beheben lassen (etwa fehlerhaft
fehlende Haltepunkte, falsche Koordinaten einzelner DIVAs).

Drei Operationen sind erlaubt — andere Schemata werden hart
abgelehnt, damit ein bösartiger oder unaufmerksamer Override nicht
das gesamte Verzeichnis umformen kann:

| `op` | Wirkung |
| ---- | ------- |
| `restore` | Schreibt einen kompletten Station-Eintrag (inkl. `wl_stops`) zurück, wenn er beim letzten Cron-Tick verschwunden war. |
| `patch_coords` | Setzt `latitude` / `longitude` auf der bestehenden Station neu — z. B. bei nachweislich falscher OGD-Koordinate. |
| `remove` | Entfernt einen kompletten Eintrag (selten benötigt). |

Jeder Eintrag trägt zwingend `reason` (kurze Begründung) und
`expires_when` (Bedingung, unter der der Override retire-bar wird) —
damit verfallene Workarounds beim nächsten Audit auffallen.
Implementierung und Tests: `scripts/apply_station_overrides.py`,
`tests/test_apply_station_overrides.py`.

Eine Override-Koordinate muss aus einem offenen Datensatz stammen, wie
jede Koordinate im Verzeichnis. Seit 2026-10-25 brauchen falsche
WL-Koordinaten keinen Override mehr: Liegen alle OGD-Haltepunkte einer
DIVA mehr als 50 km vom Netzmittelpunkt entfernt
(`src.utils.stations.is_plausible_wl_coordinate`), nimmt
`update_wl_stations.py` den Mittelwert derselben Haltestelle aus den
GTFS-Fahrplandaten der Wiener Linien (`data/wienerlinien-gtfs-stops.csv`,
GTFS-`stop_id` `at:49:1956:…` = DIVA 60201956), und die GeoRSS-Koordinate
(`open_data_coordinate`) folgt derselben Regel. Das betraf am 25.10.2026
Leopoldine-Padaurek-Straße, Halblehenweg und Wassermanngasse (60201954 bis
60201956), die WL seit Mai im Waldviertel führt. Ihre drei
`patch_coords`-Overrides sind entfernt; zwei davon trugen eine Koordinate,
die nach OpenStreetMap bzw. Google Maps bestimmt war, was der Regel „nur
offen lizenzierte Koordinaten“ (Michael, 16./17.10.2026) widersprach. Die
GTFS-Werte liegen 5 bis 19 m von den früheren Override-Werten entfernt. Der
Override für die Hummelgasse (60200558) ist ebenfalls entfernt, weil seine
`expires_when`-Bedingung eingetreten ist: WL führt wieder einen Haltepunkt.

### Zusätzliche Datenquellen

Weitere offene Datensätze (z. B. ÖBB-GTFS, Streckendaten, Wiener OGD, INSPIRE-Geodaten) können lokal in `data/` abgelegt und mit
Feed- oder Stationsdaten verknüpft werden. Hinweise zu Lizenzierung und Verknüpfung stehen in diesem Abschnitt, um eine saubere
Nachnutzung zu gewährleisten.

Wichtige Sidecar-Dateien unter `data/`, die im obigen Lizenz-Block
nicht einzeln gelistet sind:

| Datei / Verzeichnis | Zweck |
| ------------------- | ----- |
| `data/stations_metadata.json` | Kurate VZG-Streckennummern → Kilometer-Mapping für Stammstrecken-Auswertungen. Testabgesichert via `tests/test_stations_metadata.py`. |
| `data/streckendaten/` | Platzhalter-Verzeichnis für lokale VZG-Schnittdaten (per `.gitignore` versioniert, Inhalt nicht commited). |
| `data/stations_last_run.json` | Heartbeat des wöchentlichen Stations-Refreshs (Validation-Summary, Sub-Skript-Laufzeiten). |
| `data/stations_overrides.json` | Manuelle Korrekturschicht — siehe Abschnitt darüber. |

Weiter in [Teil 4](development-teil-4.md).
