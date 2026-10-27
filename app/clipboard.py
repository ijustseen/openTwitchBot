"""Чтение и запись системного буфера.

На macOS виджет Tk часто видит только то, что положил сам, поэтому там берём pbpaste и pbcopy.
"""

from __future__ import annotations

import subprocess
import sys
import time


class ClipboardError(Exception):
    """В буфере нет текста."""


def read_clipboard(widget) -> str:
    system = _read_system()
    if system:
        return system
    try:
        text = widget.clipboard_get()
    except Exception as exc:
        raise ClipboardError("Буфер обмена пуст") from exc
    if not str(text):
        raise ClipboardError("Буфер обмена пуст")
    return str(text)


def bind_paste(root, textbox) -> None:
    """Cmd+V на macOS должен вызывать ту же вставку, что и меню Edit.

    Свой обработчик <<Paste>>, который возвращает break, съедает сочетание:
    пункт меню при этом всё ещё вставляет текст напрямую, а клавиша уже нет.
    """
    if sys.platform != "darwin":
        return
    inner = getattr(textbox, "_textbox", textbox)
    recent = {"at": 0.0}

    def paste_focused(_event=None):
        now = time.monotonic()
        if now - recent["at"] < 0.12:
            return "break"
        widget = root.focus_get()
        if widget is None:
            return "break"
        recent["at"] = now
        try:
            if str(widget.winfo_class()) == "Text":
                widget.tk.call("tk_textPaste", widget._w)
            else:
                widget.event_generate("<<Paste>>")
        except Exception:
            return "break"
        return "break"

    inner.bind("<Command-Key-v>", paste_focused)
    inner.bind("<Command-Lock-Key-V>", paste_focused)
    root.bind_all("<Command-Key-v>", paste_focused, add="+")
    root.bind_all("<Command-Lock-Key-V>", paste_focused, add="+")
    _install_edit_menu(root, paste_focused)


def _install_edit_menu(root, paste_focused) -> None:
    import tkinter

    menubar = tkinter.Menu(root)
    edit = tkinter.Menu(menubar, name="edit", tearoff=0)
    menubar.add_cascade(label="Edit", menu=edit)

    def send(virtual: str) -> None:
        widget = root.focus_get()
        if widget is not None:
            widget.event_generate(virtual)

    edit.add_command(label="Undo", accelerator="Command-Z", command=lambda: send("<<Undo>>"))
    edit.add_command(label="Redo", accelerator="Shift-Command-Z", command=lambda: send("<<Redo>>"))
    edit.add_separator()
    edit.add_command(label="Cut", accelerator="Command-X", command=lambda: send("<<Cut>>"))
    edit.add_command(label="Copy", accelerator="Command-C", command=lambda: send("<<Copy>>"))
    edit.add_command(label="Paste", accelerator="Command-V", command=paste_focused)
    edit.add_separator()
    edit.add_command(label="Select All", accelerator="Command-A", command=lambda: send("<<SelectAll>>"))
    root.configure(menu=menubar)


def insert_text(widget, text: str) -> None:
    try:
        widget.delete("sel.first", "sel.last")
    except Exception:
        pass
    widget.insert("insert", text)
    widget.see("insert")


def write_clipboard(widget, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)
    try:
        widget.update_idletasks()
    except Exception:
        pass
    _write_system(text)


def _read_system() -> str:
    if sys.platform == "darwin":
        return _run(["pbpaste"])
    if sys.platform == "win32":
        return _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::Out.Write((Get-Clipboard -Raw))",
            ]
        )
    return ""


def _write_system(text: str) -> None:
    if sys.platform == "darwin":
        _run(["pbcopy"], text.encode("utf-8"))
    elif sys.platform == "win32":
        _run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"], text.encode("utf-8"))


def _run(argv: list[str], stdin: bytes | None = None) -> str:
    try:
        completed = subprocess.run(argv, input=stdin, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", errors="replace")
