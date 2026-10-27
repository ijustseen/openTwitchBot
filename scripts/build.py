"""Собирает окно OpenBot и кладёт zip в dist/release/.

На macOS получается OpenBot.app, на Windows — папка с OpenBot.exe.
Чужую систему на этой машине собрать нельзя: вторую сборку делает GitHub Actions.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build"
STAGE = WORK / "bundle-data"
RELEASE = DIST / "release"


def platform_tag() -> str:
    machine = platform.machine().lower()
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(machine, machine)
    if sys.platform == "darwin":
        return f"macos-{arch}"
    if sys.platform == "win32":
        return f"windows-{arch}"
    return f"{sys.platform}-{arch}"


def stage_data() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    dest = STAGE / "data"
    shutil.copytree(
        ROOT / "data",
        dest,
        ignore=shutil.ignore_patterns(
            "config.json",
            "config.json.*",
            "config.local.json",
            "__pycache__",
            "*.pyc",
        ),
    )
    return dest


def build() -> Path:
    data = stage_data()
    sep = os.pathsep
    host = ROOT / "app" / "script_host.py"
    for stale in (DIST / "OpenBot", DIST / "OpenBot.app"):
        if stale.is_dir():
            shutil.rmtree(stale)
    RELEASE.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "OpenBot",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK / "pyinstaller"),
        "--specpath",
        str(WORK),
        f"--add-data={data}{sep}data",
        f"--add-data={host}{sep}app",
        "--collect-all",
        "customtkinter",
        "--collect-submodules",
        "twitchio",
        str(ROOT / "main.py"),
    ]
    if sys.platform == "darwin":
        command.extend(["--osx-bundle-identifier", "app.openbot.desktop"])
    subprocess.run(command, cwd=ROOT, check=True)

    if sys.platform == "darwin":
        app = DIST / "OpenBot.app"
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app)],
            check=True,
        )
        archive = RELEASE / f"OpenBot-{platform_tag()}.zip"
        if archive.exists():
            archive.unlink()
        subprocess.run(
            ["ditto", "-c", "-k", "--norsrc", "--keepParent", str(app), str(archive)],
            check=True,
        )
        return archive

    folder = DIST / "OpenBot"
    archive = RELEASE / f"OpenBot-{platform_tag()}.zip"
    if archive.exists():
        archive.unlink()
    shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=DIST, base_dir=folder.name)
    return archive


def main() -> None:
    archive = build()
    print(archive)


if __name__ == "__main__":
    main()
