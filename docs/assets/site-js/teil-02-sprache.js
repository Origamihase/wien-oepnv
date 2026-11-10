/* Wien ÖPNV – Live-Dashboard, Modul 2 von 8: Sprache
 *
 * Englische Texte (I18N_EN und die Texte, die das Skript selbst setzt),
 * gewählte Sprache (getLang, setLang), Zahlen- und Datumsformate für die
 * gewählte Sprache, Statuszeile und Fehlermeldungen.
 */
"use strict";

(() => {
  const { $, FEED_URL_DE, FEED_URL_EN, LANG_STORAGE_KEY } = WienOepnv;

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
      "Live dashboard for Wiener Linien, ÖBB, City of Vienna and VOR/VAO: current disruptions from the RSS " +
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
      "<strong>ÖBB</strong>, <strong>City of Vienna</strong> and <strong>VOR/VAO</strong> – live from the " +
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
      "– <code>data/stats/stammstrecke_<span data-year-label>–</span>.csv</code>. " +
      "Source: VOR/VAO.",
    "year-switch-aria": "Statistics by year",
    "year-switch-label": "Statistics by year:",
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
      "– one row per occurrence of a notice in the feed. " +
      "Sources: Wiener Linien, ÖBB, City of Vienna and VOR/VAO, each notice under its own.",
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
      "<code>data/stats/stammstrecke_<span data-year-label>–</span>.csv</code>. " +
      "Source: VOR/VAO.",
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
      "<code>data/stats/ausfaelle_<span data-year-label>–</span>.csv</code>. " +
      "Source: VOR/VAO.",
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
    "footer-source-weather-html":
      '<a href="https://open-meteo.com/" rel="external noopener noreferrer" target="_blank"><strong>Weather data by Open-Meteo.com</strong></a> – weather at Wien Hbf (CC&nbsp;BY&nbsp;4.0)',
    "footer-about-heading": "About the dashboard",
    "footer-about-html":
      "Unofficial project, not affiliated with Wiener Linien, ÖBB, the City of Vienna or VOR/VAO; " +
      "the linked original notices are authoritative. " +
      "Code open-source under the MIT licence. Notice texts remain with their sources and are " +
      "shortened, edited and machine-translated for feed and dashboard: " +
      "Wiener Linien and City of Vienna – data.wien.gv.at under " +
      "<a href=\"https://creativecommons.org/licenses/by/4.0/\" rel=\"external noopener noreferrer\" target=\"_blank\">CC&nbsp;BY&nbsp;4.0</a>, " +
      "ÖBB alerts &copy;&nbsp;ÖBB (not openly licensed). " +
      "S-Bahn trunk-line data: based on the VAO “Start” access of " +
      "Verkehrsauskunft Österreich VAO GmbH. Details in " +
      "<a href=\"https://github.com/Origamihase/wien-oepnv/blob/main/NOTICE.md\" rel=\"external noopener noreferrer\" target=\"_blank\">NOTICE.md</a>. " +
      "Feed update cadence: roughly every 30&nbsp;minutes. " +
      "This dashboard fetches feed and statistics data <em>directly in the browser</em> – " +
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
  // Separator between an error-detail key and its single argument, e.g.
  // ``err-summary-version\u00012``. A C0 control character can never
  // occur in a browser/network error message or in a URL, so an unknown
  // detail can never be mistaken for a parameterised key of ours. The
  // argument lands in the template's ``{arg}`` placeholder.
  const DETAIL_ARG_SEP = "\u0001";

  const STATUS_TEXT = {
    de: {
      "status-loading": "Daten werden geladen …",
      "status-ok": "Live-Feed aktualisiert.",
      "status-error": "Live-Feed konnte nicht geladen werden.",
      "feed-error-prefix": "Feed konnte nicht geladen werden:",
      "stoerungen-error-prefix": "Störungs-Statistik nicht verfügbar:",
      "stammstrecke-error-prefix": "Stammstrecke-Statistik nicht verfügbar:",
      "ausfaelle-error-prefix": "Ausfall-Statistik nicht verfügbar:",
      // Error *details*. Thrown as keys rather than as ready-made sentences
      // so the whole error line follows the language switch — see
      // ``resolveErrorDetail``.
      "err-feed-parse": "Der Feed konnte nicht geparst werden.",
      "err-feed-no-channel": "Der Feed enthält kein <channel>-Element.",
      "err-summary-parse": "Die Statistik-Daten konnten nicht gelesen werden.",
      "err-summary-version": "Die Statistik-Daten liegen im unbekannten Format {arg} vor.",
    },
    en: {
      "status-loading": I18N_EN["status-loading"],
      "status-ok": I18N_EN["status-ok"],
      "status-error": I18N_EN["status-error"],
      "feed-error-prefix": I18N_EN["feed-error-prefix"],
      "stoerungen-error-prefix": I18N_EN["stoerungen-error-prefix"],
      "stammstrecke-error-prefix": I18N_EN["stammstrecke-error-prefix"],
      "ausfaelle-error-prefix": I18N_EN["ausfaelle-error-prefix"],
      // Kept as literals rather than routed through ``I18N_EN``: that
      // dictionary pairs with ``data-i18n`` nodes in the HTML, and these two
      // strings have none — they are raised from JS. Same split as
      // CHART_TEXT / WEATHER_TEXT / COVERAGE_TEXT.
      "err-feed-parse": "The feed could not be parsed.",
      "err-feed-no-channel": "The feed contains no <channel> element.",
      "err-summary-parse": "The statistics data could not be read.",
      "err-summary-version": "The statistics data uses unknown format {arg}.",
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

  // The formatters follow the page language. The other modules keep these
  // objects for good; ``rebuildIntl`` swaps the Intl formatter behind each
  // one when the language changes.
  function liveFormat(build) {
    let impl = build();
    return {
      format: (...args) => impl.format(...args),
      rebuild: () => {
        impl = build();
      },
    };
  }

  const dtfFull = liveFormat(() => buildDtf({ dateStyle: "medium", timeStyle: "short" }));
  const dtfTime = liveFormat(() => buildDtf({ timeStyle: "short" }));
  const dtfDate = liveFormat(() => buildDtf({ dateStyle: "medium" }));
  const rtf = liveFormat(() => new Intl.RelativeTimeFormat(localeTag(), { numeric: "auto" }));
  const nfInt = liveFormat(() => new Intl.NumberFormat(localeTag()));
  const nf1 = liveFormat(() => new Intl.NumberFormat(localeTag(), {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }));

  function buildDtf(opts) {
    return new Intl.DateTimeFormat(localeTag(), {
      timeZone: "Europe/Vienna",
      ...opts,
    });
  }

  function rebuildIntl() {
    for (const formatter of [dtfFull, dtfTime, dtfDate, rtf, nfInt, nf1]) formatter.rebuild();
  }

  // The page language for the other modules (they read it at call time).
  function getLang() {
    return currentLang;
  }

  // Language switch: remember the choice and rebuild the formatters.
  function setLang(lang) {
    currentLang = lang;
    writeStoredLang(lang);
    rebuildIntl();
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
    "tile-na": "N/A",
    "tile-no-trains": "Keine Fahrten",
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
    "tile-na": "N/A",
    "tile-no-trains": "No trains",
    "tile-em-dash": "–",
  };
  function ct(key) {
    const dict = currentLang === "en" ? CHART_TEXT_EN : CHART_TEXT_DE;
    return dict[key] || CHART_TEXT_DE[key] || key;
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

  // "Datenstand" is the feed's own ``lastBuildDate``: the moment the data
  // were built, not the moment this browser fetched them. Showing the fetch
  // time made a feed that had stopped updating read as current.
  function setLastUpdate(date) {
    const t = $("#last-update");
    if (!t || !(date instanceof Date) || Number.isNaN(date.getTime())) return;
    const iso = date.toISOString();
    t.setAttribute("datetime", iso);
    const suffix = currentLang === "en" ? " (Europe/Vienna)" : " Uhr (Europe/Vienna)";
    t.textContent = dtfFull.format(date) + suffix;
  }

  // Resolve an error detail for display. ``raw`` is one of
  //   * an ``err-*`` key thrown by parseFeed and friends,
  //   * such a key plus one argument (``key`` + DETAIL_ARG_SEP + value),
  //     substituted into the template's ``{arg}`` placeholder, or
  //   * a message from the browser/network layer, which we cannot
  //     translate and therefore pass through as-is.
  // ``statusText`` returns "" for an unknown key, which is what makes the
  // fall-through work.
  function resolveErrorDetail(raw) {
    if (!raw) return "";
    const sep = raw.indexOf(DETAIL_ARG_SEP);
    if (sep > 0) {
      const template = statusText(raw.slice(0, sep));
      if (template) return template.replace("{arg}", raw.slice(sep + 1));
    }
    return statusText(raw) || raw;
  }

  // The KEY is stored, not the resolved sentence: ``applyTranslationsToDom``
  // re-renders this line from ``dataset.errorDetail`` on every language
  // switch. Storing the sentence is what used to leave a German detail
  // stranded under an English prefix ("Feed could not be loaded: Feed konnte
  // nicht geparst werden") for the rest of the session.
  function showError(elId, prefixKey, detailKey) {
    const node = document.getElementById(elId);
    if (!node) return;
    node.hidden = false;
    node.textContent = `${statusText(prefixKey)} ${resolveErrorDetail(detailKey)}`;
    node.dataset.errorPrefixKey = prefixKey;
    node.dataset.errorDetail = detailKey;
  }

  function hideError(elId) {
    const node = document.getElementById(elId);
    if (node) node.hidden = true;
  }

  Object.assign(WienOepnv, {
    buildDtf, ct, currentFeedUrl, DETAIL_ARG_SEP, dtfDate, dtfFull, getLang,
    hideError, HOURS, I18N_EN, nf1, nfInt, resolveErrorDetail, rtf, setLang,
    setLastUpdate, setStatus, showError, statusText, weekdayLong, WEEKDAYS,
  });
})();
