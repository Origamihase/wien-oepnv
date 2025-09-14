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
      # ---- Allgemein ----
      PYTHONUNBUFFERED: "1"
      OUT_PATH: docs/feed.xml
      FEED_TITLE: "ÖPNV Störungen Wien & Umgebung"
      FEED_LINK: "https://github.com/${{ github.repository }}"
      FEED_DESC: "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"
      LOG_LEVEL: INFO
      DESCRIPTION_CHAR_LIMIT: "170"
      FRESH_PUBDATE_WINDOW_MIN: "5"
      MAX_ITEMS: "60"
      MAX_ITEM_AGE_DAYS: "365"
      ABSOLUTE_MAX_AGE_DAYS: "540"
      ACTIVE_GRACE_MIN: "10"

      # ---- Wiener Linien (bleibt wie gehabt) ----
      WL_ENABLE: "1"

      # ---- VOR/VAO (Rail + Regional-/Schnellbus; kein WL-Stadtbus, keine U-Bahn/Tram) ----
      VOR_ENABLE: "1"
      VAO_ACCESS_ID: ${{ secrets.VAO_ACCESS_ID }}
      VAO_API_BASE: "https://routenplaner.verkehrsauskunft.at/vao/restproxy/1.0"
      # products= 2^0 (Zug) + 2^1 (S-Bahn) + 2^5 (Schnellbus) + 2^6 (Regionalbus) = 99
      VOR_PRODUCTS_MASK: "99"
      # Umkreissuche: nur Stationen (type=S), 2 km Radius pro Gitterpunkt
      VOR_NEARBY_TYPE: "S"
      VOR_NEARBY_R: "2000"
      # Bounding Box Wien (inkl. Speckgürtel): lon 16.18–16.65, lat 48.10–48.35
      VOR_LON_MIN: "16.18"
      VOR_LON_MAX: "16.65"
      VOR_LAT_MIN: "48.10"
      VOR_LAT_MAX: "48.35"
      # Gitter-Auflösung in Grad (~0.02 ≈ 2.2 km in N-S)
      VOR_GRID_STEP: "0.02"
      # Sicherheits-Limits
      VOR_MAX_PER_CELL: "200"
      VOR_MAX_STATIONS_PER_RUN: "2000"
      # Datei, die an build_feed.py übergeben wird
      VOR_STATION_IDS_FILE: "cache/vor_station_ids.json"

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Discover VOR station IDs (Rail + Regional-/Schnellbus)
        if: env.VOR_ENABLE == '1'
        env:
          VAO_ACCESS_ID: ${{ env.VAO_ACCESS_ID }}
          VAO_API_BASE: ${{ env.VAO_API_BASE }}
          VOR_PRODUCTS_MASK: ${{ env.VOR_PRODUCTS_MASK }}
          VOR_NEARBY_TYPE: ${{ env.VOR_NEARBY_TYPE }}
          VOR_NEARBY_R: ${{ env.VOR_NEARBY_R }}
          VOR_LON_MIN: ${{ env.VOR_LON_MIN }}
          VOR_LON_MAX: ${{ env.VOR_LON_MAX }}
          VOR_LAT_MIN: ${{ env.VOR_LAT_MIN }}
          VOR_LAT_MAX: ${{ env.VOR_LAT_MAX }}
          VOR_GRID_STEP: ${{ env.VOR_GRID_STEP }}
          VOR_MAX_PER_CELL: ${{ env.VOR_MAX_PER_CELL }}
          VOR_MAX_STATIONS_PER_RUN: ${{ env.VOR_MAX_STATIONS_PER_RUN }}
          VOR_STATION_IDS_FILE: ${{ env.VOR_STATION_IDS_FILE }}
        run: |
          set -euo pipefail
          mkdir -p "$(dirname "${VOR_STATION_IDS_FILE}")"

          if [ -z "${VAO_ACCESS_ID:-}" ]; then
            echo "VAO_ACCESS_ID nicht gesetzt – VOR-Discovery wird übersprungen."
            echo '{"extIds":[]}' > "${VOR_STATION_IDS_FILE}"
            exit 0
          fi

          python - << 'PY'
          import os, sys, math, json, time
          import xml.etree.ElementTree as ET
          import urllib.parse, urllib.request

          base   = os.environ["VAO_API_BASE"].rstrip("/")
          acc    = os.environ["VAO_ACCESS_ID"]
          prod   = os.environ["VOR_PRODUCTS_MASK"]
          typ    = os.environ.get("VOR_NEARBY_TYPE","S")
          rad    = int(os.environ.get("VOR_NEARBY_R","2000"))
          lon0   = float(os.environ["VOR_LON_MIN"])
          lon1   = float(os.environ["VOR_LON_MAX"])
          lat0   = float(os.environ["VOR_LAT_MIN"])
          lat1   = float(os.environ["VOR_LAT_MAX"])
          step   = float(os.environ.get("VOR_GRID_STEP","0.02"))
          max_cell = int(os.environ.get("VOR_MAX_PER_CELL","200"))
          per_run = int(os.environ.get("VOR_MAX_STATIONS_PER_RUN","2000"))
          outp   = os.environ["VOR_STATION_IDS_FILE"]

          def q(url, params):
            u = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(u, headers={"Accept":"application/xml"})
            with urllib.request.urlopen(req, timeout=25) as r:
              return r.read()

          ext_ids = set()
          lats = []
          cur = lat0
          while cur <= lat1 + 1e-9:
            lats.append(round(cur, 6))
            cur += step
          lons = []
          cur = lon0
          while cur <= lon1 + 1e-9:
            lons.append(round(cur, 6))
            cur += step

          for la in lats:
            for lo in lons:
              try:
                xml = q(f"{base}/location.nearbystops", {
                  "accessId": acc,
                  "format": "xml",
                  "originCoordLat": f"{la:.6f}",
                  "originCoordLong": f"{lo:.6f}",
                  "type": typ,
                  "products": prod,
                  "r": str(rad),
                  "maxNo": str(max_cell),
                })
                # Parse StopLocation extId / mainMastExtId
                root = ET.fromstring(xml)
                for sl in root.findall(".//StopLocation"):
                  ext = sl.attrib.get("mainMastExtId") or sl.attrib.get("extId")
                  if ext:
                    ext_ids.add(ext.strip())
              except Exception as e:
                # Robust weiter – Grid ist redundant
                print(f"[warn] nearbystops @ {la},{lo}: {e}", file=sys.stderr)
                time.sleep(0.2)

          ext_list = sorted(ext_ids)
          if per_run and len(ext_list) > per_run:
            # deterministische Auswahl (rollierend via Tageszahl)
            day_of_year = int(time.strftime("%j"))
            start = (day_of_year * 997) % len(ext_list)
            sel = ext_list[start:] + ext_list[:start]
            ext_list = sel[:per_run]

          os.makedirs(os.path.dirname(outp), exist_ok=True)
          with open(outp, "w", encoding="utf-8") as f:
            json.dump({"extIds": ext_list}, f, ensure_ascii=False, indent=2)

          print(f"VOR discovery: {len(ext_list)} extIds -> {outp}")
          PY

      - name: Build feed
        env:
          OUT_PATH: ${{ env.OUT_PATH }}
          FEED_TITLE: ${{ env.FEED_TITLE }}
          FEED_LINK: ${{ env.FEED_LINK }}
          FEED_DESC: ${{ env.FEED_DESC }}
          LOG_LEVEL: ${{ env.LOG_LEVEL }}
          DESCRIPTION_CHAR_LIMIT: ${{ env.DESCRIPTION_CHAR_LIMIT }}
          FRESH_PUBDATE_WINDOW_MIN: ${{ env.FRESH_PUBDATE_WINDOW_MIN }}
          MAX_ITEMS: ${{ env.MAX_ITEMS }}
          MAX_ITEM_AGE_DAYS: ${{ env.MAX_ITEM_AGE_DAYS }}
          ABSOLUTE_MAX_AGE_DAYS: ${{ env.ABSOLUTE_MAX_AGE_DAYS }}
          ACTIVE_GRACE_MIN: ${{ env.ACTIVE_GRACE_MIN }}
          WL_ENABLE: ${{ env.WL_ENABLE }}
          VOR_ENABLE: ${{ env.VOR_ENABLE }}
          VAO_ACCESS_ID: ${{ env.VAO_ACCESS_ID }}
          VAO_API_BASE: ${{ env.VAO_API_BASE }}
          VOR_STATION_IDS_FILE: ${{ env.VOR_STATION_IDS_FILE }}
        run: python -u src/build_feed.py

      - name: Validate feed (XML + GUID uniqueness)
        run: |
          python - <<'PY'
          import sys, xml.etree.ElementTree as ET
          p = "docs/feed.xml"
          try:
              tree = ET.parse(p)
          except ET.ParseError as e:
              print("XML parse error:", e)
              sys.exit(2)
          root = tree.getroot()
          assert root.tag == "rss", "Root is not <rss>"
          ch = root.find("channel")
          assert ch is not None, "Missing <channel>"
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
