/* Wien ÖPNV – Live-Dashboard
 *
 * Vanilla JS, kein Build, keine Drittabhängigkeiten. Lädt:
 *  - feed.xml (same-origin)
 *  - data/stats/<datei>_<jahr>.csv via raw.githubusercontent.com
 * und rendert alles im Browser. Alle Fremddaten werden ausschließlich
 * über textContent in den DOM eingefügt – keine innerHTML-Pfade für
 * Daten aus Feed oder CSV. URLs werden vor der Verwendung als href
 * mit dem WHATWG-URL-Parser geprüft.
 */
"use strict";

(() => {
  const REPO = "Origamihase/wien-oepnv";
  const RAW_BASE = `https://raw.githubusercontent.com/${REPO}/main/data/stats`;
  const FEED_URL_DE = "feed.xml";
  const FEED_URL_EN = "feed.en.xml";
  const REFRESH_MS = 5 * 60 * 1000; // 5 Minuten
  const LANG_STORAGE_KEY = "wienoepnv:lang";

  // ----- Wetter (Wien) ------------------------------------------------
  // Open-Meteo GeoSphere-Austria-API (AROME-Modell der GeoSphere Austria,
  // vormals ZAMG). Wird im Browser des Besuchers abgefragt — der
  // CSP-``connect-src`` lässt nur ``api.open-meteo.com`` zu — und
  // gemeinsam mit den Verkehrsdaten in ``loadAll()`` aktualisiert.
  // Die Doku-Seite des Modells führt ``hourly``-Variablen (keinen
  // ``current``-Block), daher holen wir die Stundenreihe und wählen
  // clientseitig den Wert der aktuellen Stunde (``timeformat=unixtime`` →
  // zeitzonensichere Auswahl). Abfrage-Koordinaten: Wien Hauptbahnhof aus
  // dem Stationsverzeichnis (data/stations.json — Eintrag
  // "Wien Hauptbahnhof", bst_id 900100 / eva_nr 8103000).
  const WEATHER_URL = "https://api.open-meteo.com/v1/geosphere_arome_austria";
  const WEATHER_LAT = "48.186116";
  const WEATHER_LON = "16.374399";

  // ----- Lokalisierung (Zero-Tracker) ---------------------------------
  // Statisches Wörterbuch: das Frontend-UI hat wenig Text, deshalb wird
  // hier KEIN externes Fetch benötigt. Schlüssel decken sich mit
  // ``data-i18n``-Attributen in ``site.html``. Werte für ``de`` werden
  // einmal beim Init aus dem statischen DOM gelesen und gecached, damit
  // ein Wechsel ``EN -> DE`` ohne Reload sauber zurücksetzt.
  const I18N_EN = {
    // Document <head>
    "doc-title": "Vienna Public Transport – Live Dashboard | Disruptions, Trunk Line & Statistics",
    "meta-description":
      "Live dashboard for Wiener Linien, ÖBB and VOR: current disruptions from the RSS " +
      "feed, yearly statistics of disruption reports plus delay and cancellation data " +
      "for the S-Bahn trunk line. Proper names (stations, operators) are kept in German.",
    // Header / navigation
    "skip-link": "Skip to content",
    "brand-aria": "Wien ÖPNV Live Dashboard – home",
    "brand-sub": "Live dashboard",
    "nav-main": "Main navigation",
    "nav-feed": "Disruptions",
    "nav-stoerungen": "Disruption statistics",
    "nav-stammstrecke": "Trunk line",
    "nav-ausfaelle": "Cancellations",
    "lang-switch": "Choose language",
    "lang-de": "Deutsch",
    "lang-en": "English",
    // Hero
    "hero-eyebrow": "Real-time · Open data · Vienna & eastern Austria",
    "hero-title": "Disruptions, delays & cancellations at a glance",
    "hero-lead-html":
      "Consolidated transit information from <strong>Wiener Linien</strong>, " +
      "<strong>ÖBB</strong> and <strong>VOR/VAO</strong> – live from the " +
      "RSS feed, augmented with the latest yearly statistics for the " +
      "S-Bahn trunk line.",
    "status-loading": "Loading data …",
    "status-ok": "Live feed updated.",
    "status-error": "The live feed could not be loaded.",
    "btn-refresh": "Refresh",
    "hero-meta-stamp": "Last updated:",
    "hero-meta-rss": "Feed (RSS)",
    "hero-meta-source": "Source code",
    // Feed section
    "feed-title": "Current disruptions",
    "feed-sub-stammstrecke-html":
      "Current S-Bahn observations at Wien Hauptbahnhof " +
      "– <code>data/stats/stammstrecke_<span data-year-label>–</span>.csv</code>.",
    "feed-sub-live-html":
      "Live from <a href=\"feed.en.xml\" type=\"application/rss+xml\" " +
      "data-i18n-href=\"feed-href\" data-href-de=\"feed.xml\" " +
      "data-href-en=\"feed.en.xml\"><code>feed.en.xml</code></a> " +
      "· consolidated from official sources " +
      "· <span id=\"feed-count\" class=\"badge\" aria-live=\"polite\">–</span>",
    "live-tile-label": "Avg. trunk-line delay",
    "live-tile-window": "last 60 min · source VOR / VAO",
    "live-tile-cta": " – open detail view",
    "filters-aria": "Filter disruptions by source",
    "filter-all": "All",
    "filter-wl": "Wiener Linien",
    "filter-oebb": "ÖBB",
    "filter-baustellen": "Construction",
    "filter-other": "Other",
    "feed-empty": "No disruptions for the selected filter.",
    "feed-error-prefix": "Feed could not be loaded:",
    "stoerungen-error-prefix": "Disruption statistics unavailable:",
    "stammstrecke-error-prefix": "Trunk-line statistics unavailable:",
    "ausfaelle-error-prefix": "Cancellation statistics unavailable:",
    // Disruption-statistics section
    "stoerungen-title": "Disruption statistics",
    "stoerungen-sub-html":
      "Yearly ledger from " +
      "<code>data/stats/stoerungen_<span data-year-label>–</span>.csv</code> " +
      "– one row per newly recognised event identity.",
    "aria-stoerungen-kpis": "Disruption key figures",
    "card-by-provider": "Distribution by source",
    "aria-stoerungen-providers": "Disruptions by source",
    "card-by-weekday": "By weekday",
    "aria-stoerungen-weekday": "Disruptions by weekday",
    "card-by-hour": "By hour of day",
    "aria-stoerungen-hour": "Disruptions by hour",
    // Stammstrecke-delays section
    "stammstrecke-title": "Trunk line – delays",
    "stammstrecke-sub-html":
      "S-Bahn observations at Wien Hauptbahnhof – " +
      "<code>data/stats/stammstrecke_<span data-year-label>–</span>.csv</code>.",
    "aria-stammstrecke-kpis": "Trunk line key figures",
    "card-stammstrecke-hour": "Avg. delay by hour of day",
    "aria-stammstrecke-hour": "Avg. delay by hour",
    "card-stammstrecke-weekday": "Avg. delay by weekday",
    "aria-stammstrecke-weekday": "Avg. delay by weekday",
    "card-stammstrecke-direction": "Observations by direction",
    "aria-stammstrecke-direction": "Observations by direction",
    // Stammstrecke-cancellations section
    "ausfaelle-title": "Trunk line – cancellations",
    "ausfaelle-sub-html":
      "Cancelled S-Bahn services – deduplicated ledger " +
      "<code>data/stats/ausfaelle_<span data-year-label>–</span>.csv</code>.",
    "aria-ausfaelle-kpis": "Cancellation key figures",
    "card-by-line": "By line",
    "aria-ausfaelle-line": "Cancellations by line",
    "card-by-direction": "By direction",
    "aria-ausfaelle-direction": "Cancellations by direction",
    "aria-ausfaelle-weekday": "Cancellations by weekday",
    "aria-ausfaelle-hour": "Cancellations by hour",
    // Footer
    "footer-sources-heading": "Data sources",
    "footer-source-wl-html":
      "<strong>Wiener Linien</strong> – real-time disruption reports (OGD)",
    "footer-source-oebb-html":
      "<strong>ÖBB</strong> – nationwide rail alerts, filtered to Vienna",
    "footer-source-vor-html":
      "<strong>VOR/VAO</strong> – trunk-line observations at Wien Hbf",
    "footer-source-stadt-html":
      "<strong>City of Vienna (OGD)</strong> – construction works with district &amp; period",
    "footer-about-heading": "About the dashboard",
    "footer-about-html":
      "Open-source under the MIT licence. Feed update cadence: roughly every 30&nbsp;minutes. " +
      "This dashboard fetches feed and statistics CSVs <em>directly in the browser</em> – " +
      "no trackers, no cookies, no third-party scripts. Proper names (stations, operators) " +
      "are kept in German on purpose; only the surrounding text is translated.",
    "footer-link-repo": "Repository on GitHub",
    "footer-link-schema": "CSV schema",
    "footer-link-rss": "RSS feed",
    "footer-link-home": "Project home",
  };

  // Status-Strings können sich zur Laufzeit ändern und sind kein
  // ``data-i18n``-Knoten — sie werden via ``setStatus`` gesetzt. Wir
  // halten daher eine ergänzende DE-Übersicht für die JS-internen Texte.
  const STATUS_TEXT = {
    de: {
      "status-loading": "Daten werden geladen …",
      "status-ok": "Live-Feed aktualisiert.",
      "status-error": "Live-Feed konnte nicht geladen werden.",
      "feed-error-prefix": "Feed konnte nicht geladen werden:",
      "stoerungen-error-prefix": "Störungs-Statistik nicht verfügbar:",
      "stammstrecke-error-prefix": "Stammstrecke-Statistik nicht verfügbar:",
      "ausfaelle-error-prefix": "Ausfall-Statistik nicht verfügbar:",
    },
    en: {
      "status-loading": I18N_EN["status-loading"],
      "status-ok": I18N_EN["status-ok"],
      "status-error": I18N_EN["status-error"],
      "feed-error-prefix": I18N_EN["feed-error-prefix"],
      "stoerungen-error-prefix": I18N_EN["stoerungen-error-prefix"],
      "stammstrecke-error-prefix": I18N_EN["stammstrecke-error-prefix"],
      "ausfaelle-error-prefix": I18N_EN["ausfaelle-error-prefix"],
    },
  };

  function readStoredLang() {
    try {
      const stored = localStorage.getItem(LANG_STORAGE_KEY);
      if (stored === "en" || stored === "de") return stored;
    } catch {
      // localStorage may be unavailable (private mode, disabled) — ignore.
    }
    return "de";
  }

  let currentLang = readStoredLang();

  function writeStoredLang(lang) {
    try {
      localStorage.setItem(LANG_STORAGE_KEY, lang);
    } catch {
      // ignore — language stays in-memory only
    }
  }

  function tr(key) {
    if (currentLang === "en" && Object.prototype.hasOwnProperty.call(I18N_EN, key)) {
      return I18N_EN[key];
    }
    return null;
  }

  function statusText(key) {
    const dict = STATUS_TEXT[currentLang] || STATUS_TEXT.de;
    return dict[key] || STATUS_TEXT.de[key] || "";
  }

  function currentFeedUrl() {
    return currentLang === "en" ? FEED_URL_EN : FEED_URL_DE;
  }

  function localeTag() {
    return currentLang === "en" ? "en-GB" : "de-AT";
  }

  let dtfFull = buildDtf({ dateStyle: "medium", timeStyle: "short" });
  let dtfTime = buildDtf({ timeStyle: "short" });
  let dtfDate = buildDtf({ dateStyle: "medium" });
  let rtf = new Intl.RelativeTimeFormat(localeTag(), { numeric: "auto" });
  let nfInt = new Intl.NumberFormat(localeTag());
  let nf1 = new Intl.NumberFormat(localeTag(), {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

  function buildDtf(opts) {
    return new Intl.DateTimeFormat(localeTag(), {
      timeZone: "Europe/Vienna",
      ...opts,
    });
  }

  function rebuildIntl() {
    dtfFull = buildDtf({ dateStyle: "medium", timeStyle: "short" });
    dtfTime = buildDtf({ timeStyle: "short" });
    dtfDate = buildDtf({ dateStyle: "medium" });
    rtf = new Intl.RelativeTimeFormat(localeTag(), { numeric: "auto" });
    nfInt = new Intl.NumberFormat(localeTag());
    nf1 = new Intl.NumberFormat(localeTag(), {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
  }

  const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  const WEEKDAY_LONG_DE = {
    Mo: "Montag", Di: "Dienstag", Mi: "Mittwoch", Do: "Donnerstag",
    Fr: "Freitag", Sa: "Samstag", So: "Sonntag",
  };
  const WEEKDAY_LONG_EN = {
    Mo: "Monday", Di: "Tuesday", Mi: "Wednesday", Do: "Thursday",
    Fr: "Friday", Sa: "Saturday", So: "Sunday",
  };
  function weekdayLong(key) {
    const dict = currentLang === "en" ? WEEKDAY_LONG_EN : WEEKDAY_LONG_DE;
    return dict[key] || key;
  }
  const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));

  // KPI / chart labels rendered dynamically (NOT covered by ``data-i18n``)
  // because they are emitted by ``renderKpis`` / ``renderBars`` at runtime.
  const CHART_TEXT_DE = {
    "kpi-stoerungen-total": "Erfasste Störungen",
    "kpi-top-provider": "Häufigste Quelle",
    "kpi-peak-hour": "Spitzenstunde",
    "kpi-top-weekday": "Stärkster Tag",
    "kpi-observations": "Beobachtungen",
    "kpi-avg-delay": "Ø Verspätung",
    "kpi-max-delay": "Max. Verspätung",
    "kpi-heavy-delays": "Schwerverspätungen",
    "kpi-cancellations": "Ausfälle",
    "kpi-top-line": "Häufigste Linie",
    "sub-year": "Jahr",
    "sub-meldungen": "Meldungen",
    "sub-ausfaelle": "Ausfälle",
    "sub-no-data": "keine Daten",
    "sub-all-observations": "alle Beobachtungen",
    "sub-tick-value": "Tickwert",
    "sub-over-9": "≥ 9 Minuten",
    "tile-na": "N/A",
    "tile-em-dash": "–",
  };
  const CHART_TEXT_EN = {
    "kpi-stoerungen-total": "Recorded disruptions",
    "kpi-top-provider": "Most frequent source",
    "kpi-peak-hour": "Peak hour",
    "kpi-top-weekday": "Busiest weekday",
    "kpi-observations": "Observations",
    "kpi-avg-delay": "Avg. delay",
    "kpi-max-delay": "Max. delay",
    "kpi-heavy-delays": "Severe delays",
    "kpi-cancellations": "Cancellations",
    "kpi-top-line": "Most affected line",
    "sub-year": "Year",
    "sub-meldungen": "reports",
    "sub-ausfaelle": "cancellations",
    "sub-no-data": "no data",
    "sub-all-observations": "all observations",
    "sub-tick-value": "tick value",
    "sub-over-9": "≥ 9 minutes",
    "tile-na": "N/A",
    "tile-em-dash": "–",
  };
  function ct(key) {
    const dict = currentLang === "en" ? CHART_TEXT_EN : CHART_TEXT_DE;
    return dict[key] || CHART_TEXT_DE[key] || key;
  }

  // Weather-widget strings rendered dynamically (icon tooltip + aria
  // label), analogous to CHART_TEXT — NOT covered by ``data-i18n`` because
  // the header widget's text is built at runtime by ``renderWeather``. The
  // condition keys mirror the buckets returned by ``weatherCondition``.
  const WEATHER_TEXT_DE = {
    aria: "Aktuelles Wetter in Wien",
    clear: "klar",
    partly: "teils bewölkt",
    cloudy: "bewölkt",
    fog: "Nebel",
    rain: "Regen",
    snow: "Schnee",
    thunder: "Gewitter",
  };
  const WEATHER_TEXT_EN = {
    aria: "Current weather in Vienna",
    clear: "clear",
    partly: "partly cloudy",
    cloudy: "cloudy",
    fog: "fog",
    rain: "rain",
    snow: "snow",
    thunder: "thunderstorm",
  };
  function wt(key) {
    const dict = currentLang === "en" ? WEATHER_TEXT_EN : WEATHER_TEXT_DE;
    return dict[key] || WEATHER_TEXT_DE[key] || key;
  }

  // ----- DOM helper -----

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function el(tag, opts = {}, ...children) {
    const node = document.createElement(tag);
    if (opts.class) node.className = opts.class;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.attrs) {
      for (const [k, v] of Object.entries(opts.attrs)) {
        if (v == null || v === false) continue;
        node.setAttribute(k, v === true ? "" : String(v));
      }
    }
    if (opts.dataset) {
      for (const [k, v] of Object.entries(opts.dataset)) {
        if (v == null) continue;
        node.dataset[k] = String(v);
      }
    }
    for (const child of children) {
      if (child == null) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function clear(node) {
    if (!node) return;
    // ``replaceChildren()`` performs the same removal in a single,
    // engine-optimised call (chrome ≥ 86, firefox ≥ 78, safari ≥ 14 —
    // all roughly 2020-vintage and therefore comfortably below the
    // baseline of any browser still receiving security updates). The
    // manual ``while``-loop fallback below is kept as a defence-in-depth
    // safety net for environments without ``replaceChildren``: removing
    // it would be silently fatal on the few platforms still missing it,
    // and the branch costs nothing on modern engines.
    if (typeof node.replaceChildren === "function") {
      node.replaceChildren();
      return;
    }
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function safeHttpsUrl(raw) {
    if (typeof raw !== "string" || !raw) return null;
    try {
      const u = new URL(raw, location.href);
      if (u.protocol !== "https:" && u.protocol !== "http:") return null;
      return u.toString();
    } catch {
      return null;
    }
  }

  function setStatus(state, key) {
    const dot = $(".status__dot");
    const text = $("#status-text");
    if (dot) dot.dataset.status = state;
    if (text) {
      text.textContent = statusText(key);
      text.dataset.statusKey = key;
    }
  }

  function setLastUpdate(date) {
    const t = $("#last-update");
    if (!t) return;
    const iso = date.toISOString();
    t.setAttribute("datetime", iso);
    const suffix = currentLang === "en" ? " (Europe/Vienna)" : " Uhr (Europe/Vienna)";
    t.textContent = dtfFull.format(date) + suffix;
  }

  function showError(elId, prefixKey, detail) {
    const node = document.getElementById(elId);
    if (!node) return;
    node.hidden = false;
    node.textContent = `${statusText(prefixKey)} ${detail}`;
    node.dataset.errorPrefixKey = prefixKey;
    node.dataset.errorDetail = detail;
  }

  function hideError(elId) {
    const node = document.getElementById(elId);
    if (node) node.hidden = true;
  }

  // ----- Fetching -----

  async function fetchText(url, { signal } = {}) {
    // ``cache: "no-cache"`` forces the browser to revalidate the cached
    // entry against the origin (``If-None-Match`` / ``If-Modified-Since``)
    // on every refresh. When the upstream payload is unchanged the
    // server replies ``304 Not Modified`` and the browser serves the
    // cached body — same freshness guarantee as the previous
    // ``no-store`` + per-minute cache-buster combo, but without
    // redownloading the full feed/CSV each cycle. Both GitHub Pages
    // (``feed.xml``) and ``raw.githubusercontent.com`` (the CSV mirror)
    // emit strong ``ETag`` headers, so revalidation produces real
    // bandwidth savings on the 5-minute auto-refresh tick.
    const res = await fetch(url, {
      cache: "no-cache",
      credentials: "omit",
      redirect: "follow",
      signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} – ${url}`);
    return await res.text();
  }

  async function fetchCsvForYear(name, { signal } = {}) {
    const year = new Date().getFullYear();
    for (const candidate of [year, year - 1]) {
      try {
        const text = await fetchText(`${RAW_BASE}/${name}_${candidate}.csv`, { signal });
        return { year: candidate, text };
      } catch (err) {
        if (signal && signal.aborted) throw err;
        // try previous year
      }
    }
    throw new Error(`Keine CSV-Daten für ${name} verfügbar.`);
  }

  // ----- CSV parser (RFC-4180 light) -----

  function parseCSV(text) {
    const rows = [];
    let i = 0;
    let field = "";
    let row = [];
    let inQuotes = false;
    const len = text.length;
    while (i < len) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
          inQuotes = false; i++; continue;
        }
        field += c; i++; continue;
      }
      if (c === '"') { inQuotes = true; i++; continue; }
      if (c === ",") { row.push(field); field = ""; i++; continue; }
      if (c === "\r") { i++; continue; }
      if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; i++; continue; }
      field += c; i++;
    }
    if (field !== "" || row.length > 0) { row.push(field); rows.push(row); }
    return rows;
  }

  function rowsToObjects(rows) {
    if (rows.length === 0) return [];
    const header = rows[0].map((h) => h.trim());
    const out = [];
    for (let i = 1; i < rows.length; i++) {
      const r = rows[i];
      if (r.length === 1 && r[0] === "") continue;
      const o = {};
      for (let j = 0; j < header.length; j++) {
        o[header[j]] = (r[j] ?? "").trim();
      }
      out.push(o);
    }
    return out;
  }

  // ----- XML/RSS parser -----

  const NS_EXT = "https://wien-oepnv.example/schema";
  // A single ``DOMParser`` instance is reused for every refresh. Each
  // ``parseFromString`` call creates a fresh document, so reuse is safe
  // and avoids the per-call constructor allocation on the 5-minute
  // auto-refresh cycle.
  const domParser = new DOMParser();

  function parseFeed(xmlText) {
    const doc = domParser.parseFromString(xmlText, "application/xml");
    const err = doc.querySelector("parsererror");
    if (err) throw new Error("Feed konnte nicht geparst werden");
    const channel = doc.querySelector("channel");
    if (!channel) throw new Error("Feed ohne <channel>-Element");

    const lastBuild = channelChildText(channel, "lastBuildDate");
    const items = Array.from(doc.getElementsByTagName("item")).map((it) => {
      const get = (tag) => firstChildText(it, tag);
      const ns = (tag) => firstChildTextNs(it, NS_EXT, tag);
      const item = {
        title: get("title"),
        link: get("link"),
        guid: get("guid"),
        pubDate: get("pubDate"),
        description: get("description"),
        firstSeen: ns("first_seen"),
        startsAt: ns("starts_at"),
        endsAt: ns("ends_at"),
      };
      // ``detectSource`` is regex-based and otherwise re-runs on every
      // filter change *and* twice inside ``renderFeedItem``. Caching the
      // result on the parsed item keeps the filter/render hot path
      // allocation-free.
      item.source = detectSource(item);
      return item;
    });
    return { lastBuild, items };
  }

  function channelChildText(channel, name) {
    return firstChildText(channel, name);
  }

  function firstChildText(parent, name) {
    for (let i = 0; i < parent.children.length; i++) {
      const ch = parent.children[i];
      if (ch.localName === name && (!ch.namespaceURI || !ch.namespaceURI.startsWith("http"))) {
        return ch.textContent ? ch.textContent.trim() : "";
      }
      if (ch.tagName === name) {
        return ch.textContent ? ch.textContent.trim() : "";
      }
    }
    return "";
  }

  function firstChildTextNs(parent, ns, name) {
    const list = parent.getElementsByTagNameNS(ns, name);
    if (list.length === 0) return "";
    return list[0].textContent ? list[0].textContent.trim() : "";
  }

  // ----- Feed rendering -----

  function detectSource(item) {
    const link = (item.link || "").toLowerCase();
    const haystack = `${item.link} ${item.title}`.toLowerCase();
    if (/(wienerlinien|wiener\s*linien|wl-disp|ogd_realtime)/.test(haystack)) return "wienerlinien";
    if (/(oebb|öbb|scotty)/.test(haystack)) return "oebb";
    // City-of-Vienna construction dataset → fixed data.gv.at link. Keyed
    // on the link (not the combined haystack) so a WL/ÖBB report whose
    // title merely mentions "Baustelle" is not misclassified — those are
    // already returned above.
    if (/data\.gv\.at\/[^ ]*baustellen/.test(link)) return "baustellen";
    if (/(vor\.at|verkehrsverbund|vao\.|anachb)/.test(haystack)) return "vor";
    return "other";
  }

  function sourceLabel(key) {
    if (key === "wienerlinien") return "Wiener Linien";
    if (key === "oebb") return "ÖBB";
    if (key === "baustellen") return currentLang === "en" ? "Construction" : "Baustellen";
    if (key === "vor") return "VOR / VAO";
    return currentLang === "en" ? "Other" : "Andere";
  }

  let feedState = { items: [], filter: "all" };

  function renderFeed() {
    const list = $("#feed-list");
    const empty = $("#feed-empty");
    const countBadge = $("#feed-count");
    if (!list) return;

    list.setAttribute("aria-busy", "false");
    clear(list);

    // "Andere" is a catch-all for anything that is not Wiener Linien,
    // ÖBB or Baustellen – so a hypothetical VOR/VAO item (still labelled
    // green by detectSource) is also visible under "Andere", not just
    // under "Alle". Baustellen got its own chip in 2026-05 and is
    // therefore excluded from the catch-all; the dedicated VOR/VAO chip
    // was removed earlier because VOR data now flows only into the
    // Stammstrecken monitor. ``item.source`` is set once during feed
    // parsing and reused here, so the filter pass is a plain
    // string-compare loop.
    const filtered = feedState.filter === "all"
      ? feedState.items
      : feedState.items.filter((it) => {
          const src = it.source || detectSource(it);
          if (feedState.filter === "other") {
            return src !== "wienerlinien" && src !== "oebb" && src !== "baustellen";
          }
          return src === feedState.filter;
        });

    if (countBadge) {
      const suffix = currentLang === "en" ? "active" : "aktiv";
      countBadge.textContent = `${nfInt.format(filtered.length)} ${suffix}`;
    }

    if (filtered.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    const frag = document.createDocumentFragment();
    for (const it of filtered) {
      frag.append(renderFeedItem(it));
    }
    list.append(frag);
  }

  function renderFeedItem(item) {
    // ``item.source`` is the cached classification set at parse time;
    // fall back to a live detection so the function still works when
    // called with a hand-crafted item (e.g. from tests).
    const src = item.source || detectSource(item);
    const li = el("li", { class: "feed-item", attrs: { "data-source": src } });
    li.append(el("span", {
      class: "feed-item__source",
      attrs: { "data-source": src, title: sourceLabel(src) },
      text: sourceLabel(src),
    }));

    const body = el("div", { class: "feed-item__body" });

    const titleNode = el("h3", { class: "feed-item__title" });
    const link = safeHttpsUrl(item.link);
    const titleText = item.title || (currentLang === "en" ? "(untitled)" : "(ohne Titel)");
    if (link) {
      const a = el("a", {
        text: titleText,
        attrs: { href: link, rel: "external noopener noreferrer", target: "_blank" },
      });
      titleNode.append(a);
    } else {
      titleNode.textContent = titleText;
    }
    body.append(titleNode);

    if (item.description) {
      body.append(el("p", { class: "feed-item__desc", text: collapseWhitespace(item.description) }));
    }

    const meta = el("div", { class: "feed-item__meta" });
    const labels = currentLang === "en"
      ? { begin: "Begin: ", until: "Until: ", firstSeen: "First seen: " }
      : { begin: "Beginn: ", until: "Bis: ", firstSeen: "Erstmals: " };
    const pub = parseRfc2822(item.pubDate);
    if (pub) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: labels.begin }));
      const t = el("time", { attrs: { datetime: pub.toISOString() }, text: dtfFull.format(pub) });
      d.append(t);
      meta.append(d);
    }
    const ends = parseRfc2822(item.endsAt);
    if (ends) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: labels.until }));
      d.append(el("time", { attrs: { datetime: ends.toISOString() }, text: dtfFull.format(ends) }));
      meta.append(d);
    }
    const seen = parseRfc2822(item.firstSeen);
    if (seen) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: labels.firstSeen }));
      d.append(document.createTextNode(humanRelative(seen)));
      meta.append(d);
    }
    if (meta.childElementCount > 0) body.append(meta);

    li.append(body);
    return li;
  }

  function collapseWhitespace(s) {
    return s.replace(/\s+/g, " ").trim();
  }

  function parseRfc2822(s) {
    if (!s) return null;
    const t = Date.parse(s);
    if (Number.isNaN(t)) return null;
    return new Date(t);
  }

  function humanRelative(date) {
    const diffMs = date.getTime() - Date.now();
    const absMin = Math.abs(diffMs) / 60000;
    const sign = diffMs < 0 ? -1 : 1;
    if (absMin < 1) return currentLang === "en" ? "just now" : "gerade eben";
    if (absMin < 60) return rtf.format(sign * Math.round(absMin), "minute");
    const hours = absMin / 60;
    if (hours < 24) return rtf.format(sign * Math.round(hours), "hour");
    const days = hours / 24;
    if (days < 30) return rtf.format(sign * Math.round(days), "day");
    return dtfDate.format(date);
  }

  // ----- Statistics (stoerungen) -----

  // Display label for the source-distribution chart + KPI. The yearly
  // ledger stores the descriptive "Stadt Wien – Baustellen" in its
  // ``provider`` column (the feed pipeline keys glossary/entity-masking
  // off that exact string), so we map it — display only, data untouched —
  // to the short, localized name via the same ``sourceLabel`` helper the
  // feed uses (DE "Baustellen" / EN "Construction"). loadAll() re-renders
  // the stats on a language switch, so the label follows currentLang. The
  // ``data-key`` rendered from this label drives the yellow
  // ``--c-baustellen`` bar fill in site.css (both strings are matched).
  const providerLabel = (p) => (/Baustellen/.test(p) ? sourceLabel("baustellen") : p);

  function renderStoerungenStats(year, rows) {
    setYearLabels(year);

    const total = rows.length;
    const byProvider = countBy(rows, (r) => r.provider || "Unbekannt");
    const byWeekday = countByKey(rows, (r) => r.weekday, WEEKDAYS);
    const byHour = countByKey(rows, (r) => r.hour, HOURS);

    const topProvider = topEntry(byProvider);
    const peakHour = topEntry(byHour);
    const peakWeekday = topEntry(byWeekday);

    renderKpis("#stoerungen-kpis", [
      { label: ct("kpi-stoerungen-total"), value: nfInt.format(total), sub: `${ct("sub-year")} ${year}` },
      { label: ct("kpi-top-provider"),
        value: topProvider ? providerLabel(topProvider[0]) : ct("tile-em-dash"),
        sub: topProvider ? `${nfInt.format(topProvider[1])} ${ct("sub-meldungen")}` : "" },
      { label: ct("kpi-peak-hour"),
        value: peakHour ? `${peakHour[0]}:00` : ct("tile-em-dash"),
        sub: peakHour ? `${nfInt.format(peakHour[1])} ${ct("sub-meldungen")}` : "" },
      { label: ct("kpi-top-weekday"),
        value: peakWeekday ? weekdayLong(peakWeekday[0]) : ct("tile-em-dash"),
        sub: peakWeekday ? `${nfInt.format(peakWeekday[1])} ${ct("sub-meldungen")}` : "" },
    ]);

    renderBars("#stoerungen-providers",
      sortedEntries(byProvider).slice(0, 8).map(([p, v]) => [providerLabel(p), v]),
      { variant: "provider", formatValue: (v) => nfInt.format(v) });

    renderBars("#stoerungen-weekday",
      WEEKDAYS.map((d) => [weekdayLong(d), byWeekday[d] || 0]),
      { unit: "", formatValue: (v) => nfInt.format(v) });

    renderBars("#stoerungen-hour",
      HOURS.map((h) => [`${h}:00`, byHour[h] || 0]),
      { unit: "", formatValue: (v) => nfInt.format(v) });
  }

  function renderStammstreckeLiveTile(rows) {
    // Mirrors ``render_readme_stammstrecke_live_block`` in
    // ``scripts/generate_markdown_stats.py``: arithmetic mean of
    // ``delay_minutes`` over the rolling 60-minute window. We compute it
    // client-side instead of fetching a pre-rendered fragment so the tile
    // stays accurate between the workflow's 30-minute README refreshes.
    const node = $("#stammstrecke-live-avg");
    if (!node) return;
    const cutoff = Date.now() - 60 * 60 * 1000;
    let sum = 0;
    let count = 0;
    for (const r of rows) {
      const ts = Date.parse(r.timestamp);
      if (!Number.isFinite(ts) || ts < cutoff) continue;
      const d = parseFloat(r.delay_minutes);
      if (!Number.isFinite(d)) continue;
      sum += d;
      count += 1;
    }
    node.textContent = count === 0 ? ct("tile-na") : `${nf1.format(sum / count)} min`;
  }

  function resetStammstreckeLiveTile(text) {
    const node = $("#stammstrecke-live-avg");
    if (node) node.textContent = text;
  }

  function renderStammstreckeStats(year, rows) {
    setYearLabels(year);

    renderStammstreckeLiveTile(rows);

    const valid = rows
      .map((r) => ({ ...r, delay: parseFloat(r.delay_minutes) }))
      .filter((r) => Number.isFinite(r.delay));
    const total = valid.length;
    const avg = total ? valid.reduce((a, r) => a + r.delay, 0) / total : 0;
    const max = total ? valid.reduce((m, r) => (r.delay > m ? r.delay : m), 0) : 0;
    const over9 = valid.filter((r) => r.delay >= 9).length;
    const byDirection = countBy(valid, (r) => r.direction || "unbekannt");

    const avgByWeekday = averageBy(valid, (r) => r.weekday, (r) => r.delay, WEEKDAYS);
    const avgByHour = averageBy(valid, (r) => r.hour, (r) => r.delay, HOURS);

    renderKpis("#stammstrecke-kpis", [
      { label: ct("kpi-observations"), value: nfInt.format(total), sub: `${ct("sub-year")} ${year}` },
      { label: ct("kpi-avg-delay"), value: `${nf1.format(avg)} min`, sub: ct("sub-all-observations") },
      { label: ct("kpi-max-delay"), value: `${nf1.format(max)} min`, sub: ct("sub-tick-value") },
      { label: ct("kpi-heavy-delays"), value: nfInt.format(over9), sub: ct("sub-over-9") },
    ]);

    renderBars("#stammstrecke-hour",
      HOURS.map((h) => [`${h}:00`, avgByHour[h] || 0]),
      { unit: " min", variant: "delay", formatValue: (v) => nf1.format(v) });

    renderBars("#stammstrecke-weekday",
      WEEKDAYS.map((d) => [weekdayLong(d), avgByWeekday[d] || 0]),
      { unit: " min", variant: "delay", formatValue: (v) => nf1.format(v) });

    renderBars("#stammstrecke-direction",
      sortedEntries(byDirection),
      { unit: "", formatValue: (v) => nfInt.format(v) });
  }

  function renderAusfaelleStats(year, rows) {
    setYearLabels(year);
    const total = rows.length;
    const byLine = countBy(rows, (r) => r.line || "unbekannt");
    const byDirection = countBy(rows, (r) => r.direction || "unbekannt");
    const byHour = countByKey(rows, (r) => r.hour, HOURS);
    const byWeekday = countByKey(rows, (r) => r.weekday, WEEKDAYS);

    const topLine = topEntry(byLine);
    const topWeekday = topEntry(byWeekday);

    renderKpis("#ausfaelle-kpis", [
      { label: ct("kpi-cancellations"), value: nfInt.format(total), sub: `${ct("sub-year")} ${year}` },
      { label: ct("kpi-top-line"),
        value: topLine ? topLine[0] : ct("tile-em-dash"),
        sub: topLine ? `${nfInt.format(topLine[1])} ${ct("sub-ausfaelle")}` : ct("sub-no-data") },
      { label: ct("kpi-top-weekday"),
        value: topWeekday ? weekdayLong(topWeekday[0]) : ct("tile-em-dash"),
        sub: topWeekday ? `${nfInt.format(topWeekday[1])} ${ct("sub-ausfaelle")}` : ct("sub-no-data") },
    ]);

    renderBars("#ausfaelle-line",
      sortedEntries(byLine).slice(0, 10),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-direction",
      sortedEntries(byDirection),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-weekday",
      WEEKDAYS.map((d) => [weekdayLong(d), byWeekday[d] || 0]),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-hour",
      HOURS.map((h) => [`${h}:00`, byHour[h] || 0]),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });
  }

  // ----- Aggregation helpers -----

  function countBy(rows, keyFn) {
    const out = Object.create(null);
    for (const r of rows) {
      const k = keyFn(r);
      out[k] = (out[k] || 0) + 1;
    }
    return out;
  }

  function countByKey(rows, keyFn, order) {
    const out = Object.create(null);
    for (const k of order) out[k] = 0;
    for (const r of rows) {
      const k = keyFn(r);
      if (k in out) out[k] += 1;
    }
    return out;
  }

  function averageBy(rows, keyFn, valueFn, order) {
    const sum = Object.create(null);
    const cnt = Object.create(null);
    for (const k of order) { sum[k] = 0; cnt[k] = 0; }
    for (const r of rows) {
      const k = keyFn(r);
      const v = valueFn(r);
      if (!(k in sum) || !Number.isFinite(v)) continue;
      sum[k] += v;
      cnt[k] += 1;
    }
    const out = Object.create(null);
    for (const k of order) out[k] = cnt[k] ? sum[k] / cnt[k] : 0;
    return out;
  }

  function sortedEntries(obj) {
    return Object.entries(obj).sort((a, b) => b[1] - a[1]);
  }

  function topEntry(obj) {
    const entries = sortedEntries(obj);
    if (entries.length === 0 || entries[0][1] === 0) return null;
    return entries[0];
  }

  // ----- Render primitives -----

  function setYearLabels(year) {
    for (const node of $$("[data-year-label]")) {
      node.textContent = String(year);
    }
  }

  function renderKpis(selector, items) {
    const root = $(selector);
    if (!root) return;
    clear(root);
    const frag = document.createDocumentFragment();
    for (const it of items) {
      const card = el("article", { class: "kpi" });
      card.append(el("p", { class: "kpi__label", text: it.label }));
      card.append(el("p", { class: "kpi__value", text: it.value }));
      if (it.sub) card.append(el("p", { class: "kpi__sub", text: it.sub }));
      frag.append(card);
    }
    root.append(frag);
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        node.setAttribute(k, v === true ? "" : String(v));
      }
    }
    return node;
  }

  function renderBars(selector, entries, opts = {}) {
    const root = $(selector);
    if (!root) return;
    clear(root);
    if (opts.variant) root.classList.add(`bars--${opts.variant}`);
    if (entries.length === 0) {
      root.append(el("p", {
        class: "empty",
        text: currentLang === "en" ? "No data available." : "Keine Daten verfügbar.",
      }));
      return;
    }
    const max = entries.reduce((m, [, v]) => (Number.isFinite(v) && v > m ? v : m), 0);
    const fmt = opts.formatValue || ((v) => String(v));
    const unit = opts.unit || "";
    const frag = document.createDocumentFragment();
    for (const [label, value] of entries) {
      const row = el("div", { class: "bar-row", dataset: { key: label } });
      row.append(el("span", { class: "bar-row__label", text: label, attrs: { title: label } }));

      // SVG bar chart cell. Using SVG presentation attributes (width="X")
      // instead of CSS `style.width = ...` keeps us strictly within
      // `style-src 'self'` – no inline style attribute is written.
      const valueText = `${fmt(value)}${unit}`;
      const width = max > 0 && Number.isFinite(value) ? (value / max) * 100 : 0;
      const chart = svg("svg", {
        class: "bar-row__chart",
        viewBox: "0 0 100 12",
        preserveAspectRatio: "none",
        role: "progressbar",
        "aria-valuemin": "0",
        "aria-valuemax": String(max || 0),
        "aria-valuenow": String(Number.isFinite(value) ? value : 0),
        "aria-label": `${label}: ${valueText}`,
      });
      chart.append(svg("rect", { class: "bar-row__track", x: "0", y: "0", width: "100", height: "12" }));
      chart.append(svg("rect", {
        class: "bar-row__fill",
        x: "0", y: "0",
        width: width.toFixed(2),
        height: "12",
      }));
      row.append(chart);

      row.append(el("span", { class: "bar-row__value", text: valueText }));
      frag.append(row);
    }
    root.append(frag);
  }

  // ----- Weather widget -----

  // Lucide-style monochrome icons (stroke = currentColor). Each entry is a
  // list of <circle>/<path> primitives assembled by ``buildWeatherIcon``
  // via the same CSP-safe ``svg()`` helper the bar charts use — no inline
  // styles, no external image requests.
  const WEATHER_ICONS = {
    sun: {
      circles: [["12", "12", "4"]],
      paths: [
        "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41",
        "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2",
        "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41",
      ],
    },
    moon: { paths: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"] },
    "cloud-sun": {
      paths: [
        "M12 2v2", "m4.93 4.93 1.41 1.41", "M20 12h2",
        "m19.07 4.93-1.41 1.41", "M15.947 12.65a4 4 0 0 0-5.925-4.128",
        "M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z",
      ],
    },
    cloud: { paths: ["M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"] },
    "cloud-fog": {
      paths: [
        "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242",
        "M16 17H7", "M17 21H9",
      ],
    },
    "cloud-rain": {
      paths: [
        "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242",
        "M16 14v6", "M8 14v6", "M12 16v6",
      ],
    },
    "cloud-snow": {
      paths: [
        "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242",
        "M8 15h.01", "M8 19h.01", "M12 17h.01", "M12 21h.01",
        "M16 15h.01", "M16 19h.01",
      ],
    },
    "cloud-bolt": {
      paths: [
        "M6 16.326A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.973",
        "m13 12-3 5h4l-3 5",
      ],
    },
  };

  function buildWeatherIcon(name) {
    const def = WEATHER_ICONS[name] || WEATHER_ICONS.cloud;
    const node = svg("svg", {
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "2",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
      focusable: "false",
    });
    for (const [cx, cy, r] of def.circles || []) {
      node.append(svg("circle", { cx, cy, r }));
    }
    for (const d of def.paths || []) {
      node.append(svg("path", { d }));
    }
    return node;
  }

  // WMO weather interpretation code → coarse condition bucket. Mirrors the
  // table in the Open-Meteo docs (0 clear … 95+ thunderstorm); the bucket
  // drives both the icon and the localized label/tooltip.
  function weatherCondition(code) {
    if (code === 0) return "clear";
    if (code === 1 || code === 2) return "partly";
    if (code === 3) return "cloudy";
    if (code === 45 || code === 48) return "fog";
    if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return "rain";
    if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
    if (code >= 95) return "thunder";
    return "cloudy";
  }

  // ``isDay === false`` swaps the only two icons that read wrong at night:
  // a bright sun (clear) and the sunny-cloud (partly). Everything else is
  // day/night-agnostic. ``null`` (field absent) is treated as daytime.
  function weatherIconName(condition, isDay) {
    if (condition === "clear") return isDay === false ? "moon" : "sun";
    if (condition === "partly") return isDay === false ? "cloud" : "cloud-sun";
    if (condition === "fog") return "cloud-fog";
    if (condition === "rain") return "cloud-rain";
    if (condition === "snow") return "cloud-snow";
    if (condition === "thunder") return "cloud-bolt";
    return "cloud";
  }

  // Last successful reading, re-rendered on a language switch (like
  // ``feedState``) so the tooltip/aria label follow ``currentLang``
  // without a refetch.
  let weatherState = null;

  function renderWeather() {
    const widget = $("#weather");
    const iconNode = $("#weather-icon");
    const tempNode = $("#weather-temp");
    if (!widget || !iconNode || !tempNode || !weatherState) return;
    const { temp, code, isDay } = weatherState;
    const condition = Number.isFinite(code) ? weatherCondition(code) : "cloudy";
    // de-AT / SI convention: number, NBSP, unit ("21 °C"). The NBSP keeps
    // the value and unit on one line inside the reserved temp slot.
    const tempText = `${nfInt.format(Math.round(temp))} °C`;
    tempNode.textContent = tempText;
    clear(iconNode);
    iconNode.append(buildWeatherIcon(weatherIconName(condition, isDay)));
    widget.dataset.state = "ready";
    widget.dataset.condition = condition;
    const label = `${wt("aria")}: ${tempText}, ${wt(condition)}`;
    widget.setAttribute("aria-label", label);
    widget.setAttribute("title", label);
  }

  // Pick the hourly entry closest to "now" and map it to the compact
  // ``{temp, code, isDay}`` reading the widget renders. ``hourly.time`` is
  // requested as ``unixtime`` (absolute UTC seconds), so the nearest-now
  // search is independent of both the API timezone and the visitor's
  // local clock. Optional fields degrade to ``null`` (→ neutral icon).
  function weatherReadingFromHourly(hourly) {
    const times = hourly && hourly.time;
    const temps = hourly && hourly.temperature_2m;
    if (!Array.isArray(times) || !Array.isArray(temps) || times.length === 0) {
      return null;
    }
    const nowSec = Date.now() / 1000;
    let best = -1;
    let bestDiff = Infinity;
    for (let i = 0; i < times.length; i++) {
      const t = Number(times[i]);
      const temp = temps[i];
      // Only consider hours with a real numeric temperature. Open-Meteo
      // emits ``null`` for gaps, and ``Number(null)`` is ``0`` — without
      // the ``typeof`` guard a gap at the nearest hour would render as a
      // bogus "0 °C".
      if (!Number.isFinite(t) || typeof temp !== "number" || !Number.isFinite(temp)) {
        continue;
      }
      const diff = Math.abs(t - nowSec);
      if (diff < bestDiff) { bestDiff = diff; best = i; }
    }
    if (best < 0) return null;
    const codeArr = hourly.weather_code;
    const dayArr = hourly.is_day;
    const codeVal = Array.isArray(codeArr) ? codeArr[best] : null;
    const dayVal = Array.isArray(dayArr) ? dayArr[best] : null;
    return {
      temp: temps[best],
      code: typeof codeVal === "number" && Number.isFinite(codeVal) ? codeVal : null,
      isDay: typeof dayVal === "number" ? dayVal !== 0 : null,
    };
  }

  async function fetchWeatherReading(hourlyVars, signal) {
    const params = new URLSearchParams({
      latitude: WEATHER_LAT,
      longitude: WEATHER_LON,
      hourly: hourlyVars,
      timeformat: "unixtime",
      forecast_days: "1",
    });
    const res = await fetch(`${WEATHER_URL}?${params.toString()}`, {
      cache: "no-cache",
      credentials: "omit",
      redirect: "follow",
      signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const reading = weatherReadingFromHourly(data && data.hourly);
    if (!reading) throw new Error("weather payload without usable hourly data");
    return reading;
  }

  async function loadWeather(signal) {
    let reading;
    try {
      reading = await fetchWeatherReading(
        "temperature_2m,weather_code,is_day", signal,
      );
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      // A 4xx most likely means this model doesn't offer one of the
      // optional variables — retry with just the temperature so the widget
      // still shows a value (rendered with the neutral fallback icon).
      reading = await fetchWeatherReading("temperature_2m", signal);
    }
    weatherState = reading;
    renderWeather();
  }

  // ----- Loading orchestration -----

  let refreshTimer = null;
  let currentAbort = null;
  let sectionObserver = null;
  // IDs of *lazy* statistic sections the user has revealed by scrolling
  // (currently #stoerungen and #ausfaelle). A refresh always re-runs the
  // feed and stammstrecke loaders, plus whatever lives in this set.
  const loadedSections = new Set();

  async function loadAll() {
    if (currentAbort) currentAbort.abort();
    const ctrl = new AbortController();
    currentAbort = ctrl;

    setStatus("loading", "status-loading");
    const refreshBtn = $("#refresh-btn");
    if (refreshBtn) refreshBtn.disabled = true;

    // The global status follows the feed alone – it's decoupled from the
    // lazy statistic sections, so a successful feed reload doesn't claim
    // "alles aktuell" when #stoerungen / #ausfaelle haven't been loaded.
    // Stammstrecke loads eagerly because its CSV also powers the above-
    // the-fold live tile in the feed header. Sections that became visible
    // in this session piggyback on the same AbortController.
    const tasks = [
      loadFeed(ctrl.signal).then(
        () => {
          setStatus("ok", "status-ok");
          setLastUpdate(new Date());
        },
        () => {
          setStatus("error", "status-error");
        },
      ),
      loadStammstrecke(ctrl.signal).then(
        () => setLastUpdate(new Date()),
        () => {},
      ),
      // Weather is decoupled from the global status: a failed weather
      // fetch (API down, offline, blocked host) must not flip the feed
      // status to "error". On failure the widget simply keeps its last
      // value or the bootstrap "–" placeholder.
      loadWeather(ctrl.signal).then(() => {}, () => {}),
    ];
    for (const id of loadedSections) {
      const loader = SECTION_LOADERS[id];
      if (!loader) continue;
      tasks.push(loader(ctrl.signal).then(
        () => setLastUpdate(new Date()),
        () => {},
      ));
    }

    await Promise.allSettled(tasks);

    if (refreshBtn) refreshBtn.disabled = false;
  }

  async function loadFeed(signal) {
    try {
      hideError("feed-error");
      const text = await fetchText(currentFeedUrl(), { signal });
      const feed = parseFeed(text);
      feedState.items = feed.items;
      renderFeed();
    } catch (err) {
      // Ein AbortError stammt vom Race aus loadAll() (neuer Refresh hat
      // den alten Request verdrängt). showError() und die DOM-Mutationen
      // würden in dem Fall nur Flackern erzeugen – stilles Rethrow.
      if (err.name === "AbortError") throw err;
      const list = $("#feed-list");
      if (list) { list.setAttribute("aria-busy", "false"); clear(list); }
      showError("feed-error", "feed-error-prefix", err.message);
      throw err;
    }
  }

  async function loadStoerungen(signal) {
    try {
      hideError("stoerungen-error");
      const { year, text } = await fetchCsvForYear("stoerungen", { signal });
      const rows = rowsToObjects(parseCSV(text));
      renderStoerungenStats(year, rows);
    } catch (err) {
      if (err.name === "AbortError") throw err;
      showError("stoerungen-error", "stoerungen-error-prefix", err.message);
      throw err;
    }
  }

  async function loadStammstrecke(signal) {
    try {
      hideError("stammstrecke-error");
      const { year, text } = await fetchCsvForYear("stammstrecke", { signal });
      const rows = rowsToObjects(parseCSV(text));
      renderStammstreckeStats(year, rows);
    } catch (err) {
      // Bei Abort die Live-Kachel nicht auf "–" zurücksetzen – der neue
      // Request wird sie ohnehin in Kürze mit frischen Daten füllen.
      if (err.name === "AbortError") throw err;
      resetStammstreckeLiveTile("–");
      showError("stammstrecke-error", "stammstrecke-error-prefix", err.message);
      throw err;
    }
  }

  async function loadAusfaelle(signal) {
    try {
      hideError("ausfaelle-error");
      const { year, text } = await fetchCsvForYear("ausfaelle", { signal });
      const rows = rowsToObjects(parseCSV(text));
      renderAusfaelleStats(year, rows);
    } catch (err) {
      if (err.name === "AbortError") throw err;
      showError("ausfaelle-error", "ausfaelle-error-prefix", err.message);
      throw err;
    }
  }

  // Lazy loaders only – stammstrecke is intentionally absent because it
  // is loaded eagerly inside loadAll() to keep the live tile in the feed
  // header populated above the fold.
  const SECTION_LOADERS = {
    stoerungen: loadStoerungen,
    ausfaelle: loadAusfaelle,
  };

  function setupSectionObserver() {
    const targets = Object.keys(SECTION_LOADERS)
      .map((id) => document.getElementById(id))
      .filter((node) => node != null);
    // Anzahl der *tatsächlich* im DOM vorhandenen Targets festhalten.
    // Wenn SECTION_LOADERS mehr Einträge enthält als das Markup hergibt
    // (z. B. eine Section wurde im HTML entfernt), würde ein Vergleich
    // gegen Object.keys(SECTION_LOADERS).length nie aufgehen – der
    // Observer bliebe für die ganze Session am Leben.
    const targetCount = targets.length;

    if (typeof IntersectionObserver !== "function" || targetCount === 0) {
      // Fallback for environments without IntersectionObserver – avoid leaving
      // the skeletons stuck and just load everything eagerly.
      for (const node of targets) triggerSectionLoad(node.id);
      return;
    }

    sectionObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const id = entry.target.id;
          if (!(id in SECTION_LOADERS)) continue;
          sectionObserver.unobserve(entry.target);
          triggerSectionLoad(id);
        }
        // Sobald jede tatsächlich beobachtete Section getriggert wurde,
        // hat der Observer keine Arbeit mehr – freigeben, statt eine
        // leere Instanz für den Rest der Session zu halten.
        if (sectionObserver && loadedSections.size === targetCount) {
          sectionObserver.disconnect();
          sectionObserver = null;
        }
      },
      { rootMargin: "200px" },
    );

    for (const node of targets) sectionObserver.observe(node);
  }

  async function triggerSectionLoad(id) {
    if (loadedSections.has(id)) return;
    const loader = SECTION_LOADERS[id];
    if (!loader) return;
    loadedSections.add(id);
    const signal = currentAbort ? currentAbort.signal : undefined;
    try {
      await loader(signal);
      setLastUpdate(new Date());
    } catch {
      // showError already invoked inside the loader – nothing else to do
      // here; the global status keeps following the feed.
    }
  }

  // ----- Wiring -----

  function attachFilterHandlers() {
    const root = $("#feed-filters");
    if (!root) return;
    root.addEventListener("click", (ev) => {
      const target = ev.target;
      if (!(target instanceof HTMLElement)) return;
      const btn = target.closest("[data-filter]");
      if (!btn || !root.contains(btn)) return;
      const filter = btn.getAttribute("data-filter") || "all";
      feedState.filter = filter;
      for (const c of root.querySelectorAll(".chip")) c.classList.remove("is-active");
      btn.classList.add("is-active");
      renderFeed();
    });
  }

  function attachRefresh() {
    const btn = $("#refresh-btn");
    if (btn) btn.addEventListener("click", () => loadAll());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        const t = $("#last-update");
        const stamp = t && t.getAttribute("datetime") ? Date.parse(t.getAttribute("datetime")) : 0;
        if (!stamp || Date.now() - stamp > REFRESH_MS) loadAll();
      }
    });
  }

  function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
      if (!document.hidden) loadAll();
    }, REFRESH_MS);
  }

  // ----- Localisation runtime ----------------------------------------

  // Cache the original DE values once at init time so a switch
  // ``EN -> DE`` restores the markup verbatim without a full reload.
  // ``data-i18n-html="1"`` opts a node into innerHTML replacement (used
  // for short fragments that contain inline ``<strong>``/``<code>``);
  // every other node is restricted to ``textContent`` for XSS safety.
  function snapshotDefaultTexts() {
    for (const node of document.querySelectorAll("[data-i18n]")) {
      if (node.dataset.i18nDefault === undefined) {
        const html = node.dataset.i18nHtml === "1";
        node.dataset.i18nDefault = html ? node.innerHTML : node.textContent;
      }
    }
    for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
      if (node.dataset.i18nAriaDefault === undefined) {
        node.dataset.i18nAriaDefault = node.getAttribute("aria-label") || "";
      }
    }
    for (const node of document.querySelectorAll("[data-i18n-title]")) {
      if (node.dataset.i18nTitleDefault === undefined) {
        node.dataset.i18nTitleDefault = node.getAttribute("title") || "";
      }
    }
    for (const node of document.querySelectorAll("[data-i18n-content]")) {
      if (node.dataset.i18nContentDefault === undefined) {
        node.dataset.i18nContentDefault = node.getAttribute("content") || "";
      }
    }
  }

  function _resolveI18nValue(lang, key, defaultValue) {
    if (lang === "en" && Object.prototype.hasOwnProperty.call(I18N_EN, key)) {
      return I18N_EN[key];
    }
    return defaultValue;
  }

  function applyTranslationsToDom(lang) {
    document.documentElement.setAttribute("lang", lang);
    // Body text replacements. ``data-i18n-html="1"`` opts a node into
    // innerHTML rewriting (safe because the template strings come from
    // our static ``I18N_EN`` dictionary, never from external input).
    for (const node of document.querySelectorAll("[data-i18n]")) {
      const key = node.getAttribute("data-i18n");
      const html = node.dataset.i18nHtml === "1";
      const value = _resolveI18nValue(lang, key, node.dataset.i18nDefault);
      if (value == null) continue;
      if (html) {
        node.innerHTML = value;
      } else {
        node.textContent = value;
      }
    }
    // aria-label replacements.
    for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
      const key = node.getAttribute("data-i18n-aria-label");
      const value = _resolveI18nValue(lang, key, node.dataset.i18nAriaDefault);
      if (value != null) node.setAttribute("aria-label", value);
    }
    // title= replacements (tooltip text on lang-switch buttons).
    for (const node of document.querySelectorAll("[data-i18n-title]")) {
      const key = node.getAttribute("data-i18n-title");
      const value = _resolveI18nValue(lang, key, node.dataset.i18nTitleDefault);
      if (value != null) node.setAttribute("title", value);
    }
    // content= replacements (meta description, og:title, …).
    for (const node of document.querySelectorAll("[data-i18n-content]")) {
      const key = node.getAttribute("data-i18n-content");
      const value = _resolveI18nValue(lang, key, node.dataset.i18nContentDefault);
      if (value != null) node.setAttribute("content", value);
    }
    // href= swap for the RSS link(s).
    for (const node of document.querySelectorAll("[data-i18n-href]")) {
      const href = lang === "en"
        ? node.getAttribute("data-href-en")
        : node.getAttribute("data-href-de");
      if (href) node.setAttribute("href", href);
    }
    // ``data-i18n-html`` rewrites blew away any ``<span data-year-label>``
    // children that were filled with the current year on init. Re-apply
    // the year fill so dynamic ``code`` paths inside the translated
    // markup keep showing e.g. ``stoerungen_2026.csv`` instead of
    // ``stoerungen_–.csv``.
    setYearLabels(new Date().getFullYear());
    // Live status / error refreshes — re-render with the new locale.
    const status = $("#status-text");
    if (status && status.dataset.statusKey) {
      status.textContent = statusText(status.dataset.statusKey);
    }
    for (const sel of ["#feed-error", "#stoerungen-error", "#stammstrecke-error", "#ausfaelle-error"]) {
      const node = $(sel);
      if (node && node.dataset.errorPrefixKey) {
        node.textContent = `${statusText(node.dataset.errorPrefixKey)} ${node.dataset.errorDetail || ""}`;
      }
    }
    // Re-render last-update timestamp suffix (de "Uhr" vs en blank).
    const t = $("#last-update");
    const dt = t && t.getAttribute("datetime");
    if (dt) {
      const parsed = Date.parse(dt);
      if (Number.isFinite(parsed)) setLastUpdate(new Date(parsed));
    }
  }

  function attachLangSwitch() {
    const root = $(".lang-switch");
    if (!root) return;
    // Reflect the persisted choice on first paint.
    updateLangSwitchActive(currentLang);
    if (currentLang !== "de") {
      applyTranslationsToDom(currentLang);
    }
    root.addEventListener("click", (ev) => {
      const target = ev.target;
      if (!(target instanceof HTMLElement)) return;
      const btn = target.closest("[data-lang]");
      if (!btn || !root.contains(btn)) return;
      const lang = btn.getAttribute("data-lang");
      if (lang !== "de" && lang !== "en") return;
      if (lang === currentLang) return;
      currentLang = lang;
      writeStoredLang(lang);
      rebuildIntl();
      updateLangSwitchActive(lang);
      applyTranslationsToDom(lang);
      // Re-render dynamic UI that did not go through ``data-i18n``.
      renderFeed();
      // Relabel the weather tooltip/aria from cached state in the new
      // language (loadAll() below refetches too, but this is instant).
      renderWeather();
      // Re-fetch the feed in the new language (no full reload — keeps
      // the strict CSP intact).
      loadAll();
    });
  }

  function updateLangSwitchActive(lang) {
    const root = $(".lang-switch");
    if (!root) return;
    for (const btn of root.querySelectorAll("[data-lang]")) {
      const active = btn.getAttribute("data-lang") === lang;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function init() {
    setYearLabels(new Date().getFullYear());
    snapshotDefaultTexts();
    attachLangSwitch();
    attachFilterHandlers();
    attachRefresh();
    // loadAll() runs first so currentAbort is set before the observer can
    // fire (matters for the no-IntersectionObserver fallback path, which
    // synchronously kicks the lazy loaders from setupSectionObserver).
    loadAll();
    setupSectionObserver();
    startAutoRefresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
