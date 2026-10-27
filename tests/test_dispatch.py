from pathlib import Path

from app.dispatch import Dispatcher
from app.runner import run_script
from app.store import Command


def make(
    trigger: str = "!hello",
    enabled: bool = True,
    cooldown: float = 0,
    permission: str = "all",
    command_id: str = "hello",
) -> Command:
    return Command(
        id=command_id,
        trigger=trigger,
        description="",
        enabled=enabled,
        script=f"{command_id}.py",
        cooldown_sec=cooldown,
        permission=permission,
    )


def test_match_splits_args() -> None:
    match = Dispatcher().match("!hello мир всем", [make()])
    assert match is not None
    assert match.args == ["мир", "всем"]


def test_match_is_case_insensitive() -> None:
    match = Dispatcher().match("!HELLO", [make("!hello")])
    assert match is not None
    assert match.args == []


def test_disabled_command_is_ignored() -> None:
    assert Dispatcher().match("!hello", [make(enabled=False)]) is None


def test_plain_message_is_ignored() -> None:
    assert Dispatcher().match("просто текст", [make()]) is None


def test_cooldown_blocks_until_it_elapses() -> None:
    dispatcher = Dispatcher()
    command = make(cooldown=10)
    assert dispatcher.cooling_down(command, now=100) is False
    dispatcher.mark_used(command, now=100)
    assert dispatcher.cooling_down(command, now=105) is True
    assert dispatcher.cooling_down(command, now=110) is False


def test_mods_only_allows_mod_or_broadcaster() -> None:
    dispatcher = Dispatcher()
    command = make(permission="mods")
    assert dispatcher.allowed(command, is_mod=False, is_broadcaster=False) is False
    assert dispatcher.allowed(command, is_mod=True, is_broadcaster=False) is True
    assert dispatcher.allowed(command, is_mod=False, is_broadcaster=True) is True


def test_runner_returns_reply_and_print(tmp_path: Path) -> None:
    script = tmp_path / "ping.py"
    script.write_text(
        "def run(ctx):\n    ctx.reply('pong')\n    print('logged')\n",
        encoding="utf-8",
    )
    result = run_script(
        script,
        {"user": "a", "message": "!ping", "args": [], "channel": "c"},
    )
    assert result.ok
    assert result.replies == ["pong"]
    assert "logged" in result.log


def test_runner_reports_missing_run(tmp_path: Path) -> None:
    script = tmp_path / "bad.py"
    script.write_text("x = 1\n", encoding="utf-8")
    result = run_script(script, {})
    assert result.ok is False
    assert result.error is not None
    assert "run(ctx)" in result.error


def test_runner_times_out(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time\n\ndef run(ctx):\n    time.sleep(5)\n", encoding="utf-8")
    result = run_script(script, {}, timeout=0.4)
    assert result.ok is False
    assert result.error is not None
    assert "лимит" in result.error


def test_example_hello_replies_with_user() -> None:
    script = Path(__file__).resolve().parents[1] / "data" / "commands" / "hello.py"
    result = run_script(
        script,
        {"user": "andy", "message": "!hello", "args": [], "channel": "demo"},
    )
    assert result.ok
    assert result.replies == ["Привет, andy!"]


def test_example_hello_uses_args() -> None:
    script = Path(__file__).resolve().parents[1] / "data" / "commands" / "hello.py"
    result = run_script(
        script,
        {"user": "andy", "message": "!hello мир", "args": ["мир"], "channel": "demo"},
    )
    assert result.ok
    assert result.replies == ["Привет, мир!"]


def test_client_accepts_token_and_channel() -> None:
    import asyncio

    from app.bot import TwitchService, _make_client
    from app.store import Config

    async def probe() -> None:
        service = TwitchService(lambda _message: None, lambda *_args: None, lambda _error: None)
        service.update_config(Config(channel="#Demo", nickname="", token="oauth:abc", commands=[]))
        client = _make_client(service, asyncio.get_running_loop())
        assert client.joined == "demo"

    asyncio.run(probe())
