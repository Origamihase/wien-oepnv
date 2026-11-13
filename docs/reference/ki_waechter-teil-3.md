[KI-Wächter](ki_waechter.md), Teil 3.

## Probelauf im Pull Request

Ändert ein Pull Request `scripts/ki_waechter.py`, den Prompt oder den
Workflow, läuft der Wächter ohne Veröffentlichung über die 24 Stunden vor drei
Feed-Ständen:

| Commit | Stand | Erwartet unter anderem |
| --- | --- | --- |
| `001e47c` | 27.10.2026 16:00 | F6 „algae path“ (Algenweg, behoben in #2075); F1 40A „Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38“ neben der Verlegung Bauernfeldplatz; F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ (beide behoben in #2087) |
| `9e60b8d` | 30.10.2026 15:30 | F6 „All Saints' Worship“ (Allerheiligenverkehr, behoben in #2091) |
| `HEAD` | aktueller Feed | keine falschen Funde |

Der Prompt nennt keinen dieser Fälle (`tests/test_ki_waechter/`).

Erster Probelauf (PR #2092, 31.10.2026, noch mit Claude und nur den zehn
aktuellen Meldungen): alle vier bekannten Funde erkannt, auch die zwei, die
die Jules-Audits vom 27. bis 29.10. als „OK“ führten; kein Zitat verworfen.

Probelauf mit Gemini über 24 Stunden (PR #2092, 01.11.2026 ab 8:16 Uhr
Wiener Zeit, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/41271009232)):

| Stand | Modell | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | Gemini 3.6 Flash (3.8 Flash antwortete 503), 2 Anfragen, 37 s, 35.190 Eingabetokens | 67 Meldungstexte aus 48 Fassungen. Drei Funde, alle zutreffend: F6 „algae path“, F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ und ein neuer F1: „1/2/71/D: Veranstaltung“ (WL-Störungsticker) und „1/2/71/D: Nationalfeiertag“ (WL-Hinweis) standen am 26.10. eine Stunde lang nebeneinander im Feed und beschreiben dieselben Maßnahmen auf der Ringstraße. Nicht erkannt: der bekannte F1 40A (Verlegung Bauernfeldplatz neben „Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38“). |
| `9e60b8d` (30.10.) | – | Zwei Versuche (8:18 und 8:23 Uhr, je drei Anfragen an 3.8, 3.6 und wieder 3.8 Flash): nur „503 … high demand“. Nicht geprüft; von Hand nachholen (Actions → „KI-Wächter“ → „Run workflow“ mit `9e60b8de9dce438906b257e42a8b524e4f191f67`). |
| `HEAD` (24 h bis 01.11. 04:30) | Gemini 3.8 Flash, 1 Anfrage, 77 s, 40.186 Eingabetokens | 77 Meldungstexte aus 49 Fassungen. Ein Fund, zutreffend und bis dahin unbekannt: F6 „Continue via line 37 to High Wait.“ (31.10. 18:30, Linie 38 Polizeieinsatz; der WL-Text sagt „bis Hohen Warte“, die gebeugte Form traf die Maskierung des Haltestellennamens nicht). Keine falschen Funde; die Kranarbeiten-Ticker 29A/33A/N31 neben dem Hinweis „29A/33A/29B/N31: Bauarbeiten“ wurden nicht als Doppelung gemeldet. |

Bilanz: fünf Funde, fünf zutreffend, kein Zitat verworfen; zwei bekannte
Doppelungen (F1) nicht erkannt. Die Überlastung (503) traf an diesem Morgen
beide Modelle unregelmäßig: 3.6 Flash antwortete um 8:17 Uhr, 3.8 Flash um
8:19 Uhr, beide verweigerten um 8:18 und 8:23 Uhr.

Zweiter Probelauf mit dem Code von PR #2124 (02.11.2026 ab 10:06 Uhr
Wiener Zeit, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/41417947865)):

| Stand | Modell | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | Gemini 3.8 Flash, 1 Anfrage, 242 s | Ein Fund, zutreffend: F6 „algae path“. Die Doppelung 1/2/71/D steht diesmal nur unter den Vermutungen, F2 40A fehlt; dieselbe Eingabe ergibt nicht jedes Mal dieselben Funde. Die Antwort kam nach mehr als vier Minuten, und zwar noch ohne Keepalive (es erreichte die Verbindung erst nach der Prüfung vom 02.11.). |
| `9e60b8d` (30.10.) | Gemini 3.8 Flash, 1 Anfrage, 140 s | Erstmals geprüft. Zwei Funde, beide im Feed belegt: F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ (40A, die neue Haltestelle Liechtensteinstraße 39 fehlt) und F6 „Under-Meidlinger Straße“ (7B, Straßenname „Unter-Meidlinger Straße“ übersetzt). Unter den Vermutungen außerdem „Parlament/ Folktheter“ statt Volkstheater im EN-Feed. F2 und F6 waren mit #2087 schon behoben (30.10., 10:44 Uhr); „Folktheter“ behebt der Schrägstrich-Schutz für Haltestellennamen (CHANGELOG, 02.11.). |
| `HEAD` (24 h bis 02.11.) | – | 3.8 Flash, dann 3.6 Flash mit 503, zusammen 393 s; der Anteil des Probelaufs (2 Anfragen) war damit verbraucht. Welcher Fehler die erste Anfrage beendete, zeigte das Protokoll noch nicht; seither steht jeder Versuch ohne Antwort mit Modell und Dauer im Protokoll und in `antwort.json` (`attempts`). |

Dritter Probelauf, nur die Lite-Modelle (Eingabe `model` =
`gemini-3.5-flash-lite,gemini-3.1-flash-lite`, noch ohne Denkstufe, Code von
11fbb52; 05.11.2026 ab 10:28 Uhr Wiener Zeit):

| Stand | Modell | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | Gemini 3.1 Flash-Lite (3.5 Flash-Lite antwortete nach 3 s mit 503), 2 Anfragen, 5,5 s, 35.249 Eingabetokens, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/42002676065) | 67 Meldungstexte aus 48 Fassungen. Keine Funde: weder F6 „algae path“ noch F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ noch die Doppelungen 40A und 1/2/71/D. Drei Vermutungen ohne Bezug zu den bekannten Fehlern (Startdaten zweier ÖBB-Meldungen, „N35R“ neben „N35“ im Titel). |
| `9e60b8d` (30.10.) | – | 3.5 Flash-Lite nach 3 s mit 503, keine weitere Anfrage (der Lauf hatte nur eine), [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/42003028369). Nicht geprüft. |

Deshalb fragt der Wächter die Lite-Modelle seit 2026-11-05 mit Denkstufe
„high“ (`LITE_THINKING_LEVEL` in `scripts/ki_waechter.py`).

Vierter Probelauf, nur die Lite-Modelle mit Denkstufe „high“ (Eingabe `model`
= `gemini-3.5-flash-lite,gemini-3.1-flash-lite`, Code von PR #2164;
05.11.2026 ab 11:42 Uhr Wiener Zeit):

| Stand | Modell | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | Gemini 3.1 Flash-Lite (3.5 Flash-Lite antwortete nach 2 s mit 503), 2 Anfragen, 32,5 s, 35.249 Eingabetokens, 12.838 Denktokens, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/42012888405) | Zwei Funde, beide zutreffend: F1 „1/2/71/D: Veranstaltung“ neben „1/2/71/D: Nationalfeiertag“ und F6 „algae path“. Nicht erkannt: F1 40A (Verlegung Bauernfeldplatz neben „Busse halten Liechtensteinstraße 39“) und F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“. |
| `9e60b8d` (30.10.) | Gemini 3.1 Flash-Lite (3.5 Flash-Lite nach 3 s mit 503), 2 Anfragen, 30,9 s, 51.852 Eingabetokens, 12.251 Denktokens, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/42012893055) | Vier Funde, alle im Feed belegt: F5 „over the line line 9“ (43, bis dahin unbekannt), F6 „Under-Meidlinger Straße“ (7B), F6 „All Saints' Worship“ (38B/39B) und F6 „Folktheter“ (2; am 02.11. nur eine Vermutung). Nicht erkannt: F2 „Liechtensteinstraße 37a …“ (40A). |

Bilanz der Lite-Modelle am 05.11.: 3.5 Flash-Lite antwortete viermal mit 503
(10:28, 10:31, 11:42 und 11:43 Uhr); 3.1 Flash-Lite ohne Denken fand keinen
der bekannten Fehler, mit Denkstufe „high“ sechs Funde, alle zutreffend, in
rund 30 Sekunden je Stand. Die Kette bleibt Flash zuerst, die Lite-Modelle
als Ausweichweg.

Fünfter Probelauf, die ganze Kette im Pull Request (Code von PR #2164,
05.11.2026 ab 11:42 Uhr Wiener Zeit, [Lauf 247](https://github.com/Origamihase/wien-oepnv/actions/runs/42012890223),
und ab 11:50 Uhr, [Lauf 254](https://github.com/Origamihase/wien-oepnv/actions/runs/42013994909)):
in allen sechs Prüfungen antworteten 3.8 Flash, 3.6 Flash und 3.5 Flash-Lite
mit 503, und 3.1 Flash-Lite antwortete; vier Anfragen je Stand, zusammen 26
bis 413 Sekunden (3.8 Flash hielt eine Anfrage 359 Sekunden offen, bevor es
503 antwortete).

| Stand | Lauf 247 | Lauf 254 |
| --- | --- | --- |
| `001e47c` (27.10.) | 413 s, 35.249 Eingabetokens, 6.267 Denktokens, 67 von 67 Texten. Drei Funde, alle im Feed belegt: F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ (40A, behoben in #2087); F2 „Kurzführung in Richtung Stadion U bis Schlachthausgasse U, und wieder …“ (18, Veranstaltung am 08.11., 26.10. 17:31 Uhr; Grenzfall: die erste Richtung ist vollständig, die Kurzführung der Gegenrichtung fiel hinter die 180 Zeichen); F5 „Linie 46:Umleitung“ (46, 26.10. 21:01 Uhr: Ziffer, Doppelpunkt und Wort ohne Leerzeichen aus dem WL-Text; `repair_glued_words` in `src/utils/text_repair.py` kennt diese Form nicht). Nicht erkannt: F6 „algae path“, F1 40A, F1 1/2/71/D. | 51 s, 11.272 Denktokens, 45 von 67 Texten (der Bericht warnt). Zwei Funde, beide belegt: F6 „of the algae path“ und F5 (EN) „over the line line 2“ (46, dieselbe Meldung wie links: „über die Strecke der Linien 2“ wird Wort für Wort übersetzt, das Glossar kennt nur „auf der Strecke“). Vermutung: „Schadhafter Zug“ neben „Schadhaftes Fahrzeug“. Nicht erkannt: F2 „Liechtensteinstraße 37a …“, F1 40A, F1 1/2/71/D. |
| `9e60b8d` (30.10.) | 68 s, 51.852 Eingabetokens, 6.478 Denktokens, 101 von 101. Vier Funde, alle belegt: zweimal F4 Titel gegen Text („71: Rettungseinsatz“ mit „Fahrtbehinderung wegen Polizeieinsatz.“, 29.10. 19:01 Uhr; „42: Falschparker“ mit „Fahrtbehinderung wegen Rettungseinsatz.“, 29.10. 20:31 Uhr: zwei WL-Ticker einer Linie mit verschiedenen Ursachen in einem Zeitfenster; am 02.11. behoben, seither teilt `_split_by_cause` in `src/feed_build/wl_joins.py` das Fenster nach Ursache, und der Titel nennt beide; der Stand vom 30.10. ist älter); F5 (EN) „over the line line 9 and 44“ (43, 29.10. 20:00 Uhr); F2 „12: Umleitung zum Westbahnhof“ mit dem Text „[Seit 10:42]“ (30.10. 11:31 Uhr: der WL-Text war gleich dem Titel und fiel als Wiederholung weg). Nicht erkannt: F6 „All Saints' Worship“, F6 „Under-Meidlinger Straße“, F6 „Folktheter“, F2 „Liechtensteinstraße 37a …“. | 26 s, 5.760 Denktokens, 101 von 101. Ein Fund, belegt: F5 (EN) „over the line line 9 and 44“. Nicht erkannt: dieselben vier und die drei weiteren Funde von Lauf 247. |
| `HEAD` (24 h bis 05.11.) | 82 s, 45.355 Eingabetokens, 5.988 Denktokens, 90 von 90, keine Funde. | 38 s, 6.181 Denktokens, 90 von 90, keine Funde. |

Messung nur mit den Lite-Modellen über die fünf Tage der letzten Jules-Audits
(Eingabe `model` = `gemini-3.5-flash-lite,gemini-3.1-flash-lite`, Code von
PR #2164, 05.11.2026 zwischen 11:49 und 12:10 Uhr Wiener Zeit): 3.5 Flash-Lite
antwortete jedes Mal binnen Sekunden mit 503, 3.1 Flash-Lite antwortete, zwei
Anfragen je Stand.

| Stand | Lauf | Ergebnis |
| --- | --- | --- |
| `fc604a2` (31.10. 16:00) | [249](https://github.com/Origamihase/wien-oepnv/actions/runs/42013835569): 31 s, 41.557 Eingabetokens, 5.443 Denktokens, 71 von 71 | Ein Fund, belegt: F2 „5A: Verunreinigung Es kommt zu Verspätungen“ mit dem Text „[Seit 07:13]“ (31.10. 08:01 Uhr: WL schrieb Titel und Text gleich, der Text fiel als Wiederholung weg; um 07:31 Uhr stand noch „Unregelmäßige Intervalle in beiden Richtungen. Grund: Verunreinigung.“). |
| `87bad6b` (01.11. 16:29) | [255](https://github.com/Origamihase/wien-oepnv/actions/runs/42015241564): 32 s, 44.648 Eingabetokens, 13.605 Denktokens, 90 von 90 | Ein Fund, belegt: F6 „Continue via line 37 to High Wait.“ (behoben in #2106). Die Kranarbeiten-Ticker neben „29A/33A/29B/N31: Bauarbeiten“ wurden nicht gemeldet (Entscheidung vom 01.11.). |
| `07f9859` (02.11. 16:00) | [256](https://github.com/Origamihase/wien-oepnv/actions/runs/42015847430): 43 s, 35.850 Eingabetokens, 14.900 Denktokens, 66 von 66 | 18 Funde, alle F6 „Reason: …“ mit der Erwartung „Due to …“, dazu ein verworfenes Zitat. Keiner trifft zu: die Zeile „Grund: …“ der WL-Texte heißt im EN-Feed „Reason: …“ (`_render_label_record` in `src/feed_build/translate_run.py`), „Due to“ gilt für Ursachen im Satz („Wegen …“). Der Prompt sagt nur „Ursachen beginnen mit ‚Due to‘“. |
| `8bf589d` (03.11. 16:01) | [257](https://github.com/Origamihase/wien-oepnv/actions/runs/42016554495): 48 s, 43.040 Eingabetokens, 14.270 Denktokens, 82 von 82 | Zwei Funde, keiner trifft zu: F2 „zwischen Wien Leopoldau Bahnhst …“ (S1, 03.11. 11:01 Uhr: der Satz sagt „nicht fahren“ vor dem Doppelpunkt, die Kürzung nimmt nur den Rest der Strecken, wie `src/feed_build/oebb_summary.py` es vorsieht); F6 „Service obstruction: 1, Badner Bahn.“ mit der Erwartung „LB“ (1/LB, 03.11. 08:00 Uhr: die Kurzform nennt die Linie mit WLs eigenem Namen, `_SHORT_LINE_NAME` in `src/feed_build/wl_measures.py`; der Prompt sagt nur „die Badner Bahn heißt ‚LB‘“). |
| `b8729b6` (04.11. 16:00) | [253](https://github.com/Origamihase/wien-oepnv/actions/runs/42013863748): 26 s, 32.896 Eingabetokens, 8.406 Denktokens, 64 von 64 | Keine Funde. |

Bilanz: Mit 3.1 Flash-Lite (Denkstufe „high“) kommt jede Prüfung in unter
einer Minute zurück. Die zutreffenden Funde deckten sich mit den bekannten
Fehlern und zeigten drei bis dahin unbekannte Klassen: „line line“ für „über
die Strecke der Linie(n) …“ (behoben am 10.11.2026 mit
`src/feed_build/line_routes.py`, Issue #2188), ein WL-Text gleich dem Titel (nur die Zeitzeile
bleibt), „46:Umleitung“ ohne Leerzeichen. Zwanzig falsche Funde an zwei Tagen
gehen auf zwei knappe Sätze unter „Entschieden“ im Prompt zurück („Due to“,
„LB“); zwei weitere Funde zeigten Behobenes oder Bekanntes (71/42, am 02.11. behoben; 12). Derselbe Stand
bringt nicht in jedem Lauf dieselben Funde (27.10.: drei Läufe mit zwei bis
drei Funden, zusammen sechs verschiedene), und ein Lauf prüfte nur 45 von 67
Texten.

Sechster Probelauf, die ganze Kette im Pull Request mit der präzisen
Entschieden-Liste und zwei Antworten je Stand (Code von 6f9ca3ee, PR #2164,
05.11.2026 ab 13:51 Uhr Wiener Zeit,
[Lauf 261](https://github.com/Origamihase/wien-oepnv/actions/runs/42030586271)),
dazu die Lite-Modelle allein über die beiden Tage der falschen Funde
([Lauf 262](https://github.com/Origamihase/wien-oepnv/actions/runs/42030777475)
für den 02.11.,
[Lauf 263](https://github.com/Origamihase/wien-oepnv/actions/runs/42030905423)
für den 03.11.). In allen fünf Prüfungen antwortete 3.1 Flash-Lite, und zwar
zweimal; im Pull Request antworteten 3.8 Flash, 3.6 Flash und 3.5 Flash-Lite
davor mit 503 (3.8 Flash hielt eine Anfrage 162 Sekunden offen), in den
Lite-Läufen 3.5 Flash-Lite. Anfragen, Dauer und Tokens je Stand zählen beide
Antworten.

| Stand | Anfragen, Dauer, Tokens | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | 5 Anfragen, 232 s, 72.182 Eingabetokens, 22.628 Denktokens, 67 von 67 | Drei Funde, keiner trifft zu: F3 „Züge halten in Schleife, Wipplingerstr 39“ (D, 26.10.; der WL-Ticker „D: Züge halten Wipplingerstr 39“ steht in der Quelle, der Text zieht zwei Ticker zusammen); zweimal F6 „… station“ für ÖBBs „Bahnhof“ und „Bahnhst“ (S2, S1; so übersetzt das Glossar seit 2026-09-07, `_oebb_station_en` in `src/feed_build/en_templates.py`). Nicht erkannt: F6 „algae path“, F2 „Liechtensteinstraße 37a …“, F1 40A, F1 1/2/71/D. Verworfen: ein Zitat zu Meldung 33. |
| `9e60b8d` (30.10.) | 5 Anfragen, 116 s, 105.388 Eingabetokens, 25.019 Denktokens, 101 von 101 | Fünf Funde. Zutreffend: F6 „Reason: Harmful“ (U4, 30.10. 07:31 Uhr: die Kürzung auf 180 Zeichen trennte „Schadhaftes“ von „Fahrzeug“, die Übersetzung ging vom gekürzten Text aus und schrieb „Harmful …“; derselbe Fund steht ein zweites Mal mit dem Zitat „Reason: Harmful …“), F6 „Parlament/ Folktheter“ (2) und F4 „71: Rettungseinsatz“ neben Polizeieinsatz im Text (beide am 02.11. behoben). Nicht zutreffend: F2 „Grund: Schadhaftes“ (der Text sagt, was passiert, und der Titel nennt die Ursache ganz). Nicht erkannt: F6 „All Saints' Worship“, F6 „Under-Meidlinger Straße“, F5 „over the line line“, F2 „12: Umleitung zum Westbahnhof“ (steht als Vermutung). Verworfen: ein Quellzitat „[Seit heute]“. |
| `HEAD` (24 h bis 05.11. 13:30) | 5 Anfragen, 65 s, 96.224 Eingabetokens, 12.379 Denktokens, 96 von 96 | Zwei Funde, keiner trifft zu: F6 „Perfektastraße after Liesinger-Flur-Gasse“ (64A; WLs „nach“ nennt die Lage der Haltestelle hinter der Querstraße, `_PLACE_AFTER_CROSS_STREET_RE` in `src/feed_build/pretranslate.py` übersetzt es mit „after“ und „vor“ mit „before“); F2 „in beiden“ (40A, 04.11. 23:30 Uhr: beide neuen Haltestellen stehen, gekürzt ist der Satzrest „in beiden Richtungen. Grund: Bauarbeiten.“). |
| `07f9859` (02.11.), nur Lite, Lauf 262 | 3 Anfragen, 48 s, 73.384 Eingabetokens, 17.745 Denktokens, 66 von 66 | Zwei Funde, beide zutreffend und bis dahin unbekannt: F5 „23.2 min“ (Stammstrecken-Meldung „Durchschnittliche Verspätung von 23.2 min in Richtung Meidling“, 01.11.: `_format_minutes` in `src/feed/stammstrecke.py` schreibt den Dezimalpunkt, deutsch wäre „23,2 min“); F6 „Biró Street 17“ (17A, 02.11. 14:20 Uhr: „im Bereich Biróstraße 17“ wurde übersetzt, Straßennamen bleiben sonst deutsch). Keiner der 18 Funde „Reason: …“ aus Lauf 256. |
| `8bf589d` (03.11.), nur Lite, Lauf 263 | 3 Anfragen, 58 s, 87.764 Eingabetokens, 11.395 Denktokens, 82 von 82 | Keine Funde; die zwei falschen Funde aus Lauf 257 („Bahnhst …“, „Badner Bahn“) fehlen. |

Bilanz: Die zwanzig falschen Funde vom 02. und 03.11. kamen nicht wieder, und
die zwei Antworten brachten für den 02.11. zwei zutreffende, bis dahin
unbekannte Funde. Von den zwölf Funden treffen sechs zu (einer davon
doppelt), sechs nicht: fünf aus drei Lücken der Liste, die seither
geschlossen sind (ÖBBs „Bahnhof“/„Bahnhst“ als „station“, WLs „nach“ als
Lage der Haltestelle, die Kürzung hinter der Kernaussage), einer übersah
eine Quelle, die in der Eingabe stand (Wipplingerstr 39). Neue Feed-Klassen,
noch nicht gebaut: der Dezimalpunkt in der Stammstrecken-Meldung, ein
übersetzter Straßenname mit „ó“, die Übersetzung eines bei 180 Zeichen
getrennten Glossarworts („Schadhaftes …“ → „Harmful …“). Die Dauer je Stand
lag ohne die Wartezeit auf 503 bei 48 bis 116 Sekunden.

Mit den drei neuen Zeilen lief der Pull Request noch einmal (Code von
fd5fe389, 05.11.2026 ab 14:18 Uhr Wiener Zeit,
[Lauf 265](https://github.com/Origamihase/wien-oepnv/actions/runs/42034545510));
wieder antwortete nur 3.1 Flash-Lite, zweimal je Stand. Keiner der sechs
falschen Funde aus Lauf 261 kam wieder. 27.10.: zwei Funde, beide zutreffend
(F5 „line line 2“, F6 „algae path“). 30.10.: fünf Funde, vier zutreffend (F5
„[Seit 10:42]“ für den Text gleich dem Titel, F5 „over the line line 9 and
44“, F4 „42: Falschparker“ neben Rettungseinsatz im Text, F6 „Folktheter“)
und ein falscher: F3 „1/2/71“ mit der Erwartung „1/2“; die Quelle hat drei
Ticker „1: Polizeieinsatz“, „2: Polizeieinsatz“ und „71: Polizeieinsatz“ von
18:19 Uhr mit demselben Text, und der Titel nennt sie zusammen. HEAD: vier
Funde, zwei zutreffend (F4 deutsch und englisch zur Meldung REX50/REX51/S50,
deren Text bis 13:16 Uhr nur die REX50-Züge nannte; die Fassung danach, mit
#2165, brachte keinen Fund) und zwei falsche: die Kürzung „in beiden …“ der
Linie 40A, diesmal als F5 mit der Begründung, der Text liege unter der
Längenbegrenzung. Der volle Satz hätte 201 Zeichen; `_truncate_summary_180`
in `src/feed_build/summary_text.py` schneidet vor 180 Zeichen an einer
Wortgrenze und hängt „…“ an, der gekürzte Text ist deshalb kürzer als 180
Zeichen, und die Zeitzeile kommt danach dazu. Beide Lücken schließt die Liste
seit demselben Tag: der Zusammenzug der WL-Ticker ist kein F3, die Kürzung
vor 180 Zeichen an der Wortgrenze hinter der Kernaussage weder F2 noch F5.

Der Lauf mit den vier Zeilen (Code von dd0db0a1, ab 14:32 Uhr,
[Lauf 266](https://github.com/Origamihase/wien-oepnv/actions/runs/42036705032)):
27.10. drei Funde, alle zutreffend (F6 „algae path“ zweimal mit verschieden
langem Zitat, F5 „Linie 46:Umleitung“); 30.10. drei Funde, alle zutreffend
(F5 „over the line line 9“, F6 „Reason: Harmful“, F6 „Folktheter“), der F3 zu
„1/2/71“ kam nicht wieder; HEAD zwei Funde, beide die Kürzung der Linie 40A,
nun wieder als F2 („die Kernaussage ‚in beiden Richtungen‘ fehlt“, deutsch und
englisch). Nach den Regeln ist das kein Fund, das Modell meldete ihn in drei
von vier Läufen, und für den TV-Leser bricht der Satz hinter den beiden neuen
Haltestellen ab. Entschieden am 05.11.2026 nach dem Merge (Karte „Soll der
Feed satzweise kürzen, wenn der letzte Satz nicht mehr passt?“: „Wortweise
bleibt“): die Kürzung bleibt an der Wortgrenze, weil eine satzweise Kürzung im
Replay der 56 Fassungen vom 04.11. 16:00 bis 05.11. 15:31 Uhr (drei gekürzte
Meldungen von 65) zweimal Information genommen hätte: bei „38: Verkehrsunfall“
den Ort „Grinzinger Allee“, beim EN-Text von „25: Verkehrsunfall“ beide
Haltestellen (`changelog/2026-11-05.md`). Ein angefangener Satz hinter „…“
steht seither in der Entschieden-Liste; ein F2 bleibt, wenn die Kernaussage
selbst fehlt.

Erster planmäßiger Lauf mit der Kette (05.11., Takt 15:31 Uhr,
[Lauf 268](https://github.com/Origamihase/wien-oepnv/actions/runs/42045875295)):
3.8 Flash 503 nach 4 s, 3.6 Flash 429 (das Tageskontingent von 20 hatten die
Probeläufe verbraucht), 3.5 Flash-Lite 503 nach 2 s, 3.1 Flash-Lite
antwortete zweimal; fünf Anfragen, 61 s, 97.608 Eingabetokens, 96 von 96
Texten. Ein Fund, als Beobachtung zutreffend: der EN-Text von
„25: Verkehrsunfall“ (04.11., 20:01 bis 20:49 Uhr) endet „trains stop at
Donaufelder Straße …“, der deutsche nennt „175-177“; der englische Text hat
181 Zeichen, der deutsche 176. Das Modell nannte ihn F6, die Klasse ist die
Kürzung, nicht die Übersetzung; Issue #2166, geschlossen mit der Entscheidung
oben. Zwei Vermutungen (F1 zu den Meldungen 77 und 78) verwarf das Skript,
weil die Meldungen nie gleichzeitig im Feed standen. Der Health check wurde um
16:01 Uhr grün (Lauf 998), der erste grüne seit 04.11. 18:01 Uhr.

Siebter Probelauf, der Prompt Fassung für Fassung gegen sechs Stände
(05.11.2026 ab 16:40 Uhr Wiener Zeit, Eingabe `model` =
`gemini-3.1-flash-lite`, Denkstufe „high“, zwei Antworten je Stand; Stände
`001e47c` 27.10., `9e60b8d` 30.10., `fc604a2` 31.10., `07f9859` 02.11.,
`8bf589d` 03.11. und `56fa747`, die 24 Stunden bis 05.11. 17:01 Uhr). Jede
Fassung lief über die Stände, deren Funde sie betraf; jeder falsche Fund
wurde als Klasse im Prompt oder im Skript behoben, nie als Ausnahme für den
Fall, und die Beispiele im Prompt sind erfunden, damit der Pull-Request-Lauf
nichts vorgesagt bekommt. Läufe unter
`https://github.com/Origamihase/wien-oepnv/actions/runs/<Lauf-ID>`.

| Fassung | Läufe | Funde zutreffend / falsch | Falsche Funde und ihre Behebung |
| --- | --- | --- | --- |
| alt (Stand von #2167) | 269 (27.10., 30.10., HEAD), 270 bis 272 (31.10. bis 03.11.) | 10 / 2 | 31.10.: Kürzung hinter der Maßnahme als F2, Titel mit zwei Ursachen und erklärtem Text als F4. Neue Gliederung: Eingabe erklären, Klassen mit Grenzen und Beispielen, Vorgehen in Schritten, Zitatregeln |
| v2 | 273, 274, 276 bis 278 | 13 / 4 | Tippfehler des Betreibers als F5; Satz ohne Linienkennung als F4; Umstiegszusatz „(U)“ aus der Quelle als F3 und F6 (die Liste als Vorschrift gelesen). Prompt: ein Wort, das genauso in der Quelle steht, ist kein Fund; die Liste ist keine Vorschrift |
| v3 | 279 bis 282 | 12 / 3 | drei F3 „Betrieb ab …“ gegen WL-Ticker, deren Maßnahme im Quelltitel stand und die nur als „siehe oben“ verwiesen waren. Skript: „Q…: siehe oben (Titel)“; Prompt: Titel und Text einer Quelle zählen |
| v4 | 283 bis 287 | 12 / 0 | keine; die Beispiele im Prompt waren aber echte Fälle der Stände, deshalb v5 mit erfundenen Beispielen |
| v5 | 288 bis 293 | 9 / 4 | Satz ohne Linienkennung als F4 (noch einmal), vollständiger Satz als F2, Kürzung hinter „Kurzführung“ als F2, englische Kürzung hinter drei Maßnahmen als F2, Zeitzeile „[Seit heute]“ als F3 (verworfen); ein wahrer F1 verworfen, weil `erwartet` leer war. Prompt: F2 nur bei Kürzung und Kernaussage ist die Maßnahme, Linie des Titels ohne eigenen Satz kein F4, Zeitzeile kein Fund; Skript: F1 ohne `erwartet` gilt mit „nur eine Meldung“ |
| v6 | 294 (02.11.), 295 (31.10.) | 3 / 3 | drei F3 „fehlt im Text“ (Weglassen als Abweichung), eine Vermutung zu einer F6-Frage. Prompt: Weggelassenes ist kein F3, Klassen in fester Reihenfolge (F4 vor F3), Vermutungen nur ohne Quelle oder bei unklarem F1, Zitat je Klasse; Skript: F4 nur DE, Zitat an der Kürzung nur bei F2, Linie bei „Gleichzeitig im Feed“, „[…]“ am abgeschnittenen Quelltext |
| v7 | 296 bis 301 (alle sechs Stände) | 12 / 3 | Kürzung hinter „kommt es zu Umleitungen“ als F2 (Maßnahme erst hinter „Maßnahmen:“ erwartet), Tippfehler aus der Quelle als F5, Fakt eines zusammengezogenen WL-Texts als F3, dessen Ticker nicht unter den sechs Quellen der Linie stand. Prompt: Maßnahme als Hauptwort oder Verb, Buchstaben nur gegen die Quelle, F3 braucht eine Quelle, die den Fakt anders nennt; Skript: Quellen, deren Titel der Text wiederholt, zuerst |
| v8 | 302 bis 304 (27.10., 31.10., 30.10.) | 9 / 3 | 27.10.: zwei Kürzungen hinter der Maßnahme als F2 (hinter „kommt es zu Umleitungen“ und hinter dem Umleitungsweg), der Wochentag der Zeitzeile gegen die Quelle als F3. Skript: ein Zitat aus der Zeitzeile ist außer bei F2 kein Fund, ein F2 an der Kürzung gilt nur ohne Maßnahmenwort davor; Prompt: Schritt 1 nennt die Maßnahmenwörter |
| v9 | 305 bis 308 (27.10., 02.11., 30.10., 31.10.) | 9 / 1 | 02.11.: zwei Linien mit demselben Vorfall und eigenem Text als F1. Skript und Prompt: ein F1 braucht dieselben Linien vor dem Doppelpunkt; die englische Kürzung hinter mehreren Maßnahmen meldete das Modell noch einmal, das Skript verwarf sie |
| v10 | 309 (02.11.), 310 (03.11.), 311 (aktueller Feed) | 2 / 0 | keine; die englische Kürzung hinter mehreren Maßnahmen kam noch einmal als F2, das Skript verwarf sie. Prompt: Schritt 5 nennt die englischen Maßnahmenwörter, die F2-Klasse die Kernaussage bei mehreren Maßnahmen in einem Text |
| v11 | 312 (02.11.), 313 (27.10.), 314 (30.10.) | 11 / 1 | 27.10.: „after“ für „nach“ vor dem Pfeil einer Verlegung als F6 (die Lage der Haltestelle; die Regel stand in der Entschieden-Liste). Prompt: die F6-Klasse und Schritt 5 nennen sie; die Kürzung hinter mehreren Maßnahmen kam noch einmal, als F6, und das Skript verwarf sie. 30.10. brachte sechs zutreffende Funde, mehr als jede Fassung davor |
| v12 | 315 (27.10.), 316 (31.10.) | 6 / 1 | 27.10.: „Betrieb ab …“ als F3, obwohl der Quelltitel es so nennt (wie in v3; eine von acht Antworten zu diesem Stand). Skript: ein F3, dessen Feedzitat so in einer Quelle steht, ist verworfen; mit den 36 gespeicherten Antworten der Läufe 281 bis 316 neu verifiziert verliert die Prüfung keinen zutreffenden Fund und verwirft drei weitere falsche F3. 31.10. brachte neben der Zeitzeile erstmals den übersetzten Haltestellennamen „vocational school“ |
| PR-Lauf 317 (Endstand) | 27.10., 30.10., aktueller Feed (05.11. 19:00) | 15 / 0 | keine falschen; am 30.10. antwortete 3.8 Flash (203 Sekunden, eine Antwort) mit zehn zutreffenden Funden, Lite fand im selben Stand höchstens sechs. Neu in zwei Ständen: die WL-Haltestelle „Bahnhof Meidling“ heißt im englischen Text „Station Meidling“ (62A am 30.10., 62/LB am 05.11.), weil `src/feed_build/masking.py` Aliasse mit dem Präfix „Bahnhof“ als Schreibvariante überspringt und der Übersetzer das Wort übersetzt. Eine Klasse des Feeds, nicht des Prompts; offen für eine Entscheidung |
| PR-Lauf 318 (nach dem Doku-Commit; der Pfadfilter gilt für alle Dateien des Pull Requests, jeder Push wiederholt die drei Prüfungen) | 27.10., 30.10., aktueller Feed (05.11. 19:15) | 7 / 2 | aktueller Feed: zwei WL-Kurzformen als F2 („Grund: Gleisbauarbeiten.“ unter „N71: Busse halten auf der Hauptfahrbahn“, „Fahrtbehinderung.“ unter „12: Polizeieinsatz“), obwohl der Prompt sagt, dass ein vollständiger Satz nie F2 ist. Skript: ein F2 zu einem Text, der mit einem vollständigen Satz endet, ist verworfen; mit den 42 gespeicherten Antworten neu verifiziert verliert die Prüfung keinen zutreffenden Fund (jeder zutreffende F2 stand an einer Kürzung, einem „→ …“ oder einer Zeitzeile allein). Drei Zitate, die nicht im Text standen, verwarf das Skript ohnehin |
