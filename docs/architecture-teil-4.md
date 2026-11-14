[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 4.

* **Absatzende ist Satzende (seit 2026-10-02).** WL baut ausführliche
  Meldungen aus Absätzen und Überschriften. Die Umwandlung in Text machte
  aus jeder Grenze ein Leerzeichen, und Überschrift und Felder liefen
  ineinander: „U1: Starke Nachfrage Die U1 wird …“, „… Schwedenplatz U
  Haltestelle: Stammersdorf Von: Brünner Straße gegenüber 262 …“
  (16 von 69 Meldungen am 02.10.). `html_to_text(mark_block_ends=True)`
  markiert jetzt das Ende jedes Absatzes, und `_close_blocks` setzt dort
  den Punkt, der fehlt. Eine erste Überschrift, deren Wörter alle schon im
  Titel stehen („Bauarbeiten S80“ unter „S80: Bauarbeiten“), fällt weg; ein
  einzelnes Wort aus `_CATEGORY_PREFIX_WORDS` (`<h2>Gleisbauarbeiten</h2>`)
  bleibt den bisherigen Regeln, jedes andere einzelne Wort fällt ebenfalls
  weg (seit 2026-10-05: „Bahnsteigsanierung Wegen Sanierung …“ stand so auf
  dem TV, 11 solche Überschriften seit Juli, darunter
  „Laufsportveranstaltung“, „Netzänderung“, „Kanalgebrechen“), und eine
  Überschrift ohne Text dahinter bleibt stehen. Zwei
  Ausnahmen vom Punkt: Endet ein Absatz auf ein Funktionswort („… der damit
  einhergehenden“) oder beginnt der nächste klein („prov. Einbahnführung
  …“), läuft der Satz weiter; ein Etikett mit Doppelpunkt („Maßnahmen:“)
  gehört zum nächsten Absatz und steht nie allein am Ende. Absätze mit
  „Zeitraum:“ und „Dauer:“ fallen in jedem mehrteiligen Text weg, denn die
  Zeitzeile nennt die Daten schon, und sie nahmen den Maßnahmen den Platz.
  Ein Maßnahmenblock („Linie D: …; Linie 1: …“) folgt einem stehen
  gebliebenen Einleitungssatz auch dann, wenn beide nicht in 180 Zeichen
  passen, und wird mit „…“ gekürzt, solange die Linie und mindestens drei
  Wörter ihrer Maßnahme sichtbar bleiben (`_with_second_sentence`, seit
  2026-10-14; vorher stand bei der Demo am Ring am 13.10. nur „Wegen einer
  Demonstration kommt es zu einer Einschränkung des öffentlichen
  Verkehrs.“). Als Etikett einer Maßnahme zählt seit 2026-10-24 auch, was
  WL zwischen Linie und Doppelpunkt schreibt, eine Klammer („Linie 71
  (ganztägig):“) oder die Richtung („Linie 11A in Richtung Stadion U:“,
  „in beiden Richtungen“), und die Wochentage davor („Montag-Freitag,
  Linie 80A …:“); eine Linie als eigene Zeile über ihrer Maßnahme
  („<u>Linie D</u><br>Umleitung …“) bekommt den Doppelpunkt
  (`_WL_MEASURE_LABEL`, `_wl_measure_labels_with_colon`). Vorher fanden
  die Regeln den Block nicht, und 19 WL-Meldungen im Cache seit Juli zeigten nur
  „… kommt es zu folgenden Verkehrsmaßnahmen.“, am 24.10. das
  Sicherheitsfest und der Nationalfeiertag (Linien 1, 2, D, 71). Als
  Ankündigung fällt auch „… treten bei der Linie 80A Maßnahmen in Kraft.“
  weg, ebenso eine Bitte an den Leser vor dem Block („Beachten Sie bitte
  die unterschiedlichen Maßnahmen …“). Bleibt nach dem Schnitt von einer
  Maßnahme nur ihr Etikett („…; Linie N8“), fällt es weg, und „…“ steht
  hinter der Maßnahme davor. Eine WL-Störung mehrerer Linien zeigt jede Linie in
  Kurzform, wenn der Text sonst eine Linie verschweigen würde
  (`_lines_in_short`, seit 2026-10-15; Betreiberentscheidung „Kurzform je
  Linie“, Begründung: „Am TV sieht man sonst nur, was mit einer von drei
  Linien passiert“). WL schreibt einen Satz je Linie, und beim
  Gasrohrbruch am 11.10. stand nur die Umleitung der 36B da. Jetzt:
  „Umleitungen: 36B Berzeliusgasse – Brünner Straße S; 30A Berzeliusgasse
  – Gschweidlgasse; 31A Berzeliusgasse – Großjedlersdorf, Jochbergengasse.“
  Je Linie stehen die Maßnahme und ihre beiden Endpunkte, ohne Richtung
  und Umleitungsstraßen. Ein Endpunkt endet auch dort, wo ein eigener
  Nebensatz beginnt, also bei „und“ oder einem Komma vor einem klein
  geschriebenen Wort; was danach kommt, fällt weg wie „Weiterfahrt …“
  (seit 2026-10-25, Nachprüfung: am 10.05. las die 1 „Betrieb nur
  Stefan-Fadinger-Platz – Radetzkyplatz und wird ab Kliebergasse …“).
  Haben alle Linien dieselbe Maßnahme, steht sie einmal vorn, sonst bei
  jeder Linie („25: Betrieb nur …; 30: Kein Betrieb.“). Bekannt sind „Kein Betrieb“, „Betrieb nur“, „Umleitung“
  sowie „Fahrtbehinderung“, „Unregelmäßige Intervalle“ und „Verspätungen“
  ohne Orte; „Badner Bahn:“ ist die Linie LB. WLs „Voraussichtlich bis
  hh:mm Uhr.“ folgt, wenn es passt. Linien, die nicht in 180 Zeichen
  passen, fallen ganz weg, die letzte zuerst, mit „…“. Nennt der gezeigte
  Text schon jede Linie („Linie 26: …; Linie 27: …“) oder kennt die Regel
  die Maßnahme einer Linie nicht, bleibt der Text wie bisher. Replay über
  alle 4.899 WL-Texte seit Juli: 62 ändern sich, alle nur dort, wo vorher
  mindestens eine Linie fehlte. Im EN-Feed ist der Kopf „Umleitungen:“ fest
  „diversions:“ (`src/feed/glossary.py`); nur mit seinem Doppelpunkt, denn
  als Wort im Satz ließ der feste Begriff dem Modell kein Nomen, um das es
  den Satz baut („kommt es zu Umleitungen“ → „diversions occurs“, bis
  2026-10-24 im EN-Feed). Dieser Satz selbst ist seit 2026-10-24 fest
  „there are diversions“: Dem Modell überlassen hieß er „there are
  redirections“, während jeder andere WL-Text „diversion“ sagt (Michael,
  2026-10-24: „Der Feed soll ja mehrere Quellen vereinheitlichen.“). Für
  WL ist dazu „im Kreuzungsbereich“ fest „at the intersection of“, sonst
  rückte „intersection“ hinter den festen Ausdruck ans Satzende. Beginnt der Text mit einer Überschrift oder einem Feld, gilt die
  Zwei-Sätze-Regel nicht, sonst stünde „Haltestelle: Stammersdorf.“ ohne
  die neue Lage da: gezeigt werden die Felder bis zum ersten ganzen Satz,
  danach greift die 180-Zeichen-Grenze. Bei einer Haltestellenverlegung
  steht der Weg der Haltestelle vorn, als „Donaufelder Straße 48 →
  Donaufelder Straße 40-42“ statt „Von: … Nach: …“ (Betreiberwunsch, spart
  Zeichen), dann die Linien mit ihren Richtungen; ein
  „Haltestelle:“, das nur den Titel wiederholt, fällt weg (seit 2026-10-05,
  Betreiberentscheidung „Von/Nach zuerst“, `_relocation_first`: vorher
  fehlte bei 46 von 139 Verlegungen das „Nach:“). Nennt eine Meldung zwei
  Richtungen mit verschiedenen Orten, steht jeder Pfeil vor seiner
  Richtung, damit jeder Ort bei seiner Richtung bleibt (seit 2026-10-05
  abends; bis dahin blieb dort die Reihenfolge, und „15A/7A/N62:
  Eibesbrunnergasse“ endete auf dem TV mit „Wienerbergstraße 27b-27c →
  …“). Eine ersatzlos aufgelassene Haltestelle („Nach: Ersatzlos
  aufgelassen“) bekommt keinen Pfeil, denn der heißt „verlegt nach“: sie
  steht als „Hartäckerstraße 65: ersatzlos aufgelassen“ da (seit
  2026-10-06; vorher „Hartäckerstraße 65 → Ersatzlos aufgelassen“ bei 40A,
  im August bei 5A/5B Malzgasse); ebenso ein bloßes „Nach: aufgelassen“.
  Setzt WL beide Richtungen als eigene Felder („Fahrtrichtung: Bhf.
  Atzgersdorf S bzw. Lainzer Tor“, darunter „Von:“ und „Nach:“), steht
  jedes Paar hinter seiner Richtung, dem ersten Ziel, das WL nennt; ein
  „Von:“, das schon bei der Richtung davor stand, steht nur einmal
  (`_relocation_by_direction`, seit 2026-10-24): „Richtung Bhf.
  Atzgersdorf S: Hermesstraße nach Strampfergasse → etwa 100 Meter in
  Richtung Dr.-Schober-Straße. Richtung Hietzing U: aufgelassen.“ Vorher
  stand auf dem TV „Hermesstraße nach Strampfergasse → etwa 100 Meter …
  Hermesstraße nach Strampfergasse → aufgelassen.“, verlegt und
  aufgelassen zugleich. Fiele ein Ziel hinter die 180-Zeichen-Grenze,
  entfallen die alten Orte und jedes Ziel behält seinen Pfeil vor seiner
  Richtung (Betreiberentscheidung 2026-10-29, „Von weglassen, wenn zu
  lang“): „Richtung Döblinger Friedhof, Felix-Dahn-Straße: →
  Liechtensteinstraße 38. Richtung Schottentor U: → Liechtensteinstraße
  39.“ Vorher endete „40A: Bauernfeldplatz“ mit „… gegenüber
  Bauernfeldplatz → …“. Eine Verlegung, die alle Ziele zeigt, bleibt wie
  sie ist; zeigt auch die kürzere Form nicht mehr Ziele (15A/7A/N62, drei
  Linien je Richtung), bleibt die alte. Ob ein Ziel passt, misst die
  Kürzung des Feeds selbst (`_truncate_summary_180`, seit 2026-11-11; vorher
  las „54B: Stock im Weg“ „… → Ghelengasse gegenüber …“, Issue #2200). Seit
  2026-11-12 behält sie „32.“ als Satzende (Teil 16, §13), und 54B zeigt
  „Ghelengasse 30 → Ghelengasse 28“ mit beiden Zielen. Seit Juli betrifft
  die kürzere Form 40A Bauernfeldplatz und 17A Josef-Enslein-Platz, 54B nur
  vom Abend des 11.11. bis 12.11.2026.
  Im englischen Feed lesen beide Überschriften
  „Towards …“: Auch der Satz mit dem bloßen „aufgelassen“ geht wie jede
  Pfeilzeile ohne Modell (`_RELOCATION_CLOSED_LINE_RE`, seit 2026-10-25);
  vorher stand „Towards Bhf. Atzgersdorf S: … Direction Hietzing U:
  Discontinued.“ WLs Überschrift „Haltestellenverlegung bzw. -auflassung“
  heißt fest „stop relocation or closure“ (vorher „Stop relocation resp.
  release“). Hausnummern behalten ihren Buchstaben („27b“, „2a“, DIN 5008):
  `html_to_text` trennt nur noch Einheiten aus einem Buchstaben („2 m“,
  „5 h“) ab, „27 b-27 c“ stand mit dem Pfeil sichtbar im Feed. Die Folge einer Kurzmeldung steht
  immer als Satz mit Punkt da („Busse halten bei Haltestelle N71.“), und
  ein doppelter Punkt aus der Quelle („umgeleitet..“) wird einer.
  Die Pfeile der WL-Anzeigetafeln („<“, „>“) stehen nur dort im Text, wo
  sie auf einen Ort zeigen („Betrieb ab Schwedenplatz > Praterstern“); vor
  einem kleinen Wort oder einer Klammer und am Textende fallen sie weg, wie
  im Titel (seit 2026-10-23, `_STANDALONE_ARROW_RE`). Vorher passte der
  Text nicht mehr zu seinem Titel und der Satz stand doppelt da: „42/9:
  Falschparker / Umleitung bis Gersthof über Linien 42 und 9! Umleitung bis
  Gersthof < über Linien 42 und 9!“ (21.10.2026, Jules-Audit). Von 5.300
  WL-Texten seit Juli ändern sich 8.
  Kürzt die 180-Zeichen-Grenze einen Text, endet er nie auf einem Wort,
  das nur auf Folgendes hinführt: Artikel, Präposition, Konjunktion,
  „Richtung“, „Linie(n)“ (`_DANGLING_TAIL_WORDS`, seit 2026-10-06). Auf
  dem TV standen am 06.10. „… und N62 in Richtung …“ und „… bzw. N66 in
  Richtung …“: eine Richtung angekündigt, aber nicht genannt. Jetzt
  endet der Text auf „… und N62 …“; ein Etikett davor fällt mit
  („Maßnahmen: Linie …“), was davor steht, bleibt ganz (Liniennummern,
  „Donaumarina U“, eine geschlossene Klammer, ein Satz mit Punkt). Von
  308 gekürzten Texten seit Juli endeten 95 so. Ein Wort mit Punkt fällt
  nur weg, wenn es eine Abkürzung ist, eine Zahl nur als Ordnungszahl;
  „ein.“, „Uhr.“, „D.“, „Gleis 2.“ bleiben (seit 2026-11-12, Teil 16, §13).
* **Verklebte Wörter (seit 2026-10-02).** Die Baustellentexte der Stadt
  Wien kamen mit Wörtern ohne Leerzeichen in den Feed („Derlinke
  Fahrstreifen“, „Außerhalbder Arbeitszeit“, „zuden“). Zwei Stellen: Der
  Cache-Schreiber (`scrub_trojan_source_primitives`) und `_sanitize_text`
  löschten Zeilenumbruch-Steuerzeichen (vertikaler Tab, U+2028 …) ersatzlos;
  sie werden jetzt zu einem Leerzeichen. Ob die Quelle selbst schon
  verklebt liefert, lässt sich aus der Sandbox nicht prüfen (kein Zugriff
  auf `data.wien.gv.at`); deshalb erkennt `repair_glued_words` die Form
  auch im Text: Artikel vor Fahrstreifen-Adjektiv, Präposition vor Artikel,
  Funktionswort hinter „-straße/-gasse/-stelle/-platz/-ung“. Über 16 362
  Texte aus Feed- und Cache-Historie trifft die Regel 23 Stellen, alle
  verklebt; „zudem“, „indem“, „beiden“, „derzeit“ bleiben ganz. Seit
  2026-11-06 auch eine Buslinie am nächsten Wort („2Akann“, Salztorbrücke;
  „40Ain“, WL): Ziffern und Linienbuchstabe A/B am Wortanfang vor einem
  Funktionswort oder Verb aus fester Liste, damit „10Uhr“, „U5“ und
  „7Bauarbeiten“ ganz bleiben; über 9 028 Texte genau diese zwei Stellen.
* **St.-Abkürzung, Leerzeichen vor Satzzeichen, Ortsangabe ohne Ort (seit
  2026-10-03).** Die Nachprüfung der Änderungen vom 02.10. rendert alle
  2.126 verschiedenen Meldungen der Cache-Historie neu und fand drei
  Formen übrig. ÖBB schreibt „St.Pölten Hbf“ ohne Leerzeichen, während der
  Titel derselben Meldung „St. Pölten Hauptbahnhof“ hieß;
  `repair_saint_abbreviation` setzt es in Titel und Text („St.“ vor
  Großbuchstabe, sonst nichts). Ein Leerzeichen vor Komma oder Semikolon
  („Betrieb ab Enkplatz , Grillgasse“) fällt in `_format_item_content` vor
  jedem Vergleich weg, sonst unterschieden sich Titel-Folge und Text und
  derselbe Satz stand zweimal; ein Leerzeichen vor einem Punkt („12:15
  Uhr .“) und „im Bereich .“ ohne Ort fallen aus der Beschreibung.
* **ÖBB: der zweite Satz sagt, was noch gilt (seit 2026-10-09, Betreiberentscheidung „Stationen weglassen“ vom selben Abend).** ÖBB
  sagt im zweiten Satz, was noch gilt: „Planen Sie derzeit noch bis zu 10
  Minuten mehr Reisezeit ein.“ unter einer Störung, die vorbei ist, „Über die Dauer
  der Unterbrechung kann derzeit noch keine Angabe gemacht werden.“ unter
  einer laufenden. Der erste Satz mit beiden Stationen in ÖBBs Langform
  („zwischen Wien Leopoldau Bahnhst (U) und Wien Süßenbrunn Bahnhst“)
  brauchte bis zu 140 der 180 Zeichen; in 59 der 106 ÖBB-Texte, die der
  deutsche Feed seit September zeigte, fiel der zweite Satz weg. „Wien
  Leopoldau ↔ Wien Süßenbrunn“ las sich am 09.10. um 20:30 „… waren bis
  20:18 Uhr keine Fahrten möglich. [Heute]“, als wäre alles vorbei. Passt
  der zweite Satz nicht, lässt der erste jetzt den Ort weg, wenn der Titel
  jede seiner Stationen nennt (`_oebb_sentence_without_place`): „Wegen
  einer Stellwerkstörung am Bahnhof waren bis 20:18 Uhr keine Fahrten
  möglich. Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit ein.“
  Nennt der Titel eine Station nicht, bleibt der Ort stehen. ÖBBs
  Höflichkeitssätze („Wir bitten um Entschuldigung.“, am Ende von 310 der
  316 ÖBB-Texte seit Juni; „Sobald uns weitere Informationen vorliegen,
  informieren wir Sie.“, „Details finden Sie hier...“) sagen am Fernseher
  nichts und fallen vorher weg (`_OEBB_COURTESY_RE`). Über alle 443
  ÖBB-Texte im Cache seit Juni ändern sich 265: 251 behalten ihren
  zweiten Satz, die übrigen verlieren nur den Höflichkeitssatz. Rückblick:
  Eine Störung, die ÖBB als vorbei meldet („waren“), bleibt im Feed, weil
  98 der 100 solchen Texte seit Juni noch Folgen nennen (Reisezeit,
  Zugausfälle); der zweite Satz macht das jetzt sichtbar. Eine Liste von
  Tagen, die ÖBB Zeile für Zeile setzt („am 10.02.2026<br>am
  10.03.2026<br>…“), verliert die Tage, die vorbei sind (Wiener Datum),
  und steht als Satz da, mit Kommas und „und“ vor dem letzten
  (`_oebb_upcoming_dates`, seit 2026-10-24). Am 24.10. las R95 „… am
  10.02.2026 am 10.03.2026 am 14.04.2026 … am 11.08.2026 …“, acht
  vergangene Tage, und der Schnitt nahm den 10.11. und was nicht fährt;
  jetzt „… am 10.11.2026 (jeweils 14:00 Uhr - 22:00 Uhr) keine R95-Züge
  fahren.“ Eine Liste in einer Zeile zählt genauso („am 25.10.2026 und
  01.11.2026 der S2-Zug 23360 nicht fahren“ hieße ab 26.10. weiter so;
  jetzt „am 01.11.2026 der S2-Zug …“, Nachprüfung 25.10.): ein Tag ohne
  eigenes „am“/„von“ übernimmt das seines Vorgängers, wenn der wegfällt.
  Ein einzelner Tag oder Zeitraum bleibt, ebenso eine Liste in einer Zeile
  ohne vergangenen Tag (eine über mehrere Zeilen wird auch dann zum Satz);
  sind alle Tage vorbei, bleibt der Text wie er war
  (die Meldung ist dann ohnehin abgelaufen). 20 ÖBB-Meldungen seit Dezember 2025
  haben eine Liste über mehrere Zeilen, zwei (S2 Leopoldau–Mistelbach,
  REX 51 Marchtrenk) eine in einer Zeile. Bleibt ein Satz mit seinen
  restlichen Tagen über 180 Zeichen, liest die längste Liste „an
  einzelnen Tagen“, bis der Satz passt; die Zeitzeile nennt den Zeitraum
  (`_oebb_long_date_lists_in_words`, Michaels Entscheidung 2026-10-25
  „klingt gut. mach das bitte“). Eine Uhrzeitangabe „(jeweils …)“ der
  Liste bleibt, wenn der Satz mit ihr passt; ist eine Liste gegangen,
  geht ein einzelner Tag oder Zeitraum einer zweiten Strecke im selben
  Satz ebenso, wenn der Satz noch nicht passt. ÖBBs eigenes „an
  einzelnen Tagen“ im Satz zählt dabei wie eine gegangene Liste: „von
  13.04.2026 bis 28.05.2026 (jeweils an einzelnen Tagen, 23:20 Uhr - 04:20
  Uhr)“ (Flughafen Wien–Wien Mitte, Frühjahr 2026) liest „(jeweils an
  einzelnen Tagen, 23:20 Uhr - 04:20 Uhr)“, „von 02.04.2026 bis 07.04.2026
  (an einzelnen Tagen) einige Nachtzüge“ (Wien–Innsbruck) liest „an
  einzelnen Tagen einige Nachtzüge“ (Nachprüfung 25.10.). Eine Liste, die passt, bleibt. Am 24.10.
  brach S1 Leopoldau–Deutsch Wagram nach der Liste ab, ohne zu sagen,
  was nicht fährt; mit dieser Regel allein las sie „… an einzelnen Tagen
  der S1-Zug …“, seit der nächsten steht vorn, was nicht fährt. Über alle
  Cache-Texte seit Juni ändern sich 43 ÖBB-Texte, kein WL-Text.
  **Was nicht fährt, steht vorn (seit 2026-10-25).** ÖBB nennt die Züge und
  „nicht fahren“ am Satzende, hinter Strecken und Tagen („Wegen Bauarbeiten
  kann zwischen … und zwischen … an einzelnen Tagen der S1-Zug 23626 nicht
  fahren.“). Bleibt der Satz auch mit „an einzelnen Tagen“ über 180
  Zeichen, nahm der Schnitt genau das: Am 25.10. las S1 „… an einzelnen
  Tagen der S1-Zug … [Bis 28.11.]“ (Jules-Audit 2026-10-25), 45 ÖBB-Anzeigen
  seit Dezember 2025 endeten so („… von 31.10.2026 bis 30.11.2026 …“, „…
  einige Nahverkehrszüge …“). Ein solcher erster Satz nennt jetzt zuerst die
  Züge und das Verb, Strecken und Tage folgen hinter einem Doppelpunkt:
  „Wegen Bauarbeiten kann der S1-Zug 23626 an einzelnen Tagen nicht fahren:
  zwischen Deutsch Wagram Bahnhof und Gänserndorf Bahnhof und zwischen …“
  (`_oebb_what_does_not_run_first`). Der Schnitt trifft dann das Ende der
  Strecken und Tage, die Titel und Zeitzeile auch nennen. Es werden nur
  ÖBBs eigene Wörter umgestellt; „an einzelnen Tagen“ am Ende der Strecken
  geht mit dem Verb nach vorn. Eine Liste von drei oder mehr Zugnummern
  liest dabei als ihre Anzahl mit ihren Linien („die Züge S1 (28632), …,
  S2 (28153)“ → „11 Züge der Linien S1, S2 und S3“; „die Züge IC 1110, …,
  D 1141“ → „8 Züge (IC und D)“): elf Nummern füllten am 02.08.2026 die
  180 Zeichen samt Strecke und Verb. Die Linien bleiben genannt, weil die
  Zahl allein nicht sagt, welche Züge betroffen sind (Michaels
  Entscheidung vom 25.10.: „Nenne die Linien, nicht die Anzahl an Linien“,
  „11 Züge der Linie S1“ ist in Ordnung, „3 Linien sind betroffen“
  nicht); bis dahin las die Liste „11 Züge“. Eine Liste nackter Nummern
  nennt keine Linie und behält ihr Wort („34 Nahverkehrszüge“, „7
  S4-Züge“). Jeder Zug der Liste zählt einmal, auch einer mit kurzer
  Nummer („EC 71“), und jede Nummer einer Gruppe („REX 1 (2334, 2338 und
  2342)“ sind drei); bis zur Nachprüfung vom 25.10. zählten nur drei- bis
  fünfstellige Nummern, die Südstrecke im Frühjahr 2026 las „11 Züge“
  statt 13. Englisch: „11 trains on lines S1, S2 and S3 cannot run“. **Ein Zeitfenster bleibt (seit 2026-10-26).** ÖBBs „(jeweils …)“ hinter dem Zeitraum sagt, wann die Züge nicht fahren; abgeschnitten las „keine Nahverkehrszüge … von 31.10.2026 bis 30.11.2026 …“ (Wien Hbf–Gramatneusiedl) wie ein Monat ohne Züge, gesperrt sind nur die Wochenendnächte. Passt der umgestellte Satz mit Fenster nicht, fällt zuerst die Strecke weg, wenn der Titel sie nennt, dann der Zeitraum, wenn die Zeitzeile an seinem letzten Tag endet; das Fenster bleibt („… keine Nahverkehrszüge fahren: jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und …“, `_oebb_window_kept`). Grundlage ist Michaels Entscheidung vom 26.10. („Fenster statt Strecke und Zeitraum“). „(an einigen Tagen)“ geht wie „an einzelnen Tagen“ mit den Zügen nach vorn (Südstrecke: „können an einigen Tagen 13 Züge (EC, RJ, RJX, IC und D) nicht fahren: von 14.03.2026 (22:00 Uhr) bis 29.05.2026 (05:00 Uhr)“); bis dahin schob die längere Linienangabe aus #2070 diese Angabe ans abgeschnittene Ende. Replay über alle 932 ÖBB-Texte an 13 Stichtagen: acht Anzeigen ändern sich, im heutigen Feed keine; Gramatneusiedl erscheint ab Sa 31.10. mit Fenster. Englische Wochentage stehen nach dem Modell groß (`_english_weekdays_capitalised`, Zweitprüfung 27.10.: das Modell schrieb „(saturday, Sunday and holiday)“). Ein Satz, der passt, behält ÖBBs Reihenfolge (S2 Leopoldau–Mistelbach);
  ebenso einer mit zwei Subjekten („der Zug REX6 (7799) zwischen … der Zug
  S60 …“) oder einem anderen Verb („werden … umgeleitet und können … nicht
  halten“). Grundlage ist Michaels Auftrag vom 25.10. („Wenn Jules echte
  Fehler gefunden hat, sollten sie behoben werden“). Replay über alle 932
  ÖBB-Texte seit Dezember 2025: 57 Anzeigen ändern sich, alle mit diesem
  Satzbau; im heutigen Feed nur S1. Dieselbe Regel für ÖBBs Störungssatz „… sind zwischen … keine Fahrten möglich.“: [Teil 16, §15](architecture-teil-16.md#15-was-nicht-möglich-ist-steht-vorn-seit-2026-11-13).
  **Vollständig umgesetzt seit 2026-10-11** (dieselbe Entscheidung, keine
  neue): Der Feed-Wächter (Regel `oebb-zweiter-satz`) fand Texte, die den
  zweiten Satz trotzdem verloren, ein Replay über alle 563 ÖBB-Texte im
  Cache seit Juni 13. Zwei Ursachen: (1) Der Ort wurde aus den Slots einer
  Vorlage gelesen; schrieb ÖBB den Satz etwas anders („… und Liesing (Wien)
  Zugfahrten voraussichtlich 23:00 Uhr nur eingeschränkt möglich.“, ohne
  „bis“, 13.08.) oder war es eine Bauankündigung („Wegen Bauarbeiten können
  zwischen … am 01.11.2026 … keine S45-Züge fahren.“), blieb der Ort stehen
  und „Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet.“
  fiel weg. Jetzt bestimmt der Satzbau den Ort: Er steht hinter dem Verb
  des Hauptsatzes („Wegen … sind|waren|können|fahren …“), jede Station reicht
  bis zu ÖBBs Zusatz („Bahnhof“, „(U)“, „(Wien)“) oder bis zum ersten Wort,
  das keinen Namen beginnt („bis“, „Zugfahrten“, „am 01.11.2026“), und der
  Titel muss sie ganz nennen. „Brunn am Gebirge-Maria Enzersdorf“ unter dem
  Titel „Wien Liesing ↔ Brunn am Gebirge“ bleibt deshalb stehen, ebenso ein
  Ort in der Ursache oder in einer Aufzählung. (2) Der Satztrenner verlangt
  vier Buchstaben vor dem Punkt (wegen „Gerasdorf b. Wien“) und sah „…
  Reisezeit ein.“ und „… vorerst ab.“ nicht als Satzende; zweiter und
  dritter Satz zusammen passten nicht und fielen gemeinsam weg (Leopoldau ↔
  Süßenbrunn, 09.10., 20:00). Passt das nicht, endet der zweite Satz jetzt
  am ersten Punkt, vor dem keine Abkürzung steht
  (`_oebb_first_two_sentences`); die Stationen fallen erst weg, wenn auch
  dieser Satz allein sonst nicht passt. Alle 13 Änderungen im Replay sind
  „zweiter Satz bleibt“, kein anderer Text ändert sich.
  **Ort in einer Liste (seit 2026-10-11, Gesamtprüfung):** Setzt hinter der
  Station eine Aufzählung fort („… und Flughafen Wien Bahnhof bzw. Wien Hbf
  (U) und Flughafen Wien Bahnhof, bis …“, „in Lind-Rosegg Bahnhst und
  Föderlach Bahnhof …“, „in Wien Hbf (U) bzw Wien Meidling …“) oder folgt
  im selben Satz eine weitere Strecke („… der Zug REX6 (7799) zwischen Wien
  Hbf und Kledering der Zug S60 …“), bleibt der Ort stehen. Ohne diese
  Regel hätte die Kürzung „Wegen Verlängerung der Bauarbeiten sind bzw. Wien
  Hbf (U) und Flughafen Wien Bahnhof, bis …“ erzeugt; der echte Text vom
  07.09. lag zehn Zeichen über der Grenze und kam so nie an den TV. Replay
  über alle 563 ÖBB-Texte: keine Ausgabe ändert sich, zehn verstümmelte
  Kürzungen entfallen (`_OEBB_PLACE_LIST_RE`, Test
  `tests/test_oebb_ort_in_liste.py`).
* **ÖBB: der Text beginnt mit dem Satz, der die Titelstrecke nennt (seit 2026-10-17).**
  Der Titel einer ÖBB-Meldung ist ihre Wien-relevante Strecke, und die kann
  aus einem späteren Abschnitt des Textes stammen (`_apply_route_title`). Die
  ÖBB-Meldung „Salzburg Hbf => Flughafen Wien Bahnhof/Hegyeshalom“ stand am
  17.10. als „Wien Hütteldorf ↔ Wien Hauptbahnhof“ im Feed, der Text darunter
  sprach aber von „Salzburg Hbf und Flughafen Wien Bahnhof bzw. Hegyeshalom“
  und brach nach 180 Zeichen ab, bevor er sagte, was passiert. Die
  Titelstrecke stand nur im Abschnitt „TEILAUSFALL: … kann der Zug RJ 263
  zwischen Wien Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren.“, den
  der Fernseher nie zeigte. Nennt Satz 1 eine Strecke und keine ihrer
  Stationen steht im Titel, rückt jetzt der erste spätere Satz mit einer
  Titelstrecke nach vorn; Satz 1 folgt, wenn beide in die 180 Zeichen passen
  (`_oebb_title_route_first`). Satz 1 bleibt vorn, wenn er keine Strecke
  nennt (er gilt dann für alle) oder eine Station mit dem Titel teilt
  („zwischen Gänserndorf und Deutsch Wagram“ unter „Wien Floridsdorf ↔
  Gänserndorf“ ist derselbe Bau vom Nachbarabschnitt aus, und Satz 1 trägt
  die Daten). ÖBBs Abschnittsnamen in Großbuchstaben („TEILAUSFALL:“,
  „GEÄNDERTE FAHRZEITEN:“, „ACHTUNG:“) stehen im Text normal geschrieben
  („Teilausfall:“), Betreiberwunsch vom 17.10. („„Teilausfall“ gefällt mir
  besser als „TEILAUSFALL““, `_oebb_section_labels_cased`); Kürzel wie
  „RJ 263“ oder „ÖBB“ bleiben. Replay über alle 589 ÖBB-Texte im Cache seit
  Mai: nur diese Meldung ändert sich (drei ihrer Fassungen), Titel und Zahl
  der Items bleiben gleich. Im EN-Feed haben diese Namen feste englische
  Namen („Partial cancellation:“, §8, seit 2026-10-23).
  **Das Ende folgt dem gezeigten Abschnitt (seit 2026-10-19).** Rückt so ein
  späterer Abschnitt nach vorn, gilt sein letztes Datum als Ende der Meldung
  (Tagesende, Wiener Zeit): Zeitzeile und Verbleib im Feed folgen ihm,
  Betreiberentscheidung vom 19.10. (Karte „Ja, als Regel“). Anlass war
  dieselbe Meldung: Der Fernseher zeigte „Teilausfall: Von 18.10.2026 bis
  19.10.2026 kann der Zug RJ 263 … nicht fahren. [Bis Mi 21.10.]“, die Marke
  kam aus dem Zeitraum der ganzen Meldung, und ab 20.10. hätte die Folie einen
  vorbeigegangenen Ausfall gezeigt. `_oebb_lead_section_end` kürzt `ends_at`
  nur, verlängert nie; ein Abschnitt ohne Datum behält das Ende der Meldung,
  ebenso einer, dessen Ende kein Datum ist („von 04.07.2026 bis
  voraussichtlich Ende 2027“, „ab 04.07.2026“ ohne „bis“-Datum,
  `_oebb_section_end_is_open`, seit 2026-10-24): sein letztes Datum ist sein
  Beginn, und eine Sperre über ein Jahr hätte den Feed nach dem ersten Tag
  verlassen (unabhängige Prüfung 24.10., noch kein echter Fall). Ein
  „bis“-Datum irgendwo im Abschnitt beendet ihn immer: ein „bis“ ohne Datum
  zählt nur direkt hinter dem Datum des Zeitraums, nicht im
  Wochentagsfenster („(jeweils von Samstag 23:15 Uhr bis Sonntag 04:40
  Uhr)“) oder in einer Strecke („nur bis Wien Meidling“; Nachprüfung
  24.10.).
  `_end_with_oebb_lead_section` läuft in `_assemble_feed_items` vor dem
  Altersfilter. Prüfung aller 925 ÖBB-Texte im Cache seit Mai: nur diese
  Meldung ändert sich; Nachbau von 241 Feed-Ständen (15.–19.10.): nur ihre
  Zeitzeile („[Heute]“ am 19.10. statt „[Bis Mi 21.10.]“), Reihenfolge und
  State gleich; ein Nachbau für den 20.10. füllt ihren Platz mit der nächsten
  Meldung.
* **„Voraussichtlich bis 14:10 Uhr“ statt „Voraussichtliche Dauer: 14:10
  Uhr“ (seit 2026-10-06, Betreiberentscheidung „Dauer-Text anpassen“).**
  WLs Störungsvorlage nennt das erwartete Ende unter einem Dauer-Label. Auf
  dem TV las sich das wie eine Dauer von 14 Stunden. Hinter dem Label stand
  seit Juli in jedem der 942 verschiedenen Texte im WL-Cache ein
  Endzeitpunkt (863 Uhrzeiten, dazu „Betriebsschluss“ in drei
  Schreibweisen, „Sonntag Betriebsschluss“, „Ende August“, „31.07.2026“,
  „Montag, 03. August 2026, 04:00 Uhr“), außer „Nicht absehbar“ (48).
  `_expected_end_sentence` schreibt in `_format_item_content`
  „Voraussichtlich bis <Ende>“; ein „bis“, das WL selbst davorsetzt, steht
  nur einmal da. Umgeschrieben wird nur ein Wert, der wie ein Ende anfängt
  (Ziffer, „ca.“/„circa“/„etwa“/„gegen“ davor, „Betriebsschluss“,
  Wochentag, „heute“, „morgen“, Monat, „Anfang“/„Mitte“/„Ende“) und keine
  Dauer nennt („2 Stunden“, „etwa zwei Wochen“). „Nicht absehbar“ und jede
  andere Form behalten WLs Label. Dabei werden WLs Tippfehler in dieser
  Uhrzeit behoben („15;40 Uhr“, „12:15 Uht“, „06:30Uhr“). Der Satz ist sechs
  Zeichen kürzer; in 62 der 4.663 nachgerenderten WL-Texte passt dadurch der
  Grund wieder ganz in die 180 Zeichen. Zur Zeitzeile widerspricht er nie:
  Uhrzeiten stehen unter „[Seit hh:mm]“, „[Heute]“ oder „[Am …]“, „Ende
  August“ unter „[Bis 31.08.]“. Im EN-Feed: §8, „Expected until“.
  Ist die Uhrzeit vorbei, fällt der Satz weg (`_drop_passed_expected_end`,
  Betreiberentscheidung „Weglassen“ vom 06.10.2026): In 40 von 184
  Feed-Ständen seit Juli war sie schon vorbei (U6 „bis 19:10“ um 20:31). WL
  meldet die Störung dann weiter, ihr Ende ist offen; Grund und „[Seit
  hh:mm]“ bleiben. Die Uhrzeit gilt für den Tag der jüngsten Zeit der Meldung
  (`pubDate` oder `starts_at`), bis drei Stunden davor noch für diesen Tag
  (N29: „00:30 Uhr“ in einer Meldung von 01:12), sonst für den nächsten
  Morgen („02:45 Uhr“ in einer Meldung von 23:10). Ein Fenster um die
  aktuelle Zeit taugt nicht: WL schätzt bis 17,5 Stunden voraus („ca. 22
  Uhr“ für eine Veranstaltung, angelegt um 04:30). Ein Punkt zwischen
  Stunde und Minute gilt nur mit „Uhr“ als Uhrzeit („10.30 Uhr“), sonst
  wäre „bis 12.10.“ 12:10 Uhr. Ein Datum,
  „Betriebsschluss“ und „Nicht absehbar“ bleiben stehen. Nachgespielt über
  387 Feed-Stände: 49 der 243 angezeigten Sätze mit Uhrzeit fallen weg,
  Reihenfolge, GUIDs und Titel bleiben gleich.

Weiter in [Teil 5](architecture-teil-5.md).
