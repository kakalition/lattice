"""Telegram formatting helpers."""

from __future__ import annotations

from lattice.channel.telegram.formatting import (
    chunk_text,
    escape_html,
    markdown_to_telegram_html,
)


def test_escape_html() -> None:
    assert escape_html("a <b> & c") == "a &lt;b&gt; &amp; c"


def test_markdown_bold_and_italic() -> None:
    assert markdown_to_telegram_html("say **hello** now") == "say <b>hello</b> now"
    assert markdown_to_telegram_html("say *hello* now") == "say <i>hello</i> now"


def test_markdown_code_and_link() -> None:
    assert markdown_to_telegram_html("use `x < y`") == "use <code>x &lt; y</code>"
    assert (
        markdown_to_telegram_html("see [docs](https://example.com?a=1&b=2)")
        == 'see <a href="https://example.com?a=1&amp;b=2">docs</a>'
    )


def test_markdown_preserves_snake_case() -> None:
    assert markdown_to_telegram_html("call memory_search now") == "call memory_search now"


def test_markdown_escapes_raw_html() -> None:
    assert markdown_to_telegram_html("<script>") == "&lt;script&gt;"


def test_chunk_text() -> None:
    assert chunk_text("abc", 2) == ["ab", "c"]
