from src.providers.oebb import _is_relevant

def test_rex_51_multiple_colons():
    # REX 51: Störung: Wien Meidling ↔ Mödling
    title = "REX 51: Störung: Wien Meidling ↔ Mödling"
    description = "Wegen einer Störung..."
    # Should be True because Wien Meidling is in Vienna
    assert _is_relevant(title, description) is True
