"""Привод произвольного HTML к whitelist'у Telegram parse_mode=HTML.
br/p превращаются в переносы, всё остальное вне whitelist'а вырезается."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser


_KEEP_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "tg-spoiler", "blockquote"
}
_NORMALIZE = {
    "strong": "b",
    "em": "i",
    "ins": "u",
    "strike": "s",
    "del": "s",
}
_BLOCK_TO_NEWLINE = {"br", "p", "div", "li"}


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in _BLOCK_TO_NEWLINE:
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            return

        norm = _NORMALIZE.get(tag, tag)
        if norm == "span":
            for k, v in attrs:
                if k.lower() == "class" and v == "tg-spoiler":
                    norm = "tg-spoiler"
                    break

        if norm not in _KEEP_TAGS:
            return

        if norm == "a":
            href = ""
            for k, v in attrs:
                if k.lower() == "href" and v:
                    href = v
                    break
            if href:
                href = href.replace('"', "&quot;")
                self.parts.append(f'<a href="{href}">')
                self.tag_stack.append("a")
            return

        if norm == "code":
            lang_cls = ""
            for k, v in attrs:
                if k.lower() == "class" and v and v.startswith("language-"):
                    lang_cls = v
                    break
            if lang_cls:
                self.parts.append(f'<code class="{lang_cls}">')
            else:
                self.parts.append("<code>")
            self.tag_stack.append("code")
            return

        if norm == "blockquote":
            is_expandable = any(k.lower() == "expandable" for k, _ in attrs)
            if is_expandable:
                self.parts.append('<blockquote expandable>')
            else:
                self.parts.append('<blockquote>')
            self.tag_stack.append("blockquote")
            return

        self.parts.append(f"<{norm}>")
        self.tag_stack.append(norm)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in _BLOCK_TO_NEWLINE:
            return

        norm = _NORMALIZE.get(tag, tag)
        if norm == "span":
            norm = "tg-spoiler"

        if norm not in _KEEP_TAGS:
            return

        if norm in self.tag_stack:
            while self.tag_stack:
                top = self.tag_stack.pop()
                self.parts.append(f"</{top}>")
                if top == norm:
                    break

    def handle_data(self, data: str):
        self.parts.append(_escape(data))

    def handle_entityref(self, name: str):
        if name == "nbsp":
            self.parts.append(" ")
        elif name in ("lt", "gt", "amp", "quot"):
            self.parts.append(f"&{name};")
        else:
            self.parts.append(f"&amp;{name};")

    def handle_charref(self, name: str):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str):
        pass

    def handle_pi(self, data: str):
        self.parts.append(_escape(f"<?{data}>"))

    def unknown_decl(self, data: str):
        self.parts.append(_escape(f"<!{data}>"))

    def result(self) -> str:
        while self.tag_stack:
            top = self.tag_stack.pop()
            self.parts.append(f"</{top}>")
        return "".join(self.parts)


def sanitize_html(text: str | None) -> str:
    if not text:
        return ""
    raw = text.strip()

    def _replace_fence(m: re.Match) -> str:
        lang = m.group(1)
        code = m.group(2)
        escaped = _escape(code)
        if lang:
            return f'<pre><code class="language-{lang}">{escaped}</code></pre>'
        return f'<pre>{escaped}</pre>'

    raw = re.sub(r"```(\w+)?\n?(.*?)\n?```", _replace_fence, raw, flags=re.DOTALL)

    cleaner = _Cleaner()
    try:
        cleaner.feed(raw)
        cleaner.close()
        out = cleaner.result()
    except Exception:
        out = _escape(raw)

    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

