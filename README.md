# Wien ÖPNV Feed

Störungen und Einschränkungen für den Großraum Wien aus offiziellen Quellen.

## Entwicklung/Tests lokal

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -u src/build_feed.py  # erzeugt docs/feed.xml
```

Der erzeugte Feed liegt unter `docs/feed.xml`.
