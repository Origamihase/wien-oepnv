# Wien ÖPNV Feed

Störungen und Einschränkungen für den Großraum Wien aus offiziellen Quellen.

## Erweiterungen

Der RSS-Feed deklariert den Namespace `ext` (`xmlns:ext="https://wien-oepnv.example/schema"`) für zusätzliche Metadaten:

- `ext:first_seen`: Zeitpunkt, wann eine Meldung erstmals im Feed aufgetaucht ist.
- `ext:starts_at`: Beginn der Störung bzw. Maßnahme.
- `ext:ends_at`: Ende der Störung bzw. Maßnahme.

## Entwicklung/Tests lokal

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -u src/build_feed.py  # erzeugt docs/feed.xml
```

Der erzeugte Feed liegt unter `docs/feed.xml`.
