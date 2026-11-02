/* Wien ÖPNV – Live-Dashboard, Modul 1 von 8: Grundlagen
 *
 * Vanilla JS, kein Build, keine Drittabhängigkeiten. Lädt:
 *  - feed.xml (same-origin)
 *  - stats-summary.json (same-origin, vorverdichtet)
 * und rendert alles im Browser. Alle Fremddaten werden ausschließlich
 * über textContent in den DOM eingefügt – keine innerHTML-Pfade für
 * Daten aus Feed oder Statistik. URLs werden vor der Verwendung als
 * href mit dem WHATWG-URL-Parser geprüft.
 *
 * Das Skript besteht aus acht Modulen (docs/assets/site-js/teil-NN-*.js,
 * seit 2026-11-02, höchstens 700 Zeilen je Datei). Jedes Modul ist eine
 * eigene Funktion mit eigenem Gültigkeitsbereich; was es von früheren
 * Modulen braucht, holt es am Anfang aus dem Namensraum WienOepnv, was
 * spätere brauchen, legt es am Ende dort ab. scripts/optimize_site_assets.py
 * fügt die Module in Namensreihenfolge zu site.min.js zusammen, der
 * einzigen Skriptdatei, die die Seite lädt.
 *
 * Dieses Modul: Adressen und Konstanten, DOM-Helfer, sichere URLs, Laden
 * von Texten.
 */
"use strict";

// Shared namespace of the dashboard modules: each module adds what the
// later ones use (Object.assign at its end) and takes what it needs from
// the earlier ones (destructuring at its start). Not a property of window.
const WienOepnv = {};

(() => {
  // Audit E.3: the statistics panels used to pull the three raw yearly
  // ledgers straight from ``raw.githubusercontent.com`` — ~705 KB in
  // September, growing every tick, to render a few KPI tiles and bar
  // charts. They now read one pre-computed document written by
  // ``scripts/generate_markdown_stats.py`` on the same 30-minute tick
  // that appends to those ledgers: ~3 KB, same origin as the page, and
  // flat all year because its dimensions are fixed (7 weekdays,
  // 24 hours, a handful of providers, lines and directions).
  //
  // Same origin matters beyond the bytes: raw.githubusercontent.com is
  // not a delivery CDN, carries its own rate limits, and sits outside
  // the GitHub Pages availability promise — when it throttled, the page
  // stayed up but every chart failed. It is no longer in the page's
  // ``connect-src`` at all.
  //
  // The raw ledgers stay under ``data/stats/`` and stay linked below
  // for anyone who wants to analyse them.
  const SUMMARY_URL = "stats-summary.json";
  // Must match ``SUMMARY_SCHEMA_VERSION`` in
  // ``scripts/generate_markdown_stats.py``. A document from a newer
  // generator is refused outright rather than rendered from whichever
  // fields happen to still be recognisable — half a dashboard of stale
  // numbers reads as data, not as an error.
  const SUMMARY_SCHEMA_VERSION = 1;
  const FEED_URL_DE = "feed.xml";
  const FEED_URL_EN = "feed.en.xml";
  const REFRESH_MS = 5 * 60 * 1000; // 5 Minuten
  const LANG_STORAGE_KEY = "wienoepnv:lang";
  // Spiegelt ``DELAY_THRESHOLD_MINUTES`` aus ``src/feed/stammstrecke.py``.
  // Eine Beobachtung zählt als Schwerverspätung, wenn sie STRIKT darüber
  // liegt — genau wie im Backend (``obs.delay_minutes > DELAY_THRESHOLD_MINUTES``)
  // und im Markdown-Dashboard (``Kritische Verspätungen (> 9 min)``).
  // Das Kachel-Label unten wird aus derselben Konstante gebaut, damit
  // Anzeige und Filter nicht wieder auseinanderlaufen: vorher zählte die
  // Kachel ``>= 9`` unter der Beschriftung „≥ 9 Minuten", während jeder
  // Backend-Konsument ``> 9`` zählte.
  // ``tests/test_dashboard_delay_threshold.py`` pinnt beide Werte aneinander.
  const DELAY_THRESHOLD_MIN = 9;

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

  // ----- Fetching -----

  async function fetchText(url, { signal } = {}) {
    // ``cache: "no-cache"`` forces the browser to revalidate the cached
    // entry against the origin (``If-None-Match`` / ``If-Modified-Since``)
    // on every refresh. When the upstream payload is unchanged the
    // server replies ``304 Not Modified`` and the browser serves the
    // cached body — same freshness guarantee as the previous
    // ``no-store`` + per-minute cache-buster combo, but without
    // redownloading the full feed or summary each cycle. GitHub Pages
    // (``feed.xml``, ``stats-summary.json``) emits strong ``ETag``
    // headers, so revalidation produces real
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

  Object.assign(WienOepnv, {
    $, $$, clear, DELAY_THRESHOLD_MIN, el, FEED_URL_DE, FEED_URL_EN, fetchText,
    LANG_STORAGE_KEY, REFRESH_MS, safeHttpsUrl, SUMMARY_SCHEMA_VERSION,
    SUMMARY_URL,
  });
})();
