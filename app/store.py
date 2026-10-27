"""Настройки бота и файлы команд на диске."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from app.bundle_path import frozen_executable, writable_root


SCRIPT_TEMPLATE = """# ctx.user, ctx.args, ctx.message, ctx.channel, ctx.is_mod
# ctx.reply("текст") — сообщение в чат
# print(...) — строка в лог программы

def run(ctx):
    ctx.reply(f"Привет, {ctx.user}!")
"""


def user_root() -> Path:
    """Куда пишутся настройки: рядом с исходниками или с собранным приложением."""
    if getattr(sys, "frozen", False):
        exe = frozen_executable()
        # macOS: OpenBot.app/Contents/MacOS/OpenBot → папка, где лежит .app
        if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
            try:
                return writable_root(exe.parents[3])
            except IndexError:
                return writable_root(exe.parent)
        return writable_root(exe.parent)
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Где лежат файлы, упакованные вместе с программой."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


DATA_DIR = user_root() / "data"
COMMANDS_DIR = DATA_DIR / "commands"
CONFIG_PATH = DATA_DIR / "config.json"
EXAMPLE_PATH = DATA_DIR / "config.example.json"


@dataclass
class Command:
    id: str
    trigger: str
    description: str
    enabled: bool
    script: str
    cooldown_sec: float
    permission: str

    def script_path(self) -> Path:
        return COMMANDS_DIR / self.script


@dataclass
class Config:
    channel: str
    nickname: str
    token: str
    commands: list[Command]
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""


def _copy_if_missing(source: Path, dest: Path) -> None:
    if dest.exists() or not source.is_file():
        return
    if source.resolve() == dest.resolve():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)


def ensure_data() -> None:
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    bundled = resource_root() / "data"
    _copy_if_missing(bundled / "config.example.json", EXAMPLE_PATH)
    if bundled.joinpath("commands").is_dir():
        for script in (bundled / "commands").glob("*.py"):
            _copy_if_missing(script, COMMANDS_DIR / script.name)
    if not CONFIG_PATH.exists():
        if EXAMPLE_PATH.exists():
            shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
        else:
            CONFIG_PATH.write_text(
                json.dumps(
                    {
                        "channel": "",
                        "nickname": "",
                        "token": "",
                        "client_id": "",
                        "client_secret": "",
                        "refresh_token": "",
                        "commands": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )


def _command_from_raw(raw: dict) -> Command:
    permission = raw.get("permission") or "all"
    if permission not in ("all", "mods"):
        permission = "all"
    try:
        cooldown = float(raw.get("cooldown_sec") or 0)
    except (TypeError, ValueError):
        cooldown = 0
    if cooldown < 0:
        cooldown = 0
    return Command(
        id=str(raw.get("id") or uuid.uuid4().hex[:8]),
        trigger=str(raw.get("trigger") or "!command"),
        description=str(raw.get("description") or ""),
        enabled=bool(raw.get("enabled", True)),
        script=str(raw.get("script") or ""),
        cooldown_sec=cooldown,
        permission=permission,
    )


def load_config() -> Config:
    ensure_data()
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    commands = [_command_from_raw(item) for item in raw.get("commands") or []]
    return Config(
        channel=str(raw.get("channel") or ""),
        nickname=str(raw.get("nickname") or ""),
        token=str(raw.get("token") or ""),
        commands=commands,
        client_id=str(raw.get("client_id") or ""),
        client_secret=str(raw.get("client_secret") or ""),
        refresh_token=str(raw.get("refresh_token") or ""),
    )


def save_config(config: Config) -> None:
    ensure_data()
    payload = {
        "channel": config.channel,
        "nickname": config.nickname,
        "token": config.token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": config.refresh_token,
        "commands": [asdict(command) for command in config.commands],
    }
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_script(command: Command) -> str:
    path = command.script_path()
    if not path.is_file():
        return SCRIPT_TEMPLATE
    return path.read_text(encoding="utf-8")


def write_script(command: Command, source: str) -> None:
    ensure_data()
    command.script_path().write_text(source, encoding="utf-8")


def validate_trigger(trigger: str, commands: list[Command], current_id: str) -> str:
    cleaned = trigger.strip()
    if not cleaned.startswith("!") or len(cleaned) < 2:
        raise ValueError("Триггер должен начинаться с ! и содержать имя")
    if any(char.isspace() for char in cleaned):
        raise ValueError("В триггере не должно быть пробелов")
    for command in commands:
        if command.id != current_id and command.trigger.lower() == cleaned.lower():
            raise ValueError(f"Команда {command.trigger} уже есть")
    return cleaned


def parse_cooldown(text: str) -> float:
    raw = text.strip().replace(",", ".")
    if not raw:
        return 0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("Кулдаун должен быть числом") from exc
    if value < 0:
        raise ValueError("Кулдаун не может быть отрицательным")
    return value


def unique_trigger(base: str, commands: list[Command]) -> str:
    candidate = base if base.startswith("!") else f"!{base}"
    existing = {command.trigger.lower() for command in commands}
    if candidate.lower() not in existing:
        return candidate
    number = 2
    while f"{candidate}{number}".lower() in existing:
        number += 1
    return f"{candidate}{number}"


def create_command(commands: list[Command]) -> Command:
    command_id = uuid.uuid4().hex[:8]
    command = Command(
        id=command_id,
        trigger=unique_trigger("!new", commands),
        description="",
        enabled=True,
        script=f"{command_id}.py",
        cooldown_sec=0,
        permission="all",
    )
    write_script(command, SCRIPT_TEMPLATE)
    return command


def import_script(source: Path, commands: list[Command]) -> Command:
    command_id = uuid.uuid4().hex[:8]
    text = source.read_text(encoding="utf-8")
    stem = "".join("_" if char.isspace() else char for char in (source.stem.strip() or "imported"))
    command = Command(
        id=command_id,
        trigger=unique_trigger(stem, commands),
        description=f"Импортировано из {source.name}",
        enabled=True,
        script=f"{command_id}.py",
        cooldown_sec=0,
        permission="all",
    )
    write_script(command, text)
    return command


def delete_command_file(command: Command) -> None:
    path = command.script_path()
    if path.is_file() and path.parent == COMMANDS_DIR:
        path.unlink()
