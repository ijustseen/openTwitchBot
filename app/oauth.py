"""Вход через своё приложение Twitch: код авторизации приходит на localhost."""

from __future__ import annotations

import json
import secrets
import select
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from html import escape

REDIRECT_URI = "http://localhost:17563/callback"
PORT = 17563
SCOPES = "chat:read chat:edit"
AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


class AuthError(Exception):
    pass


@dataclass
class Session:
    token: str
    refresh_token: str
    login: str


def bare_token(token: str) -> str:
    cleaned = token.strip()
    if cleaned.lower().startswith("oauth:"):
        return cleaned.split(":", 1)[1].strip()
    return cleaned


def authorize_url(client_id: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "force_verify": "true",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _http_message(code: int, body: str) -> str:
    try:
        payload = json.loads(body)
        message = payload.get("message") or payload.get("error_description") or payload.get("error") or body
    except json.JSONDecodeError:
        message = body.strip() or f"HTTP {code}"
    return f"Twitch ответил {code}: {message}"


def _post_form(fields: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise AuthError(_http_message(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"Нет связи с Twitch: {exc.reason}") from exc


def validate_token(token: str) -> dict | None:
    request = urllib.request.Request(
        VALIDATE_URL,
        headers={"Authorization": f"OAuth {bare_token(token)}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return None
        detail = exc.read().decode(errors="replace")
        raise AuthError(_http_message(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"Нет связи с Twitch: {exc.reason}") from exc


def _require_chat_scopes(scopes: object) -> None:
    if isinstance(scopes, str):
        have = set(scopes.split())
    else:
        have = set(scopes or [])
    missing = [scope for scope in ("chat:read", "chat:edit") if scope not in have]
    if missing:
        joined = ", ".join(missing)
        raise AuthError(f"У токена нет прав: {joined}. Войдите ещё раз и разрешите доступ к чату.")


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    return _post_form(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _post_form(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )


def ensure_access_token(client_id: str, client_secret: str, token: str, refresh_token: str) -> Session:
    info = validate_token(token) if token.strip() else None
    if info and int(info.get("expires_in") or 0) > 120:
        _require_chat_scopes(info.get("scopes"))
        return Session(bare_token(token), refresh_token, str(info.get("login") or ""))
    if client_id.strip() and client_secret.strip() and refresh_token.strip():
        payload = refresh_access_token(client_id.strip(), client_secret.strip(), refresh_token.strip())
        new_token = str(payload.get("access_token") or "")
        if not new_token:
            raise AuthError("Twitch не выдал новый токен.")
        new_refresh = str(payload.get("refresh_token") or refresh_token)
        _require_chat_scopes(payload.get("scope"))
        refreshed = validate_token(new_token) or {}
        return Session(new_token, new_refresh, str(refreshed.get("login") or ""))
    if token.strip():
        raise AuthError("Токен недействителен. Нажмите «Войти через Twitch» ещё раз.")
    raise AuthError("Нет токена. Нажмите «Войти через Twitch».")


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        box: dict = self.server.box  # type: ignore[attr-defined]
        if parsed.path != "/callback":
            self._reply(404, "Не тот адрес.")
            return
        if params.get("state", [""])[0] != self.server.expected_state:  # type: ignore[attr-defined]
            box["error"] = "Ответ Twitch не совпал с запросом. Попробуйте войти ещё раз."
            self._reply(400, "Ошибка входа.")
            return
        if "error" in params:
            box["error"] = params.get("error_description", params["error"])[0]
            self._reply(400, "Вход отменён.")
            return
        code = params.get("code", [""])[0]
        if not code:
            box["error"] = "Twitch не прислал код."
            self._reply(400, "Нет кода.")
            return
        box["code"] = code
        self._reply(200, "Вход выполнен. Это окно можно закрыть и вернуться в OpenBot.")

    def log_message(self, format: str, *args) -> None:
        return

    def _reply(self, status: int, text: str) -> None:
        body = (
            "<!doctype html><meta charset=utf-8><title>OpenBot</title>"
            f"<p style=\"font-family:sans-serif\">{escape(text)}</p>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(HTTPServer):
    def __init__(self, address: tuple[str, int], family: int) -> None:
        self.address_family = family
        super().__init__(address, _CallbackHandler)


def listen(box: dict, state: str, port: int = PORT) -> list[_Server]:
    servers: list[_Server] = []
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            server = _Server((host, port), family)
        except OSError:
            continue
        server.box = box  # type: ignore[attr-defined]
        server.expected_state = state  # type: ignore[attr-defined]
        servers.append(server)
    return servers


def wait_for_code(
    servers: list[_Server],
    box: dict,
    timeout: float,
    cancel: threading.Event | None = None,
) -> str:
    import time

    deadline = time.time() + timeout
    try:
        while time.time() < deadline and "code" not in box and "error" not in box:
            if cancel is not None and cancel.is_set():
                raise AuthError("Вход отменён.")
            readable, _, _ = select.select([server.fileno() for server in servers], [], [], 0.4)
            for server in servers:
                if server.fileno() in readable:
                    server.handle_request()
    finally:
        for server in servers:
            server.server_close()
    if box.get("error"):
        raise AuthError(str(box["error"]))
    if "code" not in box:
        raise AuthError("Время входа истекло. Браузер не вернул код.")
    return str(box["code"])


def login(
    client_id: str,
    client_secret: str,
    timeout: float = 180,
    cancel: threading.Event | None = None,
) -> Session:
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not client_id or not client_secret:
        raise AuthError("Нужны Client ID и Client Secret вашего приложения Twitch.")
    state = secrets.token_urlsafe(24)
    box: dict = {}
    servers = listen(box, state)
    if not servers:
        raise AuthError("Порт 17563 занят. Закройте программу, которая его слушает, и попробуйте снова.")
    webbrowser.open(authorize_url(client_id, state))
    code = wait_for_code(servers, box, timeout, cancel)
    payload = exchange_code(client_id, client_secret, code)
    access = str(payload.get("access_token") or "")
    if not access:
        raise AuthError("Twitch не выдал токен.")
    _require_chat_scopes(payload.get("scope"))
    info = validate_token(access) or {}
    return Session(access, str(payload.get("refresh_token") or ""), str(info.get("login") or ""))
