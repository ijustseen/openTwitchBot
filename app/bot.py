"""Подключение к чату Twitch и запуск команд."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from app.dispatch import Dispatcher
from app.oauth import Session, bare_token, ensure_access_token
from app.runner import run_script
from app.store import Command, Config, save_config

TWITCH_LIMIT = 500
MAX_REPLIES = 5

LogCallback = Callable[[str], None]
StatusCallback = Callable[[str, str], None]
DoneCallback = Callable[[BaseException | None], None]
SessionCallback = Callable[[Session], None]


def normalize_channel(channel: str) -> str:
    return channel.strip().lstrip("#").lower()


def normalize_token(token: str) -> str:
    cleaned = token.strip()
    if cleaned.lower().startswith("oauth:"):
        secret = cleaned.split(":", 1)[1].strip()
        return f"oauth:{secret}"
    return f"oauth:{cleaned}"


def clean_reply(text: str) -> str:
    return " ".join(text.split())[:TWITCH_LIMIT]


class TwitchService:
    """Держит IRC-клиент в фоновом потоке и отдаёт события в интерфейс."""

    def __init__(
        self,
        on_log: LogCallback,
        on_status: StatusCallback,
        on_done: DoneCallback,
        on_session: SessionCallback | None = None,
    ) -> None:
        self._on_log = on_log
        self._on_status = on_status
        self._on_done = on_done
        self._on_session = on_session
        self.dispatcher = Dispatcher()
        self._lock = threading.Lock()
        self._config = Config(channel="", nickname="", token="", commands=[])
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot: object | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._starting = False
        self._running = False

    def update_config(self, config: Config) -> None:
        with self._lock:
            self._config = config

    def snapshot(self) -> Config:
        with self._lock:
            return self._config

    def log(self, text: str) -> None:
        self._on_log(text)

    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        with self._lock:
            if self._starting or self._running:
                self._on_status("online" if self._bot is not None else "connecting", "")
                self.log("Бот уже запущен. Нажмите «Стоп», чтобы отключить.")
                return
            config = self._config
            channel = normalize_channel(config.channel)
            token = config.token.strip()
            can_refresh = bool(config.client_id.strip() and config.client_secret.strip() and config.refresh_token.strip())
            if not channel or not (token or can_refresh):
                self._on_status("error", "Укажите канал и войдите")
                self.log("Нужны канал и вход через Twitch")
                return
            self._starting = True
            self._running = True
            self._stop.clear()
        self._on_status("connecting", "")
        self._thread = threading.Thread(target=self._run, name="openbot-twitch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=8)

    def _run(self) -> None:
        logging.getLogger("twitchio").setLevel(logging.WARNING)
        error: BaseException | None = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._prepare_token()
        except Exception as exc:
            self.log(str(exc))
            self._loop = None
            loop.close()
            self._starting = False
            self._running = False
            self._on_done(exc)
            return

        async def main() -> None:
            if self._stop.is_set():
                return
            bot = _make_client(self, loop)
            self._bot = bot
            if self._stop.is_set():
                return
            started = asyncio.create_task(bot.start())
            while not started.done():
                if self._stop.is_set():
                    await _close_client(bot)
                    break
                await asyncio.sleep(0.1)
            if started.done():
                if not started.cancelled():
                    failure = started.exception()
                    if failure is not None and not self._stop.is_set():
                        raise failure
                return
            try:
                await asyncio.wait_for(asyncio.shield(started), timeout=4)
            except Exception:
                started.cancel()
            try:
                await started
            except (asyncio.CancelledError, Exception):
                pass

        try:
            loop.run_until_complete(main())
        except Exception as exc:
            error = exc
            if not self._stop.is_set():
                self.log(f"Не удалось подключиться: {exc}")
        finally:
            try:
                loop.run_until_complete(_release_loop(self._bot))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None
            self._bot = None
            self._starting = False
            self._running = False
            self._on_done(error)

    def _prepare_token(self) -> None:
        config = self.snapshot()
        session = ensure_access_token(
            config.client_id,
            config.client_secret,
            config.token,
            config.refresh_token,
        )
        changed = (
            session.token != bare_token(config.token)
            or session.refresh_token != config.refresh_token
            or (session.login and session.login != config.nickname)
        )
        if not changed:
            return
        config.token = session.token
        config.refresh_token = session.refresh_token
        if session.login:
            config.nickname = session.login
        self.update_config(config)
        save_config(config)
        if self._on_session is not None:
            self._on_session(session)

    async def handle_message(self, client, message) -> None:
        if getattr(message, "echo", False):
            return
        author = getattr(message, "author", None)
        content = getattr(message, "content", "") or ""
        if author is None or not content.strip():
            return

        author_name = (getattr(author, "name", None) or "").lower()
        config = self.snapshot()
        found = self.dispatcher.match(content, config.commands)
        if found is None:
            return

        channel_name = normalize_channel(config.channel)
        is_mod, is_broadcaster = _author_flags(author, channel_name)
        command = found.command
        if not self.dispatcher.allowed(command, is_mod=is_mod, is_broadcaster=is_broadcaster):
            self.log(f"{author_name} не может вызвать {command.trigger}")
            return
        if self.dispatcher.cooling_down(command):
            self.log(f"Кулдаун {command.trigger}")
            return

        self.dispatcher.mark_used(command)
        user = getattr(author, "display_name", None) or getattr(author, "name", "") or ""
        context = {
            "user": user,
            "user_id": str(getattr(author, "id", "") or ""),
            "message": content,
            "args": found.args,
            "channel": channel_name,
            "is_mod": is_mod,
            "is_broadcaster": is_broadcaster,
        }
        self.log(f"{user}: {content}")
        result = await asyncio.to_thread(run_script, command.script_path(), context)
        if result.log.strip():
            for line in result.log.strip().splitlines():
                self.log(f"{command.trigger} print: {line}")
        if result.error:
            self.log(f"{command.trigger} ошибка:\n{result.error.strip()}")

        chat = getattr(message, "channel", None)
        if chat is None:
            return
        replies = result.replies[:MAX_REPLIES]
        if len(result.replies) > MAX_REPLIES:
            self.log(f"{command.trigger}: отправлены первые {MAX_REPLIES} ответов")
        for reply in replies:
            text = clean_reply(reply)
            if not text:
                continue
            try:
                await chat.send(text)
            except Exception as exc:
                self.log(f"Не удалось отправить ответ: {exc}")
                break


def _author_flags(author, channel_name: str) -> tuple[bool, bool]:
    badges = getattr(author, "badges", None) or {}
    if not isinstance(badges, dict):
        badges = {}
    name = (getattr(author, "name", "") or "").lower()
    is_broadcaster = (
        bool(getattr(author, "is_broadcaster", False))
        or "broadcaster" in badges
        or name == channel_name
    )
    is_mod = bool(getattr(author, "is_mod", False)) or "moderator" in badges or is_broadcaster
    return is_mod, is_broadcaster


async def _cancel_pending(timeout: float) -> None:
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=timeout)


async def _release_loop(bot: object | None) -> None:
    """Отменяет фоновые задачи Twitch и закрывает HTTP-сессию, не дожидаясь их вечно."""
    await _cancel_pending(2)
    http = getattr(bot, "_http", None)
    session = getattr(http, "session", None) if http is not None else None
    if session is not None and not getattr(session, "closed", True):
        try:
            await asyncio.wait_for(session.close(), timeout=2)
        except Exception:
            pass
    await _cancel_pending(1)


async def _close_client(bot: object) -> None:
    """Просит Twitch отключиться. close() до конца connect() сам по себе соединение не отпускает."""
    for _ in range(40):
        if getattr(bot, "_closing", None) is not None:
            break
        await asyncio.sleep(0.05)
    closing = getattr(bot, "_closing", None)
    if closing is not None:
        closing.set()
    try:
        await asyncio.wait_for(bot.close(), timeout=3)  # type: ignore[attr-defined]
    except Exception:
        closing = getattr(bot, "_closing", None)
        if closing is not None:
            closing.set()


def _make_client(service: TwitchService, loop: asyncio.AbstractEventLoop):
    from twitchio import Client

    config = service.snapshot()
    channel = normalize_channel(config.channel)
    token = normalize_token(config.token)

    class ChatClient(Client):
        def __init__(self) -> None:
            super().__init__(token, initial_channels=[channel], loop=loop)
            self.service = service
            self.joined = channel

        async def event_ready(self) -> None:
            actual = self.nick or "бот"
            wanted = service.snapshot().nickname.strip()
            if wanted and wanted.lower() != actual.lower():
                service.log(
                    f"Ник в поле ({wanted}) не совпадает с аккаунтом токена ({actual}). Бот вошёл как {actual}."
                )
            service.log(f"В сети как {actual}, канал #{self.joined}")
            service._on_status("online", "")

        async def event_error(self, error: Exception, data: str | None = None) -> None:
            service.log(f"Ошибка Twitch: {error}")

        async def event_message(self, message) -> None:
            try:
                await service.handle_message(self, message)
            except Exception as exc:
                service.log(f"Ошибка обработки сообщения: {exc}")

    return ChatClient()


def command_context(command: Command, channel: str) -> dict:
    """Контекст кнопки «Проверить»: в чат ничего не уходит."""
    return {
        "user": "tester",
        "user_id": "0",
        "message": f"{command.trigger} пример",
        "args": ["пример"],
        "channel": channel or "preview",
        "is_mod": False,
        "is_broadcaster": True,
    }
