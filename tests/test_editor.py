from pathlib import Path

from app.guide import PROMPT, wiki_path
from app.highlight import python_spans


def kinds(source: str) -> list[tuple[str, str]]:
    return [(source[start:end], kind) for start, end, kind in python_spans(source)]


def test_highlight_splits_code_and_strings() -> None:
    source = 'def run(ctx):\n    ctx.reply(f"def {ctx.user}")\n'
    found = kinds(source)
    assert ("def", "keyword") in found
    assert ("run", "function") in found
    assert ("ctx", "ctx") in found
    assert (".reply", "attr") in found
    assert [text for text, kind in found if kind == "string"] == ['f"def {ctx.user}"']
    assert [text for text, kind in found if kind == "keyword"] == ["def"]


def test_keyword_inside_string_is_not_a_keyword() -> None:
    found = kinds('ctx.reply("def")')
    assert [kind for _text, kind in found if kind == "keyword"] == []
    assert any(kind == "string" and "def" in text for text, kind in found)


def test_comment_and_numbers() -> None:
    found = kinds("# def 3\nctx.is_mod\n")
    assert found[0] == ("# def 3", "comment")
    assert (".is_mod", "attr") in found
    assert not any(kind == "keyword" for _text, kind in found)


def test_wiki_contains_the_prompt() -> None:
    html = Path(wiki_path()).read_text(encoding="utf-8")
    assert "Функция обычная, не async." in PROMPT
    assert "Функция обычная, не async." in html
    assert "ctx.is_broadcaster" in html
    assert "Для нейросети" in html or "нейросет" in html
