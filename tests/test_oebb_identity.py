from src.build_feed import _identity_for_item

def test_oebb_identity_uses_guid():
    item = {
        "source": "öbb",
        "guid": "oebb-guid-1234",
        "title": "Test Title",
    }
    identity = _identity_for_item(item)
    assert identity == "oebb|oebb-guid-1234"

def test_oebb_identity_uses_link_if_no_guid():
    item = {
        "source": "oebb",
        "link": "https://example.com/oebb/1234",
        "title": "Test Title",
    }
    identity = _identity_for_item(item)
    assert identity == "oebb|https://example.com/oebb/1234"

def test_oebb_identity_uses_hash_if_neither():
    item = {
        "source": "öbb",
        "title": "Test Title",
    }
    identity = _identity_for_item(item)
    assert identity.startswith("oebb|F=")
    # Hash length is 64 for sha256
    assert len(identity.split("F=")[1]) == 64
