[Entwicklerdokumentation – Wien ÖPNV Feed](development.md), Teil 2.

## Konfiguration des Feed-Builds

Der Feed-Generator liest zahlreiche Umgebungsvariablen. Für den Einstieg empfiehlt sich der
Assistent `python -m src.cli config wizard`, der eine bestehende `.env` einliest, die relevanten
Schlüssel erklärt und wahlweise interaktiv oder per `--accept-defaults` eine neue Konfiguration
schreibt. Die wichtigsten Parameter:

| Variable                 | Zweck / Standardwert                                                            |
| ------------------------ | ------------------------------------------------------------------------------- |
| `OUT_PATH`               | Zielpfad für den RSS-Feed (Standard `docs/feed.xml`).                           |
| `FEED_HEALTH_PATH` / `FEED_HEALTH_JSON_PATH` | Zielpfade für die nach jedem Build erzeugten Health-Reports (Standards: `docs/feed-health.md` / `docs/feed-health.json`). Beide nicht im Repository versioniert. |
| `FEED_TITLE` / `FEED_DESC` | Titel und Beschreibung des Feeds (Standards: `"ÖPNV Störungen Wien & Pendler"` / `"Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"`). |
| `FEED_LINK`              | Referenz-URL (nur http/https, Standard: GitHub-Repository).                     |
| `PAGES_BASE_URL`         | Basis-URL der GitHub-Pages-Site für absolute Permalinks (Standard `https://origamihase.github.io/wien-oepnv`). Wird gegen die Pages-Host-Allow-List validiert; abweichende Werte fallen auf den Standard zurück. |
| `MAX_ITEMS`              | Anzahl der Einträge im Feed (Standard 10).                                      |
| `MAX_ITEMS_PER_TOPIC`    | Höchstens so viele Einträge je Ursachenwort und Tag in den vorderen Plätzen; weitere rutschen hinter das Feld (Standard 3, 0 schaltet ab). Ohne eigene Variable gilt zusätzlich: Von mehreren ÖBB-Einträgen mit wortgleichem Titel (eine Strecke, mehrere Bauphasen) behält nur einer seinen Platz: eine laufende Störung auf dieser Strecke, sonst eine Sperre an ihrem Sperrtag (seit 2026-11-13), sonst der mit dem frühesten Zeitfenster. |
| `UPCOMING_PREVIEW_DAYS`  | Was später als so viele Wiener Kalendertage nach heute beginnt, rückt hinter alles, was schon gilt (Standard 1: ab dem Vortag des Beginns vorn, freitags und samstags bis einschließlich Montag; höchstens 365). 0 lässt nur heute Beginnendes vorn. |
| `FEED_TTL`               | Cache-Hinweis für Clients in Minuten (Standard 15).                             |
| `MAX_ITEM_AGE_DAYS`      | Maximales Alter von Meldungen aus den Caches (Standard 365).                    |
| `ABSOLUTE_MAX_AGE_DAYS`  | Harte Altersgrenze für Meldungen (Standard 540).                                |
| `ENDS_AT_GRACE_MINUTES`  | Kulanzfenster für vergangene Endzeiten (Standard 10 Minuten), höchstens bis zur Wiener Mitternacht nach dem Tag, den die Zeitzeile nennt (`_drop_after` in `src/feed_build/filters.py`, seit 2026-10-24). |
| `FRESH_PUBDATE_WINDOW_MIN` | Toleranzfenster (Minuten) für „frische" pubDates beim Aging-Check (Standard 5). |
| `CACHE_MAX_AGE_HOURS`    | Maximalalter der Provider-Cache-Dateien, ab dem eine Warnung im Log erscheint (Standard 24). |
| `FEED_TITLE_CHAR_LIMIT` / `DESCRIPTION_CHAR_LIMIT` | Maximale Zeichenzahl für Item-Titel/Beschreibungen (Standards 256 / 4000). Negative Werte werden auf `0` geklammert; eine obere Schranke wird derzeit nicht erzwungen (siehe `src/feed/config.py`). |
| `PROVIDER_TIMEOUT`       | Globales Timeout für Netzwerkprovider (Standard 25 Sekunden). Per Provider via `PROVIDER_TIMEOUT_<NAME>` oder `<NAME>_TIMEOUT` anpassbar. Der Standard ist zugleich die Obergrenze (`MAX_PROVIDER_TIMEOUT`, Schutz gegen hängende Upstreams): Werte lassen sich nur senken, höhere werden auf 25 Sekunden geklammert. |
| `PROVIDER_MAX_WORKERS`   | Anzahl paralleler Worker (0 = automatisch). Feiner steuerbar über `PROVIDER_MAX_WORKERS_<GRUPPE>` bzw. `<GRUPPE>_MAX_WORKERS`. |
| `WL_ENABLE` / `OEBB_ENABLE` / `BAUSTELLEN_ENABLE` / `STAMMSTRECKE_ENABLE` | Aktiviert bzw. deaktiviert die einzelnen Default-Provider (alle Standard: aktiv). `STAMMSTRECKE_ENABLE` steuert den VOR/VAO-basierten Verspätungs- und Ausfall-Monitor. Eine separate `VOR_ENABLE`-Variable existiert seit der 2026-05-11-Konsolidierung **nicht mehr**. |
| `WL_RSS_URL` / `OEBB_RSS_URL` / `BAUSTELLEN_DATA_URL` / `OVERPASS_URL` | Override der Upstream-URLs. Validiert gegen eine Allow-List bekannter Hosts; abweichende Werte werden ignoriert und der Default verwendet (siehe Modul-Docstrings für Details). |
| `OEBB_ONLY_VIENNA`       | Strikte ÖBB-Filterung (`1`/`true`/`0`/`false`, Standard `false`): nur Meldungen mit explizitem Wien-Bezug akzeptieren — keine Pendlerbahnhof-Fallbacks (siehe [`docs/reference/oebb_provider_logic.md`](reference/oebb_provider_logic.md)). |
| `WIEN_OEPNV_AT_RECONCILE` | Toggle (Standard `1`) für den netzgestützten Koordinaten-Abgleich der Stationen, die WL und ÖBB gemeinsam führen (WL → HAFAS, bei Widerspruch bleibt WL; seit 2026-10-17 ohne OSM-Schiedsrichter, `scripts/update_wl_stations.py`); `0` überspringt ihn (Wrapper-Test, Offline-Läufe). |
| `WIEN_OEPNV_MANUAL_ENRICH` | Toggle (Standard `1`) für die Nachreicherung manuell gepflegter Auslands-/Distant-AT-Knoten (`type=manual_*`) in `src/station_directory/coordinates.py:_enrich_manual_stations` (aufgerufen von `scripts/update_station_directory.py`). Auf `0` gesetzt, um in Test-Sandboxen die ~296 realen HAFAS-Round-trips zu vermeiden (siehe `tests/test_update_all_stations_wrapper.py`). |
| `WIEN_OEPNV_PROVIDER_PLUGINS` | Komma-separierte Liste optionaler Provider-Plugin-Module (siehe [`docs/how-to/provider_plugins.md`](how-to/provider_plugins.md)). Standard leer; nicht gesetzte Module werden ignoriert. |
| `WIEN_OEPNV_ENV_FILES` | Komma-separierte Liste zusätzlicher `.env`-Dateien, die vor der Konfiguration eingelesen werden (`src/utils/env.py`). Standard liest `.env`, `data/secrets.env`, `config/secrets.env`. |
| `LOG_LEVEL`, `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `LOG_FORMAT` | Steuerung der Logging-Ausgabe (`log/errors.log`, `log/diagnostics.log`). `LOG_LEVEL` Standard `INFO`; `LOG_FORMAT=json` aktiviert JSON-Logs. |
| `STATE_PATH`, `STATE_RETENTION_DAYS` | Pfad (eine Datei, nur für den Feed-Build) & Aufbewahrungstage für `data/first_seen.json` (Standard 600 Tage = absolute Altersgrenze 540 + 60; seit 2026-09-12, vorher 60). |
| `WIEN_OEPNV_CACHE_PRETTY` | Steuert die Formatierung der Cache-Dateien (`1` = gut lesbar, `0` = kompakt). |
| `WIEN_OEPNV_DEBUG`       | Auf `1` gesetzt zeigt die CLI (`python -m src.cli`) bei Fehlern den vollständigen Traceback; Standard verhält sich fail-secure (keine Trace-Ausgabe). |
| `VOR_ACCESS_ID`          | **Pflicht-Secret** für den Stammstrecken-Monitor (VAO-Access-Token). Niemals committen — laden via `.env`, `data/secrets.env` oder `config/secrets.env`. Validierbar mit `python -m src.cli tokens verify vor`. |
| `VOR_BASE_URL`           | Optionales Secret: Basis-URL der VAO-ReST-API (validiert und auf den VAO-Host festgenagelt in `src/providers/vor_auth.py:_validated_vor_base_url`). Leer gilt der Standard aus `src/providers/vor.py`. Legacy-Alias `VOR_BASE` (nur lokal; kein Workflow reicht ihn seit 2026-10-16 weiter). |
| `VOR_USER_AGENT`         | Custom User-Agent für VOR/VAO-API-Calls (Standard `wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)`). |
| `VOR_REQUEST_COUNT_FILE` | Override für den Persistenzpfad des VAO-Tagesbudget-Counters (Standard `data/vor_request_count.json`). |
| `VOR_AUTH_TYPE`          | Erzwingt das Auth-Schema bei der VAO-Token-Normalisierung (`bearer` oder `basic`, Standard: automatische Erkennung aus dem Token-Format in `src/providers/vor_auth.py:_normalise_access_token`). |
| `VOR_VERSION` / `VOR_VERSIONS` | Versions-String für die VAO-URL (z. B. `v1.12.0`); `VOR_VERSIONS` ist Fallback-Alias für `VOR_VERSION`. Optional, nur lokal: Kein Workflow reicht sie weiter (bis 2026-10-16 übergaben zwei Workflows ein nie angelegtes Secret `VOR_VERSION`), es gilt die Version, die der Stammstrecken-Monitor aus der VAO-Liste wählt (`cache/stammstrecke/vao_version.json`, seit 2026-10-29), sonst `DEFAULT_VERSION` aus `src/providers/vor.py`. Ist sie gesetzt, wählt der Monitor nicht. |
| `RAW_CAPTURE`            | `1` schreibt beim Abruf von WL, ÖBB und Baustellen die normalisierten Rohantworten und die verworfenen Meldungen nach `data/raw/<quelle>/` (`src/utils/raw_capture.py`, `data/raw/README.md`). Standard aus; gesetzt nur im Fetcher-Schritt von `update-cycle.yml`. |
| `BAUSTELLEN_TIMEOUT`     | Per-Request-Timeout (Sekunden) für `scripts/update_baustellen_cache.py` (Standard `20`, hart geklammert auf `MAX_BAUSTELLEN_TIMEOUT`). |
| `BAUSTELLEN_FALLBACK_PATH` | Pfad zur lokalen JSON-Fallback-Datei (Standard `data/samples/baustellen_sample.geojson`), die verwendet wird, wenn der OGD-Endpoint der Stadt Wien nicht erreichbar ist und noch kein Cache existiert. |
| `GEMINI_API_KEY`         | Schlüssel der Gemini API (Repository-Secret, Gratis-Kontingent ohne Abrechnung) für den KI-Wächter, den Jahresrückblick der Baustellen und seit 2026-11-12 die KI-Entscheidung im Feed-Build (`src/ki_entscheidung`, Schritt „Build feed“ von `update-cycle.yml`; ohne Schlüssel entscheiden die Regeln, und der Nebenschritt „KI-Entscheidung“ gilt als gescheitert). Der Feed braucht ihn nicht. Den Rückblick fragt nur der Statistik-Schritt von `update-cycle.yml` in seinen Dashboard-Läufen, bis ein Text angenommen und gespeichert ist; ohne Schlüssel fragt er nicht, und die Seite des Jahres sagt, dass ein Rückblick ergänzt wird. Siehe [`reference/ki_waechter.md`](reference/ki_waechter.md) und „Statistik-Schritt und Jahresseiten“ in [Teil 4](development-teil-4.md). |
| `JAHRESRUECKBLICK_MODEL` | Optionale andere Modellkette für den Jahresrückblick, kommagetrennt, für einen Versuch (`models_from_env` in `src/markdown_stats/review_client.py`). Leer gilt die Kette des KI-Wächters (`DEFAULT_MODELS`); ein ungültiger Name verwirft den ganzen Wert mit einer Log-Zeile. Kein Workflow setzt sie. |
| `KI_ENTSCHEIDUNG`        | `0` schaltet die Anfragen der KI-Entscheidung ab (`src/ki_entscheidung/run.py`, Standard an); gespeicherte Antworten gelten weiter, es entscheiden sonst die Regeln, und der Nebenschritt „KI-Entscheidung“ meldet Erfolg (ein früheres Rot bleibt so nicht stehen). Kein Workflow setzt sie. |
| `KI_ENTSCHEIDUNG_MODEL`  | Optionale andere Modellkette für die KI-Entscheidung, kommagetrennt, für einen Versuch (`models` in `src/ki_entscheidung/ask.py`). Leer gilt die Kette des KI-Wächters mit den Lite-Modellen voran (`MODELS`). Kein Workflow setzt sie. |
| `KI_ENTSCHEIDUNGEN_PATH` | Speicher der Antworten (Standard `data/ki_entscheidungen.json`, `store_path` in `src/ki_entscheidung/store.py`). Ein Pfad außerhalb der erlaubten Ordner: keine Anfrage, und der Nebenschritt meldet einen Fehler. |
| `SITE_BASE_URL`          | Basis-URL für die Sitemap-Generierung (`scripts/generate_sitemap.py`). Standard identisch mit `PAGES_BASE_URL`; gegen die GitHub-Pages-Allow-List validiert. |
| `WIEN_TOKEN`             | Override des Wien-Token-Matches für die `in_vienna`-Heuristik in `src/utils/stations.py` (Standard `wien`). Diakritik wird automatisch geklammert; nur für Test-Sandboxen interessant. |

Alle Pfade werden durch `resolve_env_path` (in `src/feed/config.py`) auf `docs/`, `data/` oder `log/` beschränkt, um Path-Traversal zu verhindern.

### Reihenfolge im Feed

Vorher, gleich nach dem Einsammeln, verwirft `_drop_test_messages`
Testmeldungen der Anbieter: Titel oder Text enthält „Testmeldung“, oder
Titel bzw. Text sind höchstens fünf Wörter lang und enthalten das Wort
„Test“ (ein Bindestrich gehört zum Wort: „Test-Fahrten“ ist keine
Testmeldung). Seit 2026-10-03 auch „Testfall“, „Testtext“ und ein Text
beliebiger Länge, der nach dem Linienpräfix nur aus „Test“ besteht
(„Test Test Tes Test Test Test“). Anlass: Zwei Testmeldungen der Wiener Linien standen am 23.09.2026
je einen Zyklus im Feed („71/72: Dies ist eine Testmeldung“, „62: F57f
Test“). In 488 Meldungen des Feeds und 952 der Caches traf die erste Fassung
nur diese beiden; „Haltestelle“ oder „Testbetrieb“ enthalten das Wort nicht.
Die heutige Regel trifft in der ganzen Cache-Historie 26 Titel, alle Tests.

Nach Altersfilter und beiden Dedupe-Stufen legt `_merge_wl_ticker_clusters`
die WL-Störungen eines Vorfalls zusammen: dieselben Linien, veröffentlicht
innerhalb von zehn Minuten nach der ersten. Titel ist Linie plus häufigste
Ursache, die Beschreibung sammelt alle Folgen („62: ÖBB Bauarbeiten“ über
„Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein
Betrieb.“). Sagt eine ausführliche Meldung der Gruppe mehr als WLs
Standardsatz, steht stattdessen sie, so wie allein (Details in
`docs/architecture.md`). Kurzmeldungen einzelner Linien gehen in die
ausführliche Meldung eines Vorfalls mehrerer Linien auf, wenn sie dieselbe
Ursache nennen und höchstens zehn Minuten vor ihr begannen
(`_join_part_line_tickers`, seit 2026-10-15; „30A/31A/36B:
Gasrohrgebrechen“ stand am 11.10. sonst neben drei Tickern „Gasrohrbruch“).
Zeigt ein solcher Eintrag nur die Baustelle eines
WL-Hinweises (gleiche Ursache, Linien, Zeitraum und eine gemeinsame Straße),
geht er in diesem Hinweis auf (`_absorb_works_tickers`). Meldet umgekehrt
eine laufende WL-Störung mit Vorfallsbeginn (ausführliche Meldung) dieselbe
Ursache an derselben Straße für jede Linie eines geltenden Hinweises über
höchstens einen Betriebstag, tritt der Hinweis zurück, solange sie läuft,
auch wenn WL der Störung ein Ende gibt (`_yield_notices_to_incidents`, seit
2026-11-01, Betreiberentscheidung „Störung bleibt“ und Grundsatz „Störungen
haben höchste Priorität im Feed! Hinweise sind weniger wichtig.“; Anlass „1/2/71/D:
Nationalfeiertag“ neben „1/2/71/D: Veranstaltung“ am 26.10.). Tests dazu lesen die Meldungen wie der Feed, über
`_post_filter_wl`. Danach sortiert der Build die
Items nach `first_seen` (neueste zuerst; Gleichstand: Störung vor Baustelle,
dann `pubDate`). Davor stehen immer die aktuellen Störungen
(`_is_current_incident`, Betreiberentscheidung vom 2026-10-04: „Aktuelle
Störungen sollen oberste Priorität haben. Vorangekündigte Baustellen sollen
eine niedrigere Priorität haben.“): ungeplante Störungen, deren Zeitzeile
„[Seit hh:mm]“ zeigt (`_incident_since`), die begonnen haben und seit
höchstens `_CURRENT_INCIDENT_WINDOW` (24 h) im Feed stehen. Wo die Wörter
einer neuen Störung offen lassen, ob sie plötzlich kam oder geplant ist,
entscheidet seit 2026-11-12 eine gespeicherte Antwort von Gemini
(`_ki_art`, `src/ki_entscheidung`; [`architecture-teil-16.md`](architecture-teil-16.md), §14). Untereinander
gilt in beiden Gruppen dieselbe Sortierung. Ohne diese Stufe stand am
03.10. um 15:01 „43A: Veranstaltung“ vor „9A: Rettungseinsatz“, am 04.10. um
16:30 „U6: Neue Donau, kein Halt“ vor vier laufenden Störungen. Die 24 h
halten Langläufer, die wie Störungen klingen, in der normalen Reihenfolge
(„18: Haltestelle Stadionbrücke … aufgelassen“ seit 13.07., der täglich
neue „S80: ÖBB-Ersatzbus“ seit 25.09.). Im Nachbau von 36 Feed-Ständen (Wiener Zeit, Fr
02.10. 07:00–18:30, Sa 03.10. 15:00 bis So 04.10. 22:30) änderte sich in 14
nur die Reihenfolge innerhalb der zehn Plätze, keine Meldung kam dazu oder
fiel heraus. Eine neue Meldung zählt mit `first_seen` = jetzt, eine
WL-Haltestellenverlegung oder -auflassung dagegen ab ihrer Veröffentlichung
bei WL (`pubDate`, `_initial_first_seen`, seit 2026-10-04): Mit „Alle
aufnehmen“ kamen auf einen Schlag 24 solche Meldungen dazu, manche seit 2023
gültig, und hätten als neu alle zehn Plätze belegt. So steht jede dort, wo
sie ohne den alten Filter stünde; „26E/N20: Fultonstraße“ (veröffentlicht
28.09., ab 05.10.) zählt ab dem 28.09. und rückt wie jede Ankündigung erst
ab dem Vortag nach vorn. Im Feed-Nachbau der zehn echten Läufe vom
2026-10-04 (09:31–14:00 UTC) kamen so zwei Meldungen unter die ersten zehn
(„16A/N65: Grohnergasse“, „26E/N20: Fultonstraße“), keine Welle.

Zwischen den aktuellen Störungen und dem Rest steht seit 2026-11-08 eine
ÖBB-Sperre an den Tagen, an denen sie die Strecke wirklich sperrt
(`_is_oebb_closure_day` in `src/feed_build/oebb_days.py`, Betreiberentscheidung
vom 2026-11-08: „Störungen gehen immer vor.“, „Man könnte es auch als Hinweis
mit höherer Priorität formulieren“ und „Auf ungeplante Störungen kann man sich
nicht vorbereiten. Auf geplante Bauarbeiten, Sperren etc schon.“). Die
ungeplanten Störungen bleiben vorn; die Sperre bleibt eine geplante Maßnahme,
steht an ihren Sperrtagen aber vor den übrigen Hinweisen. Die Tage stehen im
Leitsatz der Meldung, dem Satz, den der Feed zeigt: „von … bis …“ (ein Ende
mit Uhrzeit, „bis 27.10.2026 (04:00 Uhr)“, gilt am letzten Tag nur bis dahin),
„zwischen 29.10.2026 und 01.11.2026“, „am … und …“ und Listen mit Komma („am
03.02.2027, 03.03.2027 … und 02.06.2027“); ein Tag ohne Jahr nimmt das Jahr
des nächsten vollen Datums („von 08.03. bis 10.03.2027“, „am 02.07., 06.08.
… und 05.11.2026“), zwei Ziffern stehen für 20xx („am 03.02.27“). Ein
Wochentagsfenster schränkt die Tage ein („(jeweils von Samstag 23:15 Uhr bis
Sonntag 04:40 Uhr und …)“ ergibt Samstag und Sonntag, „(jeweils Sonntags … und
Montags …)“ Sonntag und Montag). Sperrstunden innerhalb eines Tages enden dort,
wenn der nächste Tag nicht weiter gesperrt ist: „R95: Wien Hauptbahnhof ↔
Felixdorf“ „(jeweils 14:00 Uhr - 22:00 Uhr)“ stand am 10.11. bis 23:30 vor
den WL-Hinweisen, nach dem Ende der Sperre. „bis voraussichtlich am
07.10.2026“ ist das erwartete Ende einer Störung, kein Sperrtag. Der Leitsatz
ist der Satz, den der Feed zuerst zeigt, auch wenn dort ein späterer
Abschnitt steht, weil er die Strecke des Titels nennt
(`_oebb_shown_sentences` in `src/feed_build/oebb_lead.py`, seit 2026-11-11;
vorher las die Prüfung ÖBBs ersten Satz: Unter „Wien Hütteldorf ↔ Wien
Hauptbahnhof“ zeigte der Feed am 17.10. „Teilausfall: Von 18.10.2026 bis
19.10.2026 …“, die Tage kamen aus den geänderten Fahrzeiten zwischen Salzburg
und Flughafen Wien, 17. bis 21.10.); nennt dieser Abschnitt kein Datum, kommen
die Tage aus ÖBBs erstem Satz. Ein Leitsatz zählt seit 2026-11-11 nur, wenn
er sagt, dass keine Züge fahren oder halten („keine Nahverkehrszüge fahren“,
„keine Züge halten“, „keine Fahrten möglich“, „Zugfahrten derzeit nicht
möglich“, auch am Ende einer Zugliste: „… keine S50-Züge … die S50-Züge 20063,
… nicht fahren“; `_oebb_closes_line`). Ein einzelner Zug, eine Liste von
Zügen, geänderte Fahrzeiten oder eine Umleitung sperren keine Strecke und
reihen sich wie die übrigen Hinweise ein (Betreiberentscheidung vom
2026-11-11, „Nein, nur echte Sperren“). Anlass (unabhängige Prüfung vom
2026-11-11): „S1: Wien Leopoldau ↔ Deutsch Wagram“ („der S1-Zug 23626 …
nicht fahren“) stünde am 14., 15., 21., 22. und 28.11. vor allen
WL-Hinweisen, für einen Zug zwischen Deutsch Wagram und Gänserndorf,
außerhalb Wiens; vom 01.10. bis 11.11. verdrängten einzelne Züge (S1 23626,
S2 23360, REX64 2679, RJ 263) in 152 von 1.220 nachgespielten Feed-Ständen
WL-Hinweise aus den ersten zehn. Seit derselben Entscheidung bleibt eine
Meldung, deren Leitsatz nur einzelne Züge nennt, ganz aus dem Feed
(„Einzelzüge sollen nicht in den Feed.“, Abschnitt „ÖBB“ unten).
Nennt eine Meldung gar keinen Tag (eine ungeplante Störung „derzeit“ oder
„bis voraussichtlich 09:00 Uhr“), zählt jeder Tag zwischen Beginn und Ende
der Meldung. Nennt sie Tage in einer Form, die das Lesen nicht kennt („in der
Nacht von 14.02. auf 15.02.2026“), ist sie geplant und zählt nur, wenn sie
sperrt (`_names_measure_day`). Die
Kategorie allein trägt das nicht: WL schickt geplante Tafel-Meldungen
(„26E: Gleisbauarbeiten“, „S80: ÖBB-Ersatzbus“) als „Störung“, und im Nachbau
von 312 Feed-Ständen seit 15.10. wären sie in 308 nach vorn gerückt. Anlass:
„Wien Hauptbahnhof ↔ Gramatneusiedl“ (Wochenendnächte 31.10.–30.11.) fiel am
07.11. hinter neuere WL-Haltestellenverlegungen auf Platz 11 bis 14 und stand
in seinen Sperrnächten nicht am Bildschirm. Nachbau derselben 312 Stände: Die
aktuellen Störungen und die Menge der Meldungen bleiben gleich; sieben
ÖBB-Meldungen kommen an ihren Sperrtagen unter die ersten zehn (höchstens
vier zugleich), dafür fallen WL-Hinweise heraus. Mit der Regel vom 11.11. sind
es vier echte Sperren (Hbf ↔ Westbahnhof, REX 50, Gramatneusiedl, R40),
höchstens zwei zugleich (Nachbau aller 872 Stände vom 15.10. bis 08.11.).
Seit 2026-11-12 hat eine Sperre, die länger als vier Wochen Tag für Tag
sperrt, den Vorrang nur in ihren ersten sieben Tagen (`_past_first_week`,
Betreiberentscheidung „Vorrang nur in der ersten Woche“; Anlass „Wien
Praterstern ↔ Wien Hauptbahnhof / Wien St. Marx“, 07.09.2026 bis 31.10.2027).
Danach steht sie bei den übrigen Hinweisen und zählt dort ab ihrem ersten Tag
(`_long_closure_start`); Wochenendnächte und kürzere Sperren behalten ihn (Lesarten: `docs/architecture.md`).

Wiederkehrende WL-Meldungen: Die WL-GUID besteht aus Kategorie, Thema und
Linien, ein Datum enthält sie nicht. „94A: Verkehrsunfall“ hat an jedem Tag
dieselbe GUID. Ohne Gegenmaßnahme erbt ein neuer Unfall das `first_seen` des
Unfalls vor Monaten und landet hinter allen Haltestellenverlegungen. Deshalb
setzt `_restart_recurring_occurrences` vor dem Altersfilter `first_seen` auf
den `pubDate` (Gültigkeitsbeginn) der Meldung, sobald zwei Bedingungen
zutreffen: `first_seen` liegt vor diesem Beginn, und die Meldung fehlte
zuletzt länger als `_OCCURRENCE_GAP` (2 h) in den Daten. Wann eine Meldung
zuletzt da war, steht im State-Feld `last_seen`. Der Build stempelt es bei
jedem Lauf für jedes WL-Item mit State-Eintrag, das noch gilt. Laufende Maßnahmen, die WL
täglich mit neuem Gültigkeitsfenster neu ausgibt („Busse halten …“), waren
bis zum neuen Beginn da und behalten ihren Platz. Geplante Maßnahmen
(dieselben Wörter wie bei der Zeitzeile: Bauarbeiten, Veranstaltung,
verlegte Haltestelle usw.) pausieren länger, etwa „66A: Busse halten
Salvatorianerplatz“ jede Nacht von 01:00 bis 04:40; für sie gilt
`_PLANNED_OCCURRENCE_GAP` (36 h). Mit 2 h zählte 66A jeden Morgen als neu
und stand seit 26.09. in 370 von 387 Feed-Ständen vorn, obwohl es seit
28.08. im Feed war (Audit und Betreiberentscheidung 2026-10-03). ÖBB, Baustellen und
Stammstrecke bleiben unberührt, denn dort ist `pubDate` kein Beginn eines
Auftretens.

Der State-Eintrag bleibt, wenn eine Meldung ausscheidet (seit 2026-11-12):
Bis dahin löschte ein Bau, der das Ende einer Meldung selbst sah, ihren
Eintrag. Ein Takt-Lauf (`update-cycle.yml`) sieht das fast nie, der Abruf
lässt eine beendete Meldung vorher weg; ein Neubau nach einem Merge
(`build-feed.yml`) liest sie noch aus dem Cache. Am 11.11.2026 um 01:24
löschte ein Neubau so den Eintrag von „66A: Busse halten
Salvatorianerplatz“ (Ende 01:00); um 05:00 stand die Maßnahme als neu auf
Platz 2. Jetzt löscht kein Bau einen Eintrag, nur die Aufbewahrungsfrist
(`STATE_RETENTION_DAYS`, ab `first_seen`) vergisst ihn, und den Eintrag
einer abgelaufenen Meldung fasst `_restart_recurring_occurrences` nicht mehr
an. Eine Meldung jenseits von `MAX_ITEM_AGE_DAYS` kommt damit nicht mit dem
nächsten Bau als neu zurück (`tests/test_state_entry_survives_expiry.py`).

Eine Meldung, eine GUID (`_carry_item_identity`, seit 2026-10-06): Ein
zusammengelegter Eintrag trägt GUID und `pubDate` einer seiner Meldungen,
etwa der frühesten Kurzmeldung (`_merge_wl_ticker_clusters`) oder des
Überlebenden von `deduplicate_fuzzy`. Fiel genau diese Meldung weg,
während die übrigen blieben, bekam der Eintrag die GUID der nächsten, und
die Zeitzeile sprang. „43: Verkehrsunfall“ stand am 06.10. ab 00:30 als
„[Seit 00:03]“ im Feed; um 01:03 liefen die Kurzmeldungen aus, und der
Rebuild um 01:14 zeigte ohne neue Daten eine neue GUID und „[Seit 00:04]“.
Ebenso „1: Verkehrsunfall“ am 06.10. um 11:30 (ausführliche Meldung
erledigt, „[Seit 10:40]“ wurde „[Seit 10:43]“), „41/42: Verkehrsunfall“
am 05.10. um 01:30 (WL ersetzte die Störung durch je eine Folgemeldung pro
Linie) und „9: Falschparker“ am 05.10. um 19:01. Seither merkt sich der
Build nach jedem Lauf im State-Eintrag jedes Items, für welche Meldungen es
steht (`members`: deren GUIDs und, für WL, die Störungsnummer
`I20261004-0019` ohne `-F01`, die `wl_fetch` als `_wl_ids` in den Cache
schreibt), wann (`members_seen`) und bei WL-Störungen das früheste `pubDate`
(`earliest_published`). Ein Item, das in keinem Lauf der letzten
`_OCCURRENCE_GAP` (2 h) unter seiner eigenen GUID stand, übernimmt die GUID
des Items, mit dem es dort eine Meldung teilte, und dessen frühestes
`pubDate`, wenn seine eigene Meldung darin enthalten war oder nicht neuer
ist als dessen Beginn (WL trägt die ausführliche Meldung oft nach den
Kurzmeldungen ein und datiert sie zurück). Eine neuere Meldung ist eine
neue Störung, die eine laufende aufnimmt („52: Rettungseinsatz“ am 05.10.
mit einer Kurzmeldung vom 30.09.): Sie behält ihre GUID und steht vorn.
Tragen beide Seiten WL-Störungsnummern und teilen keine, ist es ebenfalls
eine neue Störung (`_other_incident`): Sie übernimmt weder GUID noch Beginn.
„13A/14A: Falschparker“ am 06.10.: `I20261006-0030` (Beginn 17:22) endete,
`I20261006-0033` (Beginn 17:44) kam unter derselben GUID und zeigte ab 18:30
„[Seit 17:22]“.
Ein Eintrag mehrerer Störungen einer Linie zählt nur die Meldungen der
neuesten; endet sie, kehrt er zur GUID der älteren zurück. Eine Meldung
ohne Ursache, die älter ist als jede dieser Störungen, zählt nie mit
(`_with_unowned`, seit 2026-10-09): Die Haltestellen-Meldungen der
Gleisbauarbeiten der Linie 25 (seit 23.09.) übernahmen sonst am 09.10.
nach dem Ende von „25: Verspätungen“ deren GUID und Beginn und standen von
12:01 bis 22:02 als neue Störung im Feed, bis auf Platz 2. Und ein Item, das
unter seiner eigenen GUID früher im Feed stand als das Item, mit dem es eine
Meldung teilt, übernimmt dessen GUID nie: Es war zuerst da, das andere hat
es aufgenommen. Der Zustand aus der Zeit vor der Regel hielt die
Haltestellen der Linie 25 sonst auch nach dem Merge unter der GUID der
Verspätung, im Lauf um 22:00 auf Platz 2, und jeder weitere Lauf hätte das
fortgesetzt. „Im Feed stand“ heißt: Ein Lauf hat den eigenen State-Eintrag
seit dessen `first_seen` notiert (`members_seen`), und dieses `first_seen`
ist älter als das des anderen. `first_seen` allein genügt nicht: Bei einer
wiederkehrenden WL-GUID ist es der Beginn der Meldung selbst
(`_restart_recurring_occurrences`), und WL datiert die lange Meldung zurück
(„60: Schadhafter PKW“ am 01.10., datiert 18:48, kam nach den Tafeln von
18:50 und trägt deren GUID weiter). Der erste Lauf
nach der Änderung schreibt nur die Notizen und ändert keine GUID, es gibt
also keine Welle. Im Feed-Nachbau aller 117 Läufe vom 04.10. 11:31 bis
06.10. 14:31 (Wiener Zeit, WL-Rohdaten neu durch `wl_fetch` geschickt, State
weitergetragen) unterschieden sich 11 Feed-Stände, alle an diesen Fällen.

Angekündigte Maßnahmen zählen ab ihrem Beginn als neu
(`_note_announced_starts`, `_sort_moment`, seit 2026-10-04), stehen aber wie
alles Geplante hinter den aktuellen Störungen: Solange der Beginn (`starts_at`) eines Items mit
State-Eintrag noch bevorsteht, merkt sich der Build ihn im State-Feld
`announced_start` (ein verschobener Beginn überschreibt ihn). Ist er
erreicht, sortiert das Item mit diesem Zeitpunkt statt mit seinem
`first_seen` und altert danach wie jedes andere. Vorher stand eine Wochen
früher angekündigte Sperre schon an ihrem ersten Tag hinten: die
S-Bahn-Stammstrecke Phase 2 (gesehen 10.06.) war am 07.09. auf keinem der
zehn Plätze, die ÖBB-Sperre Hbf–Gramatneusiedl (gesehen 08.07., gültig
03.–05.10.) stand am 04.10. auf Platz 49. Was erst ab seinem Beginn in den
Daten steht, bekommt kein `announced_start`; WL-Maßnahmen, die jede Nacht neu
ausgegeben werden („66A: Busse halten Salvatorianerplatz“), behalten so
ihren Platz. Im Nachbau (Wiener Zeit) Fr 02.10. 21:00 bis Sa 03.10. 10:30 (33 Stände, State
weitergetragen) änderte diese Regel nur eines: die ÖBB-Sperre stand ab
Samstag 00:00 auf Platz 1 bis 4, auch mit der Störungsstufe davor. Mi 30.09. 20:00 bis Do 01.10. 11:30 (32 Stände)
blieb unverändert.

Danach greifen vier Regeln, die Plätze freihalten, ohne etwas zu
löschen. Alle vier stellen Items nur hinter das Feld, von wo sie nachrücken:

1. `_defer_repeated_route_titles`: Von mehreren ÖBB-Items mit wortgleichem
   Titel (dieselbe Strecke in mehreren Bauphasen, z. B. dreimal
   `Wien Hauptbahnhof ↔ Gramatneusiedl`) bleibt nur eines vorn: eine laufende
   Störung, sonst eine Sperre an ihrem Sperrtag, sonst das mit dem frühesten
   Zeitfenster. Zusammengeführt wird nicht — die Phasen sind verschiedene Maßnahmen. Seit 2026-10-05 behält eine laufende Störung
   (`_is_current_incident`) den Platz ihres Titels: ÖBB benennt eine
   Störung wie eine Bauphase nach der Strecke, und eine laufende Bauphase
   hat immer das frühere Fenster. Ein Unfall zwischen Wien Hbf und Wien
   Westbahnhof wäre sonst neben den Bauarbeiten dort (11.09. bis
   01.11.2026) hinter das Feld gerutscht. Seit Juli kam das noch nicht vor. Seit 2026-11-13 hält vor dem frühesten Fenster eine Sperre an ihrem Sperrtag den Platz (`leads`, Stufe 1 des Sortierschlüssels; von mehreren die mit dem frühesten Fenster), Betreiberentscheidung „Ja, Sperre des Tages zuerst“ („Bei gleichem ÖBB-Titel hält die Sperre mit Sperrtag den Platz, das frühere Fenster rückt hinter das Feld.“): Am 09.11.2026 von 04:31 bis 06:31 Uhr hielt das Fenster der Wochenendnächte Gramatneusiedl (31.10. bis 30.11.; der Montag zählt nicht als sein Sperrtag) den Platz, und die Meldung des Tages („Wegen Verlängerung der Bauarbeiten sind … bis voraussichtlich 05:30 Uhr keine Fahrten möglich“) stand hinter dem Feld (im Nachspiel mit der Regel vom 2026-11-12, Commit aed1c9be6, auf Platz 61 bis 64). Stufe 1 ist weiter als eine Sperre im engen Sinn (eine ÖBB-Meldung ganz ohne Tag zählt an jedem Tag ihres Zeitraums, auch „waren bis 06:34 Uhr nur eingeschränkt Fahrten möglich“) und enger (eine Sperre über vier Wochen ist nach ihrer ersten Woche ein Hinweis, Entscheidung 2026-11-12: Sie gibt den Platz ab, sobald eine Sperre derselben Strecke Sperrtag hat, und nimmt ihn keinem früheren Fenster). Unter mehreren Sperren mit Sperrtag entscheidet das Fenster und nicht der Platz in der Sortierung: Der Bau legt State-Einträge nur für das an, was er zeigt, die Meldung hinter dem Feld gälte im nächsten Bau als eben erschienen und nähme den Platz zurück, und eine neuere Meldung, die nichts sperrt, stünde vor einer Sperre in ihrer Sperrnacht. Das gilt auch am Sonntagmorgen: Dann hat auch das Fenster der Wochenendnächte Sperrtag und behält den Platz neben einer Meldung des Tages, die wie es als Sperre mit Sperrtag zählt; sie steht hinter dem Feld (Betreiberentscheidung 2026-11-13 „Nein, Fenster behält den Platz“: „Es bleibt, wie es ist: An Samstag und Sonntag steht das Wochenendfenster vorn, die Meldung des Tages hinter dem Feld.“). Es bleibt damit auch dabei, dass die aktuelle Störung vorgeht: Zählt die Meldung des Tages als aktuelle Störung (nach den Regeln oder nach der gespeicherten Gemini-Antwort „ploetzlich“ aus `src/ki_entscheidung`; so lautete die Antwort im Probelauf vom 12.11.2026 für „Wegen Verlängerung der Bauarbeiten sind … keine Fahrten möglich“ vom 09.11.), hält sie den Platz auch am Sonntag, bis ihr Text „waren bis …“ lautet. Von der Karte nicht genannt und unverändert: Am ersten Tag des Fensters beginnen beide am selben Tag, dort entscheidet der Platz in der Sortierung, und die neuere Meldung des Tages steht vorn, auch in der ersten Sperrnacht. Ohne aktuelle Störung und ohne Sperre mit Sperrtag bleibt es beim frühesten Fenster, auch neben einem neueren Hinweis zur Strecke, weil ein nur angekündigtes Fenster danach noch hinter das Feld rückt (`_defer_upcoming_items`). Nachspiel alt gegen neu über 2.241 Feed-Stände (01.10. bis 13.11.2026, 12:01 Uhr): kein Fehler, genau die fünf Stände vom 09.11. ändern sich, die Meldung des Tages steht dort auf Platz 1 bis 5 direkt hinter den aktuellen Störungen (in drei der fünf schon mit „waren bis 05:15 Uhr keine Fahrten möglich“).
2. `_apply_topic_budget`: höchstens `MAX_ITEMS_PER_TOPIC` Einträge je
   Ursachenwort und Tag in den vorderen Plätzen.
3. `_defer_upcoming_items`: Was erst nach `UPCOMING_PREVIEW_DAYS` Tagen ab
   heute (Wiener Kalendertag, Standard 1) beginnt, steht hinter allem, was
   gilt oder bald beginnt. Betreiberentscheidung vom 02.10.2026: Am 02.10.
   um 18:00 belegten fünf der zehn Plätze Meldungen, die noch nicht
   begonnen hatten („20A: Bauarbeiten“ ab 13.10., die Sperre der R 40 ab
   31.10.), während „N71: Ersatzverkehr“ und „62: ÖBB Bauarbeiten“, beide
   an diesem Abend gültig, auf den Plätzen 11 und 12 standen. Anfangs galten
   drei Tage Vorlauf. Nachgerechnet über 3.875 Feed-Stände seit 15.07.
   belegten Ankündigungen mit Beginn in ein bis drei Tagen so 1.442 Plätze
   in 1.083 Ständen, jedes Mal mit einer laufenden Meldung auf Platz 11 oder
   dahinter (Audit und Betreiberentscheidung 2026-10-03). Seitdem steht eine
   Ankündigung ab dem Vortag ihres Beginns vorn, freitags und samstags
   außerdem alles, was bis einschließlich Montag beginnt
   (`_preview_last_day`, Betreiberentscheidung „Wochenende mit“): Wer die
   Anzeige nur werktags sieht, liest die Umleitung ab Montag noch am Freitag.
   Die Sperre in vier Wochen rückt nach, sobald ein Platz frei ist.
4. `_defer_all_clear_items`: Entwarnungen der ÖBB („Aufhebung
   Verkehrseinschränkung: …“, „Aufhebung Streckenunterbrechung: …“) stehen
   hinter allen anderen Items. Betreiberentscheidung vom 25.09.2026: Eine
   laufende Störung ist wichtiger als eine Entwarnung. Eine Entwarnung ist
   aber besser als ein leerer Platz. Sie erscheint deshalb nur, wenn weniger
   als `MAX_ITEMS` andere Items vorliegen.

Erst dann schneidet `MAX_ITEMS` ab.

### Logging-Initialisierung als Bibliothek verwenden

Wird `build_feed` als Skript ausgeführt (`python -m src.cli feed build`), richtet es seine Logging-Handler automatisch über
`configure_logging()` ein. Beim Einbinden des Moduls in andere Anwendungen bleibt die globale Logging-Konfiguration ab
Python-Import unverändert; rufe in diesem Fall `src.build_feed.configure_logging()` explizit auf, bevor du die Feed-Funktionen
verwendest.

### Fehlerprotokolle

- Läuft der Feed-Build über `python -m src.cli feed build`, landen Fehler- und Traceback-Ausgaben automatisch in `log/errors.log` (rotierende Log-Datei, konfigurierbar über `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`). Ohne Fehler bleibt die Datei unberührt.
- Ausführliche Statusmeldungen (z. B. zum VOR-Abruf) werden zusätzlich in `log/diagnostics.log` gesammelt.
- Beim manuellen Aufruf der Hilfsskripte (bzw. `python -m src.cli cache update wl`) erscheinen Warnungen und Fehler direkt auf `stdout`. Für nachträgliche Analysen kannst du den jeweiligen Lauf zusätzlich mit `LOG_DIR` auf ein separates Verzeichnis umleiten.
- Setzt du `LOG_FORMAT=json`, schreibt das Projekt strukturierte JSON-Logs mit Zeitstempeln im Format `Europe/Vienna`. Ohne Angabe bleibt das klassische Textformat aktiv.

## Feed-Ausführung lokal

Vor produktiven oder manuellen Abrufen empfiehlt sich ein schneller
Vollständigkeitscheck der benötigten Secrets:

```bash
python -m src.cli tokens verify
```

Das Skript lädt automatisch `.env`, `data/secrets.env` und
`config/secrets.env` und bricht mit Exit-Code `1` ab, wenn kein gültiger
`VOR_ACCESS_ID`-Token gefunden wurde.

```bash
export WL_ENABLE=true
export OEBB_ENABLE=true
export BAUSTELLEN_ENABLE=true
export STAMMSTRECKE_ENABLE=true
# Stammstrecke-Monitor benötigt VOR-Secrets: VOR_ACCESS_ID, VOR_BASE_URL.
# Eine eigenständige VOR_ENABLE-Variable gibt es seit der 2026-05-11-
# Konsolidierung nicht mehr (VOR ist Stammstrecke-only).
python -m src.cli feed build
```

Der Feed liegt anschließend unter `docs/feed.xml`. Bei Bedarf lässt sich `OUT_PATH` auf ein alternatives Verzeichnis umbiegen.

Weiter in [Teil 3](development-teil-3.md).
