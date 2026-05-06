"""Regression tests for the second-round audit (bugs F/G/H).

- F: VOR's text-based Wien check used to drop legitimate Pendler-station
  messages (Flughafen Wien) because it only short-circuited on
  ``in_vienna``. Pendler nodes in the whitelist are now treated as
  inherently relevant.
- G: ``Strecke|Verbindung|Linie X — Y`` route phrasing was invisible to the
  route extractor, so Pendler ↔ Pendler descriptions like
  ``Strecke Mödling - Baden gesperrt`` slipped through the strict-route
  filter via the single-station fallback.
- H: ``deduplicate_fuzzy`` iterated date keys ``("pubdate", …)`` in lower
  case, but the items carry ``pubDate`` (camelCase) — peer merges silently
  kept the older timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.feed.merge import deduplicate_fuzzy
from src.providers.oebb import _extract_routes, _is_relevant
from src.providers.vor import _collect_from_board


# ---------------- Bug F: VOR Pendler-Filter ----------------

class TestVorPendlerFilter:
    """The VOR provider must keep messages from explicitly chosen Pendler
    stations even when the message text doesn't carry a Wien token."""

    def _payload_with_message(self, head: str, text: str) -> dict[str, object]:
        return {
            "DepartureBoard": {"Departure": []},
            "warnings": [
                {
                    "id": "msg1",
                    "head": head,
                    "text": text,
                    "act": "true",
                }
            ],
        }

    def test_flughafen_wien_facility_message_kept(self) -> None:
        # Real-world example: an elevator notice at the Pendler hub that
        # never explicitly names "Wien" — used to be silently dropped.
        items = _collect_from_board(
            "430470800",  # Flughafen Wien VOR ID
            self._payload_with_message(
                "Aufzug defekt",
                "Bahnsteig 3 Aufzug außer Betrieb.",
            ),
        )
        assert len(items) == 1

    def test_wien_hauptbahnhof_message_kept(self) -> None:
        # Sanity: in_vienna stations always pass.
        items = _collect_from_board(
            "490134900",  # Wien Hauptbahnhof VOR ID
            self._payload_with_message(
                "Bauarbeiten",
                "Gleis 12 gesperrt von 14:00 bis 18:00.",
            ),
        )
        assert len(items) == 1

    def test_unknown_station_still_requires_wien_text(self) -> None:
        # Defence in depth: a non-whitelist, non-Pendler station keeps the
        # text-based filter so a misconfigured VOR_STATION_NAMES entry
        # cannot flood the feed with off-topic messages.
        items = _collect_from_board(
            "999999999",  # Unknown ID — not in directory
            self._payload_with_message(
                "Aufzug defekt",
                "Bahnsteig 3 Aufzug außer Betrieb.",
            ),
        )
        assert len(items) == 0


# ---------------- Bug G: Strecke|Verbindung|Linie pattern ----------------

class TestStreckePattern:
    """``Strecke X — Y gesperrt`` and friends must feed the strict route
    classifier so Pendler ↔ Pendler descriptions are dropped consistently."""

    def test_strecke_pendler_pendler_dropped(self) -> None:
        assert _is_relevant("", "Strecke Mödling - Baden gesperrt") is False
        routes = _extract_routes("", "Strecke Mödling - Baden gesperrt")
        assert routes == [("Mödling", "Baden")]

    def test_verbindung_pendler_pendler_dropped(self) -> None:
        assert (
            _is_relevant("", "Verbindung Mödling - Baden unterbrochen") is False
        )

    def test_strecke_wien_pendler_kept(self) -> None:
        assert _is_relevant("", "Strecke Wien Hbf - Mödling gesperrt") is True

    def test_strecke_wien_distant_dropped(self) -> None:
        assert (
            _is_relevant("", "Strecke Wien Hbf - München Hbf gesperrt") is False
        )

    def test_abschnitt_keyword_supported(self) -> None:
        assert (
            _is_relevant("", "Abschnitt Wien Hbf - Mödling betroffen") is True
        )

    def test_hyphen_in_station_name_does_not_split(self) -> None:
        # Defence in depth: real station names with hyphens
        # ("Wien Mitte-Landstraße") must NOT be broken into "Wien Mitte"
        # and "Landstraße" by the new pattern.
        text = "Wien Mitte-Landstraße ist gesperrt"
        assert _extract_routes("", text) == []
        assert _is_relevant("", text) is True


class TestUeberViaBoundary:
    """Bug I: ``zwischen X und Y über Z`` extended Y into "Y über Z" because
    the via marker was not in the lookahead. The actual disrupted route is
    X↔Y; Z is just the intermediate stop on the line."""

    def test_ueber_terminates_route_endpoint(self) -> None:
        routes = _extract_routes(
            "",
            "Bauarbeiten zwischen Wien Hbf und Mödling über Wiener Neudorf",
        )
        assert routes == [("Wien", "Mödling")]

    def test_ueber_keeps_wien_pendler_relevant(self) -> None:
        # Without the fix this returned False because endpoint b was
        # "Mödling über Wiener Neudorf", which fails station_info lookup.
        assert (
            _is_relevant(
                "",
                "Bauarbeiten zwischen Wien Hbf und Mödling über Wiener Neudorf",
            )
            is True
        )

    def test_ueber_still_drops_wien_distant(self) -> None:
        # End-to-end: Wien-Salzburg via St. Pölten is still rejected as
        # Wien-Distant — extracting the right Y just exposes the strict
        # classification rule.
        assert (
            _is_relevant(
                "",
                "Bauarbeiten zwischen Wien Hbf und Salzburg über St. Pölten",
            )
            is False
        )

    def test_via_english_terminator_works(self) -> None:
        routes = _extract_routes(
            "",
            "Bauarbeiten zwischen Wien Hbf und Mödling via Wiener Neudorf",
        )
        assert routes == [("Wien", "Mödling")]


# ---------------- Bug H: pubDate case-sensitivity ----------------

class TestPubDateMerge:
    """Peer merges of two items for the same incident must surface the
    later pubDate so feed clients see the freshest timestamp."""

    def test_peer_merge_keeps_later_pubdate(self) -> None:
        items = [
            {
                "guid": "wl-1",
                "_identity": "wl|1",
                "source": "Wiener Linien",
                "title": "U6: Signalstörung Spittelau",
                "description": "Erste Meldung",
                "pubDate": datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc),
            },
            {
                "guid": "wl-2",
                "_identity": "wl|2",
                "source": "Wiener Linien",
                "title": "U6: Signalstörung Spittelau",
                "description": "Spätere Meldung mit Updates",
                "pubDate": datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc),
            },
        ]
        result = deduplicate_fuzzy(items)
        assert len(result) == 1
        assert result[0]["pubDate"] == datetime(
            2026, 5, 6, 11, 0, tzinfo=timezone.utc
        )

    def test_existing_pubdate_kept_when_newer(self) -> None:
        # The reverse case: incoming item is older — keep the existing
        # pubDate.
        items = [
            {
                "guid": "wl-a",
                "_identity": "wl|a",
                "source": "Wiener Linien",
                "title": "U6: Signalstörung Spittelau",
                "description": "Aktuelle Meldung",
                "pubDate": datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
            },
            {
                "guid": "wl-b",
                "_identity": "wl|b",
                "source": "Wiener Linien",
                "title": "U6: Signalstörung Spittelau",
                "description": "Frühere Meldung",
                "pubDate": datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc),
            },
        ]
        result = deduplicate_fuzzy(items)
        assert len(result) == 1
        assert result[0]["pubDate"] == datetime(
            2026, 5, 6, 12, 0, tzinfo=timezone.utc
        )
