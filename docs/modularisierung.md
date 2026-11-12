# Modularisierung: höchstens 700 Zeilen je Datei

Michaels Regel vom 2026-11-02: Das Projekt wird modularer, keine Datei hat
mehr als 700 Zeilen. Regel, Umfang und Wächtertest stehen in der
[Architektur-Karte](architecture.md#höchstens-700-zeilen-je-datei-seit-2026-11-02).
Diese Seite beschreibt die Etappen und wie die geteilten Dateien aufgebaut
sind.

Das CHANGELOG ist seit Etappe 1 (2026-11-02) aufgeteilt, auf Michaels Wort
nach Tagen wie die täglichen Audit-Dateien: `CHANGELOG.md` ist nur noch die
Übersicht, die Einträge stehen in `changelog/<JJJJ-MM-TT>.md`, je Datei die
neuesten oben. Maßgeblich ist das Datum im fett gesetzten Titel des
Eintrags. 21 Einträge aus dem Mai 2026 tragen keines und stehen in
`changelog/2026-05-ohne-tag.md`, die alten Versionsabschnitte in
`changelog/bis-2026-05.md`. Der längste Tag (2026-09-12) hat knapp 500
Zeilen; hätte ein Tag mehr als 700, geht es in `<JJJJ-MM-TT>-2.md` weiter.
Ein neuer Eintrag kommt oben in die Datei seines Tages (Wiener Datum); gibt
es sie noch nicht, wird sie mit demselben Kopf angelegt und in
`CHANGELOG.md` verlinkt. Der Ordner liegt bewusst nicht unter `docs/`, damit
GitHub Pages die Dateien nicht mit Jekyll rendert (das CHANGELOG enthält
Liquid-Klammern aus Code-Beispielen). `tests/test_changelog_parts.py` prüft,
dass die Übersicht jede Datei genau einmal nennt, keine Einträge enthält,
die Tage absteigend listet und jede Tagesdatei ihren Wochentag im Kopf trägt.

Etappe 2 (2026-11-02) teilt die langen Testmodule. Aus
`tests/…/test_x.py` wird das Paket `tests/…/test_x/`: `__init__.py` trägt
die alte Modulbeschreibung, `_support.py` alle Hilfen, Konstanten und
Fixtures (mit `__all__`), `conftest.py` macht die Fixtures für pytest
sichtbar (nur wo es Fixtures gibt), und `test_teil_<n>.py` enthält die Tests in ihrer alten
Reihenfolge, jeder Teil unter 700 Zeilen. Pfade aus
`Path(__file__).resolve().parents[N]` zählen eine Ebene mehr, weil der Code
eine Ebene tiefer liegt. Testnamen und Reihenfolge bleiben gleich
(Sammlung vorher und nachher: 2.251 Tests, gleiche Namen, gleiche
Reihenfolge). Ein neuer Test für so ein Paket kommt in den passenden Teil;
wird ein Teil zu lang, entsteht `test_teil_<n+1>.py`.

Etappe 3 (2026-11-02) teilt die langen Markdown-Seiten: `docs/architecture.md`,
`docs/development.md` und fünf archivierte Audits. Eine Seite `x.md` behält
ihren Anfang (Titel, Inhaltsverzeichnis), der Rest folgt unverändert in
`x-teil-2.md`, `x-teil-3.md` … im selben Ordner, damit relative Links
weiter stimmen. Geschnitten wird vor einer `##`- oder `###`-Überschrift,
sonst zwischen zwei Absätzen oder Listenpunkten, nie in einem Codeblock.
Jeder spätere Teil nennt oben die erste Seite und seine Nummer, jeder Teil
außer dem letzten verlinkt unten den nächsten; Anker-Links zeigen auf den
Teil, der die Überschrift hält. `tests/test_doc_parts.py` prüft die Kette,
`tests/doc_parts.py` (`read_doc`) liest eine Seite für Tests wieder am
Stück. Wer einen Abschnitt ergänzt, schreibt ihn in den Teil, in den er
gehört; wird der Teil zu lang, kommt ein neuer Teil dahinter.

Etappe 4a (2026-11-02) teilt das Stylesheet der Webseite. Statt
`docs/assets/site.css` gibt es `docs/assets/site-css/teil-01.css`,
`teil-02.css` … ; geschnitten wird nur zwischen zwei Regeln der obersten
Ebene, nie in einer Regel oder einem `@media`-Block. Die Seite lädt weiter
nur `site.min.css`: `scripts/optimize_site_assets.py` fügt die Teile in
Namensreihenfolge zusammen und minifiziert sie wie vorher, das Ergebnis ist
Byte für Byte gleich. Gelesen werden die Teile über
`src/utils/site_sources.py` (`read_capped_source` mit derselben
Größengrenze wie vorher, `read_site_css` für Tests). Ein neuer Abschnitt
kommt in den Teil, in den er gehört; wird ein Teil zu lang, kommt ein neuer
Teil dahinter.

Etappe 4b (2026-11-02) macht das Skript der Webseite auf Michaels Wahl
(„Echte Module“) zu acht Modulen in `docs/assets/site-js/`:
`teil-01-grundlagen.js` (Adressen, DOM-Helfer, Laden), `teil-02-sprache.js`
(englische Texte, gewählte Sprache, Zahlen- und Datumsformate, Status- und
Fehlerzeile), `teil-03-diagramme.js`, `teil-04-feed.js`,
`teil-05-statistik.js` (Störungen, Zugausfälle), `teil-06-live-kachel.js`
(Live-Kachel, Stammstrecke, Abdeckungshinweis), `teil-07-wetter.js` und
`teil-08-start.js` (Laden, Nachladen beim Scrollen, Filter, Sprachwechsel).
Jedes Modul ist eine eigene Funktion mit eigenem Gültigkeitsbereich. Was
es von früheren Modulen braucht, holt es am Anfang aus dem gemeinsamen
Namensraum `WienOepnv` (`const { … } = WienOepnv;`), was spätere brauchen,
legt es am Ende dort ab (`Object.assign(WienOepnv, { … })`); den
Namensraum legt nur das erste Modul an, als Konstante des Skripts, nicht
als Eigenschaft von `window`. Ein Modul darf nur Namen früherer Module
nehmen. Die gewählte Sprache lesen die anderen Module über `getLang()`,
der Sprachwechsel läuft über `setLang()`. Die Zahlen- und Datumsformate
sind feste Objekte, hinter denen `setLang()` das Intl-Format tauscht.
Die Seite lädt weiter nur `site.min.js`: `optimize_site_assets.py` fügt
die Module in Namensreihenfolge zusammen. Prüfung vorher/nachher in
Chromium (Desktop und Telefon: Start, Statistik nach dem Scrollen, Filter,
Englisch, zurück auf Deutsch, Aktualisieren): DOM in jedem Schritt gleich,
Bildschirmfotos mit reduzierter Bewegung pixelgleich, keine Fehler in der
Konsole. `tests/test_site_sources.py` prüft die Reihenfolge der Module,
den Namensraum und den Sprachwechsel der Formate. Eine neue Funktion kommt
in das Modul ihres Themas; braucht ein früheres Modul sie, gehört sie
dorthin oder in ein früheres.

Etappe 5 (2026-11-02) teilt `tests/conftest.py` (966 Zeilen). Die Fixtures
und Hilfen stehen jetzt in `tests/_conftest/`: `streckendaten.py` (der
Datensatz Streckendaten Notfallmanagement), `zustand.py` (Fixtures, die den
Zustand zwischen Tests zurücksetzen), `netz.py` (kein echter DNS, kein Netz,
Umgebung bleibt) und `koordinaten.py` (`assert_coords_close`).
`tests/conftest.py` behält die Einstellungen, die vor jedem Import gelten
müssen (Hypothesis-Profil, Suchpfad, `HF_HUB_OFFLINE`), und importiert jede
Fixture von dort. So bleiben sie Fixtures des ganzen Test-Ordners, und
`from tests.conftest import …` funktioniert weiter. pytest ordnet
Autouse-Fixtures nach ihrem Namen, die Reihenfolge bleibt also gleich:
Der Setup-Plan aller 12.460 Tests (`pytest --setup-plan`) ist vorher und
nachher gleich. Eine neue Fixture kommt in das Modul ihres Themas und wird
in `tests/conftest.py` mit importiert.

Etappe 6 (2026-11-02) teilt die zwölf langen Skripte (zusammen rund 18.400
Zeilen). Wegen der Regel „Skripte importieren nur aus `src.`“ liegt der
verschobene Code unter `src/`, das Skript behält Aufruf, Optionen und die
Namen, die andere Dateien und Tests lesen (als ausdrückliche
Weiterreichung, `from src.x import name as name`):

| Skript | Neue Module |
|---|---|
| `enrich_station_aliases.py` | `src/places/station_aliases.py` |
| `feed_watch.py` | `src/feed_watch/` (model, feed_rules, sources, evaluate, issues, state_io; seit 2026-11-10 skipped_items) |
| `generate_markdown_stats.py` | `src/markdown_stats/` (rows, direction_coverage, markdown_report, readme, summary; seit 2026-11-10 year_archive, year_stats, baustellen_year, year_review, review_client) |
| `health_check.py` | `src/health_check/` (common, sources, outputs, workflow_secrets, github_runs) |
| `ki_waechter.py` | `src/ki_waechter/` (inputs, audit) |
| `update_all_stations.py` | `src/stations_refresh/` (diff, quarantine, heartbeat) |
| `update_baustellen_cache.py` | `src/providers/baustellen_events.py`, `baustellen_wfs.py` |
| `update_oebb_station_lines.py` | `src/oebb_lines/` (hafas, refresh) |
| `update_stammstrecke_status.py`, `update_stammstrecke_hbf.py` | `src/stammstrecke/` (core, directions, ledger, vao_trip, legs, vao_errors, hbf_board, hbf_classify) |
| `update_station_directory.py` | `src/station_directory/` (model, csv_sources, json_sources, matching, coordinates, workbook) |
| `update_wl_stations.py` | `src/wl_stations/` (base, ogd_download, ogd_csv, entries, colocated, merge, reconcile) |

Jedes Skript und jedes Modul hat höchstens 650 Zeilen. Der Code ist
wörtlich verschoben: Ein Abgleich mit `ast` findet jede Anweisung der
obersten Ebene des alten Skripts (außer Importen) mit gleichem Quelltext in
genau einer neuen Datei. Ein Test, der eine Funktion ersetzt
(`monkeypatch.setattr`), zielt jetzt auf das Modul, in dem der Aufruf
aufgelöst wird; sonst würde die Ersetzung nicht mehr greifen. Wer eine
Funktion eines Skripts in einem Test ersetzt, tut das daher im Modul unter
`src/`, nicht im Skript. Logger-Namen und Pfade bleiben gleich, die
Workflows `ki-waechter.yml` und `test-vor-api.yml` lösen auch bei
Änderungen der neuen Module aus, und `.secret-scan-ignore` nennt die neuen
Ordner wie vorher `scripts/*`. Replay: `generate_markdown_stats.py` schreibt
in einer Kopie von `main` und einer Kopie des Umbaus dieselben Dateien
(`README.md`, `docs/statistik.md`, `docs/stats-summary.json` bis auf den
Zeitstempel), `ki_waechter.py prepare` dieselbe Eingabe.

Etappe 7 (2026-11-03) teilt die zwölf langen Module unter `src/` außer
`build_feed.py` (zusammen rund 20.100 Zeilen). Jedes behält seinen Pfad als
Fassade: Es enthält den Rest seines Codes und reicht jeden Namen weiter, den
andere Dateien importieren oder Tests lesen, in genau einer Liste
`__all__ = [...]` (CodeQL liest nur eine solche Liste). Importe im übrigen
Projekt ändern sich daher nicht. Der verschobene Code steht in Nachbarmodulen
oder einem Unterpaket:

| Modul | Neue Module |
|---|---|
| `src/utils/logging.py` | `log_patterns_generic.py`, `log_patterns_cloud_ai.py`, `log_patterns_devops_saas.py`, `log_patterns_platforms.py` |
| `src/utils/secret_scanner.py` | `src/utils/secret_patterns/` (detectors, tokens_vcs_payment_ai, tokens_devtools_saas, tokens_gitlab_commerce, tokens_cloud_platform) |
| `src/utils/http.py` | `src/utils/http_safety/` (limits, redaction, ip_guard, url_guard, peer, adapters, sessions, request_steps) |
| `src/utils/stations.py` | `stations_normalize.py`, `stations_geometry.py`, `stations_data.py`, `stations_lookup.py`; seit 2026-11-10 `term_search.py` (Suche nach sehr vielen Begriffen über einen Index) |
| `src/utils/stations_validation.py` | `src/utils/station_checks/` (issues, report, basic, identity, security, names) |
| `src/utils/stats.py` | `stats_sanitize.py`, `stats_location.py` |
| `src/utils/text.py` | `text_html.py`, `text_markdown.py`, `text_repair.py` |
| `src/feed/merge.py` | `merge_titles.py`, `merge_texts.py`, `merge_items.py` |
| `src/feed/reporting.py` | `report_messages.py`, `feed_health.py`, `github_issue.py` |
| `src/providers/oebb.py` | `oebb_title.py`, `oebb_routes.py`, `oebb_scope.py`, `oebb_relevance.py`, `oebb_route_title.py` |
| `src/providers/vor.py` | `vor_env.py`, `vor_auth.py`, `vor_quota.py` |
| `src/providers/wl_fetch.py` | `wl_api.py`, `wl_item_fields.py`, `wl_title_context.py`, `wl_source_outage.py`, `wl_ticker_fold.py`, `wl_aggregates.py` |

Jede Datei hat höchstens 650 Zeilen, der Code ist wie in Etappe 6 wörtlich
verschoben (Abgleich mit `ast`). Zwei Ausnahmen betreffen nur Daten: Die 85
Suchmuster von `sanitize_log_message` waren eine Liste, die die Funktion bei
jedem Aufruf neu baute (1.537 Zeilen); sie stehen jetzt als feste Listen in
den vier `log_patterns_*`-Modulen, die Fassade hängt sie in der alten
Reihenfolge zu `_PATTERNS` zusammen. Ebenso ist `_KNOWN_TOKENS` des
Secret-Scanners (2.162 Zeilen) auf vier Teillisten verteilt. Beide Listen
sind Eintrag für Eintrag gleich (Muster, Flags, Ersatztext, Reihenfolge), und
alte und neue Fassung liefern für rund 43.000 Texte aus `tests/`, `src/` und
`scripts/` dasselbe Ergebnis; jeder Kommentar steht weiter bei seinem
Eintrag. Wer eine Funktion in einem Test ersetzt, tut das im Modul, in dem
der Aufruf aufgelöst wird; für `_resolve_hostname_safe` und `is_ip_safe`,
die mehrere Module von `http_safety` lesen, gibt es `patch_all` in
`tests/_conftest/http_bindings.py`. Logger-Namen bleiben gleich: Die Teile
loggen unter dem Namen ihrer Fassade, etwa `src.utils.http`. Wächtertests, die Dateien nach
Mustern durchsuchen, lesen die neuen Module mit, und
`.secret-scan-ignore` nennt die neuen Unterpakete. Replay: Der echte Feed-Bau
(`build_feed.main()`) liefert über 982 Feed-Stände vom 15.10. bis 03.11. mit
alter und neuer Fassung dieselben Feeds (DE, EN) und denselben State, der
Abruf von WL und ÖBB (`fetch_events` mit den Rohantworten aus `data/raw/`)
über 395 Stände vom 04.10. bis 03.11. dieselben Meldungen und
Verwerfungsgründe. Damit steht nur noch `src/build_feed.py` auf der
Altfall-Liste.

Nachtrag (2026-11-03): Auch das Teilmodul, das einen Namen definiert, führt
ihn in `__all__`, sobald ein anderes Modul oder ein Test ihn liest. CodeQL
(`py/unused-global-variable`) zählt das Lesen eines privaten Namens über
einen Import sonst nicht als Nutzung und meldet ihn als ungenutzt. 16 solche
Fehlalarme standen nach Etappe 6 und 7 offen auf `main`; seither tragen
`src/utils/stations_data.py`, `src/utils/secret_patterns/detectors.py`,
`src/stammstrecke/core.py`, `src/stammstrecke/hbf_board.py` und
`src/providers/baustellen_wfs.py` je eine Liste aller Namen, die sie
definieren. Kein Modul wird mit `*` importiert, das Verhalten bleibt gleich.

Etappe 8 (ab 2026-11-03) teilt `src/build_feed.py` (13.725 Zeilen) in fünf
Schritten auf das Paket `src/feed_build/` auf (Plan mit Modulliste und
Reihenfolge: Übersetzung, Zustand und Abruf, Filter und Verlauf, Texte und
WL-Meldungen, RSS). `src/build_feed.py` bleibt Einstieg und Fassade: Es
behält die Kette (`lint`, `main`, `_assemble_feed_items`) und reicht jeden
Namen weiter, den `src/`, `scripts/` oder die Tests lesen, in genau einer
Liste `__all__`. Veränderliche Objekte haben genau einen Besitzer und werden
als dasselbe Objekt weitergereicht (`_TRANSLATION_STATE` in `translator`, die
Platzhalter-Nonce in `placeholders`, der Logger `build_feed` in `base`).

Für Tests gilt seit Etappe 8 eine feste Regel: Ein Name wird in dem Modul
ersetzt, dessen Code ihn als globalen Namen liest. Lesen ihn mehrere Module
(etwa `_get_translation_pipeline`, `validate_path`, `atomic_write`,
`feed_config`), ersetzt `setattr_all` oder `patch_all` aus
`tests/_conftest/build_feed_bindings.py` ihn in jedem davon, so wie früher
der eine Patch auf `src.build_feed`. `part(bf, "translate_run")` liefert einen
Teil, `fresh_build_feed()` importiert Fassade und Teile neu.
`tests/test_build_feed_patch_targets.py` liest jeden Ersatz der Tests
(`monkeypatch.setattr` auch in der Punkt-Form, `patch`, `patch.object`,
`patch.multiple`, direkte Zuweisung) und schlägt fehl, wenn er nicht genau
die Module trifft, die den Namen lesen; die Meldung nennt das richtige
Modul. Damit fällt der Grund weg, aus dem die Aufteilung am 2026-10-15
unterblieb.

Schritt 1 verschiebt die Übersetzung in 14 Module (`base`, `entities`,
`placeholders`, `relocation`, `display_titles`, `oebb_summary`, `translator`,
`masking`, `pretranslate`, `en_repairs`, `model_pass`, `en_templates`,
`translate_run`, `translate_titles`, je 85 bis 581 Zeilen);
`src/build_feed.py` hat danach 8.528 Zeilen und bleibt bis zum letzten
Schritt auf der Altfall-Liste. Die Teile entstehen mit einem Generator, der
jede Anweisung wörtlich in ihr Modul schreibt und bei einem unbekannten Namen
abbricht, sodass er vor dem Merge auf dem neuesten `main` neu laufen kann.
Replay mit echtem `main()`: 980 Feed-Stände vom 15.10. bis 03.11. einzeln
neu gebaut und 400 Stände vom 26.10. bis 03.11. nacheinander, jeder mit dem
Zustand seines Vorgängers samt Übersetzungen; deutscher und englischer Feed
und Zustand sind in jedem Stand gleich.

Schritt 2 verschiebt Zustand und Abruf in fünf Module: `state`
(Zustandsdatei, Identität, Schlüssel), `providers_setup` (Provider-Registry
mit `PROVIDERS`, `DEFAULT_PROVIDERS`, `reset_module_state`, dem Laden der
Plugins beim Import und den Cache-Lesern),
`collect` (Abruf der Provider im Thread-Pool), `cache_filters` (Nachfilter für
WL, ÖBB und Baustellen) und `wl_measures` (WL-Beschreibung und Maßnahmen), je
305 bis 521 Zeilen; `src/build_feed.py` hat danach 6.460 Zeilen. Die Registry
hat nur noch einen Besitzer: Tests ersetzen `PROVIDERS` und
`DEFAULT_PROVIDERS` in `providers_setup`, und statt `importlib.reload` laden
die Tests Fassade und Teile mit `fresh_build_feed()` neu. Der Generator hat
dafür eine Stufe `--stage pr2`, die auf der schon geteilten Fassade arbeitet
und die Teile aus Schritt 1 unverändert lässt.
Replay wie in Schritt 1: 980 Stände einzeln und 400 nacheinander, deutscher
und englischer Feed und Zustand in jedem Stand gleich.

Schritt 3 verschiebt Zeitzeile, Texte, Filter und Verlauf in sechs Module:
`time_line` (Zeitzeile, `format_local_times`, Entwarnung), `summary_text`
(Satzbereinigung, 180-Zeichen-Kürzung, `_compose_description`), `title_words`
(Ursache und Folge im Titel, gemeinsame Prädikate), `item_history`
(Vorkommen, first_seen, Identitätsübertrag), `item_meta` (Reihenfolge, Links,
Quellen, Bilder, `_update_item_state`) und `filters` (Alter, Duplikate,
Themenbudget, Testmeldungen, Zurückstellungen), je 303 bis 554 Zeilen; `src/build_feed.py`
hat danach 4.104 Zeilen. `_dedupe_items` steht in `.c901-baseline.txt` jetzt
unter `src/feed_build/filters.py`, mit unverändertem Wert 18.
Replay wie in Schritt 1 und 2: 980 Stände einzeln und 400 nacheinander,
deutscher und englischer Feed und Zustand in jedem Stand gleich.

Schritt 4 verschiebt WL-Ticker, ÖBB-Leitsatz, Übersetzungs-Cache und
Textformat in acht Module: `wl_tickers` (Ticker lesen und gruppieren),
`wl_ticker_text` (Text eines zusammengeführten Eintrags), `wl_joins`
(Einstieg `_merge_wl_ticker_clusters`), `wl_notices` (Baustellen-Zwillinge,
Bauarbeiten-Ticker, Hinweise hinter Störungen), `oebb_lead` (ÖBB-Strecken und
Leitsatz), `translation_epoch` (Epoche, Verbatim- und Digest-Marken),
`translation_cache` (Cache-Prüfung, `_apply_lang_overlay`) und `item_format`
(`_format_item_content` mit Helfern), je 303 bis 523 Zeilen;
`src/build_feed.py` hat danach 1.142 Zeilen. Tests ersetzen
`format_local_times` in `item_format`, dem einzigen Leser.
Replay wie in den Schritten davor: 980 Stände einzeln und 400 nacheinander,
deutscher und englischer Feed und Zustand in jedem Stand gleich.

Schritt 5 verschiebt den RSS-Teil in das Modul `rss` (GeoRSS, ein `<item>`
mit `_emit_item`, Kanal-Metadaten und Lizenzzeilen, `_make_rss`, 507 Zeilen).
Tests ersetzen `_emit_item` in `rss`, dem einzigen Leser. `src/build_feed.py`
hat danach 661 Zeilen und enthält nur noch die Kette (`lint`, `main`,
`_assemble_feed_items`, `FeedAssemblyStats`, `refresh_from_env`,
`_invoke_collect_items`, den Aufruf als Skript) und die Weiterreichung der
übrigen Namen in einer Liste `__all__`. Weitergereicht wird nur noch, was
`src/`, `scripts/` oder die Tests über die Fassade lesen: `PROVIDERS`,
`DEFAULT_PROVIDERS`, `ThreadPoolExecutor` und `format_datetime` stehen nicht
mehr in `__all__`, weil sie niemand mehr dort liest (die Registry lesen und
ersetzen Tests in `providers_setup`), ebenso fallen Importe weg, die nur noch
die Teile brauchen. `src/build_feed.py` verlässt die Altfall-Liste.
Replay wie in den Schritten davor: 980 Stände einzeln und 400 nacheinander,
deutscher und englischer Feed und Zustand in jedem Stand gleich; dazu der WL-
und ÖBB-Abruf (`fetch_events`) über 1.397 aufgezeichnete Stände vom 04.10.
bis 03.11. mit gleichen Meldungen und Verwerfungsgründen.

Die Teile von `src/feed_build/` nach dem Schritt, in dem sie entstanden sind
(`__init__.py` enthält nur die Beschreibung des Pakets und die Regel für
Tests):

| Modul | Inhalt |
|---|---|
| `base` | Logger `build_feed`, XML-Namensräume, Bereinigung von Texten, Datumswerte |
| `entities` | Entitätsmuster für die Übersetzung (Marken, Linien, Straßen, Orte) |
| `placeholders` | Platzhalterformate, ihre Nonce (einziger Besitzer) und das Fachglossar |
| `relocation` | Felder einer Haltestellenverlegung (Pfeile zur Ersatzhaltestelle) |
| `display_titles` | Anzeigetitel: WL-Satztitel, Baustellentitel, Titelvokabular |
| `oebb_summary` | ÖBB-Zusammenfassung: Datumsgrammatik, Züge, die nicht fahren, Abschnittslabels |
| `translator` | Übersetzungsmodell: Laden, Status, Dienst-Komposita; Besitzer von `_TRANSLATION_STATE` |
| `masking` | Maskierung von Entitäten (Haltestellen, Linien, Marken) vor dem Modelllauf |
| `pretranslate` | Deutsche Normalisierung vor der Übersetzung, Label-Records ohne Modell |
| `en_repairs` | Englische Nachreparaturen (Artikel, Großschreibung, Anführungszeichen) |
| `model_pass` | Modelllauf eines Textes und Prüfung auf Platzhalterreste |
| `en_templates` | Nicht rekursive EN-Vorlagen für ÖBB- und WL-Sätze |
| `translate_run` | Die Rekursion um `_translate_text_attempt` |
| `translate_titles` | Übersetzung ganzer Texte und Titel auf `_translate_text_attempt` |
| `state` | Zustandsdatei (first_seen), Identitäts- und Zustandsschlüssel eines Eintrags |
| `providers_setup` | Provider-Registry (`PROVIDERS`, `DEFAULT_PROVIDERS`), Cache-Leser, Provider-Einstellungen |
| `collect` | Abruf aller Provider: Cache-Leser und Thread-Pool |
| `cache_filters` | Nachfilter der Caches von WL, ÖBB und Baustellen vor dem Feed-Bau |
| `wl_measures` | WL-Beschreibungen: Linienpräfixe, wiederholte Abschnitte, Maßnahmen zuerst, jede Linie kurz |
| `time_line` | Zeitzeile eines Eintrags („Seit“, „Bis“, Wochentage) und Beginn einer Störung |
| `summary_text` | Sätze der Zusammenfassung: Bereinigung, voraussichtliches Ende, 180-Zeichen-Kürzung, Beschreibung |
| `title_words` | Ursache und Folge im Titel, kleine gemeinsame Prüfungen eines Eintrags |
| `item_history` | Buchführung über die Läufe: Auftreten, first_seen, Identitätsübertrag |
| `item_meta` | Reihenfolge im Feed und Daten eines Eintrags: Links, Quellen, Bilder, Zustand |
| `filters` | Alter, Duplikate, Themenbudget, Testmeldungen, Zurückstellungen |
| `wl_tickers` | WL-Ticker: Teile, Veröffentlichungsfenster, Gruppen, Ursachen, Linienfolgen |
| `wl_ticker_text` | Text eines zusammengeführten WL-Eintrags: Folgen, Vorfälle, Ursachen |
| `wl_joins` | Zusammenlegen von WL-Einträgen, Einstieg `_merge_wl_ticker_clusters` |
| `wl_notices` | WL-Hinweise: Baustellen-Zwillinge, Tafel-Meldungen zu Bauarbeiten, Hinweise hinter Störungen |
| `oebb_lead` | ÖBB-Texte: Strecken eines Titels, Leitsatz, zweiter Satz |
| `oebb_lines_short` | Seit 2026-11-05: ÖBB-Meldung über mehrere Linien, jede Linie kurz mit Abschnitt und Tagen |
| `oebb_days` | Seit 2026-11-08: Sperrtage einer ÖBB-Meldung aus ihrem Leitsatz, die Sperre an ihren Tagen vor den übrigen Hinweisen |
| `oebb_single_trains` | Seit 2026-11-11: ÖBB-Meldung über einzelne Züge, sie bleibt aus dem Feed |
| `translation_epoch` | Epoche des Übersetzungs-Caches, Verbatim- und Digest-Marken |
| `line_routes` | Seit 2026-11-10: feste englische Form für „über die Strecke der Linien 43 und 9“ („the route of lines 43 and 9“) vor dem Modelllauf |
| `translation_cache` | Prüfung eines gecachten EN-Textes, Sprach-Overlay (`_apply_lang_overlay`) |
| `item_format` | Titel und Beschreibung eines Eintrags (`_format_item_content`) mit Helfern |
| `rss` | RSS-Dokument: GeoRSS, ein `<item>` (`_emit_item`), Kanal-Metadaten und Lizenzzeilen, `_make_rss` |
| `isolation` | Seit 2026-11-10: eine Meldung, an der Kette oder Ausgabe scheitert, fällt aus dem Lauf (`build_isolating`) |
| `abbreviations` | Seit 2026-11-12: ob ein Punkt eine Abkürzung schließt oder einen Satz beendet, eine Regel für jeden Schritt (`is_abbreviation_period`, `without_full_stop`) |

Wer eine Funktion des Feed-Baus ändert, ändert sie in ihrem Teil; ein neuer
Name kommt in den Teil seines Themas, und liest ihn `src/`, `scripts/` oder ein
Test über `src.build_feed`, auch in die Weiterreichung der Fassade.

Damit ist Etappe 8 abgeschlossen, und die Altfall-Liste der 700-Zeilen-Regel
(`tests/line_limit_altfaelle.txt`) ist leer: Alle 58 Dateien, die am
2026-11-02 länger waren, sind aufgeteilt. `tests/test_file_line_limit.py`
hält die Grenze jetzt für jede gezählte Datei ohne Ausnahme fest
(`_ALTFAELLE_COUNT` ist 0).

Nachtrag (2026-11-03, unabhängige Prüfung): Eine Ortsangabe in der Doku
(„`Name` in `Datei.py`“ oder „`Datei.py:Name`“) nennt die Datei, die den
Namen definiert, nicht die Fassade oder das Skript, das ihn nur weiterreicht.
`tests/test_doc_code_pointers.py` prüft das für `AGENTS.md`, `README.md`,
`CONTRIBUTING.md` und `docs/` außer `docs/archive/`; das CHANGELOG beschreibt
den Stand seines Tages und bleibt außen vor.

Nachtrag (2026-11-11, Jahresseite der Statistik): Die Seite eines
abgeschlossenen Jahres (Betreiberentscheidung vom 10.11.2026) kam als neue
Module dazu, nicht als Teilung; jedes hält die Grenze von 700 Zeilen
(gemessen mit `wc -l` am 11.11.2026):

| Modul | Zeilen | Inhalt |
|---|---|---|
| `src/markdown_stats/year_archive.py` | 522 | Seiten der abgeschlossenen Jahre, Reihenfolge des Dashboard-Laufs, Zeitbudget der Anfragen (`ReviewBudget`) |
| `src/markdown_stats/year_stats.py` | 587 | Abschnitt „Das Jahr im Überblick“ |
| `src/markdown_stats/baustellen_year.py` | 700 | Abschnitt Baustellen aus dem Ledger, Faktenblock |
| `src/markdown_stats/year_review.py` | 700 | Prompt, Prüfung und gespeicherter Rückblick |
| `src/markdown_stats/review_client.py` | 422 | Anfrage an Gemini, `clean_review` |
| `src/utils/stats_baustellen.py` | 636 | Schreiber des Ledgers `data/stats/baustellen_<Jahr>.csv` und die gemeinsame Zeilenprüfung |
| `scripts/jahresrueckblick_probe.py` | 155 | Probelauf für `jahresrueckblick-probe.yml` |

`scripts/generate_markdown_stats.py` hat danach 693 Zeilen (vorher 603).
`baustellen_year.py` und `year_review.py` stehen genau an der Grenze: Was dort
dazukommt, braucht ein weiteres Modul.

Nachtrag (2026-11-12, KI-Entscheidung): Die Entscheidung von Gemini für
unklare Störungen (Betreiberentscheidung vom 12.11.2026,
[`architecture-teil-8.md`](architecture-teil-8.md), §14) kam als eigenes Paket
dazu; der Feed-Build ruft es an einer Stelle auf (`decide` in
`_assemble_feed_items`), und `_incident_since` liest nur das Feld `_ki_art`
(gemessen mit `wc -l` am 12.11.2026):

| Modul | Zeilen | Inhalt |
|---|---|---|
| `src/ki_entscheidung/candidates.py` | 269 | Welche Störungen unklar sind (drei Klassen), ihr Abdruck, was das Modell von ihnen liest |
| `src/ki_entscheidung/ask.py` | 193 | Prompt, Schema, Modellkette (Lite voran), Lesen der Antwort |
| `src/ki_entscheidung/store.py` | 160 | Speicher `data/ki_entscheidungen.json` (Antworten, Zeitpunkte der Anfragen, abgelehnter Schlüssel) |
| `src/ki_entscheidung/run.py` | 283 | Ein Build: lesen, höchstens einmal fragen, die Antwort ihres Ereignisses anwenden, speichern, Ergebnis für den Health check |
| `scripts/ki_entscheidung_probe.py` | 253 | Probelauf für `ki-entscheidung-probe.yml` |

`src/markdown_stats/review_client.py` trägt seither beide Anfragen
(`ask_chain` mit einem `JsonCall` je Aufrufer) und hat 522 Zeilen (vorher
422), `src/build_feed.py` 693 (vorher 682).
