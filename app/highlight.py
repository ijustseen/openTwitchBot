"""Подсветка Python в редакторе команд. Сканер не красит слова внутри строк и комментариев."""

from __future__ import annotations

KEYWORDS = frozenset(
    {
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "try",
        "while",
        "with",
        "yield",
        "True",
        "False",
        "None",
    }
)
CTX_ATTRS = frozenset(
    {"user", "user_id", "args", "message", "channel", "is_mod", "is_broadcaster", "reply"}
)
STRING_PREFIXES = frozenset(
    {"r", "f", "b", "u", "fr", "rf", "br", "rb", "fu", "uf", "bf", "fb", "ru", "ur"}
)
COLORS = {
    "comment": "#7d8590",
    "string": "#b5d99c",
    "keyword": "#c792ea",
    "number": "#f0a070",
    "ctx": "#82aaff",
    "attr": "#7fd1ff",
    "function": "#e6c07b",
}


def python_spans(source: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    i = 0
    n = len(source)
    expect_name = False
    while i < n:
        ch = source[i]
        if ch in " \t\r":
            i += 1
            continue
        if ch == "\n":
            expect_name = False
            i += 1
            continue
        if ch == "#":
            j = source.find("\n", i)
            if j < 0:
                j = n
            spans.append((i, j, "comment"))
            i = j
            continue
        prefixed = _string_at(source, i)
        if prefixed is not None:
            spans.append((i, prefixed, "string"))
            i = prefixed
            expect_name = False
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            if expect_name and word not in KEYWORDS:
                spans.append((i, j, "function"))
                expect_name = False
            elif word in KEYWORDS:
                spans.append((i, j, "keyword"))
                expect_name = word == "def"
            elif word == "ctx":
                spans.append((i, j, "ctx"))
                expect_name = False
                attr_end = _ctx_attr(source, j)
                if attr_end is not None:
                    spans.append((j, attr_end, "attr"))
                    i = attr_end
                    continue
            else:
                expect_name = False
            i = j
            continue
        if ch.isdigit():
            j = i + 1
            while j < n and (source[j].isdigit() or source[j] == "."):
                j += 1
            spans.append((i, j, "number"))
            expect_name = False
            i = j
            continue
        expect_name = False
        i += 1
    return spans


def attach_highlight(box) -> None:
    for kind, color in COLORS.items():
        box.tag_config(kind, foreground=color)
    busy = {"on": False}

    def on_modified(_event=None):
        if busy["on"]:
            return
        try:
            changed = box.edit_modified()
        except Exception:
            return
        if not changed:
            return
        busy["on"] = True
        try:
            apply_highlight(box)
            box.edit_modified(False)
        finally:
            busy["on"] = False

    box.bind("<<Modified>>", on_modified)
    on_modified()


def refresh_highlight(box) -> None:
    apply_highlight(box)
    try:
        box.edit_modified(False)
    except Exception:
        pass


def apply_highlight(box) -> None:
    source = box.get("1.0", "end-1c")
    for kind in COLORS:
        box.tag_remove(kind, "1.0", "end")
    for start, end, kind in python_spans(source):
        box.tag_add(kind, f"1.0+{start}c", f"1.0+{end}c")


def _string_at(source: str, i: int) -> int | None:
    if source[i] in "'\"":
        return _scan_string(source, i)
    j = i
    while j < len(source) and source[j].isalpha():
        j += 1
    if j == i or source[i:j].lower() not in STRING_PREFIXES:
        return None
    if j < len(source) and source[j] in "'\"":
        return _scan_string(source, j)
    return None


def _scan_string(source: str, i: int) -> int:
    quote = source[i]
    triple = source.startswith(quote * 3, i)
    end = quote * 3 if triple else quote
    j = i + len(end)
    n = len(source)
    while j < n:
        if source.startswith(end, j):
            return j + len(end)
        if source[j] == "\\":
            j += 2
            continue
        if not triple and source[j] == "\n":
            return j
        j += 1
    return n


def _ctx_attr(source: str, i: int) -> int | None:
    if i >= len(source) or source[i] != ".":
        return None
    j = i + 1
    while j < len(source) and (source[j].isalnum() or source[j] == "_"):
        j += 1
    if source[i + 1 : j] in CTX_ATTRS:
        return j
    return None
