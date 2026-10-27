"""Путь к собранному приложению.

Скачанный с сайта .app macOS запускает из служебной папки только для чтения.
Писать настройки туда нельзя: окно падает сразу, ещё до появления на экране.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen_executable() -> Path:
    exe = Path(sys.executable)
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        original = original_translocated_path(exe)
        if original is not None:
            return original
    return exe.resolve()


def original_translocated_path(path: Path) -> Path | None:
    """Настоящий файл, если macOS подменила путь на App Translocation."""
    try:
        import ctypes
    except ImportError:
        return None
    try:
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    except OSError:
        return None

    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease.restype = None
    core.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    core.CFURLCreateFromFileSystemRepresentation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_ubyte,
    ]
    core.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
    core.CFURLGetFileSystemRepresentation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_char_p,
        ctypes.c_long,
    ]
    security.SecTranslocateCreateOriginalPathForURL.restype = ctypes.c_void_p
    security.SecTranslocateCreateOriginalPathForURL.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]

    raw = os.fsencode(path)
    url = core.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), 0)
    if not url:
        return None
    error = ctypes.c_void_p()
    try:
        original = security.SecTranslocateCreateOriginalPathForURL(url, ctypes.byref(error))
    except Exception:
        original = None
    if error.value:
        core.CFRelease(error)
    if not original:
        core.CFRelease(url)
        return None
    try:
        buffer = ctypes.create_string_buffer(4096)
        if not core.CFURLGetFileSystemRepresentation(original, True, buffer, len(buffer)):
            return None
        found = Path(os.fsdecode(buffer.value))
    finally:
        core.CFRelease(original)
        core.CFRelease(url)
    if found.resolve() == path.resolve():
        return None
    return found


def writable_root(candidate: Path) -> Path:
    if _can_write(candidate):
        return candidate
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenBot"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OpenBot"
    return Path.home() / ".local" / "share" / "OpenBot"


def _can_write(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".openbot-write"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True
