/* Wien ÖPNV – Live-Dashboard, Modul 3 von 8: Diagramme
 *
 * Hilfen für die Statistik-Abschnitte: sortieren, Jahreszahlen, KPI-Kacheln
 * und Balkendiagramme (SVG).
 */
"use strict";

(() => {
  const { $, $$, buildDtf, clear, el, getLang } = WienOepnv;

  // ----- Aggregation helpers -----

  function sortedEntries(obj) {
    return Object.entries(obj).sort((a, b) => b[1] - a[1]);
  }

  function topEntry(obj) {
    const entries = sortedEntries(obj);
    if (entries.length === 0 || entries[0][1] === 0) return null;
    return entries[0];
  }

  // ----- Render primitives -----

  // The CSV files are written per year in Vienna, so the year shown is
  // Vienna's too, not the visitor's clock (around New Year abroad).
  function viennaYear() {
    return Number(buildDtf({ year: "numeric" }).format(new Date()));
  }

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
        text: getLang() === "en" ? "No data available." : "Keine Daten verfügbar.",
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

  Object.assign(WienOepnv, {
    renderBars, renderKpis, setYearLabels, sortedEntries, svg, topEntry,
    viennaYear,
  });
})();
