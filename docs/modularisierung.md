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
| `generate_markdown_stats.py` | `src/markdown_stats/` (rows, direction_coverage, markdown_report, readme, summary; seit 2026-11-10 year_archive, year_stats, baustellen_year, year_review, review_client; seit 2026-11-13 cli, cli_options, baustellen_sites, baustellen_facts, year_review_prompt, year_review_guard; `main` und die Optionen stehen seither in `cli.py` und `cli_options.py`, siehe Nachtrag 2026-11-13) |
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
| `oebb_days` | Seit 2026-11-08: Sperrtage einer ÖBB-Meldung aus ihrem Leitsatz, die Sperre an ihren Tagen vor den übrigen Hinweisen; seit 2026-11-12 eine Sperre, die länger als vier Wochen Tag für Tag sperrt, nur in ihrer ersten Woche, danach zählt sie ab ihrem ersten Tag und steht hinter einer Sperre mit Sperrtag, die seit 2026-11-13 den Platz ihres ÖBB-Titels vor dem frühesten Fenster hält |
| `oebb_single_trains` | Seit 2026-11-11: ÖBB-Meldung über einzelne Züge, sie bleibt aus dem Feed |
| `translation_epoch` | Epoche des Übersetzungs-Caches, Verbatim- und Digest-Marken |
| `line_routes` | Seit 2026-11-10: feste englische Form für „über die Strecke der Linien 43 und 9“ („the route of lines 43 and 9“) vor dem Modelllauf |
| `translation_cache` | Prüfung eines gecachten EN-Textes, Sprach-Overlay (`_apply_lang_overlay`) |
| `item_format` | Titel und Beschreibung eines Eintrags (`_format_item_content`) mit Helfern |
| `rss` | RSS-Dokument: GeoRSS, ein `<item>` (`_emit_item`), Kanal-Metadaten und Lizenzzeilen, `_make_rss` |
| `isolation` | Seit 2026-11-10: eine Meldung, an der Kette oder Ausgabe scheitert, fällt aus dem Lauf (`build_isolating`) |
| `abbreviations` | Seit 2026-11-12: ob ein Punkt eine Abkürzung schließt oder einen Satz beendet, eine Regel für jeden Schritt (`is_abbreviation_period`, `without_full_stop`) |
| `outputs` | Seit 2026-11-14: was ein Lauf schreibt, als Funktionen, die `main` in dieser Reihenfolge aufruft: Kennzahlen des Health-Berichts (`health_metrics_from`, auch von `lint` aufgerufen), deutscher Feed (`write_feed`), englischer Spiegel (`write_en_mirror`), Zustand (`save_state_or_warn`), Abschluss des Laufberichts (`record_successful_build`), Health-Bericht (`write_health_outputs`) |
| `lint_report` | Seit 2026-11-14: der Bericht, den `lint` ausgibt, und der Exit-Code, der aus ihm folgt (`print_lint_report`) |

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
[`architecture-teil-16.md`](architecture-teil-16.md), §14) kam als eigenes Paket
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

Nachtrag (2026-11-13, teilen statt kürzen): Zehn Tage nach Etappe 8 lag
keine Datei über der Grenze, aber 14 standen genau auf 700 Zeilen (am
2026-11-03 eine) und 21 bei 690 oder mehr. Änderungen an ihnen fügten Zeilen
ein und nahmen an anderer Stelle derselben Datei gleich viele heraus, meist
indem Kommentare zusammengezogen wurden (`.github/workflows/update-cycle.yml`
am 2026-11-12: zwölf Zeilen dazu, zwölf weg, aus neun Zeilen Begründung
wurden zwei). Betreiberentscheidung vom 2026-11-13 (Michael, auf die Frage
„Soll ich die 21 Dateien ab 690 Zeilen nach Themen aufteilen und im Test
festhalten, dass eine Datei am Anschlag geteilt statt gekürzt wird?“: „Ja,
bitte“):

* **Die Regel.** Eine Datei mit 690 oder mehr Zeilen ist am Anschlag. Die
  nächste Änderung, die ihr eine Zeile hinzufügt, teilt sie nach Themen, im
  selben Pull Request. Kommentare kürzen, Zeilen zusammenziehen oder
  Leerzeilen streichen, um unter 700 zu bleiben, ist kein Weg.
  `scripts/check_line_limit_edits.py` vergleicht dafür jede Änderung mit
  ihrem Ausgangsstand: Hatte eine gezählte Datei dort mindestens 690 Zeilen,
  kam eine Zeile dazu und hat sie danach mehr als 550, scheitert der Lauf
  und nennt die Datei. Der Abstand zwischen 690 und 550 trennt die Teilung
  vom Kürzen: Um eine Zeile unterzubringen, streicht niemand 140 Zeilen
  Kommentar. Eine Datei darf in den Bereich 690 bis 700 hineinwachsen; nur
  Zeilen entfernen ist erlaubt. Eine umbenannte Datei wird mit ihrem alten
  Pfad verglichen. Die Prüfung sieht eine Änderung: Wer erst nur kürzt, bis
  die Datei unter 690 liegt, und in einer zweiten Änderung auffüllt, wird
  von ihr nicht erkannt; die Regel untersagt auch das, und es bleibt Sache
  der Durchsicht. Ein Zurücknehmen (`git revert`), das einer Datei am
  Anschlag Zeilen zurückgibt, scheitert wie jede andere Änderung und teilt
  die Datei im selben Pull Request. Der Workflow `test.yml` ruft die Prüfung vor den Tests gegen
  `HEAD^1` auf (bei einem Pull Request der Stand von `main`, bei einem Push
  der Commit davor; beide Checkouts holen dafür zwei Commits). Lokal
  vergleicht `python scripts/check_line_limit_edits.py` mit dem gemeinsamen
  Vorfahren von `HEAD` und `origin/main`. Ohne Vergleichsstand endet sie mit
  Exit 2, nicht grün. Zahlen und Zählweise beider Prüfungen stehen in
  `src/utils/line_limit.py` (`LIMIT`, `SPLIT_FROM`, `SPLIT_TO`);
  `tests/test_check_line_limit_edits.py` spielt die Fälle in einem kleinen
  Git-Repository nach.
* **Wie geteilt wird.** Nach Themen, mit Luft: Jede Datei, die bei einer
  Teilung entsteht, hat ein benennbares Thema und höchstens rund 450 Zeilen
  (Etappe 6 und 7 schnitten auf 650; zehn Tage später stand
  `scripts/generate_markdown_stats.py` mit 693 Zeilen wieder an der Grenze).
  Sonst gilt, was die Etappen oben festlegen:
  wörtlich verschieben, der alte Pfad bleibt Fassade mit einer Liste
  `__all__`, ein Test ersetzt einen Namen in dem Modul, das ihn liest.

Am 2026-11-13 so geteilt (wörtlich verschoben; jede nicht leere Codezeile der
alten Datei steht in einer der neuen. Neu sind nur Importe, `__all__`, die
Modulbeschreibungen (die der alten Dateien wurden je Thema neu gefasst), Kopf-
und Fußzeilen der Doku-Teile und in `cli_options.py` die Hülle
`build_parser()` (`def`, Docstring, `return parser`) um den Parser-Block aus
`main`, das ihn nun mit `parser = build_parser()` aufruft):

| Datei (Zeilen vorher) | Danach |
|---|---|
| `src/providers/wl_text.py` (700) | Fassade mit den Relevanz- und Ausschlussfiltern; `wl_title_tidy.py` (Titelkosmetik), `wl_title_dates.py` (Datum aus dem Titel), `wl_period.py` (Abschnitte „Zeitraum:“, „Zeitpunkt:“, „Dauer:“, `is_stop_notice`), `wl_topic_key.py` (Themenschlüssel für Duplikate) |
| `src/markdown_stats/baustellen_year.py` (700) | Fassade mit dem Abschnitt Baustellen; `baustellen_sites.py` (Ledger lesen, Zeilen zu Baustellen zusammenführen), `baustellen_facts.py` (Bezirk, Zeitraum, Faktenblock) |
| `src/markdown_stats/year_review.py` (700) | Fassade mit dem gespeicherten Rückblick (`ensure_review`, `load_review`); `year_review_prompt.py` (Prompt, `source_text`), `year_review_guard.py` (Prüfung `check_review`, Regeln a bis j) |
| `src/utils/files.py` (691) | Fassade mit `atomic_write` und den drei `read_capped_*`-Lesern (sie loggen unter `src.utils.files`); `files_paths.py` (Pfade zusammensetzen, Dateinamen, Datei-Hash), `files_json.py` (`loads_finite`), `files_zip.py` (`validate_zip_archive_safe`) |
| `scripts/generate_markdown_stats.py` (693) | Skript mit Aufruf und Weiterreichung; `src/markdown_stats/cli.py` (`main` mit der Reihenfolge seiner Schritte), `cli_options.py` (`build_parser`). Tests ersetzen Namen, die `main` aufruft, in `src.markdown_stats.cli`. `main` steht in `.c901-baseline.txt` unter dem neuen Pfad, Wert 16 wie vorher |
| `tests/test_year_review.py` (700) | `tests/_year_review_support.py` und `test_year_review_guard.py`, `_cleaning.py`, `_prompt.py`, `_stored.py` |
| `tests/test_year_stats.py` (700) mit `test_year_stats_more.py` | `tests/_year_stats_support.py` und `test_year_stats.py`, `_ranking.py`, `_partial_years.py`, `_calendar.py`, `_formats.py`, `_coverage_note.py`, `_real_ledger.py` |
| `tests/test_baustellen_ledger.py` (696) mit `test_baustellen_ledger_more.py` | `tests/_baustellen_ledger_support.py` und `test_baustellen_ledger.py`, `_file.py`, `_year.py`, `_failures.py`, `_isolation.py`, `_hook.py` |
| `tests/test_sentinel_clear_text_logging_drift_utils.py` (700) | dazu `test_sentinel_clear_text_logging_drift_walker.py` (der Inventar-Walker) |
| `tests/test_sentinel_heroku_docker_token_drift.py` (692) | dazu `test_sentinel_heroku_docker_token_drift_invariants.py` (Abschnitte 6 bis 10) und `tests/_sentinel_heroku_docker_token_support.py` |
| `tests/test_ki_waechter/test_teil_1.py` (698) | `test_eingabe.py`, `test_funde.py`, `test_bericht.py`, `test_gemini.py`, `test_prompt.py` im selben Paket |
| `docs/architecture.md` und Teile 2 bis 8 (sechs davon 696 bis 700) | 16 Teile nach Abschnitten, höchstens 468 Zeilen; die Abschnitte 10 bis 12 stehen in Teil 15, 13 bis 15 in Teil 16. Ausnahme: Der Abschnitt „Titel und Beschreibung im deutschen Feed“ hat keine Zwischenüberschriften und rund 1.180 Zeilen; er läuft von Teil 2 bis in Teil 5 und ist dort an Aufzählungspunkten geschnitten. Teil 3 und Teil 4 haben deshalb keine eigene Überschrift und keinen Eintrag im Inhaltsverzeichnis |
| `docs/development.md` (700) mit Teil 2 | vier Teile: Überblick, Setup und CLI; Konfiguration und lokaler Lauf; Provider, Datenquelle, Stationsverzeichnis; Workflows, Skripte, Qualitätssicherung, Betrieb |
| `docs/reference/ki_waechter.md` (700) | drei Teile: Arbeitsweise; Betrieb; Probelauf im Pull Request |

Die Tests sind dieselben wie vorher (gleiche Namen, nur der Dateiteil der
Kennung ist anders). Wo ein Testmodul ohne klares Thema zu lang wird, bleibt
das Paket mit `test_teil_<n>.py` aus Etappe 2 der Weg; wo die Themen
erkennbar sind, bekommt jedes Thema sein Modul und gemeinsame Hilfen stehen
in einem Modul `_<name>_support.py`.

Eine Datei bleibt am Anschlag und ist ein eigener Schritt:
`.github/workflows/update-cycle.yml` (700, der Workflow des Feed-Laufs). Für
sie gilt die Regel oben: Die nächste Änderung, die eine Zeile hinzufügt, muss
teilen. Das trifft auch eine Änderung, die nur eine Zeile ersetzt:
`update-cycle.yml` nennt fünf Actions mit festem Commit, und ein
Versions-Update von Dependabot (monatlich) scheitert an der Prüfung, bis der
Workflow geteilt ist.

`src/build_feed.py` stand am 2026-11-13 bei 692 Zeilen. Einen Schnitt, der
wörtlich verschiebt und die Entscheidung „Eine Feed-Kette, ein Modul“
einhält, gab es nicht: `lint`, `main` und `_assemble_feed_items` brauchten
mit ihren Importen rund 480 Zeilen, der Rest ist die Weiterreichung in der
einen Liste `__all__`. Betreiberentscheidung vom 2026-11-14 (Michael, auf die
Karte „Wähle, wie build_feed.py (692 Zeilen) wieder Luft bekommt“: „Teile von
main auslagern“; Text der Karte dazu: „Schreiben von Feed, Zustand und
Health-Bericht wird zu eigenen Funktionen; das ist ein Umbau, kein
Verschieben, und bringt rund 570 Zeilen.“). Die Kette zu verschieben war
nicht gewählt: `lint`, `main` und `_assemble_feed_items` stehen weiter in
`src/build_feed.py`, mit `FeedAssemblyStats` und der einen Liste `__all__`.

Am 2026-11-14 so umgebaut (kein wörtliches Verschieben: Aus Blöcken einer
Funktion wurden Funktionen mit Parametern; die Kommentare sind dieselben,
die Anweisungen bis auf die unter der Tabelle genannten Stellen auch, `main`
und `lint` rufen die Funktionen an der Stelle des Blocks auf, die
Reihenfolge der Schritte ist unverändert):

| Block (vorher in) | Danach |
|---|---|
| Deutschen Feed schreiben (`main`) | `write_feed` in `src/feed_build/outputs.py`; gibt den geprüften Pfad zurück |
| Englischen Spiegel erzeugen und schreiben (`main`) | `write_en_mirror`; die Pfadprüfung steht wie vorher außerhalb des `try`, ein Fehler beim Erzeugen oder Schreiben wird wie vorher nur gewarnt |
| Zustand speichern, bei Fehler warnen und im Laufbericht vermerken (`main`) | `save_state_or_warn` |
| Health-Bericht als Markdown und JSON schreiben (innere Funktion `_write_health_outputs` in `main`) | `write_health_outputs`; Laufbericht, Kennzahlen und die beiden Pfade sind Parameter, die Pfade prüft `main` weiter vor dem Bau |
| Kennzahlen des Health-Berichts aus den Zählern bilden (zweimal in `main`, einmal in `lint`) | `health_metrics_from` |
| Zeile „Feed geschrieben“ loggen und den Laufbericht als erfolgreich abschließen (`main`) | `record_successful_build` |
| Lint-Bericht ausgeben und den Exit-Code bestimmen (`lint`) | `print_lint_report` in `src/feed_build/lint_report.py` |

Die Karte nannte rund 570 Zeilen. Die Prüfung verlangt nach einer Teilung
höchstens 550; deshalb gingen außer dem Schreiben von Feed, Zustand und
Health-Bericht auch die drei letzten Zeilen der Tabelle mit.
`src/build_feed.py` hat danach 539 Zeilen, `outputs.py` 183 und
`lint_report.py` 92 (gemessen mit `wc -l` am 2026-11-14). Anders als vorher
lauten nur diese Anweisungen, ohne Wirkung auf das Ergebnis: In `lint`
entfielen drei Zwischenvariablen, die nur die Kennzahlen speisten; die
Gesamtdauer rechnet `main` beim Aufruf von `record_successful_build` aus, an
derselben Stelle des Ablaufs wie vorher; `write_feed`, `health_metrics_from`
und `print_lint_report` geben ihr Ergebnis zurück. Wo zwei Parameter
verwechselt werden könnten (zwei Zählwerte, zwei Pfade, die drei Listen des
Lint-Berichts), übergeben die Aufrufe sie mit Namen. Kein Wert von
`.c901-baseline.txt` ändert sich; die neuen Funktionen messen höchstens 11.

Für die Tests gilt die Regel aus Etappe 8: `_save_state`,
`write_feed_health_report` und `write_feed_health_json` liest jetzt nur
`outputs` (36 Ersetzungen in den Tests zeigen seither dorthin), `_make_rss`
lesen die Fassade und `outputs` (14 Ersetzungen laufen über
`setattr_all`/`patch_all`); zusammen 50 Stellen in 20 Testdateien. Der
Wächter `tests/test_build_feed_patch_targets.py` meldet seither in jeder
Patch-Form, die er kennt, ein Ziel, das er nicht als Modul auflösen kann (ein
Attribut wie `run.outputs`, ein Parameter, `part(bf, name)`, ein
zusammengesetzter String, `patch.multiple`, direkte Zuweisung, `__dict__`),
wenn ein Modul von `src.build_feed` den Namen bindet, und einen Namen, der in
einer Variablen steht. Frei bleiben Ziele, die nachweislich außerhalb liegen:
importierte andere Module, Funktionen und Objekte des Tests. `setattr_all`
und `patch_all` lehnen einen Teil statt der Fassade ab. So fand der Wächter
die Stelle in `tests/test_build_feed_skipped_item_edges.py`, die `_make_rss`
über eine Variable nur in der Fassade ersetzte und damit den englischen
Spiegel nicht mehr traf. Mit allen Formen wäre die Datei des Wächters über
700 Zeilen gewachsen; er steht seither in drei Dateien (wörtlich verschoben):
Regel und Tests in `tests/test_build_feed_patch_targets.py`, das Wissen über
die Module und die AST-Hilfen in `tests/_conftest/patch_census.py`, die
Erfassung einer Testdatei in `tests/_conftest/patch_scan.py`. `atomic_write` binden mehrere Teile; die Tests, die ein Schreiben
scheitern lassen, ersetzen es über `setattr_all` und lassen nur die eine
Datei scheitern. Die Fassade bindet die beiden Schreiber des Health-Berichts
nicht mehr. Neu in `__all__` stehen `atomic_write` und `save_state_or_warn`, weil
Tests sie über die Fassade lesen. `tests/test_build_feed_outputs.py` (acht
Tests über `main`) hält die Reihenfolge der Ausgaben und die Fehlerpfade fest
(Zustand nicht gespeichert, englischer Spiegel nicht erzeugt oder nicht
geschrieben, deutscher Feed nicht geschrieben, Kette gescheitert,
Health-Markdown nicht geschrieben); diese acht Tests laufen auch gegen den
Stand vor dem Umbau, wenn die Ersetzungen auf die Fassade zeigen.
`tests/test_build_feed_output_functions.py` (elf Tests) ruft die neuen
Funktionen einzeln auf und hält fest, dass zwei Parameter desselben Typs nur
mit Namen übergeben werden. Zwei Tests in `tests/test_feed_lint.py` halten
fest, dass `lint` jeden Wert unter seiner Überschrift ausgibt.

Replay mit echtem `main()` am 2026-11-14 auf dem Endstand: alle 813
Feed-Stände des Vergleichslaufs vom 2026-11-13 (28.10. bis 13.11., einzeln
neu gebaut, mit einem Echo an der Stelle des Übersetzungsmodells) gegen die
Ergebnisse dieses Laufs für den Stand vor den Teilungen (`0f22303cd0`):
deutscher und englischer Feed und Zustand sind in jedem der 813 Stände
gleich, kein Lauf endete mit einem Fehler. `lint()` über dieselben 813
Stände, alt gegen neu: Ausgabe und Exit-Code in jedem Stand gleich (der
Exit-Code war überall 0; die Wege zu 1 und 2 halten die Tests fest).
