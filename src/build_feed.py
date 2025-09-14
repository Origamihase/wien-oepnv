name: Build RSS

on:
  workflow_dispatch:
  schedule:
    - cron: "*/30 * * * *"   # alle 30 Minuten (UTC)

permissions:
  contents: write

concurrency:
  group: build-rss
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest

    env:
      # --- Allgemein / Feed ---
      OUT_PATH: docs/feed.xml
      FEED_TITLE: "ÖPNV Störungen Wien & Umgebung"
      FEED_LINK: "https://github.com/${{ github.repository }}"
      FEED_DESC: "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"
      LOG_LEVEL: INFO
      DESCRIPTION_CHAR_LIMIT: "170"
      MAX_ITEMS: "60"
      MAX_ITEM_AGE_DAYS: "365"
      ABSOLUTE_MAX_AGE_DAYS: "540"
      ACTIVE_GRACE_MIN: "10"

      # --- Provider-Schalter ---
      WL_ENABLE: "1"       # Wiener Linien
      OEBB_ENABLE: "1"     # ÖBB-RSS
      VOR_ENABLE: "1"      # VOR/VAO optional

      # --- Wiener Linien (Basis-URL aus Secret, Fallback im Code) ---
      WL_RSS_URL: ${{ secrets.WL_RSS_URL }}

      # --- ÖBB-RSS (aus Secret) ---
      OEBB_RSS_URL: ${{ secrets.OEBB_RSS_URL }}
      OEBB_ONLY_VIENNA: "1"

      # --- VOR/VAO Zugang (Discovery nur wenn gesetzt) ---
      VOR_ACCESS_ID: ${{ secrets.VOR_ACCESS_ID }}    # leer = Discovery übersprungen
      VOR_BASE: "https://routenplaner.verkehrsauskunft.at/vao/restproxy"
      VOR_VERSION: "v1.3"

      # --- VOR Feintuning ---
      VOR_ALLOW_BUS: "1"
      VOR_BUS_INCLUDE_REGEX: "(?:\\b[2-9]\\d{2,4}\\b)"
      VOR_BUS_EXCLUDE_REGEX: "^(?:N?\\d{1,2}[A-Z]?)$"
      VOR_MAX_STATIONS_PER_RUN: "3"
      VOR_ROTATION_INTERVAL_SEC: "1800"
      VOR_STATION_IDS: ""

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      # ------------------------------------------------------------
      # (Optional) VOR Stationen (nur falls Zugang vorhanden)
      # ------------------------------------------------------------
      - name: Discover VOR Station IDs for Vienna (PLZ 1010–1230)
        if: ${{ env.VOR_ACCESS_ID != '' }}
        shell: bash
        run: |
          set -euo pipefail
          FILE="data/vor_station_ids_wien.txt"
          mkdir -p data
          if [[ -f "$FILE" ]]; then
            IDS="$(tr -d '\r\n ' < "$FILE")"
            echo "VOR_STATION_IDS=$IDS" >> "$GITHUB_ENV"
            exit 0
          fi
          python - <<'PY'
          import os, sys, time, xml.etree.ElementTree as ET
          import requests
          ACCESS = os.environ.get("VOR_ACCESS_ID","").strip()
          BASE   = os.environ.get("VOR_BASE","").rstrip("/")
          VER    = os.environ.get("VOR_VERSION","v1.3").strip()
          OUT    = "data/vor_station_ids_wien.txt"
          if not ACCESS: sys.exit(0)
          session = requests.Session()
          session.headers.update({"Accept":"application/xml","User-Agent":"origamihase-wien-oepnv/auto-discover"})
          url = f"{BASE}/{VER}/location.name"
          plz_list = ["1010","1020","1030","1040","1050","1060","1070","1080","1090",
                      "1100","1110","1120","1130","1140","1150","1160","1170","1180","1190",
                      "1200","1210","1220","1230"]
          ext_ids = set()
          for plz in plz_list:
              params = {"accessId":ACCESS,"format":"xml","input":f"{plz} Wien","type":"S","stations":"49","maxNo":"200"}
              try:
                  r = session.get(url, params=params, timeout=12)
                  if r.status_code >= 400 or not r.content: continue
                  root = ET.fromstring(r.content)
                  for sl in root.findall(".//StopLocation"):
                      ext = (sl.get("extId") or sl.get("id") or "").strip()
                      name = (sl.get("name") or "").strip()
                      if name.startswith("Wien") and ext: ext_ids.add(ext)
              except Exception:
                  continue
              time.sleep(0.2)
          if not ext_ids: sys.exit(0)
          with open(OUT,"w",encoding="utf-8") as f:
              f.write(",".join(sorted(ext_ids)) + "\n")
          PY
          if [[ -f "$FILE" ]]; then
            IDS="$(tr -d '\r\n ' < "$FILE")"
            echo "VOR_STATION_IDS=$IDS" >> "$GITHUB_ENV"
          else
            echo "VOR_STATION_IDS=" >> "$GITHUB_ENV"

      - name: Commit discovered VOR stations (if any)
        if: ${{ env.VOR_ACCESS_ID != '' }}
        uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "chore(vor): add/update Vienna station IDs"
          file_pattern: data/vor_station_ids_wien.txt
          branch: ${{ github.ref_name }}

      # ------------------------------------------------------------
      # Feed bauen (WL + ÖBB-RSS + optional VOR)
      # ------------------------------------------------------------
      - name: Build feed
        env:
          OUT_PATH: ${{ env.OUT_PATH }}
          FEED_TITLE: ${{ env.FEED_TITLE }}
          FEED_LINK: ${{ env.FEED_LINK }}
          FEED_DESC: ${{ env.FEED_DESC }}
          LOG_LEVEL: ${{ env.LOG_LEVEL }}
          DESCRIPTION_CHAR_LIMIT: ${{ env.DESCRIPTION_CHAR_LIMIT }}
          MAX_ITEMS: ${{ env.MAX_ITEMS }}
          MAX_ITEM_AGE_DAYS: ${{ env.MAX_ITEM_AGE_DAYS }}
          ABSOLUTE_MAX_AGE_DAYS: ${{ env.ABSOLUTE_MAX_AGE_DAYS }}
          ACTIVE_GRACE_MIN: ${{ env.ACTIVE_GRACE_MIN }}

          WL_ENABLE: ${{ env.WL_ENABLE }}
          OEBB_ENABLE: ${{ env.OEBB_ENABLE }}
          VOR_ENABLE: ${{ env.VOR_ENABLE }}

          # Wiener Linien (Basis-URL aus Secret)
          WL_RSS_URL: ${{ env.WL_RSS_URL }}

          # ÖBB-RSS
          OEBB_RSS_URL: ${{ env.OEBB_RSS_URL }}
          OEBB_ONLY_VIENNA: ${{ env.OEBB_ONLY_VIENNA }}

          # VOR/VAO
          VOR_ACCESS_ID: ${{ env.VOR_ACCESS_ID }}
          VOR_BASE: ${{ env.VOR_BASE }}
          VOR_VERSION: ${{ env.VOR_VERSION }}
          VOR_STATION_IDS: ${{ env.VOR_STATION_IDS }}
          VOR_ALLOW_BUS: ${{ env.VOR_ALLOW_BUS }}
          VOR_BUS_INCLUDE_REGEX: ${{ env.VOR_BUS_INCLUDE_REGEX }}
          VOR_BUS_EXCLUDE_REGEX: ${{ env.VOR_BUS_EXCLUDE_REGEX }}
          VOR_MAX_STATIONS_PER_RUN: ${{ env.VOR_MAX_STATIONS_PER_RUN }}
          VOR_ROTATION_INTERVAL_SEC: ${{ env.VOR_ROTATION_INTERVAL_SEC }}
        run: python -u src/build_feed.py

      - name: Validate feed (XML + GUID uniqueness)
        run: |
          python - <<'PY'
          import sys, xml.etree.ElementTree as ET
          p = "docs/feed.xml"
          try:
              tree = ET.parse(p)
          except ET.ParseError as e:
              print("XML parse error:", e); sys.exit(2)
          root = tree.getroot()
          assert root.tag == "rss", "Root is not <rss>"
          ch = root.find("channel"); assert ch is not None, "Missing <channel>"
          items = ch.findall("item")
          guids = [i.findtext("guid") or "" for i in items]
          if len(guids) != len(set(guids)):
              dupes = [g for g in set(guids) if guids.count(g) > 1]
              raise SystemExit(f"Duplicate GUIDs detected: {dupes}")
          print(f"Feed OK: {len(items)} items, GUIDs unique.")
          PY

      - name: Commit & push feed (only if changed)
        uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "chore(feed): update"
          file_pattern: docs/feed.xml
          branch: ${{ github.ref_name }}
