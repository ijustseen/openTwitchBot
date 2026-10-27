"""Разбор сообщений чата в команды."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.store import Command


@dataclass
class Match:
    command: Command
    args: list[str]


class Dispatcher:
    def __init__(self) -> None:
        self._last_used: dict[str, float] = {}

    def match(self, text: str, commands: list[Command]) -> Match | None:
        parts = text.strip().split()
        if not parts:
            return None
        trigger = parts[0]
        if not trigger.startswith("!"):
            return None
        lowered = trigger.lower()
        for command in commands:
            if command.enabled and command.trigger.lower() == lowered:
                return Match(command, parts[1:])
        return None

    def allowed(self, command: Command, *, is_mod: bool, is_broadcaster: bool) -> bool:
        if command.permission == "mods":
            return is_mod or is_broadcaster
        return True

    def cooling_down(self, command: Command, now: float | None = None) -> bool:
        if command.cooldown_sec <= 0:
            return False
        last = self._last_used.get(command.id)
        if last is None:
            return False
        moment = time.monotonic() if now is None else now
        return (moment - last) < command.cooldown_sec

    def mark_used(self, command: Command, now: float | None = None) -> None:
        self._last_used[command.id] = time.monotonic() if now is None else now
