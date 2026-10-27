"""Запускает пользовательский скрипт и печатает JSON-результат в stdout.

Скрипт пользователя не должен писать в настоящий stdout: его print попадает в лог.
Родитель передаёт контекст JSON в stdin.
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import json
import sys
import traceback
from pathlib import Path


class Ctx:
    def __init__(self, data: dict) -> None:
        self.user = str(data.get("user") or "")
        self.user_id = str(data.get("user_id") or "")
        self.message = str(data.get("message") or "")
        self.args = [str(item) for item in data.get("args") or []]
        self.channel = str(data.get("channel") or "")
        self.is_mod = bool(data.get("is_mod"))
        self.is_broadcaster = bool(data.get("is_broadcaster"))
        self._replies: list[str] = []

    def reply(self, text: object) -> None:
        self._replies.append(str(text))


def main() -> int:
    real_stdout = sys.stdout
    log_buffer = io.StringIO()
    replies: list[str] = []
    try:
        if len(sys.argv) < 2:
            raise RuntimeError("Не передан путь к скрипту")
        script_path = Path(sys.argv[1])
        payload = json.loads(sys.stdin.read() or "{}")
        ctx = Ctx(payload)
        replies = ctx._replies
        if not script_path.is_file():
            raise FileNotFoundError(f"Файл скрипта не найден: {script_path}")

        spec = importlib.util.spec_from_file_location("openbot_user_command", script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Не удалось прочитать скрипт")
        module = importlib.util.module_from_spec(spec)
        sys.stdout = log_buffer
        try:
            spec.loader.exec_module(module)
            run = getattr(module, "run", None)
            if not callable(run):
                raise RuntimeError("В скрипте нет функции run(ctx)")
            outcome = run(ctx)
            if inspect.iscoroutine(outcome):
                outcome.close()
                raise RuntimeError("run(ctx) должна быть обычной функцией, не async")
        finally:
            sys.stdout = real_stdout

        result = {
            "ok": True,
            "replies": ctx._replies,
            "log": log_buffer.getvalue(),
            "error": None,
        }
    except Exception:
        sys.stdout = real_stdout
        result = {
            "ok": False,
            "replies": replies,
            "log": log_buffer.getvalue(),
            "error": traceback.format_exc(),
        }

    with contextlib.suppress(Exception):
        sys.stdout = real_stdout
    real_stdout.write(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
