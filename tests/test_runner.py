import sys
from pathlib import Path

from app.runner import host_path, python_executable


def test_host_path_is_real_script() -> None:
    path = host_path()
    assert path.name == "script_host.py"
    assert path.is_file()


def test_frozen_host_path_uses_bundle(tmp_path: Path, monkeypatch) -> None:
    packed = tmp_path / "app"
    packed.mkdir()
    script = packed / "script_host.py"
    script.write_text("# bundled\n", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert host_path() == script


def test_dev_python_is_this_interpreter() -> None:
    assert python_executable() == sys.executable
