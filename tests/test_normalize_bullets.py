import pytest

from src.utils.text import normalize_bullets


@pytest.mark.parametrize(
    "text,expected",
    [
        ("bei • Station", "bei Station"),
        ("In • der Station", "In der Station"),
        ("An • der Haltestelle", "An der Haltestelle"),
        ("auf • dem Bahnsteig", "auf dem Bahnsteig"),
        ("Am • Rathaus", "Am Rathaus"),
        ("Vom • Bahnsteig", "Vom Bahnsteig"),
        ("zur • Station", "zur Station"),
        ("zum • Ausgang", "zum Ausgang"),
        ("Nach • Wien", "Nach Wien"),
        ("über • den Dächern", "über den Dächern"),
        ("ueber • die Brücke", "ueber die Brücke"),
        ("Gegen • den Verkehr", "Gegen den Verkehr"),
        ("durch • den Tunnel", "durch den Tunnel"),
        ("Bis • morgen", "Bis morgen"),
        ("ab • sofort", "ab sofort"),
        ("bei\n• Station", "bei\nStation"),
        ("In •\n der Station", "In\nder Station"),
    ],
)
def test_normalize_bullets_prepositions(text, expected):
    assert normalize_bullets(text) == expected
