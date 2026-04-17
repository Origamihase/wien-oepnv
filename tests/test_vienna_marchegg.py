from src.utils.stations import text_has_vienna_connection

def test_marchegg_bratislava_is_false():
    text = "REX 8: Marchegg ↔ Bratislava hl.st."
    assert text_has_vienna_connection(text) is False

def test_hadersdorf_am_kamp_is_false():
    text = "Hadersdorf am Kamp"
    assert text_has_vienna_connection(text) is False

def test_bruck_an_der_leitha_is_false():
    # MUST return False. (It is a commuter station, not a Vienna station, and 'Wien' is not in the text).
    assert text_has_vienna_connection("Zugausfall: Bruck an der Leitha") is False

def test_bruck_an_der_leitha_with_wien_is_true():
    # MUST return True. (It remains unmasked, and the word 'Wien' triggers the true condition).
    assert text_has_vienna_connection("Zugausfall: Bruck an der Leitha ↔ Wien") is True

def test_bratislava_hl_st_is_false():
    # MUST return False. (Marchegg is left untouched, Bratislava is masked out, no Vienna station or 'Wien' word is found).
    assert text_has_vienna_connection("REX 8: Marchegg ↔ Bratislava hl.st.") is False
    assert text_has_vienna_connection("Zugausfall: Bratislava hl.st.") is False

def test_via_wien_regression_is_true():
    # MUST return True. (The text explicitly contains 'Wien').
    assert text_has_vienna_connection("REX 8: Marchegg ↔ Bratislava hl.st. via Wien") is True
