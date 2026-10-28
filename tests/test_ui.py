"""Окно собирается и показывает команды из примера."""

import sys
import time
import tkinter
from pathlib import Path

import pytest

from app.ui import App
from app.update import UpdateOffer


def open_app() -> App:
    """На Windows в GitHub Actions чтение init.tcl иногда срывается и проходит со следующей попытки."""
    last: BaseException | None = None
    for attempt in range(5):
        try:
            return App()
        except tkinter.TclError as exc:
            last = exc
            if "init.tcl" not in str(exc) or attempt == 4:
                raise
            time.sleep(0.2)
    assert last is not None
    raise last


def test_window_shows_example_command(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")

    app = open_app()
    try:
        app.update()
        assert app.title() == "OpenBot"
        assert app.channel_entry.get() == ""
        assert app.trigger_entry.get() == "!hello"
        script = app.script_box.get("1.0", "end-1c")
        assert "def run(ctx):" in script
        assert app.status_label.cget("text") == "Не подключён"
        assert not app.update_bar.winfo_ismapped()
        assert app.script_box.tag_ranges("keyword")
        assert app.script_box.tag_ranges("ctx")
        assert app._save_editor() is True
        saved = (tmp_path / "commands" / "hello.py").read_text(encoding="utf-8")
        assert "def run(ctx):" in saved
    finally:
        app.destroy()


def test_paste_inserts_without_erasing_the_script(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")
    monkeypatch.setattr("app.ui.read_clipboard", lambda _widget: "MARKER_PASTE_42")

    app = open_app()
    try:
        app.update()
        app.script_box.mark_set("insert", "end")
        app._paste_script()
        text = app.script_box.get("1.0", "end-1c")
        assert "def run(ctx):" in text
        assert "MARKER_PASTE_42" in text
    finally:
        app.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="Cmd+V есть только на macOS")
def test_command_v_pastes_clipboard(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")

    app = open_app()
    try:
        app.update()
        box = app.script_box._textbox
        box.focus_set()
        app.update()
        app.clipboard_clear()
        app.clipboard_append("CMD_V_MARKER")
        box.event_generate("<Command-Key-v>")
        app.update()
        text = app.script_box.get("1.0", "end-1c")
        assert text.count("CMD_V_MARKER") == 1
        assert "def run(ctx):" in text
    finally:
        app.destroy()


def test_copy_prompt_uses_the_guide(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")
    copied: dict[str, str] = {}
    monkeypatch.setattr("app.ui.write_clipboard", lambda _widget, text: copied.setdefault("text", text))

    app = open_app()
    try:
        app._copy_prompt()
        assert "run(ctx)" in copied["text"]
        assert "ctx.reply" in copied["text"]
        assert "не async" in copied["text"]
    finally:
        app.destroy()


def test_update_bar_shows_newer_version(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")

    app = open_app()
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


def test_stop_is_available_only_while_the_bot_is_online(tmp_path: Path, monkeypatch) -> None:
    import app.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "COMMANDS_DIR", tmp_path / "commands")
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(store, "EXAMPLE_PATH", tmp_path / "config.example.json")

    app = open_app()
    try:
        app.bot.running = lambda: True  # type: ignore[method-assign]
        app.set_status("connecting", "")
        app.update()
        assert str(app.stop_btn.cget("state")) == "disabled"
        assert str(app.start_btn.cget("state")) == "disabled"
        app.set_status("online", "")
        app.update()
        assert str(app.stop_btn.cget("state")) == "normal"
        assert str(app.start_btn.cget("state")) == "disabled"
        app.set_status("stopping", "")
        app.update()
        assert str(app.start_btn.cget("state")) == "disabled"
        app.bot.running = lambda: False  # type: ignore[method-assign]
        app.set_status("idle", "")
        app.update()
        assert str(app.start_btn.cget("state")) == "normal"
        assert str(app.stop_btn.cget("state")) == "disabled"
    finally:
        app.destroy()
