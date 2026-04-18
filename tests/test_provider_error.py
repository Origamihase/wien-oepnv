import pytest
from unittest.mock import patch
from src.feed.reporting import RunReport

def test_provider_error_unbound_local_error_avoided():
    report = RunReport([])

    with patch("src.feed.reporting.clean_message", side_effect=Exception("Clean failed")):
        with pytest.raises(Exception, match="Clean failed"):
            report.provider_error("wl", "Some error")

    # The key test is that an UnboundLocalError is NOT raised.
    # The patch side_effect should propagate the Exception.
