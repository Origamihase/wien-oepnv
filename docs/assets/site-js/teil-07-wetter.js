/* Wien ÖPNV – Live-Dashboard, Modul 7 von 8: Wetter
 *
 * Das Wetter am Wiener Hauptbahnhof im Kopf der Seite (Open-Meteo).
 */
"use strict";

(() => {
  const { $, clear, getLang, nfInt, svg } = WienOepnv;

  // ----- Wetter (Wien) ------------------------------------------------
  // Open-Meteo (GeoSphere-Austria-Modell AROME). Wird im Browser des
  // Besuchers abgefragt — der CSP-``connect-src`` lässt nur
  // ``api.open-meteo.com`` zu — und gemeinsam mit den Verkehrsdaten in
  // ``loadAll()`` aktualisiert. ``loadWeather`` probiert zwei
  // Request-Formen der Reihe nach (siehe dort): primär den dokumentierten
  // ``/v1/forecast``-Endpunkt mit ``models=geosphere_arome_austria``,
  // dann Best-Match als Notnagel — so zeigt das Widget möglichst immer
  // einen Wert.
  // Abfrage-Koordinaten: Wien Hauptbahnhof aus dem Stationsverzeichnis
  // (data/stations.json — Eintrag "Wien Hauptbahnhof", bst_id 900100 /
  // eva_nr 8103000; Koordinate aus dem ÖBB-GeoNetz, CC BY 3.0 AT, seit
  // 16.10.2026 statt der früheren Google-Places-Koordinate).
  const WEATHER_API = "https://api.open-meteo.com/v1";
  const WEATHER_MODEL = "geosphere_arome_austria";
  const WEATHER_LAT = "48.185201";
  const WEATHER_LON = "16.376296";

  // Weather-widget strings rendered dynamically (icon tooltip + aria
  // label), analogous to CHART_TEXT — NOT covered by ``data-i18n`` because
  // the header widget's text is built at runtime by ``renderWeather``. The
  // condition keys mirror the buckets returned by ``weatherCondition``.
  const WEATHER_TEXT_DE = {
    aria: "Aktuelles Wetter am Wiener Hbf",
    credit: "Wetterdaten von Open-Meteo.com",
    clear: "klar",
    partly: "teils bewölkt",
    cloudy: "bewölkt",
    fog: "Nebel",
    rain: "Regen",
    snow: "Schnee",
    thunder: "Gewitter",
  };
  const WEATHER_TEXT_EN = {
    aria: "Current weather at Wien Hbf",
    credit: "Weather data by Open-Meteo.com",
    clear: "clear",
    partly: "partly cloudy",
    cloudy: "cloudy",
    fog: "fog",
    rain: "rain",
    snow: "snow",
    thunder: "thunderstorm",
  };
  function wt(key) {
    const dict = getLang() === "en" ? WEATHER_TEXT_EN : WEATHER_TEXT_DE;
    return dict[key] || WEATHER_TEXT_DE[key] || key;
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
  // ``feedState``) so the tooltip/aria label follow ``getLang()``
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
    // The widget links to open-meteo.com (their CC BY 4.0 terms ask for a
    // link next to every place the data appear), so the name says whose
    // data the reading is.
    const label = `${wt("aria")}: ${tempText}, ${wt(condition)} – ${wt("credit")}`;
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

  // Map an Open-Meteo response to the compact ``{temp, code, isDay}``
  // reading. Prefers the ``current`` block (single-model responses use
  // unsuffixed keys) and falls back to the nearest-now hourly entry, so
  // the same parser handles both request shapes below.
  function readingFromData(data) {
    const cur = data && data.current;
    if (cur && typeof cur.temperature_2m === "number"
        && Number.isFinite(cur.temperature_2m)) {
      const code = cur.weather_code;
      const day = cur.is_day;
      return {
        temp: cur.temperature_2m,
        code: typeof code === "number" && Number.isFinite(code) ? code : null,
        isDay: typeof day === "number" ? day !== 0 : null,
      };
    }
    return weatherReadingFromHourly(data && data.hourly);
  }

  async function fetchWeather(url, signal) {
    const res = await fetch(url, {
      cache: "no-cache",
      credentials: "omit",
      redirect: "follow",
      signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const reading = readingFromData(data);
    if (!reading) throw new Error("weather payload without usable data");
    return reading;
  }

  async function loadWeather(signal) {
    const loc = `latitude=${WEATHER_LAT}&longitude=${WEATHER_LON}`;
    const current = "current=temperature_2m,weather_code,is_day";
    // Tried in order; the first that yields a value wins (every URL stays
    // on api.open-meteo.com, the only host the CSP allows). GeoSphere data
    // is requested via the documented ``/v1/forecast`` + ``models=``, and a
    // plain best-match request is the last resort so the widget still shows
    // a value even if the GeoSphere model identifier changes. A third form,
    // ``/v1/geosphere_arome_austria``, answered 404 on every call (checked
    // live on 17.10.2026) and only added a failed request to the console
    // whenever the first form failed, so it is gone.
    const urls = [
      `${WEATHER_API}/forecast?${loc}&${current}&models=${WEATHER_MODEL}`,
      `${WEATHER_API}/forecast?${loc}&${current}`,
    ];
    let reading = null;
    let lastErr = null;
    for (const url of urls) {
      try {
        reading = await fetchWeather(url, signal);
        break;
      } catch (err) {
        if (err && err.name === "AbortError") throw err;
        lastErr = err;
      }
    }
    if (!reading) throw lastErr || new Error("weather unavailable");
    weatherState = reading;
    renderWeather();
  }

  Object.assign(WienOepnv, { loadWeather, renderWeather });
})();
