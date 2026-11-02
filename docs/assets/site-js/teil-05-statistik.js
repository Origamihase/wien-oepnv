/* Wien ÖPNV – Live-Dashboard, Modul 5 von 8: Statistik
 *
 * Lädt stats-summary.json (einmal je Aktualisierung) und zeigt die
 * Abschnitte Störungen und Zugausfälle.
 */
"use strict";

(() => {
  const {
    ct, DETAIL_ARG_SEP, fetchText, getLang, HOURS, nfInt, renderBars,
    renderKpis, setYearLabels, sortedEntries, sourceLabel,
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
    renderStoerungenStats, resetSummaryCache,
  });
})();
