"""Окно собирается и показывает команды из примера."""

from pathlib import Path

from app.ui import App
from app.update import UpdateOffer


def test_window_shows_example_command(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")

    app = App()
    try:
        app.update()
        assert app.title() == "OpenBot"
        assert app.channel_entry.get() == ""
        assert app.trigger_entry.get() == "!hello"
        script = app.script_box.get("1.0", "end-1c")
        assert "def run(ctx):" in script
        assert app.status_label.cget("text") == "Не подключён"
        assert not app.update_bar.winfo_ismapped()
        assert app._save_editor() is True
        saved = (tmp_path / "commands" / "hello.py").read_text(encoding="utf-8")
        assert "def run(ctx):" in saved
    finally:
        app.destroy()


def test_update_bar_shows_newer_version(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")

    app = App()
    try:
        app._show_update(
            UpdateOffer(
                "0.2.0",
                "OpenBot-macos-arm64.zip",
                "https://github.com/ijustseen/openTwitchBot/releases/download/v0.2.0/OpenBot-macos-arm64.zip",
                10,
            )
        )
        app.update()
        assert app.update_bar.winfo_ismapped()
        assert "0.2.0" in app.update_label.cget("text")
        app._dismiss_update()
        app.update()
        assert not app.update_bar.winfo_ismapped()
    finally:
        app.destroy()
