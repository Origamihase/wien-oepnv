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
| `feed_watch.py` | `src/feed_watch/` (model, feed_rules, sources, evaluate, issues, state_io) |
| `generate_markdown_stats.py` | `src/markdown_stats/` (rows, direction_coverage, markdown_report, readme, summary) |
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
| `src/utils/stations.py` | `stations_normalize.py`, `stations_geometry.py`, `stations_data.py`, `stations_lookup.py` |
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
