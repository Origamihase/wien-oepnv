import pytest
from src.utils.stations import text_has_vienna_connection

@pytest.mark.parametrize(
    "text",
    [
        "REX 8: Marchegg ↔ Bratislava hl.st.",
        "Wegen Bauarbeiten der ZSR zwischen Marchegg Bahnhof und Bratislava hl.st.",
        "Villach ↔ Villach Westbahnhof",
        "Wegen eines Rettungseinsatzes sind zwischen Villach Hbf und Villach Westbahnhof keine Fahrten möglich.",
        "Graz Hbf ↔ Graz Ostbahnhof",
        "Linz Hbf ↔ Wels Hbf",
        "Innsbruck Westbahnhof gesperrt",
        "Hadersdorf am Kamp",
        "Zugausfall: Bruck an der Leitha",
        "Zugausfall: Bratislava hl.st.",
        "St. Pölten Hbf ist groß.",
    ],
)
def test_text_has_vienna_connection_false(text: str) -> None:
    assert text_has_vienna_connection(text) is False

@pytest.mark.parametrize(
    "text",
    [
        "Wien Hbf ↔ Bratislava hl.st.",
        "S-Bahn Wien: Störung zwischen Wien Mitte und Floridsdorf",
        "REX: Marchegg ↔ Wien Praterstern",
        "Meidling gesperrt",
        "Störung auf der U6",
        "Flughafen Wien: Zubringerbus ausgefallen",
        "Stockerau ↔ Wien Franz-Josefs-Bahnhof",
        "Zugausfall: Bruck an der Leitha ↔ Wien",
        "REX 8: Marchegg ↔ Bratislava hl.st. via Wien",
    ],
)
def test_text_has_vienna_connection_true(text: str) -> None:
    assert text_has_vienna_connection(text) is True

