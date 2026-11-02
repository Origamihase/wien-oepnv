/* Wien ÖPNV – Live-Dashboard, Modul 6 von 8: Live-Kachel und Stammstrecke
 *
 * Die Kachel „Ø Verspätung Stammstrecke“ im Kopf des Feeds, der Abschnitt
 * Stammstrecke und der Hinweis auf eingeschränkte Abdeckung.
 */
"use strict";

(() => {
  const {
    $, bucket, ct, DELAY_THRESHOLD_MIN, dtfDate, getLang, HOURS, nf1, nfInt,
    numberOr, renderBars, renderKpis, setYearLabels, sortedEntries,
    weekdayLong, WEEKDAYS,
  } = WienOepnv;

  // Canonical Stammstrecke directions. Mirrors ``STAMMSTRECKE_DIRECTIONS`` in
  // ``src/utils/stats.py`` — kept in sync by hand because the site is static.
  const STAMMSTRECKE_DIRECTIONS = ["Meidling", "Praterstern"];
  // The banner names NO cause. It used to carry an operator-maintained one
  // ("Streckensperre – Bauarbeiten und Kabelbrand-Folgen"), which was
  // accurate for the 2026 outage and wrong for every other way a direction
  // can fall silent: a timetable change, a provider renaming the platform,
  // our own monitor failing. It states the observation instead, in one
  // sentence shape that fits every case — one direction, the other, or both.
  // Mirrors ``render_direction_coverage_note`` in
  // ``scripts/generate_markdown_stats.py``.
  //
  // Measurement point of the observations. Same wording as the footer
  // ("Stammstrecken-Beobachtungen am Wien Hbf") and as
  // ``COVERAGE_ORIGIN_LABEL`` on the Python side.
  const COVERAGE_ORIGIN = "Wien Hbf";
  const COVERAGE_TEXT_DE = {
    "coverage-title": "Eingeschränkte Abdeckung",
    "coverage-last-one": "zuletzt am",
    "coverage-last-many": "zuletzt",
    "coverage-at": "am",
    // Three consequences, because the silence reaches the figures at a
    // different pace than it reaches the present moment. The figures are the
    // year's: while the silent direction still has rows in them, "yearly"
    // says how far they reach; only a direction absent from the whole year
    // makes them a PART-corridor sample ("partial"), or simply OLD ("none")
    // when no direction is left.
    "coverage-body-yearly": (missing, since) =>
      `Aktuell keine Fahrten von ${COVERAGE_ORIGIN} Richtung ${missing} ` +
      `auf der Stammstrecke${since}. Die Jahreswerte enthalten Richtung ` +
      `${missing} daher nur bis dahin.`,
    "coverage-body-partial": (missing, since, covered) =>
      `Aktuell keine Fahrten von ${COVERAGE_ORIGIN} Richtung ${missing} ` +
      `auf der Stammstrecke${since}. Die Kennzahlen decken daher nur ` +
      `Richtung ${covered} ab — kein Korridor-Gesamtwert.`,
    "coverage-body-none": (missing, since) =>
      `Aktuell keine Fahrten von ${COVERAGE_ORIGIN} Richtung ${missing} ` +
      `auf der Stammstrecke${since}. Die Kennzahlen beruhen daher ` +
      "ausschließlich auf älteren Daten.",
  };
  const COVERAGE_TEXT_EN = {
    "coverage-title": "Limited coverage",
    "coverage-last-one": "last seen",
    "coverage-last-many": "last seen:",
    "coverage-at": "on",
    "coverage-body-yearly": (missing, since) =>
      `Currently no departures from ${COVERAGE_ORIGIN} towards ${missing} ` +
      `on the Stammstrecke${since}. The yearly figures therefore include ` +
      `the ${missing} direction only up to then.`,
    "coverage-body-partial": (missing, since, covered) =>
      `Currently no departures from ${COVERAGE_ORIGIN} towards ${missing} ` +
      `on the Stammstrecke${since}. The figures therefore cover the ` +
      `${covered} direction only — not a whole-corridor total.`,
    "coverage-body-none": (missing, since) =>
      `Currently no departures from ${COVERAGE_ORIGIN} towards ${missing} ` +
      `on the Stammstrecke${since}. The figures therefore rest on older ` +
      "data alone.",
  };
  function cov(key) {
    const dict = getLang() === "en" ? COVERAGE_TEXT_EN : COVERAGE_TEXT_DE;
    return dict[key] || COVERAGE_TEXT_DE[key] || key;
  }

  // Mirrors ``render_readme_stammstrecke_live_block`` in
  // ``scripts/generate_markdown_stats.py``: arithmetic mean of
  // ``delay_minutes`` over the rolling 60-minute window.
  //
  // The mean used to be computed here, from the raw ledger, with the
  // stated reason that it would otherwise go stale between the
  // workflow's 30-minute refreshes. That reasoning did not hold: the
  // ledger this page read is written by that same workflow, so the
  // browser was recomputing a window over data that only ever changed
  // on the tick. The value now arrives pre-computed from the tick that
  // appended the newest sample — the same freshness, 705 KB cheaper.
  //
  // An empty window says "Keine Fahrten" only while the monitor's last
  // successful fetch lies inside that window (``coverage.poll``): then the
  // fetch worked and no train ran, e.g. every train of the hour cancelled
  // (track damage Meidling–Liesing, 30.10.2026) or the night pause. Without
  // such a fetch nothing was measured and the tile keeps "N/A". Operator
  // decision 2026-10-30; mirrors ``_live_fetch_covers`` in
  // ``scripts/generate_markdown_stats.py``.
  function renderStammstreckeLiveTile(live, poll) {
    const node = $("#stammstrecke-live-avg");
    if (!node) return;
    const avg = live ? live.avg_delay_minutes : undefined;
    // ``null`` is the generator's explicit "window held no sample" and
    // must never render as a zero-minute delay.
    if (Number.isFinite(avg)) {
      node.textContent = `${nf1.format(avg)} min`;
      return;
    }
    const windowMs = numberOr(live && live.window_minutes, 60) * 60000;
    const lastOk = poll && poll.last_success ? Date.parse(poll.last_success) : NaN;
    const fetchCovers = Number.isFinite(lastOk) && Date.now() - lastOk <= windowMs;
    node.textContent = ct(fetchCovers ? "tile-no-trains" : "tile-na");
  }

  function resetStammstreckeLiveTile(text) {
    const node = $("#stammstrecke-live-avg");
    if (node) node.textContent = text;
  }

  function renderStammstreckeStats(year, stats) {
    setYearLabels(year);

    renderStammstreckeLiveTile(stats.live, stats.coverage && stats.coverage.poll);

    const avgByWeekday = stats.by_weekday_avg || {};
    const avgByHour = stats.by_hour_avg || {};
    const byDirection = stats.by_direction || {};
    // The count of severe delays is now made in Python, against
    // ``DELAY_THRESHOLD_MINUTES``. ``DELAY_THRESHOLD_MIN`` still labels
    // the tile and is pinned to that same constant by
    // ``tests/test_dashboard_delay_threshold.py``, so the label cannot
    // describe a different threshold than the number above it.
    renderKpis("#stammstrecke-kpis", [
      { label: ct("kpi-observations"),
        value: nfInt.format(numberOr(stats.total_observations, 0)),
        sub: `${ct("sub-year")} ${year}` },
      { label: ct("kpi-avg-delay"),
        value: `${nf1.format(numberOr(stats.avg_delay_minutes, 0))} min`,
        sub: ct("sub-all-observations") },
      { label: ct("kpi-max-delay"),
        value: `${nf1.format(numberOr(stats.max_delay_minutes, 0))} min`,
        sub: ct("sub-tick-value") },
      { label: ct("kpi-heavy-delays"),
        value: nfInt.format(numberOr(stats.threshold_exceedances, 0)),
        sub: `> ${DELAY_THRESHOLD_MIN} min` },
    ]);

    renderBars("#stammstrecke-hour",
      HOURS.map((h) => [`${h}:00`, bucket(avgByHour, h)]),
      { unit: " min", variant: "delay", formatValue: (v) => nf1.format(v) });

    renderBars("#stammstrecke-weekday",
      WEEKDAYS.map((d) => [weekdayLong(d), bucket(avgByWeekday, d)]),
      { unit: " min", variant: "delay", formatValue: (v) => nf1.format(v) });

    renderBars("#stammstrecke-direction",
      sortedEntries(byDirection),
      { unit: "", formatValue: (v) => nfInt.format(v) });

    renderCoverageNotice(stats.coverage, byDirection);
  }

  // Audit B.1: the northbound direction stopped reporting on 2026-08-14 while
  // the southbound one kept going, and every published figure silently became
  // a half-corridor sample. This banner says so on the site, using the same
  // rule as ``render_direction_coverage_note`` in
  // ``scripts/generate_markdown_stats.py``, evaluated here against the
  // reader's own clock.
  //
  // Derived from the data, never hard-coded: the summary ships the
  // *evidence* per direction (rows in the window, last timestamp seen,
  // peer journeys since) plus the thresholds, and the verdict is made here.
  // That way a page left open overnight re-decides on every refresh instead
  // of showing a frozen answer, and neither surface can drift into a rule of
  // its own.
  //
  // The banner names the date the direction was last seen and disappears by
  // itself the moment it reports again: the closure is temporary and the
  // restart has to show up without anyone editing the page.
  //
  // One hour without a journey is the trigger, but it is only believed once
  // something proves trains were actually running: a peer direction that
  // logged ``peer_rows_required`` journeys in the same stretch, or — when no
  // peer is left — a corridor silence longer than any normal pause. Without
  // that gate the banner would appear every night, when the Stammstrecke
  // stops for about 3:41 h by timetable.
  //
  // The figures this page shows are the year's (``by_direction`` and every
  // KPI), so whether the silence reaches them is judged against the year's
  // rows per direction, ``yearByDirection``; ``rows_in_window`` is the
  // README's 30-day window. Judged against the window, the banner said the
  // figures covered Meidling only, right above a direction chart that showed
  // Praterstern with 3,831 observations.
  function renderCoverageNotice(coverage, yearByDirection) {
    const node = $("#stammstrecke-coverage");
    if (!node) return;
    const hide = () => {
      node.hidden = true;
      node.textContent = "";
    };
    const perDirection = (coverage && coverage.directions) || {};
    // Fall back to the Python defaults when an older cached summary omits
    // the thresholds; the rule must not silently become "never fires".
    const silenceMs = numberOr(coverage && coverage.silence_hours, 1) * 3600000;
    const corridorMs =
      numberOr(coverage && coverage.corridor_silence_hours, 8) * 3600000;
    const peerNeeded = numberOr(coverage && coverage.peer_rows_required, 6);

    const rowsIn = (dir) => bucket(yearByDirection, dir);
    const lastSeenOf = (dir) => {
      const entry = perDirection[dir];
      const stamp = entry && entry.last_seen ? Date.parse(entry.last_seen) : NaN;
      return Number.isFinite(stamp) ? stamp : null;
    };
    const peerRowsSince = (dir) => {
      const entry = perDirection[dir];
      return entry ? numberOr(entry.peer_rows_since, 0) : 0;
    };

    const now = Date.now();
    // A direction never measured is not a direction that stopped — a fresh
    // deployment has nothing to caveat.
    const everSeen = STAMMSTRECKE_DIRECTIONS.filter((d) => lastSeenOf(d) !== null);
    const quiet = everSeen.filter((d) => now - lastSeenOf(d) >= silenceMs);
    if (!quiet.length) {
      hide();
      return;
    }
    // Peer route: trains demonstrably ran and this direction was not among
    // them. Corridor route: nothing left to compare against, so only a
    // stretch longer than any healthy pause counts — and only while the
    // monitor's own fetch works (``coverage.poll``, operator decision
    // 2026-10-10): a dark corridor behind a broken fetch is not "no
    // departures", it is the health check's to report. Mirrors
    // ``fetch_working`` in ``find_silent_coverage_directions``. An older
    // summary without ``poll`` keeps the earlier behaviour.
    let fetchWorking = true;
    const poll = coverage && coverage.poll;
    if (poll) {
      const lastOk = poll.last_success ? Date.parse(poll.last_success) : NaN;
      const staleMs = numberOr(poll.stale_hours, 3) * 3600000;
      fetchWorking = Number.isFinite(lastOk) && now - lastOk <= staleMs;
    }
    let missing = quiet.filter((d) => peerRowsSince(d) >= peerNeeded);
    if (
      !missing.length &&
      fetchWorking &&
      quiet.length === everSeen.length &&
      quiet.every((d) => now - lastSeenOf(d) >= corridorMs)
    ) {
      missing = quiet.slice();
    }
    if (!missing.length) {
      hide();
      return;
    }
    missing = STAMMSTRECKE_DIRECTIONS.filter((d) => missing.indexOf(d) !== -1);

    const joiner = getLang() === "en" ? " and " : " und ";
    // Same date format as the tiles ("14.08.2026", "14 Aug 2026").
    const onDate = (stamp) => dtfDate.format(new Date(stamp));
    // One silent direction gets the short form; several get one date each,
    // because a single date would otherwise stand for a sentence covering
    // all of them.
    const dated = missing.filter((d) => lastSeenOf(d) !== null);
    let since = "";
    if (dated.length === 1 && missing.length === 1) {
      since = ` (${cov("coverage-last-one")} ${onDate(lastSeenOf(dated[0]))})`;
    } else if (dated.length) {
      const inner = dated
        .map((d) => `${d} ${cov("coverage-at")} ${onDate(lastSeenOf(d))}`)
        .join(", ");
      since = ` (${cov("coverage-last-many")} ${inner})`;
    }

    const missingTxt = missing.join(joiner);
    // Does the silence reach the figures below, or only the present moment?
    const absent = missing.filter((d) => rowsIn(d) === 0);
    const covered = STAMMSTRECKE_DIRECTIONS.filter((d) => rowsIn(d) > 0);
    let body;
    if (!absent.length) {
      // Silent now, but still in the year's figures: say how far they reach.
      body = cov("coverage-body-yearly")(missingTxt, since);
    } else if (covered.length) {
      body = cov("coverage-body-partial")(missingTxt, since, covered.join(joiner));
    } else {
      // The whole corridor is absent from the window: the figures are not
      // restricted, they are old. This used to be suppressed on the grounds
      // that the freshness checks own it — but those live in
      // ``scripts/health_check.py`` and never reach a visitor, so the page
      // simply showed stale numbers with no caveat at all.
      body = cov("coverage-body-none")(missingTxt, since);
    }
    node.textContent = "";
    const strong = document.createElement("strong");
    strong.textContent = `⚠️ ${cov("coverage-title")}: `;
    node.appendChild(strong);
    node.appendChild(document.createTextNode(body));
    node.hidden = false;
  }

  Object.assign(WienOepnv, { renderStammstreckeStats, resetStammstreckeLiveTile });
})();
