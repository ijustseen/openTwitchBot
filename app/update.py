"""Проверка GitHub Releases и замена собранного окна.

Из исходников обновление не ставится. Папка data с токеном и командами остаётся:
на macOS она лежит рядом с .app, на Windows её переносит вспомогательный скрипт.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import certifi

from app.bundle_path import frozen_executable
from app.store import resource_root

REPO = "ijustseen/openTwitchBot"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
MAX_BYTES = 250 * 1024 * 1024
MAX_UNCOMPRESSED = 1024 * 1024 * 1024
_ASSET_NAME = re.compile(r"^OpenBot-(macos|windows)-(arm64|x64)\.zip$")


class UpdateError(Exception):
    """Обновление нельзя скачать или поставить."""


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    name: str
    url: str
    size: int


def parse_version(value: str) -> tuple[int, int, int] | None:
    text = value.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        numbers = tuple(int(part) for part in parts)
    except ValueError:
        return None
    if any(number < 0 for number in numbers):
        return None
    return numbers


def normalize_version(value: str) -> str:
    parsed = parse_version(value)
    if parsed is None:
        return "0.0.0"
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"


def platform_tag(platform_name: str | None = None, machine: str | None = None) -> str:
    platform_name = platform_name or sys.platform
    machine = (machine if machine is not None else platform.machine()).lower()
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(machine, machine)
    if platform_name == "darwin":
        return f"macos-{arch}"
    if platform_name == "win32":
        return f"windows-{arch}"
    return f"{platform_name}-{arch}"


def current_version() -> str:
    path = resource_root() / "version.txt"
    if not path.is_file():
        return "0.0.0"
    return normalize_version(path.read_text(encoding="utf-8"))


def install_target() -> Path | None:
    """Что заменять: сам .app или папка, в которой лежит OpenBot.exe."""
    if not getattr(sys, "frozen", False):
        return None
    exe = frozen_executable()
    if sys.platform == "darwin" and exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        return exe.parents[2]
    if sys.platform == "win32":
        return exe.parent
    return None


def updates_supported() -> bool:
    if install_target() is None:
        return False
    parsed = parse_version(current_version())
    return parsed is not None and parsed != (0, 0, 0)


def check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or "@" in parsed.netloc:
        raise UpdateError("Ссылка на обновление ведёт не на GitHub")
    allowed = host == "github.com" or host.endswith(".github.com") or host.endswith(".githubusercontent.com")
    if not allowed:
        raise UpdateError("Ссылка на обновление ведёт не на GitHub")


def offer_from_release(
    payload: dict,
    *,
    current: str,
    platform_name: str,
    machine: str,
) -> UpdateOffer | None:
    """None, если релиз не новее. Ошибка, если новее, но сборки для этой системы нет."""
    latest = parse_version(str(payload.get("tag_name") or ""))
    have = parse_version(current)
    if latest is None or have is None or latest <= have:
        return None
    version = f"{latest[0]}.{latest[1]}.{latest[2]}"
    want = f"OpenBot-{platform_tag(platform_name, machine)}.zip"
    if not _ASSET_NAME.fullmatch(want):
        raise UpdateError(f"В релизе {version} нет сборки для этой системы")
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict) or str(asset.get("name") or "") != want:
            continue
        url = str(asset.get("browser_download_url") or "")
        if not url.startswith(DOWNLOAD_PREFIX):
            raise UpdateError("Ссылка на обновление ведёт не на GitHub")
        check_url(url)
        try:
            size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size < 0:
            size = 0
        return UpdateOffer(version=version, name=want, url=url, size=size)
    raise UpdateError(f"В релизе {version} нет сборки для этой системы")


def find_update() -> UpdateOffer | None:
    if not updates_supported():
        return None
    payload = _fetch_latest()
    if payload is None:
        return None
    return offer_from_release(
        payload,
        current=current_version(),
        platform_name=sys.platform,
        machine=platform.machine(),
    )


def install_update(offer: UpdateOffer, progress: Callable[[int, int], None] | None = None) -> None:
    target = install_target()
    if target is None:
        raise UpdateError("Обновление ставится только в собранное приложение")
    check_url(offer.url)
    if not offer.url.startswith(DOWNLOAD_PREFIX) or not _ASSET_NAME.fullmatch(offer.name):
        raise UpdateError("Ссылка на обновление ведёт не на GitHub")
    cleanup = Path(tempfile.mkdtemp(prefix="openbot-update-"))
    archive = cleanup / offer.name
    extracted = cleanup / "extracted"
    extracted.mkdir()
    try:
        _download(offer, archive, progress)
        extract_archive(archive, extracted)
        payload = find_payload(extracted)
        spawn_swap(os.getpid(), target, payload, cleanup)
    except Exception:
        shutil.rmtree(cleanup, ignore_errors=True)
        raise


def assert_zip_safe(archive: Path) -> None:
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            drive = bool(re.match(r"^[A-Za-z]:", name))
            if not name or name.startswith("/") or drive or ".." in parts:
                raise UpdateError("Архив обновления содержит небезопасный путь")
            total += max(info.file_size, 0)
    if total > MAX_UNCOMPRESSED:
        raise UpdateError("Архив обновления слишком большой")


def extract_archive(archive: Path, dest: Path) -> None:
    assert_zip_safe(archive)
    dest.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["ditto", "-x", "-k", str(archive), str(dest)],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise UpdateError("Не удалось распаковать обновление") from exc
        return
    extract_zip(archive, dest)


def extract_zip(archive: Path, dest: Path) -> None:
    assert_zip_safe(archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(dest)


def find_payload(root: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    root = root.resolve()
    if platform_name == "darwin":
        candidates = []
        direct = root / "OpenBot.app"
        if direct.is_dir():
            candidates.append(direct)
        candidates.extend(path for path in root.glob("*/OpenBot.app") if path.is_dir())
        found = [path for path in candidates if (path / "Contents" / "MacOS" / "OpenBot").is_file()]
        label = "OpenBot.app"
    elif platform_name == "win32":
        found = []
        direct = root / "OpenBot.exe"
        if direct.is_file():
            found.append(root)
        found.extend(path.parent for path in root.glob("*/OpenBot.exe") if path.is_file())
        label = "OpenBot.exe"
    else:
        raise UpdateError("Эту систему обновить нельзя")
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    if len(unique) != 1:
        raise UpdateError(f"В архиве нет {label}")
    payload = unique[0]
    if payload.is_symlink():
        raise UpdateError(f"В архиве нет {label}")
    resolved = payload.resolve()
    if resolved != root and root not in resolved.parents:
        raise UpdateError(f"В архиве нет {label}")
    return payload


def validate_swap(target: Path, source: Path, cleanup: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    cleanup = cleanup.resolve()
    if cleanup not in source.parents:
        raise UpdateError("Внутренняя ошибка обновления")
    if target == cleanup or cleanup in target.parents or target in cleanup.parents:
        raise UpdateError("Внутренняя ошибка обновления")


def spawn_swap(pid: int, target: Path, source: Path, cleanup: Path) -> None:
    validate_swap(target, source, cleanup)
    if sys.platform == "darwin":
        suffix = ".sh"
        script = MACOS_HELPER
        argv_prefix = ["/bin/sh"]
    elif sys.platform == "win32":
        suffix = ".ps1"
        script = WINDOWS_HELPER
        powershell = shutil.which("powershell") or "powershell"
        argv_prefix = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
        ]
    else:
        raise UpdateError("Эту систему обновить нельзя")
    fd, name = tempfile.mkstemp(prefix="openbot-update-", suffix=suffix)
    os.close(fd)
    path = Path(name)
    path.write_text(script, encoding="utf-8")
    if sys.platform == "darwin":
        path.chmod(0o755)
    argv = [
        *argv_prefix,
        str(path),
        str(pid) if sys.platform == "darwin" else "-ProcessId",
    ]
    if sys.platform == "win32":
        argv.extend(
            [
                str(pid),
                "-Target",
                str(target),
                "-Source",
                str(source),
                "-Cleanup",
                str(cleanup),
            ]
        )
    else:
        argv.extend([str(target), str(source), str(cleanup)])
    _popen_detached(argv)


MACOS_HELPER = """#!/bin/sh
set -eu
pid="$1"
target="$2"
source="$3"
cleanup="$4"
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 300 ]; then
    exit 1
  fi
  sleep 0.2
done
backup="${target}.replacing"
rm -rf "$backup"
mv "$target" "$backup"
if mv "$source" "$target"; then
  rm -rf "$backup"
  xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
  open "$target"
  rm -rf "$cleanup"
  rm -f "$0"
else
  mv "$backup" "$target" 2>/dev/null || true
  exit 1
fi
"""


WINDOWS_HELPER = r"""param(
  [Parameter(Mandatory=$true)][int]$ProcessId,
  [Parameter(Mandatory=$true)][string]$Target,
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$Cleanup
)
$ErrorActionPreference = "Stop"
$deadline = (Get-Date).AddSeconds(60)
while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
  if ((Get-Date) -gt $deadline) { exit 1 }
  Start-Sleep -Milliseconds 200
}
$dataBackup = Join-Path $env:TEMP ("openbot-data-" + [guid]::NewGuid().ToString())
$data = Join-Path $Target "data"
$hadData = Test-Path -LiteralPath $data
if ($hadData) {
  Move-Item -LiteralPath $data -Destination $dataBackup
}
$old = "$Target.replacing"
if (Test-Path -LiteralPath $old) {
  Remove-Item -LiteralPath $old -Recurse -Force
}
try {
  Rename-Item -LiteralPath $Target -NewName (Split-Path -Leaf $old)
  Move-Item -LiteralPath $Source -Destination $Target
  if ($hadData) {
    Move-Item -LiteralPath $dataBackup -Destination (Join-Path $Target "data")
  }
  Start-Process -FilePath (Join-Path $Target "OpenBot.exe") -WorkingDirectory $Target
  if (Test-Path -LiteralPath $old) {
    Remove-Item -LiteralPath $old -Recurse -Force
  }
  if (Test-Path -LiteralPath $Cleanup) {
    Remove-Item -LiteralPath $Cleanup -Recurse -Force
  }
} catch {
  if ((Test-Path -LiteralPath $old) -and -not (Test-Path -LiteralPath $Target)) {
    Rename-Item -LiteralPath $old -NewName (Split-Path -Leaf $Target)
  }
  $restored = Join-Path $Target "data"
  if ($hadData -and (Test-Path -LiteralPath $dataBackup) -and (Test-Path -LiteralPath $Target) -and -not (Test-Path -LiteralPath $restored)) {
    Move-Item -LiteralPath $dataBackup -Destination $restored
  }
  exit 1
}
"""


def _fetch_latest() -> dict | None:
    request = urllib.request.Request(
        LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"OpenBot/{current_version()}",
        },
    )
    try:
        with _opener().open(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError("GitHub не отдал список релизов") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise UpdateError("Нет связи с GitHub") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub ответил неожиданно")
    return payload


def _download(offer: UpdateOffer, dest: Path, progress: Callable[[int, int], None] | None) -> None:
    request = urllib.request.Request(offer.url, headers={"User-Agent": f"OpenBot/{current_version()}"})
    read = 0
    try:
        with _opener().open(request, timeout=60) as response, dest.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                read += len(chunk)
                if read > MAX_BYTES or (offer.size and read > offer.size):
                    raise UpdateError("Архив обновления слишком большой")
                handle.write(chunk)
                if progress is not None and offer.size:
                    progress(read, offer.size)
    except UpdateError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Не удалось скачать обновление") from exc
    if offer.size and read != offer.size:
        raise UpdateError("Архив обновления скачался не полностью")
    if read == 0:
        raise UpdateError("Не удалось скачать обновление")


def _opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        _GuardRedirect,
    )


class _GuardRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _popen_detached(argv: list[str]) -> None:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": tempfile.gettempdir(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)
