from src.utils.stations import text_has_vienna_connection

def test_marchegg_bratislava_is_false():
    text = "REX 8: Marchegg ↔ Bratislava hl.st."
    assert text_has_vienna_connection(text) is False

def test_hadersdorf_am_kamp_is_false():
    text = "Hadersdorf am Kamp"
    assert text_has_vienna_connection(text) is False
