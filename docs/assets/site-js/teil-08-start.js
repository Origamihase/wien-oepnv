/* Wien ÖPNV – Live-Dashboard, Modul 8 von 8: Start
 *
 * Lädt alles beim Start und alle fünf Minuten, lädt Statistik-Abschnitte
 * erst beim Hinscrollen, verbindet Filter, Aktualisieren und Sprachwahl
 * und übersetzt die Seite beim Sprachwechsel.
 */
"use strict";

(() => {
  const {
    $, clear, currentFeedUrl, feedState, fetchSummary, fetchText, getLang,
    hideError, I18N_EN, loadWeather, parseFeed, parseRfc2822, REFRESH_MS,
    renderAusfaelleStats, renderFeed, renderStammstreckeStats,
    renderStoerungenStats, renderWeather, renderYearSwitch,
    resetStammstreckeLiveTile, resetSummaryCache, resolveErrorDetail, setLang,
    setLastUpdate, setStatus, setYearLabels, showError, statusText, viennaYear,
  } = WienOepnv;

  // ----- Loading orchestration -----

  let refreshTimer = null;
  // When this page last fetched the feed successfully (for the refresh on
  // returning to the tab); "Datenstand" shows the feed's own build time.
  let lastFetchAt = 0;
  let currentAbort = null;
  let sectionObserver = null;
  // IDs of *lazy* statistic sections the user has revealed by scrolling
  // (currently #stoerungen and #ausfaelle). A refresh always re-runs the
  // feed and stammstrecke loaders, plus whatever lives in this set.
  const loadedSections = new Set();
  // The summary of the last successful fetch. The year switch is drawn from
  // it again after a language switch, before the refetch has answered.
  let lastSummary = null;

  async function loadAll() {
    if (currentAbort) currentAbort.abort();
    const ctrl = new AbortController();
    currentAbort = ctrl;
    // Fresh cycle, fresh numbers: the three statistics panels share one
    // fetch *within* a cycle, never across cycles.
    resetSummaryCache();

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
          lastFetchAt = Date.now();
          setLastUpdate(parseRfc2822(feedState.lastBuild));
        },
        () => {
          setStatus("error", "status-error");
        },
      ),
      loadStammstrecke(ctrl.signal).then(() => {}, () => {}),
      // Weather is decoupled from the global status: a failed weather
      // fetch (API down, offline, blocked host) must not flip the feed
      // status to "error". On failure the widget simply keeps its last
      // value or the bootstrap "–" placeholder.
      loadWeather(ctrl.signal).then(() => {}, () => {}),
    ];
    for (const id of loadedSections) {
      const loader = SECTION_LOADERS[id];
      if (!loader) continue;
      tasks.push(loader(ctrl.signal).then(() => {}, () => {}));
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
      feedState.lastBuild = feed.lastBuild;
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
      const summary = await fetchSummary(signal);
      renderStoerungenStats(summary.year, summary.stoerungen || {});
    } catch (err) {
      if (err.name === "AbortError") throw err;
      showError("stoerungen-error", "stoerungen-error-prefix", err.message);
      throw err;
    }
  }

  async function loadStammstrecke(signal) {
    try {
      hideError("stammstrecke-error");
      const summary = await fetchSummary(signal);
      // Stammstrecke is the one section that loads on every cycle, so the
      // year switch in the feed header is drawn here, from the same document.
      lastSummary = summary;
      renderYearSwitch(summary);
      renderStammstreckeStats(summary.year, summary.stammstrecke || {});
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
      const summary = await fetchSummary(signal);
      renderAusfaelleStats(summary.year, summary.ausfaelle || {});
    } catch (err) {
      if (err.name === "AbortError") throw err;
      showError("ausfaelle-error", "ausfaelle-error-prefix", err.message);
      throw err;
    }
  }

  // Lazy loaders only – stammstrecke is intentionally absent because it
  // is loaded eagerly inside loadAll() to keep the live tile in the feed
  // header populated above the fold. All three now read the same
  // ``fetchSummary`` document, so a lazy section that scrolls into view
  // during a refresh cycle costs no extra request.
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
        if (!lastFetchAt || Date.now() - lastFetchAt > REFRESH_MS) loadAll();
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
    setYearLabels(viennaYear());
    // Live status / error refreshes — re-render with the new locale.
    const status = $("#status-text");
    if (status && status.dataset.statusKey) {
      status.textContent = statusText(status.dataset.statusKey);
    }
    for (const sel of ["#feed-error", "#stoerungen-error", "#stammstrecke-error", "#ausfaelle-error"]) {
      const node = $(sel);
      if (node && node.dataset.errorPrefixKey) {
        // Detail goes through the same resolver as in ``showError`` so an
        // ``err-*`` key re-renders in the newly selected language instead of
        // staying in the one it was raised under.
        node.textContent =
          `${statusText(node.dataset.errorPrefixKey)} ` +
          `${resolveErrorDetail(node.dataset.errorDetail)}`;
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
    updateLangSwitchActive(getLang());
    if (getLang() !== "de") {
      applyTranslationsToDom(getLang());
    }
    root.addEventListener("click", (ev) => {
      const target = ev.target;
      if (!(target instanceof HTMLElement)) return;
      const btn = target.closest("[data-lang]");
      if (!btn || !root.contains(btn)) return;
      const lang = btn.getAttribute("data-lang");
      if (lang !== "de" && lang !== "en") return;
      if (lang === getLang()) return;
      setLang(lang);
      updateLangSwitchActive(lang);
      applyTranslationsToDom(lang);
      // Re-render dynamic UI that did not go through ``data-i18n``.
      renderFeed();
      // Relabel the weather tooltip/aria from cached state in the new
      // language (loadAll() below refetches too, but this is instant).
      renderWeather();
      // The links of the year switch carry text in the page language.
      renderYearSwitch(lastSummary);
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
    setYearLabels(viennaYear());
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
