import pytest

from src.utils.text import normalize_bullets


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ab • sofort", "ab sofort"),
        ("Ab • heute", "Ab heute"),
        ("Am • Rathaus", "Am Rathaus"),
        ("An • der Haltestelle", "An der Haltestelle"),
        ("auf • dem Bahnsteig", "auf dem Bahnsteig"),
        ("bei • Station", "bei Station"),
        ("Bis • morgen", "Bis morgen"),
        ("durch • den Tunnel", "durch den Tunnel"),
        ("Durch • das Tor", "Durch das Tor"),
        ("Gegen • den Verkehr", "Gegen den Verkehr"),
        ("In • der Station", "In der Station"),
        ("Nach • Wien", "Nach Wien"),
        ("ueber • die Brücke", "ueber die Brücke"),
        ("Ueber • das Gleis", "Ueber das Gleis"),
        ("über • den Dächern", "über den Dächern"),
        ("Über • den Dächern", "Über den Dächern"),
        ("Vom • Bahnsteig", "Vom Bahnsteig"),
        ("zum • Ausgang", "zum Ausgang"),
        ("zur • Station", "zur Station"),
        ("bei\n• Station", "bei\nStation"),
        ("In •\n der Station", "In\nder Station"),
    ],
)
def test_normalize_bullets_prepositions(text, expected):
    assert normalize_bullets(text) == expected
