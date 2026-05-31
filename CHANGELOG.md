# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
* **Feed-Filter & Dedup: Korrektheits-Welle (Bugs b1–b14, 2026-06-01)**:
  Eine Reihe verifizierter Filter-Fehler behoben, die echte Wien-Meldungen
  fälschlich verwarfen oder irrelevante Meldungen aufnahmen. Jeder Fix ist
  durch dedizierte Regressionstests abgesichert:
  * **ÖBB-Routen-Erkennung** (`src/providers/oebb.py`): Die Routen-Regexes
    `_ZWISCHEN_`/`_VON_NACH_`/`_STRECKE_PLAIN_RE` haben Grenzwort-Token
    erhalten (`zu`/`zur`, `über`/`via`, Prädikatsverben) — die Standard-
    Formulierung „… kommt es zwischen X und Y **zu** …" überdehnte zuvor
    den zweiten Endpunkt und verwarf die echte Wien-Route (b1). Ein
    beschreibendes Schluss-Substantiv im Einzelstation-Titel
    („Wien Meidling Stellwerk") wird nicht mehr als impliziter unbekannter
    Endpunkt fehlgedeutet (`_TITLE_NOISE_WORDS` um Infrastruktur-Nomen
    ergänzt, b2). Wetter-präfigierte Bahn-Störungen („Hochwasser:
    Gleissperrung …") werden gerettet (`_TRANSIT_KEYWORD_RE` um
    rail-spezifische Compound-Sperrungen + `Zugverspätung`, b3).
  * **Strikter Modus** (`OEBB_ONLY_VIENNA`): Das bloße Arealwort „Wien"
    kanonisiert nicht mehr zu einer Phantom-Station (b10), und eine allein
    stehende Pendler-Erwähnung zählt nicht mehr als relevant (b12) — siehe
    `docs/reference/oebb_provider_logic.md`.
  * **Fuzzy-Dedup** (`src/feed/merge.py`): „Wien"/„Vienna" sind jetzt
    Stoppwörter (verhindert das Verschmelzen verschiedener Routen),
    Bahnhof-Abkürzungen werden vor der Tokenisierung normalisiert
    (`Hbf`/`Bhf`/`Bf` → `…bahnhof`, sodass „Wien Hbf" und „Wien
    Hauptbahnhof" weiter zusammengeführt werden, b6), und unterschiedliche
    Bahnsteig-/Gleis-Nummern (`_platform_numbers`) verhindern eine
    Verschmelzung verschiedener Bahnsteige (b7).
  * **Baustellen** (`src/providers/baustellen.py`): Die `\bu-/s-bahn`-Token
    tragen eine führende Wortgrenze (kein Fehltreffer mehr auf
    „Hochschaubahn", b4), und `oepnv_lead` zerschneidet Sätze
    abkürzungsbewusst (`_split_into_sentences`, kein Bruch an „Nr." /
    „3. März", b5).
  * **Stationsverzeichnis**: Opake Betriebsstellencodes mit ≤ 3 Zeichen
    werden aus dem Wien-Erkennungs-Regex entfernt (Mindest-Alias-Länge
    3 → 4 in `src/utils/stations.py`, b11); der Alias-Generator blockt die
    Müll-Aliase „aug"/„am" an der Wurzel (`_GENERIC_ALIAS_BLOCKLIST` in
    `scripts/enrich_station_aliases.py`), 40 fehlerhafte Aliase aus
    `data/stations.json` entfernt (b8/b9).
  * **Feed-Rendering** (`src/build_feed.py`): Mehrmonatige Enddaten bleiben
    erhalten (180-Tage-Kappe → `feed_config.ABSOLUTE_MAX_AGE_DAYS` = 540,
    b13); doppelte U-Bahn-Linien-Präfixe („U2: U2 …") werden erkannt
    (`_baustellen_title_names_station` akzeptiert `U1`–`U6`, b14).

* **Feed-Pipeline: Robustheits- & Korrektheitsfixes (Review-Funde #1–#8, 2026-06-01)**:
  Projektweite Prüfung jenseits des Relevanzfilters; verifizierte Fehler
  behoben (jeweils mit Regressionstest):
  * **Wiener-Linien-Titel** (`src/providers/wl_fetch.py`): Ein reiner
    Satzzeichen-Titel („---") fällt auf „Meldung" zurück, statt als bloße
    Linien-Codes („U1/U2") ohne Beschreibung zu erscheinen (#1).
  * **Feed-Sortierung** (`src/build_feed.py`): Eine in der Zukunft liegende
    `pubDate` wird auf „jetzt" gekappt und rangiert nicht mehr vor aktuellen
    Items (#2); ein Fehler beim Zusammenführen eines Provider-Ergebnisses
    wird pro Provider isoliert und verwirft nicht mehr alle bereits
    eingesammelten Items (`_drain_completed_futures`, #3).
  * **Stammstrecke** (`scripts/update_stammstrecke_status.py`): Ein leerer
    `rtDate`/`rtDepDate` (statt fehlend) deaktiviert nicht mehr die
    Mitternachts-Heuristik — verhinderte Schein-Verspätungen von ≈ −1430 min
    (#4).
  * **`.env`-Parser** (`src/utils/env.py`): Ein nicht geschlossenes
    Anführungszeichen (`KEY="abc`) liefert den dekodierten Inhalt statt des
    streunenden Quotes — schützt Tokens/Credentials vor Korruption (#5).
  * **Stationsvalidierung** (`src/utils/stations_validation.py`): Die
    Identitätsfelder (`bst_code`) akzeptieren `str | int` wie die
    Geschwister-Validatoren; ein ganzzahliger `bst_code` wird nicht mehr
    stillschweigend übersprungen (#8).

* **Dashboard: Wetter-Widget im Header (Wien, 2026-05-30)**:
  Der Header zeigt jetzt links neben der Marke ein kleines Wetter-Symbol
  plus die aktuelle Temperatur in °C für Wien. Als Abfrage-Koordinaten
  dienen die des **Wiener Hauptbahnhofs aus dem Stationsverzeichnis**
  (`data/stations.json`, Eintrag „Wien Hauptbahnhof", 48.186116 /
  16.374399). Datenquelle ist das **GeoSphere-Austria-Modell AROME**
  über die Open-Meteo-API, direkt im Browser abgefragt und gemeinsam mit
  den Verkehrsdaten über `loadAll()` im 5-Minuten-Takt aktualisiert. Die
  Abfrage nutzt eine **Fallback-Kette** (erster Treffer gewinnt), da der
  offizielle OpenAPI-Spec nur `/v1/forecast` dokumentiert: primär
  `/v1/forecast` mit
  `current=temperature_2m,weather_code,is_day&models=geosphere_arome_austria`,
  dann der dedizierte Endpunkt `/v1/geosphere_arome_austria` mit
  zeitzonensicher (über `timeformat=unixtime`) gewählter aktueller Stunde
  der `hourly`-Reihe, zuletzt Best-Match als Notnagel — so zeigt das
  Widget verlässlich einen Wert, ohne sich auf nur teilweise
  dokumentierte Endpunkte zu verlassen. Das Symbol ist ein monochromes
  Inline-SVG (WMO-Wettercode →
  Sonne/Mond/Wolke/Regen/Schnee/Nebel/Gewitter inkl. Tag-/Nacht-Variante),
  Tooltip und `aria-label` (`role="img"`) werden zweisprachig (DE/EN)
  gesetzt. CSP `connect-src` um `https://api.open-meteo.com` erweitert
  (plus `dns-prefetch`-Hinweis); ein fehlgeschlagener Wetterabruf lässt
  den globalen Feed-Status unberührt — das Widget behält dann seinen
  letzten Wert bzw. den `–`-Platzhalter. Die reservierte Breite des
  Temperatur-Slots verhindert Layout-Shift (kein CLS).
* **Backup-Cron: Freshness-Gate gegen redundante API-Last (2026-05-26)**:
  Der neue stündliche Sicherheits-Cron lief bisher rein additiv zu IFTTT
  und verursachte ~+24 Ticks/Tag (VAO ~48→~72/Tag — innerhalb des durch
  den Preflight hart gedeckelten 100/Tag-Budgets, aber an gesunden Tagen
  komplett redundant). Neu prüft ein **Freshness-Gate** vor den
  Fetch-/Build-/Publish-Schritten bei `schedule`-Läufen, ob bereits ein
  Tick innerhalb der letzten ~35 min committet hat (Signal: Commit-Zeit
  von `data/vor_request_count.json` / `docs/feed.xml`), und überspringt
  dann die gesamte API-/Build-Arbeit. Ergebnis: an gesunden Tagen ~0
  Extra-API-Abfragen, voller Schutz nur bei echtem IFTTT-Ausfall.
  IFTTT- (`repository_dispatch`) und manuelle (`workflow_dispatch`) Läufe
  bleiben ungegated. Unbekannt/Parse-Fehler ⇒ „run" (fail-safe).
* **Robustheit: 30-Minuten-Zyklus durchgehärtet (Tier 1–3, 2026-05-26)**:
  Folgeschritt zur Action-Download-Resilienz — `update-cycle.yml` an allen
  verbleibenden Fehlerstellen abgesichert, damit der wichtigste Workflow
  des Projekts nicht an transienten oder kosmetischen Problemen scheitert:
  * **Statistik-Step entkoppelt** (`continue-on-error`): Der kosmetische
    README-/Dashboard-Render lief vor dem Commit ohne Fehlertoleranz —
    ein Crash dort brach den Job ab und **verhinderte die
    Feed-Veröffentlichung**. Jetzt orange Annotation statt Blockade.
  * **torch-Self-Heal ohne Netzwerk auf dem gesunden Pfad**: Der Step rief
    bei *jedem* Tick unbedingt `pip install torch` auf — ein transienter
    `download.pytorch.org`-Ausfall färbte den Lauf rot. Jetzt
    Import-Check zuerst (null Netzwerk-I/O, wenn torch im Cache liegt),
    sonst Install mit Retry; fehlt torch endgültig, degradiert nur der
    EN-Feed für einen Tick (kein harter Fehler).
  * **Pre-Publish-Sanity-Check**: `feed.xml`/`feed.en.xml` werden vor dem
    Commit auf Wohlgeformtheit + nicht-leer geprüft; bei Defekt wird der
    Publish übersprungen (letzter guter Feed bleibt online).
  * **Reconcile + Commit/Push zu einem Never-Fail-Block konsolidiert**:
    entfernt den letzten harten `exit 1`-Pfad der Publish-Sequenz; das
    Reconcile auf konkurrierende Pushes steckt jetzt im Retry-Loop.
  * **Step-Timeouts** (Build feed 5 min, Statistik 2 min) fangen Hänger
    deutlich vor dem 10-min-Job-Limit ab; **git-Netzwerk-Timeouts**
    (`LOW_SPEED_LIMIT/TIME`) bremsen hängende push/pull-Sockets aus.
  * **Backup-Cron** (`schedule: '17 * * * *'`, off-cadence) als reines
    Sicherheitsnetz gegen verlorene Ticks / IFTTT-Ausfall — IFTTT bleibt
    primärer :00/:30-Treiber; das VAO-100/Tag-Budget ist durch den
    `preflight_quota_check`-Gate weiterhin garantiert (Backup-Ticks bauen
    notfalls nur aus den Free-API-Caches neu).
  * **`PAGES_BASE_URL` schedule-robust** aus `$GITHUB_REPOSITORY` statt
    `${{ github.event.repository.name }}` (auf `schedule`-Events leer) und
    **git-Identität einmalig** direkt nach dem Checkout gesetzt.
* **Robustheit: 30-Minuten-Zyklus übersteht Action-Download-Ausfälle und
  publiziert mit Retry + Never-Fail (2026-05-26)**: Der Lauf vom
  2026-05-26 12:30 UTC von `update-cycle.yml` starb bereits in der
  Setup-Phase („Prepare all required actions"), weil
  `stefanzweifel/git-auto-commit-action` nicht von `codeload.github.com`
  geladen werden konnte (404 „after 1 attempts"). Ein per `uses:`
  referenzierter Action-Download wird vor dem ersten Step aufgelöst —
  ein Retry/Fallback im Workflow kann dort nicht mehr greifen, der Job
  ist tot, bevor irgendein Step läuft. Fix: Der finale Commit-/Push-
  Schritt nutzt jetzt Inline-`git` (auf dem Runner vorinstalliert, kein
  Download) statt der externen Action. `git add -A` bildet das frühere
  `add_options: -A` 1:1 ab; bei „nichts zu committen" ist der Schritt ein
  sauberer No-op. Der Push läuft mit bis zu 4 Versuchen und
  exponentiellem Backoff (2/4/8 s) und re-synchronisiert zwischen den
  Versuchen via `git pull --rebase --autostash` auf konkurrierende Pushes
  (`update-stations.yml` sonntags, `seo-guard.yml` täglich liegen in
  eigenen Concurrency-Lanes); `--force-with-lease` bleibt erhalten.
  Rebase-Konflikte werden zugunsten der lokal gebauten Vollregeneration
  aufgelöst (append-only CSV-Ledger lösen sich ohnehin über
  `merge=union`). Schlägt jeder Versuch fehl, beendet der Schritt mit
  `::warning::` statt rotem Status — die zuletzt veröffentlichten Daten
  bleiben stehen und der nächste ~30-Minuten-Tick baut neu auf. Damit ist
  die wichtigste Pipeline des Projekts gegen den beobachteten transienten
  CDN-Ausfall **und** gegen Push-Fehler abgesichert. Der bestehende
  Vertrag aus `tests/test_en_feed_workflow_deps.py` (torch-CPU-Install,
  HuggingFace-Cache, `feed build`) bleibt unberührt.
* **Doku: HAFAS-Profil wird wöchentlich (nicht pro Tick) aktualisiert
  (2026-05-26)**: Die Docstrings in `src/places/hafas_client.py` und
  `scripts/sync_hafas_profile.py` sagten „before each cron tick" bzw.
  „so the cron pipeline always picks up the freshest credentials" und
  erweckten so den Eindruck, das Profil werde alle 30 Minuten geladen.
  Tatsächlich läuft `sync_hafas_profile.py` ausschließlich im
  *wöchentlichen* `update-stations.yml` (Sonntags 01:00 UTC); der
  30-Minuten-Zyklus liest nur das committete `data/hafas_profile.json`
  und lädt nichts nach (der Laufzeit-Client cached zudem in-process).
  Das Profil wird damit höchstens einmal pro Woche geladen. Docstrings
  entsprechend präzisiert.
* **Sicherheit: Stored-XSS im veröffentlichten `<content:encoded>`-Feld
  geschlossen (2026-05-24)**: `_compose_description` (`src/build_feed.py`)
  bettete den reinen Text aus `summary`/`time_line` un-escaped in den
  RSS-`<content:encoded>`-CDATA-Body ein – das einzige Feed-Feld, das
  Feed-Reader als HTML rendern. Da `html_to_text`
  (`HTMLParser(convert_charrefs=True)`) entity-kodierte Spitzklammern
  dekodiert, konnte eine kompromittierte/MITM-behaftete Upstream-Quelle
  (`&lt;img onerror=…&gt;` bzw. die doppelt-escaped Form bei ÖBB-RSS) ein
  ausführbares `<img onerror=…>`-Tag in jeden Abonnenten-Reader schleusen
  (Stored XSS). Fix: kontextkorrektes HTML-Encoding der Text-Teile via
  `html.escape(part, quote=False)` am gemeinsamen DE/EN-Chokepoint; nur das
  vom Builder selbst erzeugte `<br/>` bleibt aktiv. CDATA-als-Text-Senken
  (`<title>`) bleiben bewusst un-escaped (CDATA dekodiert keine Entities).
  Abgedeckt durch `tests/test_content_encoded_html_injection.py`
  (Reader-genaue `HTMLParser`-PoC, scheitert vor dem Fix). Die Zeilennummern
  der `allow_nan`-Writer-Walker-Allowlist (`_identity_for_item`) wurden an den
  durch den neuen `import html` verschobenen Block angepasst (2359/2368 →
  2360/2369).
* **SEO/GEO: `llms.txt`-Generator, Sitemap-Batching & JSON-LD-Sentinel
  (2026-05-24)**:
  * `scripts/generate_llms_txt.py` (neu) erzeugt `docs/llms.txt` nach dem
    [llms.txt-Standard](https://llmstxt.org/): eine kuratierte,
    Markdown-formatierte Karte der informationsdichtesten Seiten für
    LLM-/KI-Crawler (H1 + Summary-Blockquote, Abschnitte Dokumentation /
    API-Referenz / How-to / Feeds). Titel und Beschreibungen der Referenz-
    und How-to-Seiten stammen aus deren vorhandenem Front-Matter, sodass
    neue Seiten automatisch erscheinen; die übrigen Einträge sind statisch
    kuratiert. URLs werden über `generate_sitemap._to_url` erzeugt und
    teilen den `SITE_BASE_URL`-Host-Pin (`_base_url`), damit `llms.txt`-
    Links nie von ihren `sitemap.xml`-Pendants abweichen. Die Ausgabe ist
    deterministisch (keine Zeitstempel), sodass der tägliche `seo-guard`-
    Lauf nur bei echten Doku-Änderungen committet. Abgedeckt durch
    `tests/scripts/test_generate_llms_txt.py`.
  * `.github/workflows/seo-guard.yml`: neue Schritte „Refresh llms.txt"
    und „Verify llms.txt" (H1-Pflicht, `SITE_BASE`-Referenz, mindestens
    ein Link) analog zur Sitemap-Prüfung; der Auto-Commit erfasst jetzt
    `docs/sitemap.xml` **und** `docs/llms.txt`.
  * **Sitemap-Performance — N+1 aufgelöst**: `scripts/generate_sitemap.py`
    startete in `_last_modified()` pro Datei einen eigenen
    `git log -1`-Subprozess (61 Prozess-Starts ≈ 215 ms im aktuellen
    Baum). Ersetzt durch `_git_lastmod_map()`, das die Historie in **einem**
    gestreamten `git log --name-only`-Aufruf durchläuft und abbricht,
    sobald jede angefragte Datei ihren neuesten Commit gezeigt hat
    (≈ 12 ms, ~18×). Semantik (Commit-Datum → mtime-Fallback →
    Zukunfts-Clamp) bleibt identisch; abgedeckt durch
    `tests/scripts/test_generate_sitemap_lastmod.py` (inkl.
    „genau ein git-Prozess"-Regression).
  * **JSON-LD-Sentinel** (`tests/test_site_html_structured_data.py`):
    stellt sicher, dass der bestehende `application/ld+json`-Block in
    `docs/site.html` erhalten und valide bleibt (Schema.org-`@context`,
    `@type`), damit die KI-/Such-Sichtbarkeit nicht unbemerkt regrediert.
* **Performance: CLS ≈ 0.94 → ≈ 0 und WebP-Varianten für die zwei
  Bild-Assets (2026-05-17)**:
  * Zwei frische Lighthouse-Läufe (13.0.2) gegen
    `https://origamihase.github.io/wien-oepnv/site.html` zeigten, dass
    der Asset-Payload nach dem ersten Optimierungs-Pass (Eintrag oben)
    zwar passte, das mobile Profil aber bei `cumulative-layout-shift =
    0.937` (Desktop 0.598) hängen blieb. Schuld waren drei dynamische
    Ladestellen, die ihren Platzbedarf vor dem CSV-Render nicht
    reservierten: die zehn `.bars`-Container in den drei `chart-grid`
    Sektionen (ohne jedes Skelett), die `[data-year-label]`-Spans
    (Text-Sprung „–" → „YYYY") und die KPI-/Feed-Skelette, die mit
    `min-height: 92/96 px` deutlich unter der Endhöhe der späteren
    Inhalte saßen. Auf Mobil verschob sich daher `section#ausfaelle`
    um 0.937 Layout-Score, auf Desktop traten drei separate Shifts
    auf (chart-grid#ausfaelle, card--wide#stammstrecke-direction und
    der Jahres-Paragraph in `#ausfaelle > p.section__sub`).
  * `docs/assets/site.css`:
    - Pro `#…-hour` / `#…-weekday` / `#…-line` / `#…-providers` /
      `#…-direction` ein expliziter `min-height` (700 / 200 / 290 /
      290 / 140 px), bemessen an „Bar-Anzahl × ~28 px Row + Gap". Die
      Container reservieren damit beim ersten Paint exakt den Platz,
      den `renderBars()` später mit den CSV-Werten füllt — kein
      Push-Down mehr, wenn die Daten ankommen.
    - `.kpi { min-height: 112px }` und `.kpi.skeleton { min-height:
      112px }` (war 92 px) — die Skelette matchen jetzt die echte
      Card-Höhe (Label + clamp-Wert + Sub-Zeile + Padding), so dass
      KPI-Reveals weder schrumpfen noch wachsen.
    - `.skeleton--feed { min-height: 152px }` (war 96 px) — die
      Feed-Items rendern typischerweise Titel + Description + Meta in
      ~150 px, also gleicht die Reserve den Endwert an.
    - `[data-year-label] { display: inline-block; min-width: 4ch;
      text-align: center; font-variant-numeric: tabular-nums }` —
      reserviert die volle „YYYY"-Breite für den „–"-Platzhalter, so
      dass der Jahres-Tausch durch `setYearLabels()` keine
      Zeilenumbruch- oder Word-Spacing-Verschiebung mehr auslöst.
  * `docs/assets/site.html` + `docs/assets/site.css`:
    - `train.png` (63 KB Palette-PNG) bekommt ein verlustfreies
      `train.webp` Geschwister (57 KB, –9 %) und wird via
      `<picture><source type="image/webp" srcset="…webp"><img …></picture>`
      ausgeliefert. Engines ohne WebP-Unterstützung laden weiterhin
      direkt das PNG, die `<img>` behält ihre 1584×224-Attribute und
      damit die identische CLS-Reservierung.
    - `footer-bg.jpg` (195 KB JPEG q=72) bekommt ein lossy
      `footer-bg.webp` Geschwister (108 KB q=75, –45 %, weil die
      78–94 % dunkle Verlaufs-Overlay jegliche WebP-Artefakte
      maskiert). Die `.site-footer::before`-Regel deklariert
      `background-image` zweimal: erst mit JPEG-`url()` als
      Universal-Fallback, dann mit `image-set(url("…webp"),
      url("…jpg"))` — moderne Engines (Chrome 88+, Safari 14+,
      Firefox 88+ ≈ 95 % Global Reach) wählen die WebP, ältere
      ignorieren die zweite Deklaration und behalten die JPEG.
  * `scripts/optimize_site_assets.py`:
    - Neue `TRAIN_WEBP` / `FOOTER_WEBP` Konstanten und zwei
      Pillow-Save-Aufrufe am Ende der bestehenden
      `_optimise_train_png()` / `_optimise_footer_jpg()` Funktionen
      generieren die WebP-Varianten in einem einzigen Skript-Lauf.
      Train.webp ist verlustfrei (`lossless=True`), Footer.webp läuft
      mit `quality=75`, beide nutzen `method=6` (langsame, beste
      Kompression — wird nur bei einer Quell-Änderung neu erzeugt).
      `--skip-images` lässt beide WebP-Pfade unverändert (Test- und
      Pre-commit-Pfad bleiben Pure-Python und brauchen kein libwebp).
  * Erwartete Lighthouse-Wirkung (rechnerisch — neue Reports liefern
    Maintainer nach):
    - **CLS Mobil 0.937 → ≈ 0.00, Desktop 0.598 → ≈ 0.00** durch die
      vier `min-height`-Cluster (Bars, KPIs, Feed, Year).
    - **Performance-Score Mobil 76 → 95+, Desktop 78 → 95+** —
      CLS hatte beide Profile auf den Performance-Schlüsselmetriken
      blockiert, FCP/LCP/TBT lagen schon im grünen Bereich.
    - **Transfer-Gewinn ~92 KB** für moderne Browser (Train WebP
      ‑5 KB, Footer WebP ‑87 KB), bei unverändertem Fallback-Pfad
      für ältere Engines.
  * Was sich **nicht** ändert: Final-State-Rendering (alle
    Reservierungen werden im geladenen Zustand überschrieben oder
    perfekt aufgefüllt), CSP (`img-src 'self' data:` deckt WebP
    aus demselben Origin schon ab), Feed-/CSV-Pfade, Cache-Strategie
    (GitHub Pages liefert weiterhin 10 min `max-age`, was Lighthouse
    via `cache-insight` zwangsläufig als „nicht ideal" markiert —
    außerhalb der Reichweite eines statischen Workflows).
  * `python scripts/optimize_site_assets.py --check` läuft grün;
    `tests/scripts/test_optimize_site_assets.py` (6 Tests) bleibt
    grün, weil der Image-Pfad wie bisher hinter `shutil.which`-
    Guards lebt und die WebP-Save-Calls innerhalb der bestehenden
    `_optimise_*` Funktionen liegen.
  * Marker: SENTINEL_LIGHTHOUSE_2026_05_17_CLS_RESERVATION.

* **Dashboard: „Ausfälle nach Wochentag" als eigener Chart-Block
  (2026-05-17)**:
  * Die Ausfall-Sektion auf `docs/site.html#ausfaelle` zeigt jetzt –
    analog zu „Ø Verspätung nach Wochentag" im Stammstrecke-Block –
    eine eigene Balken-Karte „Nach Wochentag" zwischen „Nach Richtung"
    und „Nach Tageszeit". `renderAusfaelleStats()` hatte die
    Wochentag-Aggregation (`countByKey(rows, r => r.weekday, WEEKDAYS)`)
    schon für die `Stärkster Tag`-KPI berechnet; sie war aber nirgends
    visualisiert. Der neue `renderBars("#ausfaelle-weekday", …)`-Aufruf
    nutzt dieselbe `cancel`-Bar-Variante (`var(--c-danger)`-Fill) wie
    die übrigen Ausfall-Charts und reuse das `WEEKDAY_LONG`-Mapping
    auf die deutschen Vollnamen.
  * Layout: Die neue Karte ist eine reguläre `card` (kein
    `card--wide`); auf breiten Viewports stehen Linie/Richtung/Wochentag
    in einer Reihe, „Nach Tageszeit" bleibt die volle Breite einnehmende
    untere Karte – exakt das Muster aus dem Stammstrecke-Grid (Stunde
    + Wochentag schmal, Richtung wide). Keine CSS-Änderung nötig:
    `.chart-grid` ist `repeat(auto-fit, minmax(min(320px, 100%), 1fr))`,
    so dass die zusätzliche Karte responsive einrastet.
  * `docs/assets/site.min.js` mit
    `python scripts/optimize_site_assets.py --skip-images` regeneriert;
    `--check` läuft grün. Kein neuer CSS-Hook, keine neue Datenquelle,
    keine CSP-Anpassung – die Spalten `weekday`/`hour` waren bereits
    Teil des `data/stats/ausfaelle_<YYYY>.csv`-Schemas seit 2026-05-15.

* **Performance: Asset-Payload der statischen Website um ~86 % reduziert (2026-05-17)**:
  * Zwei Lighthouse-Läufe gegen `docs/site.html` (Mobil + Desktop,
    Lighthouse 13.0.2) flaggten dieselbe Diagnose-Kette: `train.png`
    (992 KiB) und `footer-bg.jpg` (661 KiB) dominierten den Netzwerk-
    Payload und waren die einzigen materiellen Ziele des
    `image-delivery-insight`-Audits (Score 0.5, geschätzte Einsparung ~989 KiB).
    Das handgepflegte `site.css` (19 KiB) und `site.js` (25 KiB)
    lösten zusätzlich `unminified-javascript` (Score 0.5) aus und
    trugen zu einer 429-ms-HTML→CSS-Render-Blocking-Kette bei.
    Accessibility flaggte `label-content-name-mismatch` (Score 0) am
    Marken-Link im Header, dessen `aria-label="Wien ÖPNV – Startseite"`
    das sichtbare „Live-Dashboard"-Sub-Label nicht enthielt.
  * Bild-Assets verlustfrei auf Anzeigegröße neu kodiert:
    - `train.png`: 3168×448 → 1584×224 (identisches 7.07:1-Seiten-
      verhältnis; die per `clamp(1.5rem, 4vw, 2.5rem)` gedeckelte
      Anzeigehöhe unterschritt die neue 224-px-Nativhöhe schon bei 3× DPR),
      pngquant-`--quality 65`-Palettenquantisierung + optipng `-o7 -fix`.
      **1015927 → 63046 bytes (93.8 % Reduktion)**; mittlere
      Pro-Kanal-RGB-Differenz bei Anzeigegröße 0.8–1.1 von 255
      (nicht wahrnehmbar).
    - `footer-bg.jpg`: 2732×1536 → 1920×1080 (16:9 erhalten), JPEG
      Quality 72 + progressiv + `jpegoptim --strip-all`. **677250 →
      194825 bytes (71.2 % Reduktion)**; die Pro-Kanal-Differenz wird
      durch das darübergelegte Dunkel-Gradient-Overlay mit 78–94 %
      Deckkraft weiter abgeschwächt, sodass der sichtbare Unterschied
      deutlich unter 0.2 % liegt.
  * CSS/JS-Pipeline auf eingecheckte minifizierte Bundles umgestellt:
    - `docs/assets/site.css` und `site.js` bleiben die maßgeblichen
      lesbaren Quellen; neue `site.min.css` (15486 bytes, 18.4 %
      kleiner) und `site.min.js` (18628 bytes, 25.9 % kleiner) werden
      daneben erzeugt und von `docs/site.html` referenziert.
    - Pure-Python (`rcssmin` / `rjsmin`) — keine Node-Toolchain
      eingeführt, was die „kein Build-Schritt"-Haltung des Projekts
      für das Dashboard bewahrt. Das neue `scripts/optimize_site_assets.py`
      treibt die Pipeline; der `--check`-Modus schlägt fehl (fail-closed),
      wenn die committeten Bundles aus dem Sync mit ihren Quellen
      geraten, und ist über einen `files:`-Filter so in pre-commit
      verdrahtet, dass er nur läuft, wenn ein
      `site.{css,js,min.css,min.js}`-Blob gestaged ist.
  * HTML-Mikro-Fixes, die das visuelle Layout **nicht** berühren:
    - `aria-label="Wien ÖPNV Live-Dashboard – Startseite"` enthält
      beide sichtbaren Sub-Labels und erfüllt damit die
      `label-content-name-mismatch`-Regel von axe.
    - `width`/`height` des Train-Sprite-`<img>` auf die neue
      1584×224-Nativgröße aktualisiert, sodass die Aspect-Ratio-
      Reservierung des Browsers zur Bitmap passt (CSS `height:100%`
      bestimmt weiterhin die gerenderte Größe — CLS unverändert).
    - `fetchpriority="low"` am dekorativen Train-Sprite gibt Bandbreite
      für höher priorisierte Ressourcen während des frühen Ladens frei.
    - `<link rel="dns-prefetch">` + `<link rel="preconnect" crossorigin>`
      lösen `raw.githubusercontent.com` vorab auf, sodass die von
      `site.min.js` angestoßenen verzögerten CSV-Fetches den
      Cold-DNS-Treffer überspringen.
  * Netto-Effekt auf den dokumentierten Payload: 1693 KiB → 258 KiB
    allein bei den zwei Bild-Assets (1435 KiB gespart, ~86 %); zusammen
    mit dem CSS/JS-Schrumpf sinkt das von Lighthouse gemeldete
    Gesamt-Seitengewicht von `docs/` von 1677 KiB auf ≈ 300 KiB. Visuelles
    Rendering und JS-Verhalten sind bei Anzeigegröße und im
    vollständig geladenen Zustand byte-für-byte identisch; keine
    CSP-Lockerung, kein neuer Third-Party-Request.
  * `requirements-dev.txt` erhält `rcssmin`, `rjsmin` und `Pillow`
    (Pure-Python, wo es zählt; Pillow ist in jeder Umgebung, die zuvor
    bildbezogene Skripte ausgeführt hat, bereits installiert). Die
    Bild-Binaries (`pngquant`, `optipng`, `jpegoptim`) bleiben optional —
    Mitwirkende, die nur CSS/JS bearbeiten, brauchen sie nicht, und das
    Skript degradiert graziös (warnt + fährt fort), wenn eines davon
    nicht im PATH liegt.
  * Marker: SENTINEL_LIGHTHOUSE_2026_05_17_ASSET_PAYLOAD.

* **Security: Secret-Scanner-Drift Runde 14 — Erkennungslücke beim Präfix
  des AWS STS Service Bearer Tokens (`ABIA<16>`) (2026-05-16)**:
  * Schließt das vierte Credential-Präfix in der AWS-Familie der
    4-Zeichen-Unique-Identifier, das `_AWS_ID_RE` ausdrücklich als
    benannt-aber-ungedeckt aufführte: `ABIA` (AWS STS Service Bearer
    Token, ausgestellt von `sts:GetServiceBearerToken` für
    Service-zu-Service-Authentifizierung im Namen eines AWS-Nutzers). Die
    Abschluss-Checkliste von Runde 13 führte nur `AKIA`/`ASIA`/`ACCA`
    auf — `ABIA` war das dokumentierte vierte Credential-Präfix, das ungedeckt blieb.
  * Vor dem Fix rutschten blanke `ABIAV2EXAMPLE12345AB`-Tokens (20 Zeichen:
    4-Zeichen-Präfix + 16-Zeichen-`[A-Z0-9]`-Body) durch jeden Erkennungs-
    zweig in `_scan_content`: `_HIGH_ENTROPY_RE` verlangt `{24,}` Zeichen
    und weist die 20-Zeichen-Form ab; `_AWS_ID_RE` führte nur drei von
    vier Präfixen auf; die Zuweisungs-Heuristik verliert die
    AWS-spezifische Attribution. Netto: stille Nicht-Erkennung in
    Nicht-Zuweisungs-Kontexten (CloudTrail-Debug-Log-Zeilen, AWS-SDK-Debug-
    Traces mit `AWS_DEBUG=true`, JSON-Fixtures ohne sensible Schlüssel,
    Doku-Snippets, feindselige PR-Fragmente) und Attribution-Drift
    in Zuweisungs-Kontexten (nur das generische "Verdächtige Zuweisung"
    feuerte und verlor die AWS-STS-spezifische Revocation-Flow-Attribution).
  * Einzelnes Tupel ergänzt zu `_KNOWN_TOKENS` in
    `src/utils/secret_scanner.py`:
    `re.compile(r"(?<![A-Za-z0-9])ABIA[A-Z0-9]{16}(?![A-Za-z0-9])")`
    mit Begründung `"AWS STS Service Bearer Token gefunden"`. Das strikte
    `[A-Z0-9]{16}`-Body-Alphabet schützt gegen False Positives auf
    Lowercase-/Mixed-Case-Strings, die zufällig mit `ABIA` beginnen;
    das `(?<![A-Za-z0-9])`-Lookbehind verhindert Treffer mitten im Wort.
    Die KNOWN_TOKENS-Verarbeitung läuft vor `_AWS_ID_RE`, sodass
    `is_covered` die spezifischere Aussteller-Attribution korrekt verankert.
  * Umfassende Testabdeckung in
    `tests/test_sentinel_secret_scanner_drift_round14.py` (10 Tests):
    Plaintext-Kontext-PoC (Silent-Undetection-Zweig), JSON-Fixture-
    PoC, Zuweisungs-Kontext-PoC (Attribution-Drift-Zweig), drei
    Negativfälle (kurzer Body / Lowercase-Body / ABIA mitten im Wort),
    drei Regressions-Guards (AKIA/ASIA/ACCA erhalten weiterhin das
    kanonische `AWS Access Key ID gefunden` — keine Kollision) und ein
    Inventar-Invariant-Pin (`ABIA`- + `AWS STS Service Bearer Token`-Strings
    im Quelltext von `secret_scanner.py` vorhanden).
  * Marker: SENTINEL_AWS_ABIA_PREFIX_DRIFT.

* **Security: Schließung der Non-Finite-Literal-Drift für Netzwerk/Env/Sidecar — 18 JSON-
  Parser-Stellen (2026-05-15)**:
  * Schließt das **symmetrische Gegenstück** zur Closure des
    Committed-State-File-Readers aus PR #1503 über drei orthogonale
    Taint-Kanäle hinweg: 13 netzwerk-getaintete HTTP-Antworten
    (`wl_fetch._get_json`,
    `places.client._post`/`_format_error_message`,
    `hafas_client._fetch_hafas_location`,
    `osm_client.OSMOverpassClient._fetch_payload`,
    `reporting._GithubIssueReporter.submit`,
    `check_overpass_status._evaluate_response`,
    `verify_vor_access_id`, `update_baustellen_cache._load_json_from_content`,
    `update_stammstrecke_hbf` + `update_stammstrecke_status` VAO-Endpunkte),
    2 env-getaintete `BOUNDINGBOX_VIENNA`-Parser
    (`fetch_google_places_stations._parse_bounding_box`,
    `update_station_directory._parse_bounding_box`) und 3
    Disk-Sidecar-State-Reader, die PR #1503 übersehen hatte
    (`build_feed._read_state_capped`,
    `update_stammstrecke_status._load_pending_trips` /
    `_load_recently_finalised`).
  * Ohne diese Pins kann ein kompromittierter Upstream / DNS-Hijack /
    MITM / geleaktes CI-Env / feindseliger Operator an jeder dieser
    Parse-Grenzen `NaN`- / `Infinity`- / `-Infinity`- / `1e1000`-Literale
    einschleusen. Der Lenient-Mode-Parser liefert eine Python-Struktur
    mit `float('nan')` / `float('inf')` darin zurück und vergiftet so
    Vergleiche (`nan != nan` ist True — bricht Dedup-Invarianten),
    Arithmetik (`nan + x` ist nan — korrumpiert still Latenz-Mittelwerte
    und Verspätungsberechnungen) und das Zurückschreiben an den
    Writer-Pin (`allow_nan=False` aus Runde 1485/1487/1488/1491) — die
    Cron-Pipeline stürzt beim nächsten Persistieren mitten im Schreiben ab.
  * Neuer kanonischer Helper `loads_finite()` in `src/utils/files.py`
    (dünner Shim über `json.loads`, der die von PR #1503 etablierten
    Hooks `_reject_non_finite_constant` + `_reject_non_finite_float`
    fest einbaut). Neue Aufrufstellen sollten `loads_finite()` nutzen
    statt `json.loads()` direkt aufzurufen; `response.json()`-Stellen
    übergeben die Hooks als kwargs.
  * Umfassende Testabdeckung in
    `tests/test_sentinel_network_tainted_non_finite_drift.py` (38
    Tests): 5 Verhaltenstests für den kanonischen Helper, 18
    Inventar-Pins (Source-Grep jeder aufgezählten Stelle nach dem Hook),
    pro-Stelle-Verhaltens-PoCs über NaN / Infinity / Scientific-Notation-
    Überlauf + Finite-Round-Trip-Regressionsschutz, plus der
    Writer-Reader-Round-Trip-Symmetriebeweis.
  * Marker: SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT.

* **Stammstrecke-Ausfälle — Neue Statistik aus bestehenden VAO-Abfragen
  (2026-05-15)**:
  * Der Hbf-`/departureBoard`-Reader (`scripts/update_stammstrecke_hbf.py`)
    und der Legacy-`/trip`-Reader (`scripts/update_stammstrecke_status.py`)
    haben Abfahrten mit `cancelled: true` bislang **stillschweigend
    verworfen**: das `delay_minutes`-Signal war `None`, und die
    Sammelschicht filterte solche Beobachtungen vor dem Ledger
    heraus. Folge: jeder tatsächliche Zugausfall auf der Stammstrecke
    war für die Statistik unsichtbar.
  * Neue Datei `data/stats/ausfaelle_<YYYY>.csv` mit Schema
    `timestamp, weekday, hour, direction, line` — eine Zeile pro
    ausgefallenem Zug. Beide Reader leiten Ausfälle nun durch die
    bestehende Pending-Trip-Identity-Key-Dedup (`(direction, name,
    scheduled)`) und durch den Recently-finalised-Schutz, sodass
    derselbe physische Ausfall NIE über mehrere Cron-Ticks doppelt
    gezählt wird. Der Cancellation-Check läuft jetzt VOR dem
    `rtTime`-Filter, weil VAO bei ausgefallenen Zügen regelmäßig
    keinen Realtime-Wert mehr ausliefert — der frühere Filter hat
    die Cancellation-Signale gemeinsam mit den No-rtTime-
    Beobachtungen verworfen.
  * `_PendingTrip` trägt jetzt ein `cancelled: bool`-Flag, das
    auch im Pending-Ledger (`cache/stammstrecke/pending_trips.json`)
    serialisiert wird. Legacy-Einträge ohne das Feld laden als
    `cancelled=False` (Backwards-Compat). Der Finalize-Pass teilt
    pro Cron-Tick die ausgelaufenen Pending-Trips in zwei Buckets:
    delay-tragende Beobachtungen fließen wie bisher in eine
    aggregierte CSV-Zeile pro Richtung+Jahr
    (`stammstrecke_<YYYY>.csv`); Ausfälle erzeugen jeweils eine
    eigene Zeile in `ausfaelle_<YYYY>.csv`, damit der Dashboard-
    Aggregator sie als diskrete Ereignisse zählen kann.
  * **Dashboard** (`docs/statistik.md`): neue Sektion `## Ausfälle`
    mit Tabellen pro Richtung und pro Linie sowie Wochentag-/
    Stunde-Balken. Die `Kennzahlen auf einen Blick`-Tabelle zeigt
    zusätzlich die Jahressumme. **README**: zwei neue Marker
    `STATS:AUSFAELLE_LIVE` (60-Min-Fenster) und `STATS:AUSFAELLE`
    (30-Tage-Fenster). Die Ausfälle-Marker werden bedingungslos
    aktualisiert, auch bei `0` Beobachtungen — ein explizites
    `0` ist das operationell wertvolle „stabiler Betrieb"-Signal
    und unterscheidet sich klar von „Daten fehlen".
  * **Tests**: neue Pin-Tests in `tests/test_utils_stats.py`
    (Writer + CSV-Formula-Injection-Defang), `tests/scripts/
    test_update_stammstrecke_status.py` (Collector + Pending-Trip-
    JSON-Roundtrip + Backwards-Compat-Loader + End-to-End-Finalize-
    Routing inkl. „mixed delay+cancellation in einem Tick"),
    `tests/scripts/test_update_stammstrecke_hbf.py` (Collector +
    Cancellation-Bool-vs.-String + No-rtTime-mit-Cancellation),
    `tests/scripts/test_generate_markdown_stats.py` (Aggregator,
    Renderer, README-Block) und
    `tests/scripts/test_generate_markdown_stats_readme.py` (volle
    Marker-Integration mit explizitem 0-Render).
* **Stammstrecke-Feed-Trigger — Legacy-Label-Auflösung im
  Compute-Pfad (2026-05-15)**:
  * Der Trigger-Compute in `src.feed.stammstrecke.compute_
    stammstrecke_events` bucket'te Observations bisher nach
    `obs.direction` (raw CSV value); der Backwards-Compat-Alias in
    `DIRECTIONS_BY_LABEL` (Floridsdorf → Praterstern-`_Direction`)
    war auf dem heißen Pfad nicht aktiv. Folge: CSV-Zeilen mit dem
    Legacy-Label `"Floridsdorf"` (z.B. nach Backup-Restore, Partial-
    Deploy oder Hand-Edit) wären silently im Loop ignoriert worden,
    weil das Loop-Lookup `direction.target_label = "Praterstern"`
    den `by_direction["Floridsdorf"]`-Bucket nicht aufsucht. Fix:
    Observations werden via `DIRECTIONS_BY_LABEL` zur kanonischen
    Direction aufgelöst, bevor sie in den Bucket landen.
  * Neue Test-Suite `tests/test_feed_stammstrecke_trigger.py`
    (9 Tests) pinnt die Trigger-Semantik: Happy-Path (2 Praterstern-
    Zeilen > 9 min), Legacy-Compat (2 Floridsdorf-Zeilen fold-in),
    Mixed (1+1), Threshold-Gate (Single-row + boundary-9.0),
    Window-Cutoff (Beobachtung knapp außerhalb 1h), Empty-Input,
    Direction-Isolation (beide Richtungen feuern parallel),
    Constants-Pinning (`DELAY_THRESHOLD_MINUTES`, `FEED_WINDOW`).
* **Stammstrecke-Monitor — Nord-Richtungs-Label umbenannt:
  "Floridsdorf" → "Praterstern" (2026-05-15)**:
  * Die CSV-Spalte `direction` und das `DIRECTION_LABEL_NORTHBOUND`
    der Schreiber + des Feed-Renderers verwenden ab sofort
    `"Praterstern"` statt `"Floridsdorf"` für nordwärts gerichtete
    Stammstrecken-Beobachtungen. Begründung: Bei kurzen Wendezügen,
    die bereits am Praterstern oder Wien Mitte terminieren (und nicht
    bis Floridsdorf weiterfahren), bezeichnete die alte Beschriftung
    fälschlich einen Endpunkt, den die meisten Züge gar nicht
    erreichen. Die Süd-Beschriftung `"Meidling"` benennt seit jeher
    die nächste Stammstrecken-Haltestelle nach dem Hbf — die
    Umbenennung gibt der Nord-Beschriftung die gleiche Semantik:
    `"Stammstrecken-Züge in Richtung <nächster Stammstrecken-
    Haltestelle nach Hbf>"`.
  * **Datenmigration**: Alle bestehenden Zeilen in
    `data/stats/stammstrecke_2026.csv` wurden mit dem Rename-Commit
    `Floridsdorf` → `Praterstern` umgeschrieben. Die in-flight Pending-
    Trip- und Recently-finalised-Ledger
    (`cache/stammstrecke/pending_trips.json` /
    `cache/stammstrecke/recently_finalised.json`) wurden ebenfalls
    konvertiert — sowohl die `direction`-Feldwerte als auch die
    Identity-Key-Präfixe.
  * **Backwards-Compat-Shim**: Der Feed-Renderer
    (`src/feed/stammstrecke.py`) akzeptiert in
    `DIRECTIONS_BY_LABEL` weiterhin den Legacy-Wert `"Floridsdorf"`
    (alias auf die `Praterstern`-Direction). Der Hbf-Cron-Pfad ruft
    `_finalize_departed` zusätzlich für `LEGACY_DIRECTION_LABEL_
    NORTHBOUND` auf, sodass ein extern wiederhergestellter Pending-
    State mit alten Schlüsseln transparent in den Praterstern-Bucket
    fließt. Das CSV wird stets unter dem neuen Label geschrieben.
  * **Feed-Item-GUID**: Die `identity_prefix` für Nord wurde von
    `stammstrecke_delay_floridsdorf` auf `stammstrecke_delay_praterstern`
    umbenannt. Da der `data/first_seen.json` aktuell keinen aktiven
    Nord-Eintrag enthält, propagiert die Umbenennung als saubere
    "neue Direction" für RSS-Abonnenten, ohne ein laufendes Event
    doppelt zu emittieren. Sollte bei einem zukünftigen Nord-Incident
    ein laufendes Event aus der Zeit vor dem Rename existieren, würde
    es einmalig als „neues" Event in RSS-Readern erscheinen.
* **Stammstrecke-Monitor — Platform-Level Bahnsteig-Filter
  (2026-05-15)**:
  * Der `/departureBoard`-Reader filtert seit dieser Änderung jede
    Abfahrt am Wien Hauptbahnhof nach ihrem effektiven Bahnsteig
    (`rtTrack` mit Fallback auf scheduled `track`). Nur Abfahrten
    auf **Bahnsteig 1** (Stammstrecke nordwärts → Floridsdorf) oder
    **Bahnsteig 2** (Stammstrecke südwärts → Meidling) qualifizieren
    sich für die Stammstrecke-Statistik. Alle anderen Hbf-Bahnsteige
    (3-12, inkl. Halb-Bahnsteige „1A", „10A-B" usw.) tragen
    Fernverkehr (RJ/IC/EC/NJ), Hbf-endende REX-Züge, die Marchegger
    Ostbahn, die Pottendorfer Linie, die Westbahn und weitere
    Korridore, die NICHT die Stammstrecke nutzen — sie werden seit
    diesem Patch deterministisch ausgeschlossen.
  * Begleitend wurden die Substring-Listen für die Richtungsbestimmung
    bereinigt: `marchegg` und `bratislava` entfernt, weil beide
    Termini mehrdeutig waren (Marchegg verkehrt rein östlich über die
    Ostbahn ohne Stammstrecken-Bezug; Bratislava ist sowohl via
    Stammstrecke + Břeclav als auch via Ostbahn erreichbar). Der
    Bahnsteig-Filter macht die Substring-Heuristik nur noch für die
    Richtungsbestimmung notwendig (Nord vs Süd), nicht mehr für die
    Stammstrecke-Zugehörigkeit selbst.
  * Diagnostik: Zwei neue Counter (`dropped_no_track`,
    `dropped_non_stammstrecke_track`) im Tick-Log machen sowohl ein
    VAO-Schema-Drift (Bahnsteig-Info fehlt) als auch das gesunde
    Ausscheiden von Nicht-Stammstrecken-Zügen operativ sichtbar,
    ohne dass die Bahnsteig-Strings zwischen den Filtern wandern.
  * Semantik: Die Hbf-basierte Messung bleibt eine Stammstrecken-
    Messung (am Korridor-Mittelpunkt), aber jetzt mit strenger
    Linien-Eindeutigkeit auf Bahnsteig-Niveau — vergleichbar mit der
    ursprünglichen `/trip`-basierten Floridsdorf-↔-Meidling-Selektion
    der Pre-Hbf-Ära, ohne deren `numF=6`-Sampling-Lücke.
* **Stammstrecke-Monitor — Migration auf `/departureBoard` @ Wien Hbf
  (2026-05-15)**:
  * Der Cron-Pfad ruft seit dem Merge von PR #1496 das neue
    `scripts/update_stammstrecke_hbf.py`-Skript auf, das die
    `/departureBoard`-API einmal pro Tick am Wien Hauptbahnhof
    befragt und die Abfahrten anhand der Endhaltestelle per
    Substring-/Whitelist-Klassifikation in die bestehenden
    Richtungs-Labels (`Meidling`, `Floridsdorf`) einsortiert. Im
    Vergleich zum Vorgänger (`/trip` × 2 Richtungen mit hartem
    `numF=6`-Cap) verdoppelt sich die Coverage bei gleichzeitiger
    Halbierung des API-Budgets (1 statt 2 Requests/Tick).
  * **Semantischer Bruch in der Verspätungs-Messung**: bis
    2026-05-15 wurde die Verspätung **am Ursprungsbahnhof**
    (Floridsdorf für Meidling-Bound-Züge, Meidling für
    Floridsdorf-Bound-Züge) gemessen, ab 2026-05-15 **am Wien
    Hauptbahnhof** — einem Stammstrecken-Mittelpunkt. Beide Zahlen
    sind für denselben physischen Zug nicht identisch (Verspätung
    kann zwischen Ursprung und Hbf akkumulieren oder eingeholt
    werden). Die 30-Tage-Statistik im README überspannt den
    Migrations-Tag und zeigt deshalb für einige Wochen eine
    Diskontinuität, die ein Mess-Semantik-Wechsel ist, kein Bug
    und keine reale Qualitäts-Veränderung. Wer Werte vor und
    nach 2026-05-15 vergleicht, sollte diesen Stichtag im Auge
    behalten.
  * `data/stats/stammstrecke_<YYYY>.csv`-Schema und
    `cache/stammstrecke/*.json`-Ledger-Format bleiben unverändert
    (die README-Dashboard- und Feed-Event-Pipelines lesen byte-
    weise identisch weiter). `manual-full-refresh.yml` ist
    ebenfalls auf das neue Skript umgezogen, damit ein manueller
    Refresh keine konkurrierenden Identity-Key-Formate in den
    geteilten Pending-Trip-Ledger schreibt.
* **Quota-Bug Fix (Phantom-Request pro Skript-Lauf, 2026-05-15)** —
  `_flush_quota_cache` rief `save_request_count` auf, das jeden
  Aufruf als neuen Request zählte: jeder Stammstrecke-Cron-Tick
  buchte 3 Requests statt 2 auf den 100/Tag-VAO-Start-Counter. Bei
  48 Ticks/Tag wurde die Quote nach ~33 Ticks (~16 h) erschöpft und
  der Preflight-Gate übersprang die restlichen Ticks, wodurch sich
  im Ledger eine ~8h-Lücke pro Tag ergab und die README-Statistik
  "Letzte 60 Minuten" zeitweise auf 1-3 Beobachtungen abrutschte.
  Fix in PR #1494: Persist-Logik aus `save_request_count` in einen
  separaten `_persist_quota_to_disk`-Helper ausgegliedert, den der
  atexit-Flush direkt aufruft ohne den Counter zu inkrementieren.
  Regression-Tests pinnen das No-Inflation-Invariant.
* **Docs/Cleanup (Nachzug zur VOR-Stammstrecke-only-Konsolidierung)** —
  Doku- und Workflow-Drift nach der 2026-05-11-Konsolidierung (VOR
  ist nur noch für den Stammstrecken-Monitor zuständig) bereinigt:
  * Tote Skript-Verweise auf `update_vor_cache.py`,
    `update_vor_stations.py` und `fetch_vor_haltestellen.py` aus
    `docs/development.md`, `docs/architecture.md`,
    `.github/workflows/manual-full-refresh.yml` und
    `.github/workflows/update-stations.yml` entfernt; die Scripts
    existieren seit 2026-05-11 nicht mehr.
  * Verwaiste `cache/vor_929f1c/last_run.json` (kein aktiver Writer
    nach der Konsolidierung; Status seit 2026-05-09 `api_unreachable`)
    plus leeres Parent-Verzeichnis gelöscht.
  * CLI-Help-Text `python -m src.cli cache update …` listet `vor`
    nicht mehr als gültigen Provider-Identifier (der Handler hat es
    ohnehin schon abgewiesen, jetzt ist die Hilfe konsistent).
  * Stale `update-vor-cache.yml`-Workflow-Verweis in
    `src/utils/cache.py` (`write_status`-Sicherheitskommentar) und in
    `tests/test_sentinel_quota_status_trojan_source.py` als historisch
    gekennzeichnet — die Trojan-Source-Defence im Writer bleibt
    unverändert in Kraft.
* **Changed (WL-OGD-Reaktivierungskette, PR #1441-#1453)**: Dreizehn
  konsolidierte PRs reaktivieren den Wiener-Linien-OGD-Merge-Pfad
  vollständig gegen den kanonischen
  `www.wienerlinien.at/ogd_realtime/doku/ogd/`-Endpunkt (der vorherige
  `data.wien.gv.at/csv/`-Proxy wurde in der 60. OGD-Phase im September
  2025 abgeschaltet).
  * **Endpoint + Workflow (#1441, #1442)** — den redundanten
    Inline-curl-Schritt aus `update-stations.yml` entfernt, beide
    Konstanten `OGD_HALTESTELLEN_URL` / `OGD_HALTEPUNKTE_URL` auf den
    kanonischen Wiener-Linien-Host migriert. Soft-Fail auf gepinnte
    lokale CSVs bei Upstream-Ausfall.
  * **Schema-Fuzzy-Keys (#1444)** — Spalten-Aliase ergänzt, sodass der
    Loader sowohl das Legacy-Proxy-CSV
    (`HALTESTELLEN_ID`/`NAME`/`WGS84_*`) als auch das kanonische
    OGD-Echtzeit-CSV (`DIVA`/`PlatformText`/`StopText`/`Latitude`) parst.
  * **WL-only-Einträge im `wl_diva`-Namensraum (#1446)** — synthetische
    `bst_id` (`9{DIVA}`) und synthetischen `bst_code`
    (`WL-{name[:3]}`) bei WL-only-Einträgen entfernt; das kanonische
    `wl_diva`-Feld ist der einzige strukturelle Identifier, und
    Cross-Station-ID-Kollisionen / `WL-ABS`-artige Code-Duplikate
    sind verschwunden.
  * **Pendler-Default für Border-Stops (#1443)** — nicht zugeordnete
    WL-Haltestellen außerhalb des Wien-Polygons werden automatisch auf
    `pendler=True` hochgestuft.
  * **Validator-Identifier (#1447)** — `_format_identifier` schließt
    jetzt `wl_diva` ein, sodass WL-only-Einträge distinkte Schlüssel
    erhalten, statt auf `"source:wl"` zu kollabieren (was 1759 Stationen
    über 30 echte Naming-Gruppen in die Auto-Quarantäne gezogen hatte).
  * **StopID- + Richtungsmarker-Sanitisierung (#1445)** — kurze
    `StopID`-Zählerwerte werden aus den `aliases` herausgefiltert (die
    Legacy-8-stellige RBL bleibt); `<`- und `>`-Richtungsmarker in
    `StopText` werden durch `←` / `→` ersetzt, sodass sie nicht mehr
    `_UNSAFE_CHARS_RE` treffen.
  * **`in_vienna`-Konsistenz (#1449)** — `build_wl_entries` leitet
    `in_vienna` jetzt aus den aggregierten Haltepunkt-Koordinaten ab
    statt nach Any-Stop-wins, sodass Grenzstationen kein Flag mehr
    tragen, das ihren persistierten Koordinaten widerspricht. Gepinnt
    durch `test_coordinates_match_in_vienna_flag`.
  * **ÖBB-Workbook-Soft-Fail (#1450)** — `download_workbook` schreibt
    bei jedem erfolgreichen Lauf atomar einen Snapshot nach
    `data/oebb-verkehrsstationen.xlsx` und liest aus dem Snapshot, wenn
    `data.oebb.at` einen Netzwerkfehler liefert. Schließt den
    asymmetrischen Fehlermodus, in dem ÖBB die einzige Fail-Fast-
    Upstream-Quelle war. Die CodeQL-Config
    (`.github/codeql/codeql-config.yml`) schließt das
    `py/clear-text-storage-sensitive-data`-False-Positive aus, das auf
    jeden Public-Data-Cache-Writer in diesem Projekt zutrifft.
  * **Multi-DIVA-Merge <150 m (#1451)** — `_merge_colocated_dupli
    cates` faltet gleichnamige Haltestellen mit Haltepunkte-Mittel-
    Koordinaten innerhalb von 150 m zueinander zu einem einzigen
    Eintrag zusammen (die lexikographisch kleinste DIVA gewinnt, alle
    Haltepunkte und Aliase werden vereinigt). Entfernt 4 Doppelungen
    aus dem aktuellen `stations.json` (Stock im Weg, Vorgartenstraße,
    Lieblgasse, Altmannsdorfer Straße).
  * **`name` ist Display-Label, kein PK (#1452)** — die
    Kanonische-Namens-Eindeutigkeitsprüfung des Validators wird
    entfernt. Strukturelle Eindeutigkeit lebt in `wl_diva` / `bst_id` /
    `vor_id` / `bst_code`; `name` ist operator-zugewandt. Der
    `_disambiguate_duplicate_names`-DIVA-Suffix-Workaround
    (`Wien Bahnhof (WL 60205022)`) ist abgeschafft — doppelte
    Display-Labels sind jetzt zulässig und der RSS-Feed zeigt die
    saubere `Wien Bahnhof (WL)`-Form.
  * **Aussagekräftige Display-Namen aus `StopText` (#1453)** —
    `_derive_station_label` überschreibt generische transport-typisierte
    `PlatformText`-Tokens der Haltestelle (`Bahnhof`, `Lokalbahn`,
    `Hauptbahnhof`, `Station`, `Halt`, `Bf`, `Hbf`, `Bahn`,
    `U-Bahn`) mit dem Haltepunkte-`StopText`, sofern einer
    verfügbar ist. Sechs Einträge bekamen ein echtes Toponym:
    `Wien Bahnhof (WL)` × 2 → `Wien Tribuswinkel - Josefsthal
    (WL)`, `Wiener Neudorf (WL)`; `Wien Lokalbahn (WL)` × 4 →
    `Wien Guntramsdorf Lokalbahn (WL)`, `Wien Möllersdorf (WL)`,
    `Wien Neu Guntramsdorf (WL)`, `Wien Traiskirchen Lokalbahn
    (WL)`. Nicht-generische PlatformText-Werte bleiben unangetastet,
    sodass ÖBB- / VOR-Name-basierte Joins stabil bleiben.
  * **Test-Daten-Refresh (#1449)** — drei Stationsverzeichnis-Tests
    hatten Legacy-DIVAs hartkodiert, die Wiener Linien seither
    umnummeriert hat (`60201076` war vor PR #1442 Karlsplatz und ist
    jetzt Ratzenhofergasse; `60201002` war Schottentor und ist jetzt
    Pensionsversicherungsanstalt). Auf aktuelle DIVAs aktualisiert.
  * **Ergebnis auf Produktivdaten**: `stations.json` wuchs von 196 auf
    1951 Einträge (4 ko-lokalisierte Doppelungen aus 1803 WL-Einträgen
    herausgemergt), 0 DIVA-Suffixe in kanonischen Namen, 0 generische
    `Wien Bahnhof (WL)` / `Wien Lokalbahn (WL)`-Labels, der Validator
    meldet 0 Alias- / Naming- / Security-Issues, `quarantine.json`
    bleibt über Cron-Ticks hinweg leer.
* **Changed (Auto-Quarantine für `update_all_stations.py`)**: Blockierende
  Validation-Issues (`provider_issues`, `cross_station_id_issues`,
  `naming_issues`, `security_issues`) brechen die Pipeline nicht mehr ab.
  Stattdessen werden die betroffenen Einträge aus dem gemergten
  `tmp_stations_path` herausgefiltert, in `data/quarantine.json`
  persistiert (mit `timestamp` / `count` / pro-Station-Issues) und der
  Rest des Pipelines (Diff, Heartbeat, Atomic-Copy-Back) läuft mit dem
  gültigen Subset weiter. Damit überlebt der Feed eine partielle
  Upstream-Korruption (einzelne kaputte VOR-/OEBB-/WL-Einträge) und
  exitet mit `0`. Der ``<global>``-Sentinel der Provider-Issue-Liste
  (z. B. "Need at least two VOR entries") wird übersprungen — er
  korrespondiert mit keinem einzelnen Eintrag und kann nicht
  quarantänisiert werden. Tests: 5 neue Cases in
  `test_update_all_stations_diff_heartbeat.py` /
  `test_update_all_stations_wrapper.py` decken Identifier-Filterung,
  Partition-Logik, End-to-End-Quarantine-Schreiben und den
  ``<global>``-Skip ab. Mypy `--strict` bleibt clean.
* **Changed (Stammstrecke-Monitor → VOR/VAO ReST API)**: Der S-Bahn-
  Stammstrecken-Verspätungs-Monitor wurde von `pyhafas` (`OEBBProfile`)
  auf die offizielle VOR/VAO ReST `/trip`-API portiert. Hintergrund:
  das auf PyPI veröffentlichte `pyhafas` exportiert kein
  `OEBBProfile`, der Import schlug seit Wochen still fehl und
  `data/stats/stammstrecke_*.csv` blieb leer (siehe Audit-Bericht
  zu PR #1378).
  - **Removed**: `pyhafas` aus `requirements.txt`,
    `from pyhafas import HafasClient` / `_build_client` /
    `_query_journeys` / `_patch_session_timeout` aus
    `scripts/update_stammstrecke_status.py`.
  - **Replaced**: HAFAS-Aufruf durch `fetch_content_safe` gegen
    `${VOR_BASE_URL}trip` mit `originId` / `destId` / `numF=5` /
    `maxChange=0` / `rtMode=SERVER_DEFAULT`. Auth via
    `vor_provider.VorAuth` (gleicher Stack wie Disruption-Provider).
    Quota-Slot wird **vor** jedem Network Call via
    `_charge_one_request` reserviert.
  - **Stabil**: Event-Schema (`source: "ÖBB"`), `first_seen`-
    Persistenz, `DELAY_THRESHOLD_MINUTES = 9`, Self-Healing-Regel,
    Atomic-Write, CSV-Statistik-Logging, Cron-Schedule
    (`*/30 * * * *`). Feed-Reader-Subscribers bemerken den Wechsel
    nicht.
  - **Tests**: Mocks an der `_query_trips`-Boundary statt an einer
    pyhafas-`HafasClient`-Imitation. 64 Tests in
    `tests/scripts/test_update_stammstrecke_status.py` decken
    `_is_sbahn_leg` (3 Signal-Quellen), Direct-Connection-Filter,
    Realtime-Erkennung, Quota-Charge-vor-Fetch, Threshold-Semantik,
    `first_seen`-Persistenz, Self-Healing und Schema-Compliance ab.
  - **Doku**: `docs/reference/oebb_provider_logic.md` enthält jetzt
    nur noch die ÖBB-RSS-Scraper-Logik (`src/providers/oebb.py`); der
    Stammstrecke-Monitor ist nach
    `docs/reference/stammstrecke_provider_logic.md` ausgegliedert.
* **Changed (VOR API quota optimization)**: `DEFAULT_MONITOR_WHITELIST`
  in `src/providers/vor.py` ist jetzt **leer** (vorher
  `"Wien Hauptbahnhof,Flughafen Wien"`). Begründung: das
  Tagesbudget von 100 VAO-Requests wird nach der Stammstrecke-
  Migration von 96 Stammstrecken-Calls (`/trip` × 2 × 48) dominiert;
  parallele Departure-Board-Polls würden das Limit überschreiten.
  Operatoren, die das Legacy-Verhalten brauchen, setzen
  `VOR_MONITOR_STATIONS_WHITELIST` explizit per Umgebungsvariable.
* **Changed (Station-Enrichment-Whitelist)**: `fetch_vor_stops_from_api`
  in `scripts/update_vor_stations.py` macht Live-API-Calls jetzt nur
  noch für die 10 Stammstrecke-Stationen (`STAMMSTRECKE_VOR_IDS`).
  Alle anderen Station-IDs fallen auf die gepinnte
  `data/vor-haltestellen.csv` zurück. Begründung wie oben — bewahrt
  das Tagesbudget für den heißen Pfad. Test-Coverage:
  `test_fetch_vor_stops_from_api_skips_non_stammstrecke_ids`.
* **Added (Statistik-Dashboard)**: Zero-dependency Append-only-CSV-
  Pipeline und Markdown-Dashboard — Architektur-Kontext in
  [`docs/architecture.md` § 6](docs/architecture.md).
  - Producer — `scripts/update_stammstrecke_status.py` hängt nach
    jeder Median-Berechnung eine Zeile an
    `data/stats/stammstrecke_YYYY.csv` an (auch unterhalb der
    RSS-Schwelle, damit das Dashboard die *gesamte* Verteilung
    abbildet).
  - Producer — `src/build_feed.py:_update_item_state` schreibt im
    Strict-New-Pfad (Cache-Miss auf `_identity` *und* `guid`) eine
    Zeile in `data/stats/stoerungen_YYYY.csv`. Lange Streckeninformationen
    werden genau einmal gezählt.
  - Aggregator — `scripts/generate_markdown_stats.py` (Standardlib
    only: `csv`, `collections`, `datetime`, `statistics`, `pathlib`,
    `zoneinfo`, `argparse`) rendert `docs/statistik.md` mit
    ASCII/Emoji-Bars: Verteilung je Wochentag/Stunde, ⌀ Verspätung,
    Top-5-Hotspots mit Tageszeit-Profil.
  - Workflow — `.github/workflows/generate-stats.yml`
    (Cron `15 0 * * *` + `workflow_dispatch`) committet das Dashboard
    plus neue CSV-Dateien via `stefanzweifel/git-auto-commit-action`.
* **Added (Test-Isolation)**: Autouse-Fixture `isolate_stats_writes`
  in `tests/conftest.py` monkeypatcht `src.utils.stats.DEFAULT_STATS_DIR`
  pro Test auf `tmp_path` — verhindert, dass Suite-Läufe synthetische
  Zeilen ins committete Ledger schreiben (PR #1372).
* **Security (Bounded CSV reads)**: Aggregator routet jede CSV durch
  `read_capped_text` + `io.StringIO` (entspricht dem
  `tests/test_sentinel_csv_size_bomb.py`-Sentinel) und schreibt das
  Dashboard atomar via `atomic_write`. Producer-Writer sind best-effort
  (jeder `OSError` wird auf WARNING-Level geschluckt) — Statistik
  kann den Build nie kippen.
* **Changed (Audit-Report)**: Addendum (§ 14) zum bestehenden
  [`docs/archive/audits/oebb_stammstrecke_audit.md`](docs/archive/audits/oebb_stammstrecke_audit.md)
  dokumentiert die Statistik-Pipeline-Integration und bestätigt, dass
  die Audit-Befunde der Sections 1–13 unverändert bestehen
  (Verdict bleibt **0 Findings**, production-ready).
* **Changed (Reference-Doku)**: `docs/reference/oebb_provider_logic.md`
  korrigiert auf `MAX_JOURNEYS_PER_QUERY = 5` (vormals stale `12`)
  und enthält jetzt einen Abschnitt zur Statistik-Logging-Integration
  des Stammstrecke-Skripts.
* **Audit**: Vollständige Audit-Abnahme des S-Bahn Stammstrecke
  Monitors mit Bericht unter
  [`docs/archive/audits/oebb_stammstrecke_audit.md`](docs/archive/audits/oebb_stammstrecke_audit.md).
  Verifiziert: Mypy-Strict 0 Fehler, Bandit 0 Issues, Circuit Breaker
  trippt nach 10 Failures auf 1 h Recovery, HTTP-Timeout via
  Session-Patch, Europe/Vienna an allen 13 datetime-Sites, Schema-
  Compliance gegen `docs/schema/events.schema.json` (3 / 3 Szenarien
  grün), 47 Tests + 95.3 % Coverage. Audit-Resultat: **0 Findings**,
  Feature ist production-ready.
* **Tuning (Stammstrecke)**: `MAX_JOURNEYS_PER_QUERY` von 12 auf
  **5** gesenkt. Damit wird der Median nur über die *unmittelbar
  nächsten 5* anstehenden S-Bahnen pro Richtung gebildet (10 Journeys
  pro Cron-Tick gesamt) — schärferer Median, kleinere HAFAS-Payload,
  bessere Operator-Erwartung („wie ist es jetzt?"). Zwei neue
  Pin-Tests (`test_max_journeys_per_query_is_pinned_to_five` +
  `test_query_journeys_forwards_max_journeys_kwarg`) verhindern
  zukünftige Regressionen.
* **Feat (Stammstrecke)**: Self-Healing + first_seen-Persistenz +
  erweitertes Description-Schema. Konkret:
  - **first_seen-Persistenz**: Jedes Event in
    `cache/stammstrecke/events.json` trägt nun ein eigenes
    `first_seen`-Feld (ISO-8601, Europe/Vienna). Beim nächsten
    Cron-Tick liest das Skript den vorherigen Cache, erkennt für
    jede Richtung das ursprüngliche `first_seen` und behält es bei,
    solange die Episode anhält. Damit bleibt die `guid` für die
    Dauer einer Verspätungs-Episode stabil (Feed-Reader zeigen *eine*
    fortlaufende Meldung statt einer Flut neuer Einträge alle
    30 Minuten).
  - **Description-Format**: `"Durchschnittliche Verspätung von [X]
    Minuten in Richtung [Zielbahnhof] [Seit DD.MM.YYYY]"` —
    DD.MM.YYYY ist das `first_seen`-Datum, lokalisiert auf
    Europe/Vienna.
  - **Self-Healing**: Die Cache-Datei wird *zwingend* auf `[]`
    geleert, sobald (a) die Schnittstelle nicht erreichbar ist
    (jede pyhafas-Exception, ImportError oder offener Circuit
    Breaker) ODER (b) für *alle* Richtungen der Median ≤ 9 ist.
    Dies verhindert veraltete Warnungen im RSS-Feed bei einem
    Recovery oder einem API-Ausfall.
  - **GUID-Stabilität**: `guid` wird jetzt aus
    `(identity_prefix, iso_first_seen)` abgeleitet (statt
    `iso_pubDate`), `starts_at` ist das `first_seen` (statt der
    aktuellen Beobachtungszeit). `pubDate` bleibt als Freshness-
    Indikator dynamisch.
  - **Schema-Pin-Test**: Neuer `test_build_event_validates_against_schema`
    validiert das emittierte Event-Objekt gegen
    `docs/schema/events.schema.json` (via `pytest.importorskip("jsonschema")`).
* **Security/Liveness**: Stammstrecke-Monitor erzwingt jetzt einen
  echten HTTP-Timeout für pyhafas-Aufrufe. Das vorherige Code-Snippet
  versuchte, ``client.profile.requests.timeout`` zu setzen — pyhafas
  kennt diesen Attribut-Pfad nicht (``request_session`` heißt das
  Attribut), und ``requests.Session`` honoriert ``session.timeout``
  als Attribut ohnehin nicht. Resultat: ein hängender HAFAS-Endpoint
  hätte den Cron-Run bis zur GitHub-Actions-Wallclock (6 h) blockiert
  (DoS via Slow Upstream). Neuer ``_patch_session_timeout`` patcht
  ``session.request`` (die Low-Level-Methode, an die ``post/get/...``
  delegieren) und injiziert ``timeout=QUERY_TIMEOUT`` als Default.
* **Consistency**: Stammstrecke-Events nutzen jetzt das kanonische
  Stationsverzeichnis (``src.utils.stations``) für die Auflösung der
  Ziel-Stationsnamen statt sie hartzucodieren. Damit propagiert ein
  Rename in ``data/stations.json`` (z. B. wie zuletzt bei "Wien
  Hauptbahnhof") automatisch in die Beschreibung. Der kompakte
  "in Richtung Meidling"-Stil bleibt erhalten — der ``Wien ``-Präfix
  wird nach der Lookup-Auflösung gestrippt, weil die Beschreibung
  Wien implizit voraussetzt.
* **Feat**: S-Bahn Stammstrecke Monitoring jetzt **richtungsgetrennt**.
  `scripts/update_stammstrecke_status.py` wertet beide Fahrtrichtungen
  (Floridsdorf → Meidling und Meidling → Floridsdorf) strikt
  unabhängig aus und emittiert pro Richtung **separat** ein Event,
  wenn der Median der `departure_delay`-Werte > 9 Minuten liegt
  (Liste mit 0/1/2 Events). Eine Zusammenlegung beider Richtungen
  hatte das Signal verfälscht — eine Störung in eine Richtung läuft
  oft in der Gegenrichtung normal weiter. Pro Richtung eindeutige
  `guid`/`_identity` (`stammstrecke_delay_meidling` bzw.
  `stammstrecke_delay_floridsdorf`) damit Feed-Reader die Meldungen
  als separate Notifications darstellen. Description-Format jetzt
  "Durchschnittliche Verspätung von X Minuten in Richtung
  Meidling/Floridsdorf" (Plain Text, keine HTML-Tags).
* **Feat**: Circuit-Breaker-Konfiguration auf das documented
  10-Requests-pro-Stunde-Budget der ÖBB-Abfragen ausgerichtet:
  `failure_threshold=10`, `recovery_timeout=3600.0` (1 Stunde).
  Im Normalbetrieb produziert die Pipeline 4 Calls/h
  (Cron `*/30` × 2 Richtungen) — komfortabel unter der Schwelle;
  im Fehlermodus deckelt der Breaker zusätzlich auf 10 Versuche/h.
* **Feat**: S-Bahn Stammstrecke Monitoring. Neuer Workflow
  `.github/workflows/update-stammstrecke-status.yml` (Cron `*/30 * * * *`)
  ruft via `pyhafas` mit `OEBBProfile` direkte S-Bahn-Verbindungen
  Wien Floridsdorf (8100518) ↔ Wien Meidling (8100514) ab
  (`max_changes=0`) und schreibt schema-konforme Meldungen in
  `cache/stammstrecke/events.json`. Schreibt atomar via
  `atomic_write` und ist mit dem bestehenden Feed-Build über
  `read_cache_stammstrecke()` (Provider-Flag `STAMMSTRECKE_ENABLE`)
  integriert. Dokumentiert in `docs/reference/oebb_provider_logic.md`.
  Tests mocken `pyhafas` vollständig
  (`tests/scripts/test_update_stammstrecke_status.py`).
* **Security**: Der VOR-Tagesquota-Zähler wird jetzt sowohl in
  `load_request_count` als auch in `save_request_count` (dem
  Disk-Re-Read unter Lock) nach unten auf 0 begrenzt. Vor dem Fix konnte
  eine manipulierte `data/vor_request_count.json` mit
  `{"date": "<today>", "requests": -1000}` die Laufzeit-Quota-Prüfung
  stillschweigend umgehen (`todays_count >= MAX_REQUESTS_PER_DAY` ist für
  jeden negativen Zählerstand False) und wäre durch das nächste Speichern
  fortgeschrieben worden. Defense-in-Depth gegen kompromittierte
  CI-Runner und Korruption durch partielles Flushen.
* **Security**: Der Secret-Scanner erkennt jetzt vier zusätzliche
  Aussteller-Taxonomien, die der Entropie-Fallback verfehlt: JSON Web
  Tokens (`eyJ<base64url>.<base64url>.<base64url>` — drei
  punktgetrennte Segmente umgehen das `[A-Za-z0-9+/=_-]`-Alphabet),
  Hugging Face Access Tokens (`hf_<32+>`), DigitalOcean PATs
  (`dop_v1_<64 hex>`) und OAuth Refresh Tokens (`doo_v1_<64 hex>`)
  sowie GitLab Pipeline Trigger Tokens (`glptt-<40>`). Jeder Fund meldet
  jetzt den ausstellerspezifischen Grund statt eines generischen
  High-Entropy-Treffers, was Triage und Revocation beschleunigt.

## [2026-05-05]
* **Data**: Wien-Stadtgrenzen-Polygon ersetzt — neu: offizielle
  `LANDESGRENZEOGD`-Quelle der MA 41 – Stadtvermessung (5.637 Vertices,
  EPSG:4326, CC BY 4.0). Vorher: hand-kuratiertes 31-Vertex-Polygon
  (PR #1190), davor 8-Vertex-Konvex-Hülle (PR #1189). Genauigkeit
  ~200 m → ~1–2 m.
* **Data**: 9 ÖBB-Stationskoordinaten gegen offizielle VOR-Werte
  korrigiert (Aspern Nord 1.160 m, Gersthof 1.694 m, Jedlersdorf 1.219 m,
  Handelskai 543 m, Rennweg 522 m, Breitensee 491 m, Floridsdorf 293 m,
  Kaiserebersdorf 319 m, Mitte-Landstraße 161 m, Liesing 359 m). PR #1188.
* **Data**: Kanonische Namen vereinheitlicht — `Hbf`/`Bf`-Abkürzungen
  durch ausgeschriebene Vollformen ersetzt (Wien Hauptbahnhof, Wien
  Westbahnhof, Wien Franz-Josefs-Bahnhof, Wiener Neustadt Hauptbahnhof,
  St. Pölten Hauptbahnhof, München Hauptbahnhof). Abkürzungen bleiben als
  Aliase erhalten. PR #1188.
* **Data**: Rennweg-Doublette aufgelöst — irreführende Bahnhof-Aliase
  aus dem Google-Places-U3-Eintrag entfernt. PR #1188.
* **Fix**: `_normalize_token` Umlaut-Faltung wird nur ab Token-Länge ≥ 4
  angewendet. Damit bleiben kurze ÖBB-Stellencodes wie `Sue` (Wien
  Süßenbrunn) und `Su` (Stockerau) distinkt im Lookup. PR #1189.
* **Fix**: source-Feld-Format in stations.json vereinheitlicht
  (Komma-getrennt, kein Whitespace); `stations.py`-Tie-Break nutzt
  Token-Set statt String-Equality, sodass Drift toleriert wird. PR #1188.
* **Feat**: NamingIssue-Validator-Kategorie hinzugefügt — prüft
  kanonische Namens-Eindeutigkeit und no-space-Source-Format. PR #1188.
* **Feat**: WL-OGD-Auto-Download in `update_wl_stations.py` —
  haltestellen/haltepunkte werden vor dem Merge live von
  `data.wien.gv.at` geladen, mit graceful Fallback auf lokale Dateien.
  Schließt die `wl_diva`-Lücke beim monatlichen CI-Lauf. PR #1189.
* **Feat**: JSON Schema für `data/stations.json` unter
  `docs/schema/stations.schema.json` plus Pin-Test
  `tests/test_stations_schema.py`.
* **Feat**: `docs/stations_validation_report.md` wird im monatlichen
  `update-stations.yml`-Lauf automatisch regeneriert; veraltete
  Archiv-Kopie entfernt.
* **Docs**: README-Stationsverzeichnis-Abschnitt vollständig überarbeitet
  (alle Felder, alle Quellen mit Lizenzen + Pflicht-Attribution, neue
  CLI-Flags, NamingIssue-Validator).
* **Docs**: Audit-Bericht-Reihe unter
  `docs/archive/audits/stations_data_audit_2026-05-05*.md` mit
  zentralem Index.

## [2026-02-02]
* `Fix`: VOR API auf `departureBoard` umgestellt und authentifizierte Requests repariert.
* `Security`: Rate-Limit-Sperre (max 100 Req/Tag) implementiert.
* `Data`: Stations-IDs auf HAFAS-Format aktualisiert.
* **Feat**: Verbessertes Deep-Parsing für Störungsmeldungen in Abfahrtsdaten.

## Quelle: PDF-Handbuch

- 2026-01-14 – Feed-Deduplizierungslogik optimiert, um VOR-Provider-Events (API) gegenüber ÖBB-Provider-Events (Scraper) zu priorisieren. Konflikte werden jetzt aufgelöst, indem das VOR-Event als Master-Record beibehalten und eindeutige Beschreibungsdetails aus dem ÖBB-Event eingemergt werden. Das sichert höhere Datenqualität und Stabilität.
- 2025-08-11 – Line Info Service ergänzt. (Kapitel 19)
- 2025-07-02 – Aktualisierung 5.9.2 zu Informationstexten bei Störungen.
- 2025-05-22 – Neuer Parameter `includeDrt` im Trip-Service.
- 2025-02-11 – Überarbeitung der Handbuchstruktur.
- 2024-12-10 – Kapitel 13.2 und 14.2 zu Scrolling in DepartureBoard und ArrivalBoard erweitert.
- 2024-11-27 – Kapitel 5 um neue Inhalte (5.4, 5.5, 5.11, 5.13, 5.16) und Meta-Parameter in `location.name` ergänzt.

Weitere Einträge und Detailbeschreibungen finden sich in der Änderungshistorie des PDFs (Kapitel 1.1).
