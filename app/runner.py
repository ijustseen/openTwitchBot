"""Запуск команды в отдельном процессе Python."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HOST_PATH = Path(__file__).resolve().parent / "script_host.py"
DEFAULT_TIMEOUT = 10


@dataclass
class RunResult:
    ok: bool
    replies: list[str]
    log: str
    error: str | None


def python_executable() -> str:
    """Интерпретатор для скриптов. В собранном приложении это системный Python, не сам exe."""
    if getattr(sys, "frozen", False):
        found = shutil.which("python3") or shutil.which("python")
        if found:
            return found
    return sys.executable


def run_script(script_path: Path, context: dict, timeout: float = DEFAULT_TIMEOUT) -> RunResult:
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [python_executable(), str(HOST_PATH), str(script_path)],
            input=json.dumps(context, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(script_path.parent),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            ok=False,
            replies=[],
            log="",
            error=f"Скрипт превысил лимит {int(timeout)} секунд",
        )
    except OSError as exc:
        return RunResult(ok=False, replies=[], log="", error=f"Не удалось запустить Python: {exc}")

    stdout = (completed.stdout or "").strip()
    if not stdout:
        detail = (completed.stderr or "").strip() or "Скрипт ничего не вернул"
        return RunResult(ok=False, replies=[], log="", error=detail)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        detail = (completed.stderr or "").strip()
        return RunResult(
            ok=False,
            replies=[],
            log=stdout,
            error=detail or "Скрипт вернул не JSON",
        )

    replies = [str(item) for item in payload.get("replies") or [] if str(item).strip()]
    log = str(payload.get("log") or "")
    stderr = (completed.stderr or "").strip()
    if stderr:
        log = f"{log.rstrip()}\n{stderr}\n" if log.strip() else f"{stderr}\n"
    return RunResult(
        ok=bool(payload.get("ok")),
        replies=replies,
        log=log,
        error=payload.get("error"),
    )
