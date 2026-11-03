/* Wien ÖPNV – Live-Dashboard, Modul 4 von 8: Feed
 *
 * Liest feed.xml / feed.en.xml und zeigt die Meldungen als Kacheln mit
 * Quellenfilter.
 */
"use strict";

(() => {
  const { $, clear, dtfDate, dtfFull, el, getLang, nfInt, rtf, safeHttpsUrl } = WienOepnv;

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
    if (err) throw new Error("err-feed-parse");
    const channel = doc.querySelector("channel");
    if (!channel) throw new Error("err-feed-no-channel");

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
        sourceName: get("source"),
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

  // Every item carries an RSS ``<source>`` naming where it comes from
  // (``_ITEM_SOURCES`` in src/feed_build/item_meta.py, German and English
  // names). It decides the tile, so the label says what the tag says: the
  // Stammstrecke monitor links to the ÖBB route information but comes from
  // VOR/VAO, and Stadt Wien construction works link to wien.gv.at. The link
  // and title patterns below only serve an item without a known tag.
  const SOURCE_TAGS = {
    "wiener linien": "wienerlinien",
    "öbb": "oebb",
    "stadt wien": "baustellen",
    "city of vienna": "baustellen",
    "vor/vao": "vor",
  };

  function detectSource(item) {
    const tagged = SOURCE_TAGS[(item.sourceName || "").trim().toLowerCase()];
    if (tagged) return tagged;
    const link = (item.link || "").toLowerCase();
    const haystack = `${item.link} ${item.title}`.toLowerCase();
    if (/(wienerlinien|wiener\s*linien|wl-disp|ogd_realtime)/.test(haystack)) return "wienerlinien";
    if (/(oebb|öbb|scotty)/.test(haystack)) return "oebb";
    // City-of-Vienna construction works → wien.gv.at/verkehr/baustellen
    // (since 17.10.2026; the data.gv.at dataset link before). Keyed
    // on the link (not the combined haystack) so a WL/ÖBB report whose
    // title merely mentions "Baustelle" is not misclassified — those are
    // already returned above.
    if (/(data\.gv\.at\/[^ ]*baustellen|wien\.gv\.at\/verkehr\/baustellen)/.test(link)) return "baustellen";
    if (/(vor\.at|verkehrsverbund|vao\.|anachb)/.test(haystack)) return "vor";
    return "other";
  }

  function sourceLabel(key) {
    if (key === "wienerlinien") return "Wiener Linien";
    if (key === "oebb") return "ÖBB";
    if (key === "baustellen") return getLang() === "en" ? "Construction" : "Baustellen";
    if (key === "vor") return "VOR/VAO";
    return getLang() === "en" ? "Other" : "Andere";
  }

  let feedState = { items: [], filter: "all", lastBuild: "" };

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
      const suffix = getLang() === "en" ? "active" : "aktiv";
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
    const titleText = item.title || (getLang() === "en" ? "(untitled)" : "(ohne Titel)");
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
    const labels = getLang() === "en"
      ? { begin: "Begin: ", until: "Until: ", firstSeen: "First seen: " }
      : { begin: "Beginn: ", until: "Bis: ", firstSeen: "Erstmals: " };
    const begin = itemBegin(item);
    if (begin) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: labels.begin }));
      const shown = isViennaMidnight(begin) ? dtfDate.format(begin) : dtfFull.format(begin);
      d.append(el("time", { attrs: { datetime: begin.toISOString() }, text: shown }));
      meta.append(d);
    }
    const ends = parseRfc2822(item.endsAt);
    if (ends) {
      const d = document.createElement("div");
      d.append(el("dfn", { text: labels.until }));
      d.append(el("time", { attrs: { datetime: ends.toISOString() }, text: dtfDate.format(ends) }));
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

  // "Beginn" and "Bis" say what the item's own time line says ("[Seit
  // 22.10.]", "[Am Mo 26.10.]", "[Bis 02.11.]", see ``_time_line_text`` in
  // src/feed_build/time_line.py), so a tile does not contradict its text.
  //
  // ``pubDate`` is not the begin: WL publishes planned events days ahead
  // ("Nationalfeiertag", published 16.10. for 26.10.) and ÖBB moves it on
  // with every update. ``starts_at`` is the begin. An earlier ``pubDate`` of
  // the same Vienna day is the minute WL entered the incident, which the
  // time line reads as its start too (``_since_time``), unless it sits on a
  // full hour, the mark of a pre-entered measure (``_scheduled_clock``;
  // Vienna's offsets are whole hours, so the UTC minute is Vienna's). The
  // time line still shows a full-hour ``starts_at`` of an incident cause
  // (``_incident_cause``, 2026-11-03); the tile shows that start too. A begin at 00:00
  // Vienna time marks a day and shows as a date. An end shows as a date
  // alone, like on the TV: its clock time is a buffer (23:59, 01:00, 04:00),
  // not an announced time.
  const dtfViennaDay = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Vienna", year: "numeric", month: "2-digit", day: "2-digit",
  });
  const dtfViennaClock = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Vienna", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });

  function itemBegin(item) {
    const start = parseRfc2822(item.startsAt);
    const pub = parseRfc2822(item.pubDate);
    if (!start) return pub;
    if (
      pub && pub < start && pub.getUTCMinutes() !== 0 &&
      dtfViennaDay.format(pub) === dtfViennaDay.format(start)
    ) {
      return pub;
    }
    return start;
  }

  function isViennaMidnight(date) {
    return dtfViennaClock.format(date) === "00:00";
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
    if (absMin < 1) return getLang() === "en" ? "just now" : "gerade eben";
    if (absMin < 60) return rtf.format(sign * Math.round(absMin), "minute");
    const hours = absMin / 60;
    if (hours < 24) return rtf.format(sign * Math.round(hours), "hour");
    const days = hours / 24;
    if (days < 30) return rtf.format(sign * Math.round(days), "day");
    return dtfDate.format(date);
  }

  Object.assign(WienOepnv, { feedState, parseFeed, parseRfc2822, renderFeed, sourceLabel });
})();
