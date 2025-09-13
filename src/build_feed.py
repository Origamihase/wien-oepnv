#!/usr/bin/env python3
import os, sys, logging
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from providers import wiener_linien, oebb, vor

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))

FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK  = os.getenv("FEED_LINK",  "https://github.com/Origamihase/wien-oepnv")
FEED_DESC  = os.getenv("FEED_DESC",  "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")
OUT_PATH   = os.getenv("OUT_PATH",   "docs/feed.xml")
MAX_ITEMS  = int(os.getenv("MAX_ITEMS", "200"))

def _rss_root(title: str, link: str, description: str):
    rss = Element("rss", version="2.0")
    ch  = SubElement(rss, "channel")
    SubElement(ch, "title").text = title
    SubElement(ch, "link").text = link
    SubElement(ch, "description").text = description
    SubElement(ch, "language").text = "de-AT"
    SubElement(ch, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    SubElement(ch, "ttl").text = "15"  # Reader-Hinweis: alle 15 Min refresh
    SubElement(ch, "generator").text = "wien-oepnv (GitHub Actions)"
    return rss, ch

def _add_item(ch, ev):
    it = SubElement(ch, "item")
    SubElement(it, "title").text = f"[{ev['source']}/{ev['category']}] {ev['title']}"
    SubElement(it, "link").text = ev.get("link") or FEED_LINK
    SubElement(it, "description").text = ev.get("description") or ""
    SubElement(it, "pubDate").text = format_datetime(ev["pubDate"])
    SubElement(it, "guid").text = ev["guid"]
    # Kategorien helfen beim Filtern im Reader
    for c in (ev.get("source"), ev.get("category")):
        if c:
            SubElement(it, "category").text = c

def _write_xml(elem, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(elem, encoding="utf-8")
    with open(path, "wb") as f:
        f.write(data)
    logging.info("Feed geschrieben: %s", path)

def main():
    rss, ch = _rss_root(FEED_TITLE, FEED_LINK, FEED_DESC)

    # Provider-Reihenfolge: WL (aktiv), ÖBB/VOR (optional)
    providers = (wiener_linien, oebb, vor)
    global_dedup: set[str] = set()  # quer über alle Provider
    total = 0

    for p in providers:
        try:
            events = p.fetch_events()
            logging.info("%s lieferte %d Items", p.__name__, len(events))
            for ev in events:
                if ev["guid"] in global_dedup:
                    continue
                global_dedup.add(ev["guid"])
                _add_item(ch, ev)
                total += 1
                if total >= MAX_ITEMS:
                    break
        except Exception as e:
            logging.exception("Provider-Fehler bei %s: %s", p.__name__, e)
        if total >= MAX_ITEMS:
            break

    _write_xml(rss, OUT_PATH)
    logging.info("Fertig: %d Items im Feed", total)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("Abbruch: %s", e)
        sys.exit(1)
