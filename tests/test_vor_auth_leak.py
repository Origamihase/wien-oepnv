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

    # Confirm header is NOT set on session directly (Task 1 fix)
    assert "Authorization" not in session.headers

    # Confirm VorAuth injects both header and param as requested
    assert isinstance(session.auth, vor.VorAuth)

    from requests import PreparedRequest
    req = PreparedRequest()
    # Use VOR_BASE_URL to ensure auth is applied
    target_url = vor.VOR_BASE_URL + "endpoint"
    req.prepare(method="GET", url=target_url, headers={})

    # Apply auth
    req = session.auth(req)

    # 1. Header should be injected
    assert req.headers["Authorization"] == "Bearer secret"

    # 2. Param should be injected (as requested by user)
    assert "accessId=secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)
