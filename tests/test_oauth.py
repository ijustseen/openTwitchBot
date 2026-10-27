import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from app.oauth import (
    REDIRECT_URI,
    AuthError,
    _Server,
    authorize_url,
    ensure_access_token,
)


def test_authorize_url_points_at_local_callback() -> None:
    url = authorize_url("client-1", "state-1")
    assert url.startswith("https://id.twitch.tv/oauth2/authorize?")
    assert "client_id=client-1" in url
    assert "state=state-1" in url
    assert "chat%3Aread" in url or "chat:read" in url
    assert "chat%3Aedit" in url or "chat:edit" in url
    assert urllib.parse.unquote(url).count(REDIRECT_URI) == 1 or REDIRECT_URI in urllib.parse.unquote(url)


def test_callback_accepts_matching_state() -> None:

    box: dict = {}
    server = _Server(("127.0.0.1", 0), socket.AF_INET)
    server.box = box  # type: ignore[attr-defined]
    server.expected_state = "state-1"  # type: ignore[attr-defined]
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=state-1") as response:
        page = response.read().decode()
    thread.join(timeout=2)
    server.server_close()
    assert box["code"] == "abc"
    assert "OpenBot" in page


def test_callback_rejects_foreign_state() -> None:
    box: dict = {}
    server = _Server(("127.0.0.1", 0), socket.AF_INET)
    server.box = box  # type: ignore[attr-defined]
    server.expected_state = "state-1"  # type: ignore[attr-defined]
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=other")
    assert caught.value.code == 400
    thread.join(timeout=2)
    server.server_close()
    assert "не совпал" in box["error"]


def test_ensure_keeps_a_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.oauth.validate_token",
        lambda _token: {"login": "bot", "expires_in": 1000, "scopes": ["chat:read", "chat:edit"]},
    )
    session = ensure_access_token("", "", "oauth:abc", "refresh")
    assert session.token == "abc"
    assert session.refresh_token == "refresh"
    assert session.login == "bot"


def test_ensure_refreshes_an_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def validate(token: str):
        if token == "old":
            return None
        return {"login": "bot", "expires_in": 4000, "scopes": ["chat:read", "chat:edit"]}

    def refresh(client_id: str, _secret: str, _refresh: str) -> dict:
        assert client_id == "id"
        return {
            "access_token": "new",
            "refresh_token": "r2",
            "scope": ["chat:read", "chat:edit"],
        }

    monkeypatch.setattr("app.oauth.validate_token", validate)
    monkeypatch.setattr("app.oauth.refresh_access_token", refresh)
    session = ensure_access_token("id", "secret", "old", "r1")
    assert session.token == "new"
    assert session.refresh_token == "r2"
    assert session.login == "bot"


def test_ensure_rejects_token_without_chat_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.oauth.validate_token",
        lambda _token: {"login": "bot", "expires_in": 1000, "scopes": ["chat:read"]},
    )
    with pytest.raises(AuthError, match="chat:edit"):
        ensure_access_token("", "", "abc", "")
