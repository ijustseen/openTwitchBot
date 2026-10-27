"""Собирает окно OpenBot и кладёт zip в dist/release/.

На macOS получается OpenBot.app, на Windows — папка с OpenBot.exe.
Чужую систему на этой машине собрать нельзя: вторую сборку делает GitHub Actions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.update import normalize_version, platform_tag  # noqa: E402

DIST = ROOT / "dist"
WORK = ROOT / "build"
STAGE = WORK / "bundle-data"
RELEASE = DIST / "release"


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


def version_file() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "version.txt"
    path.write_text(normalize_version(os.environ.get("OPENBOT_VERSION", "")) + "\n", encoding="utf-8")
    return path


def build() -> Path:
    data = stage_data()
    sep = os.pathsep
    host = ROOT / "app" / "script_host.py"
    version = version_file()
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
        f"--add-data={version}{sep}.",
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
