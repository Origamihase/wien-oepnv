/* Wien ÖPNV – Live-Dashboard, Modul 5 von 8: Statistik
 *
 * Lädt stats-summary.json (einmal je Aktualisierung) und zeigt die
 * Abschnitte Störungen und Zugausfälle sowie die Jahreswahl unter
 * „Aktuelle Meldungen“.
 */
"use strict";

(() => {
  const {
    $, clear, ct, DETAIL_ARG_SEP, el, fetchText, getLang, HOURS, nfInt,
    renderBars, renderKpis, setYearLabels, sortedEntries, sourceLabel,
    SUMMARY_SCHEMA_VERSION, SUMMARY_URL, topEntry, weekdayLong, WEEKDAYS,
  } = WienOepnv;

  // ----- Statistics summary -----

  // One document feeds all three statistics panels, so the fetch is
  // shared per refresh cycle rather than repeated once per panel.
  // ``loadAll`` clears the cache at the start of every cycle; a section
  // that becomes visible between cycles fetches on its own.
  let summaryRequest = null;

  function resetSummaryCache() {
    summaryRequest = null;
  }

  function parseSummary(text) {
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error("err-summary-parse");
    }
    if (!data || typeof data !== "object") throw new Error("err-summary-parse");
    if (data.schema_version !== SUMMARY_SCHEMA_VERSION) {
      throw new Error(`err-summary-version${DETAIL_ARG_SEP}${data.schema_version}`);
    }
    return data;
  }

  function fetchSummary(signal) {
    if (!summaryRequest) {
      summaryRequest = fetchText(SUMMARY_URL, { signal })
        .then(parseSummary)
        .catch((err) => {
          // Do not cache a failure: the next panel (or the next refresh)
          // must be free to try again rather than inherit this rejection
          // for the rest of the session.
          summaryRequest = null;
          throw err;
        });
    }
    return summaryRequest;
  }

  // ----- Year switch -----

  // Owner decision 2026-11-10: "the year can be chosen on the website under
  // the heading Aktuelle Meldungen". A finished year has its own page
  // ``statistik-<year>.html`` (German only, written on 1 January); the
  // summary names those years in ``archive_years``. The figures on this page
  // are always the running year's, so its chip is the marked one and leads
  // to the first of its three statistics sections (``#stoerungen``, then
  // ``#stammstrecke`` and ``#ausfaelle``), as a finished year's page opens
  // with the overview of all three (review of 2026-11-10).
  //
  // Text of the links to finished years, as tooltip and accessible name: a
  // bare "2026" does not say where the link leads, and an English reader is
  // told beforehand that the page behind it is in German. The accessible
  // name of the chip "Ältere Jahre" starts with its visible text (WCAG 2.5.3).
  const YEAR_TEXT_DE = {
    "year-link-title": "Statistik {year} – abgeschlossenes Jahr",
    "year-more": "Ältere Jahre",
    "year-more-title": "Ältere Jahre – Liste aller Jahre auf der Statistikseite",
  };
  const YEAR_TEXT_EN = {
    "year-link-title": "Statistics {year} – finished year (page in German)",
    "year-more": "Earlier years",
    "year-more-title": "Earlier years – list of all years on the statistics page (page in German)",
  };
  function yearText(key) {
    const dict = getLang() === "en" ? YEAR_TEXT_EN : YEAR_TEXT_DE;
    return dict[key] || YEAR_TEXT_DE[key] || key;
  }

  // The summary is fetched JSON, so the list is checked before any of it
  // becomes a link: whole numbers only, a plausible range, strictly before
  // the running year (a year page of the running year does not exist), each
  // once, newest first. The row shows at most ARCHIVE_YEAR_LIMIT of them so a
  // broken document cannot fill the header with chips; a longer list is not
  // dropped without a word but ends in one more chip "Ältere Jahre" to
  // ``statistik.html``, whose line "Frühere Jahre" names every year (review
  // of 2026-11-10: no silent caps). With one page per year from 2026 the cut
  // first happens on 1 January 2057 (31 pages, 2026 to 2056).
  const ARCHIVE_YEAR_MIN = 2000;
  const ARCHIVE_YEAR_MAX = 2100;
  const ARCHIVE_YEAR_LIMIT = 30;

  function archiveYears(summary) {
    const current = summary ? summary.year : undefined;
    const listed = summary ? summary.archive_years : undefined;
    if (!Number.isInteger(current) || !Array.isArray(listed)) return [];
    const years = new Set();
    for (const item of listed) {
      if (!Number.isInteger(item)) continue;
      if (item < ARCHIVE_YEAR_MIN || item > ARCHIVE_YEAR_MAX || item >= current) continue;
      years.add(item);
    }
    return Array.from(years).sort((a, b) => b - a);
  }

  // Fills the row from the summary and hides it again when the summary names
  // no finished year, or does not carry the field at all (a document written
  // before the field existed). Called with every freshly fetched summary and
  // after a language switch, which changes the link texts. The years change
  // once a year, so a draw with the same years and language leaves the row as
  // it is: rebuilding it on every 5-minute refresh would take the keyboard
  // focus off a chip and drop it on <body> (review of 2026-11-10). The key
  // alone decides: nothing else sets nav.hidden, and a draw without years
  // stores the empty key, so a row hidden and shown again is drawn anew.
  function renderYearSwitch(summary) {
    const nav = $("#year-switch");
    const list = $("#year-switch-list");
    if (!nav || !list) return;
    const all = archiveYears(summary);
    const drawn = all.length === 0 ? "" : `${getLang()}|${summary.year}|${all.join(",")}`;
    if (list.dataset.drawn === drawn) return;
    list.dataset.drawn = drawn;
    clear(list);
    if (all.length === 0) {
      nav.hidden = true;
      return;
    }
    const years = all.slice(0, ARCHIVE_YEAR_LIMIT);
    const frag = document.createDocumentFragment();
    frag.append(el("li", {}, el("a", {
      class: "chip is-active",
      text: summary.year,
      attrs: { href: "#stoerungen", "aria-current": "true" },
    })));
    for (const year of years) {
      const title = yearText("year-link-title").replace("{year}", String(year));
      frag.append(el("li", {}, el("a", {
        class: "chip",
        text: year,
        attrs: {
          href: `statistik-${year}.html`,
          hreflang: "de",
          title,
          "aria-label": title,
        },
      })));
    }
    if (all.length > years.length) {
      const title = yearText("year-more-title");
      frag.append(el("li", {}, el("a", {
        class: "chip",
        text: yearText("year-more"),
        attrs: { href: "statistik.html", hreflang: "de", title, "aria-label": title },
      })));
    }
    list.append(frag);
    nav.hidden = false;
  }

  // ----- Statistics (stoerungen) -----

  // Display label for the source-distribution chart + KPI. The yearly
  // ledger stores the descriptive "Stadt Wien – Baustellen" in its
  // ``provider`` column (the feed pipeline keys glossary/entity-masking
  // off that exact string), so we map it — display only, data untouched —
  // to the short, localized name via the same ``sourceLabel`` helper the
  // feed uses (DE "Baustellen" / EN "Construction"). loadAll() re-renders
  // the stats on a language switch, so the label follows getLang(). The
  // ``data-key`` rendered from this label drives the yellow
  // ``--c-baustellen`` bar fill in site.css (both strings are matched).
  const providerLabel = (p) => (/Baustellen/.test(p) ? sourceLabel("baustellen") : p);

  // The summary's per-dimension maps are sparse: the generator only
  // emits a key it actually observed. A missing bucket therefore means
  // "no observations", which is a zero — not missing data.
  function bucket(dict, key) {
    const value = dict ? dict[key] : undefined;
    return Number.isFinite(value) ? value : 0;
  }

  function numberOr(value, fallback) {
    return Number.isFinite(value) ? value : fallback;
  }

  function renderStoerungenStats(year, stats) {
    setYearLabels(year);

    const byProvider = stats.by_provider || {};
    const byWeekday = stats.by_weekday || {};
    const byHour = stats.by_hour || {};

    const topProvider = topEntry(byProvider);
    const peakHour = topEntry(byHour);
    const peakWeekday = topEntry(byWeekday);

    renderKpis("#stoerungen-kpis", [
      { label: ct("kpi-stoerungen-total"),
        value: nfInt.format(numberOr(stats.total, 0)),
        sub: `${ct("sub-year")} ${year}` },
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
      WEEKDAYS.map((d) => [weekdayLong(d), bucket(byWeekday, d)]),
      { unit: "", formatValue: (v) => nfInt.format(v) });

    renderBars("#stoerungen-hour",
      HOURS.map((h) => [`${h}:00`, bucket(byHour, h)]),
      { unit: "", formatValue: (v) => nfInt.format(v) });
  }

  // A cancelled train VAO named by its train number counts as "ohne
  // Linienangabe" (``NO_LINE_LABEL`` in scripts/generate_markdown_stats.py);
  // the label follows the page language like the source names do.
  const lineLabel = (l) =>
    l === "ohne Linienangabe" && getLang() === "en" ? "no line given" : l;

  function renderAusfaelleStats(year, stats) {
    setYearLabels(year);
    const byLine = stats.by_line || {};
    const byDirection = stats.by_direction || {};
    const byHour = stats.by_hour || {};
    const byWeekday = stats.by_weekday || {};

    const topLine = topEntry(byLine);
    const topWeekday = topEntry(byWeekday);

    renderKpis("#ausfaelle-kpis", [
      { label: ct("kpi-cancellations"),
        value: nfInt.format(numberOr(stats.total, 0)),
        sub: `${ct("sub-year")} ${year}` },
      { label: ct("kpi-top-line"),
        value: topLine ? lineLabel(topLine[0]) : ct("tile-em-dash"),
        sub: topLine ? `${nfInt.format(topLine[1])} ${ct("sub-ausfaelle")}` : ct("sub-no-data") },
      { label: ct("kpi-top-weekday"),
        value: topWeekday ? weekdayLong(topWeekday[0]) : ct("tile-em-dash"),
        sub: topWeekday ? `${nfInt.format(topWeekday[1])} ${ct("sub-ausfaelle")}` : ct("sub-no-data") },
    ]);

    renderBars("#ausfaelle-line",
      sortedEntries(byLine).slice(0, 10).map(([l, v]) => [lineLabel(l), v]),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-direction",
      sortedEntries(byDirection),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-weekday",
      WEEKDAYS.map((d) => [weekdayLong(d), bucket(byWeekday, d)]),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });

    renderBars("#ausfaelle-hour",
      HOURS.map((h) => [`${h}:00`, bucket(byHour, h)]),
      { unit: "", variant: "cancel", formatValue: (v) => nfInt.format(v) });
  }

  Object.assign(WienOepnv, {
    bucket, fetchSummary, numberOr, renderAusfaelleStats,
    renderStoerungenStats, renderYearSwitch, resetSummaryCache,
  });
})();
