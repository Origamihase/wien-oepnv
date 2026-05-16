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
  const FEED_URL = "feed.xml";
  const REFRESH_MS = 5 * 60 * 1000; // 5 Minuten

  const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  const WEEKDAY_LONG = {
    Mo: "Montag", Di: "Dienstag", Mi: "Mittwoch", Do: "Donnerstag",
    Fr: "Freitag", Sa: "Samstag", So: "Sonntag",
  };
  const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));

  const dtfFull = new Intl.DateTimeFormat("de-AT", {
    timeZone: "Europe/Vienna",
    dateStyle: "medium",
    timeStyle: "short",
  });
  const dtfTime = new Intl.DateTimeFormat("de-AT", {
    timeZone: "Europe/Vienna",
    timeStyle: "short",
  });
  const dtfDate = new Intl.DateTimeFormat("de-AT", {
    timeZone: "Europe/Vienna",
    dateStyle: "medium",
  });
  const rtf = new Intl.RelativeTimeFormat("de", { numeric: "auto" });
  const nfInt = new Intl.NumberFormat("de-AT");
  const nf1 = new Intl.NumberFormat("de-AT", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

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
    while (node && node.firstChild) node.removeChild(node.firstChild);
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

  function setStatus(state, message) {
    const dot = $(".status__dot");
    const text = $("#status-text");
    if (dot) dot.dataset.status = state;
    if (text) text.textContent = message;
  }

  function setLastUpdate(date) {
    const t = $("#last-update");
    if (!t) return;
    const iso = date.toISOString();
    t.setAttribute("datetime", iso);
    t.textContent = dtfFull.format(date) + " Uhr (Europe/Vienna)";
  }

  function showError(elId, message) {
    const node = document.getElementById(elId);
    if (!node) return;
    node.hidden = false;
    node.textContent = message;
  }

  function hideError(elId) {
    const node = document.getElementById(elId);
    if (node) node.hidden = true;
  }

  // ----- Fetching -----

  async function fetchText(url, { signal } = {}) {
    const buster = Math.floor(Date.now() / 60000);
    const sep = url.includes("?") ? "&" : "?";
    const res = await fetch(url + sep + "t=" + buster, {
      cache: "no-store",
      credentials: "omit",
      redirect: "follow",
      mode: "cors",
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

  function parseFeed(xmlText) {
    const doc = new DOMParser().parseFromString(xmlText, "application/xml");
    const err = doc.querySelector("parsererror");
    if (err) throw new Error("Feed konnte nicht geparst werden");
    const channel = doc.querySelector("channel");
    if (!channel) throw new Error("Feed ohne <channel>-Element");

    const lastBuild = channelChildText(channel, "lastBuildDate");
    const items = Array.from(doc.getElementsByTagName("item")).map((it) => {
      const get = (tag) => firstChildText(it, tag);
      const ns = (tag) => firstChildTextNs(it, NS_EXT, tag);
      return {
        title: get("title"),
        link: get("link"),
        guid: get("guid"),
        pubDate: get("pubDate"),
        description: get("description"),
        firstSeen: ns("first_seen"),
        startsAt: ns("starts_at"),
        endsAt: ns("ends_at"),
      };
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
    const haystack = `${item.link} ${item.title}`.toLowerCase();
    if (/(wienerlinien|wiener\s*linien|wl-disp|ogd_realtime)/.test(haystack)) return "wienerlinien";
    if (/(oebb|öbb|scotty)/.test(haystack)) return "oebb";
    if (/(vor\.at|verkehrsverbund|vao\.|anachb)/.test(haystack)) return "vor";
    return "other";
  }

  function sourceLabel(key) {
    return key === "wienerlinien" ? "Wiener Linien"
         : key === "oebb" ? "ÖBB"
         : key === "vor" ? "VOR / VAO"
         : "Andere";
  }

  let feedState = { items: [], filter: "all" };

  function renderFeed() {
    const list = $("#feed-list");
    const empty = $("#feed-empty");
    const countBadge = $("#feed-count");
    if (!list) return;

    list.setAttribute("aria-busy", "false");
    clear(list);

    // "Andere" is a catch-all for anything that is not Wiener Linien
    // or ÖBB – so a hypothetical VOR/VAO item (still labelled green
    // by detectSource) is also visible under "Andere", not just under
    // "Alle". The dedicated VOR/VAO chip was removed in 2026-05 because
    // VOR data now flows only into the Stammstrecken monitor.
    const filtered = feedState.filter === "all"
      ? feedState.items
      : feedState.items.filter((it) => {
          const src = detectSource(it);
          if (feedState.filter === "other") {
            return src !== "wienerlinien" && src !== "oebb";
          }
          return src === feedState.filter;
        });

    if (countBadge) {
      countBadge.textContent = `${nfInt.format(filtered.length)} aktiv`;
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
    const li = el("li", { class: "feed-item", attrs: { "data-source": detectSource(item) } });
    const src = detectSource(item);
    li.append(el("span", {
      class: "feed-item__source",
      attrs: { "data-source": src, title: sourceLabel(src) },
      text: sourceLabel(src),
    }));

    const body = el("div", { class: "feed-item__body" });

    const titleNode = el("h3", { class: "feed-item__title" });
    const link = safeHttpsUrl(item.link);
    const titleText = item.title || "(ohne Titel)";
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
    const pub = parseRfc2822(item.pubDate);
    if (pub) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: "Beginn: " }));
      const t = el("time", { attrs: { datetime: pub.toISOString() }, text: dtfFull.format(pub) });
      d.append(t);
      meta.append(d);
    }
    const ends = parseRfc2822(item.endsAt);
    if (ends) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: "Bis: " }));
      d.append(el("time", { attrs: { datetime: ends.toISOString() }, text: dtfFull.format(ends) }));
      meta.append(d);
    }
    const seen = parseRfc2822(item.firstSeen);
    if (seen) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: "Erstmals: " }));
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
    if (absMin < 1) return "gerade eben";
    if (absMin < 60) return rtf.format(sign * Math.round(absMin), "minute");
    const hours = absMin / 60;
    if (hours < 24) return rtf.format(sign * Math.round(hours), "hour");
    const days = hours / 24;
    if (days < 30) return rtf.format(sign * Math.round(days), "day");
    return dtfDate.format(date);
  }

  // ----- Statistics (stoerungen) -----

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
      { label: "Erfasste Störungen", value: nfInt.format(total), sub: `Jahr ${year}` },
      { label: "Häufigste Quelle",
        value: topProvider ? topProvider[0] : "–",
        sub: topProvider ? `${nfInt.format(topProvider[1])} Meldungen` : "" },
      { label: "Spitzenstunde",
        value: peakHour ? `${peakHour[0]}:00` : "–",
        sub: peakHour ? `${nfInt.format(peakHour[1])} Meldungen` : "" },
      { label: "Stärkster Tag",
        value: peakWeekday ? (WEEKDAY_LONG[peakWeekday[0]] || peakWeekday[0]) : "–",
        sub: peakWeekday ? `${nfInt.format(peakWeekday[1])} Meldungen` : "" },
    ]);

    renderBars("#stoerungen-providers",
      sortedEntries(byProvider).slice(0, 8),
      { variant: "provider", formatValue: (v) => nfInt.format(v) });

    renderBars("#stoerungen-weekday",
      WEEKDAYS.map((d) => [WEEKDAY_LONG[d] || d, byWeekday[d] || 0]),
      { unit: "", formatValue: (v) => nfInt.format(v) });

    renderBars("#stoerungen-hour",
      HOURS.map((h) => [`${h}:00`, byHour[h] || 0]),
      { unit: "", formatValue: (v) => nfInt.format(v) });
  }

  function renderStammstreckeStats(year, rows) {
    setYearLabels(year);

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
      { label: "Beobachtungen", value: nfInt.format(total), sub: `Jahr ${year}` },
      { label: "Ø Verspätung", value: `${nf1.format(avg)} min`, sub: "alle Beobachtungen" },
      { label: "Max. Verspätung", value: `${nf1.format(max)} min`, sub: "Tickwert" },
      { label: "Schwerverspätungen", value: nfInt.format(over9), sub: "≥ 9 Minuten" },
    ]);

    renderBars("#stammstrecke-hour",
      HOURS.map((h) => [`${h}:00`, avgByHour[h] || 0]),
      { unit: " min", variant: "delay", formatValue: (v) => nf1.format(v) });

    renderBars("#stammstrecke-weekday",
      WEEKDAYS.map((d) => [WEEKDAY_LONG[d] || d, avgByWeekday[d] || 0]),
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
      { label: "Ausfälle", value: nfInt.format(total), sub: `Jahr ${year}` },
      { label: "Häufigste Linie",
        value: topLine ? topLine[0] : "–",
        sub: topLine ? `${nfInt.format(topLine[1])} Ausfälle` : "keine Daten" },
      { label: "Stärkster Tag",
        value: topWeekday ? (WEEKDAY_LONG[topWeekday[0]] || topWeekday[0]) : "–",
        sub: topWeekday ? `${nfInt.format(topWeekday[1])} Ausfälle` : "keine Daten" },
    ]);

    renderBars("#ausfaelle-line",
      sortedEntries(byLine).slice(0, 10),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-direction",
      sortedEntries(byDirection),
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
      root.append(el("p", { class: "empty", text: "Keine Daten verfügbar." }));
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

  // ----- Loading orchestration -----

  let refreshTimer = null;
  let currentAbort = null;

  async function loadAll() {
    if (currentAbort) currentAbort.abort();
    const ctrl = new AbortController();
    currentAbort = ctrl;

    setStatus("loading", "Daten werden geladen …");
    const refreshBtn = $("#refresh-btn");
    if (refreshBtn) refreshBtn.disabled = true;

    const tasks = await Promise.allSettled([
      loadFeed(ctrl.signal),
      loadStoerungen(ctrl.signal),
      loadStammstrecke(ctrl.signal),
      loadAusfaelle(ctrl.signal),
    ]);

    if (refreshBtn) refreshBtn.disabled = false;

    const errors = tasks.filter((t) => t.status === "rejected");
    if (errors.length === 0) {
      setStatus("ok", "Alle Daten aktuell.");
    } else if (errors.length === tasks.length) {
      setStatus("error", "Daten konnten nicht geladen werden.");
    } else {
      setStatus("warning", `Teilweise geladen (${tasks.length - errors.length}/${tasks.length}).`);
    }
    setLastUpdate(new Date());
  }

  async function loadFeed(signal) {
    try {
      hideError("feed-error");
      const text = await fetchText(FEED_URL, { signal });
      const feed = parseFeed(text);
      feedState.items = feed.items;
      renderFeed();
    } catch (err) {
      const list = $("#feed-list");
      if (list) { list.setAttribute("aria-busy", "false"); clear(list); }
      showError("feed-error", `Feed konnte nicht geladen werden: ${err.message}`);
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
      showError("stoerungen-error", `Störungs-Statistik nicht verfügbar: ${err.message}`);
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
      showError("stammstrecke-error", `Stammstrecke-Statistik nicht verfügbar: ${err.message}`);
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
      showError("ausfaelle-error", `Ausfall-Statistik nicht verfügbar: ${err.message}`);
      throw err;
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

  function init() {
    setYearLabels(new Date().getFullYear());
    attachFilterHandlers();
    attachRefresh();
    loadAll();
    startAutoRefresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
