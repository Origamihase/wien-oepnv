"""Regression-Test für die 900100-Aspern-Nord-Regression.

Stellt sicher, dass kein Alias in STATIC_VOR_ENTRIES (in scripts/update_vor_stations.py)
mit bst_id oder bst_code einer Station kollidiert, die eine andere vor_id-Identität
in data/stations.json hat.

Hintergrund: Commit 7881373 (2025-10-15, "Fix VOR station metadata for airport
and Aspern Nord") hatte einen Override-Eintrag für Wien Aspern Nord ergänzt, der
'900100' als Alias führte und zusätzlich bst_id/bst_code=900100 setzte. Das
sind in Wahrheit die Werte für Wien Hauptbahnhof, der den ursprünglichen
Cross-Station-Alias-Konflikt im validate_stations-Lauf produzierte. PR #1082
hat das Symptom in data/stations.json bereinigt, den Code-Verursacher aber
nicht angetastet — bei jedem Lauf von update-stations.yml wurde der Konflikt
re-injiziert. Der Override-Eintrag wurde entfernt und dieser Test verhindert
die Wiedereinführung in dieser oder einer ähnlichen Klasse.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_stations() -> list[dict]:
    raw = (REPO_ROOT / "data" / "stations.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict):
        return list(data.get("stations", []))
    if isinstance(data, list):
        return data
    return []


def _load_static_vor_entries() -> list[dict]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.update_vor_stations import STATIC_VOR_ENTRIES

    return [dict(entry) for entry in STATIC_VOR_ENTRIES]


def test_static_vor_entries_aliases_no_collision_with_other_station_bst_id() -> None:
    """Kein STATIC_VOR_ENTRIES-Alias darf bst_id oder bst_code einer
    anderen Station (= einer Station mit anderer vor_id) sein.

    vor_id wird als Identitäts-Schlüssel genutzt statt name, weil Namens-
    Varianten (z.B. 'Wiener Neustadt Hbf' vs 'Wiener Neustadt Hauptbahnhof')
    sonst falsch positives erzeugen würden.
    """
    stations = _load_stations()

    # Map: vor_id -> set der eigenen bst_id/bst_code-Werte.
    # Wir nehmen NICHT vor_id selbst auf, weil ein STATIC-Eintrag legitim
    # seine eigene vor_id als Alias führen darf.
    vor_id_to_canonical_ids: dict[str, set[str]] = {}
    for station in stations:
        if not isinstance(station, dict):
            continue
        vor_id = str(station.get("vor_id") or "").strip()
        if not vor_id:
            continue
        bucket = vor_id_to_canonical_ids.setdefault(vor_id, set())
        for key in ("bst_id", "bst_code"):
            value = station.get(key)
            if value:
                bucket.add(str(value).strip())

    static_entries = _load_static_vor_entries()

    failures: list[str] = []
    for entry in static_entries:
        entry_vor_id = str(entry.get("vor_id") or "").strip()
        entry_name = str(entry.get("name") or "<unnamed>")
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            alias_str = str(alias).strip()
            if not alias_str:
                continue
            for other_vor_id, other_ids in vor_id_to_canonical_ids.items():
                if other_vor_id == entry_vor_id:
                    continue
                if alias_str in other_ids:
                    owner_name = next(
                        (
                            str(s.get("name") or "<unnamed>")
                            for s in stations
                            if isinstance(s, dict)
                            and str(s.get("vor_id") or "").strip() == other_vor_id
                        ),
                        "<unknown>",
                    )
                    failures.append(
                        f"STATIC_VOR_ENTRIES[name={entry_name!r}, vor_id={entry_vor_id!r}] "
                        f"hat Alias {alias_str!r}, das mit bst_id/bst_code von "
                        f"{owner_name!r} (vor_id={other_vor_id!r}) kollidiert."
                    )

    assert not failures, "\n".join(failures)
