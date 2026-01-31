import importlib
import src.providers.vor as vor
from typing import Any

def test_vor_sends_param_when_header_present(monkeypatch):
    """
    Verify that currently VOR provider sends BOTH header and query param.
    This test serves as a reproduction of the "leak" behavior (param injection).
    """
    monkeypatch.setenv("VOR_ACCESS_ID", "secret")
    importlib.reload(vor)

    class DummySession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls: list[tuple[str, str, Any]] = []

        def request(self, method: str, url: str, params: Any = None, **kwargs: Any) -> Any:
            self.calls.append((method, url, params))
            return {"method": method, "url": url, "params": params, **kwargs}

    session = DummySession()
    vor.apply_authentication(session)  # type: ignore[arg-type]

    # Confirm header is set
    assert session.headers["Authorization"] == "Bearer secret"

    # Confirm param is injected (THIS IS THE LEAK)
    response = session.request("GET", "https://example.test/endpoint", params={"format": "json"})

    # With fix, accessId should NOT be in params
    try:
        assert "accessId" not in response["params"]
    finally:
        monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
        importlib.reload(vor)
