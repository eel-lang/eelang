#!/usr/bin/env python3
"""Render site/index.md into site/index.html.

The Markdown is the source of truth for the page content; this script turns it into
the static HTML that style.css and script.js already style. Run it after editing the
Markdown:

    python3 site/build.py

Conventions used in the Markdown (also documented at the top of index.md):

- `_text_` inside a heading (H1/H2) is rendered as the grey highlight.
- `<br>` in a heading forces a line break.
- `###` blocks fill the current section: a square, a syntax tab, a stdlib tile or a
  plus/minus item, depending on which H2 they follow.
- The first line after a `###` block, when italic, is its caption.
"""

from __future__ import annotations

import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / "index.md"
TARGET = ROOT / "index.html"

SECTION_IDS = {"squares": "goals", "syntax": "syntax", "stdlib": "stdlib", "tradeoffs": "alpha"}
FOOTER_LABELS = {"squares": "Goals", "syntax": "Syntax", "stdlib": "Stdlib", "tradeoffs": "Alpha"}

PY_KEYWORDS = (
    "def|match|case|class|for|in|from|import|as|lambda|return|not|and|or|if|else|"
    "None|True|False"
)
PY_TOKEN = re.compile(
    r"(?P<comment>#[^\n]*)"
    r"|(?P<string>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
    r"|(?P<number>\b\d+(?:\.\d+)?\b)"
    r"|(?P<keyword>\b(?:" + PY_KEYWORDS + r")\b)"
)


# --------------------------------------------------------------------------- parsing


def read_source(text: str) -> tuple[dict[str, str], list[tuple[str, str, str | None]]]:
    """Return (front matter, blocks). A block is (kind, payload, extra)."""
    if not text.startswith("---"):
        raise SystemExit("index.md must start with a front matter block")
    _, front, body = text.split("---", 2)

    meta: dict[str, str] = {}
    for line in front.strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()

    body = body.split("<!--", 1)[1].split("-->", 1)[1] if "<!--" in body else body

    blocks: list[tuple[str, str, str | None]] = []
    lines = body.splitlines()
    i = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(("p", " ".join(part.strip() for part in paragraph), None))
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            language = stripped[3:].strip()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", "\n".join(code), language))
            continue

        if line.startswith("# "):
            flush_paragraph()
            blocks.append(("h1", line[2:].strip(), None))
        elif line.startswith("## "):
            flush_paragraph()
            blocks.append(("h2", line[3:].strip(), None))
        elif line.startswith("### "):
            flush_paragraph()
            blocks.append(("h3", line[4:].strip(), None))
        elif stripped.startswith("- "):
            flush_paragraph()
            items: list[str] = [stripped[2:].strip()]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("  ") and nxt.strip() and not nxt.strip().startswith("- "):
                    items[-1] += " " + nxt.strip()
                    i += 1
                    continue
                if nxt.strip().startswith("- "):
                    items.append(nxt.strip()[2:].strip())
                    i += 1
                    continue
                break
            blocks.append(("list", "\n".join(items), None))
            continue
        elif not stripped:
            flush_paragraph()
        else:
            paragraph.append(line)
        i += 1

    flush_paragraph()
    return meta, blocks


def group_sections(blocks):
    """Split blocks into the hero (before the first H2) and one group per H2."""
    hero: list = []
    sections: list[tuple[str, list]] = []
    for block in blocks:
        if block[0] == "h2":
            sections.append((block[1], []))
        elif sections:
            sections[-1][1].append(block)
        else:
            hero.append(block)
    return hero, sections


# --------------------------------------------------------------------------- inline


def inline(text: str) -> str:
    """Escape, then apply the tiny inline vocabulary the page uses."""
    held: list[str] = []

    def hold(fragment: str) -> str:
        held.append(fragment)
        return f"\x00{len(held) - 1}\x00"

    text = re.sub(r"`([^`]+)`", lambda m: hold(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)", lambda m: hold(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>').replace("&amp;", "&"), text
    )
    text = re.sub(r"\*\*([^*]+)\*\*", lambda m: hold(f"<strong>{html.escape(m.group(1))}</strong>"), text)

    escaped = html.escape(text)
    escaped = escaped.replace("&lt;br&gt;", "<br>")
    escaped = re.sub(r"_([^_]+)_", r'<span class="muted">\1</span>', escaped)

    for index, fragment in enumerate(held):
        escaped = escaped.replace(f"\x00{index}\x00", fragment)
    return escaped


def heading(text: str) -> str:
    return inline(text)


def code_html(code: str, language: str | None, number_lines: bool = False) -> str:
    lines = code.split("\n")
    rendered: list[str] = []
    for number, line in enumerate(lines, start=1):
        body = highlight(line) if language == "python" else html.escape(line)
        if number_lines:
            rendered.append(f'<span class="ln" aria-hidden="true">{number}</span>{body}')
        else:
            rendered.append(body)
    return "\n".join(rendered)


def highlight(line: str) -> str:
    out: list[str] = []
    position = 0
    for match in PY_TOKEN.finditer(line):
        out.append(html.escape(line[position:match.start()]))
        kind = match.lastgroup
        classes = {"comment": "c", "string": "s", "number": "n", "keyword": "k"}
        out.append(f'<span class="{classes[kind]}">{html.escape(match.group(0))}</span>')
        position = match.end()
    out.append(html.escape(line[position:]))
    return "".join(out)


def caption_from(block) -> str | None:
    if block and block[0] == "p" and block[1].startswith("_") and block[1].endswith("_"):
        return block[1][1:-1]
    return None


# --------------------------------------------------------------------------- sections


def render_hero(hero, meta) -> str:
    title = next(b[1] for b in hero if b[0] == "h1")
    paragraphs = [b[1] for b in hero if b[0] == "p"]
    sub = next((p for p in paragraphs if not p.startswith("Built on:") and not p.startswith("[")), "")
    built_on = next((p for p in paragraphs if p.startswith("Built on:")), "")
    link = next((p for p in paragraphs if p.startswith("[")), "")

    examples = []
    index = 0
    while index < len(hero):
        if hero[index][0] == "h3":
            name = hero[index][1]
            rest = hero[index + 1 : index + 3]
            caption = caption_from(rest[0]) if rest else None
            code = next((b[1] for b in rest if b[0] == "code"), "")
            examples.append((name, caption, code))
            index += 1 + len(rest)
        else:
            index += 1

    tabs = []
    panels = []
    for position, (name, caption, code) in enumerate(examples):
        tab_id = f"tab-{position}"
        panel_id = f"panel-{position}"
        active = position == 0
        tabs.append(
            f'<button class="vtab" id="{tab_id}" role="tab" aria-selected="{"true" if active else "false"}" '
            f'aria-controls="{panel_id}"{" tabindex=\"-1\"" if not active else ""} type="button">{html.escape(name)}</button>'
        )
        panels.append(
            f'<div class="viewer-panel" id="{panel_id}" role="tabpanel" aria-labelledby="{tab_id}"'
            f'{" hidden" if not active else ""}>\n'
            f"<pre>{code_html(code, 'python', number_lines=True)}</pre>\n"
            f'<p class="viewer-caption">{inline(caption or "")}</p>\n'
            f"</div>"
        )

    return f"""  <!-- HERO -->
  <section class="hero">
    <div class="hero-left">
      <h1>{heading(title)}</h1>
      <p class="hero-sub">{inline(sub)}</p>

      <a class="after-link" href="#syntax">{inline(link[1:link.index(']')])}</a>

{render_marks(built_on)}
    </div>

    <div class="hero-right">
      <div class="viewer" data-tabs>
        <div class="viewer-tabs" role="tablist" aria-label="Eel examples">
          {"\n          ".join(tabs)}
        </div>
        <div class="viewer-body">
          {"\n".join(panels)}
        </div>
      </div>
    </div>
  </section>
"""


def render_marks(built_on: str) -> str:
    """The 'Built on … inspired by …' line, rendered under the hero copy."""
    if not built_on:
        return ""
    body = built_on.split(":", 1)[1]
    parts = re.split(r"Inspired by:", body)
    mark_items = [item.strip() for item in parts[0].split("·") if item.strip()]

    def mark_item(text: str) -> str:
        words = text.rsplit(" ", 1)
        if len(words) == 2 and re.match(r"^[0-9][0-9.]*$", words[1]):
            return f'<span class="mark-item">{html.escape(words[0])} <span>{html.escape(words[1])}</span></span>'
        return f'<span class="mark-item">{html.escape(text)}</span>'

    marks = "".join(mark_item(item) for item in mark_items)
    inspired = ""
    if len(parts) == 2 and parts[1].strip():
        inspired = (
            '<span class="inspired"><span class="strip-tag">inspired by</span>'
            + mark_item(parts[1].strip())
            + "</span>"
        )

    return f"""      <div class="hero-meta">
        <span class="label">Built on</span>
        {marks}{inspired}
      </div>"""


def split_blocks(blocks):
    """Group a section body into (heading, [blocks]) pairs plus the preamble."""
    preamble: list = []
    chunks: list[tuple[str, list]] = []
    for block in blocks:
        if block[0] == "h3":
            chunks.append((block[1], []))
        elif chunks:
            chunks[-1][1].append(block)
        else:
            preamble.append(block)
    return preamble, chunks


def render_squares(title: str, blocks) -> str:
    preamble, chunks = split_blocks(blocks)
    intro = next((b[1] for b in preamble if b[0] == "p"), "")
    tiles = []
    for heading_text, body in chunks:
        number, _, name = heading_text.partition(" · ")
        sub = next((b[1][1:-1] for b in body if b[0] == "p" and b[1].startswith("_")), "")
        desc = next((b[1] for b in body if b[0] == "p" and not b[1].startswith("_")), "")
        chip = next((b for b in body if b[0] == "code"), None)
        chip_html = (
            f'\n<pre class="chip">{code_html(chip[1], "python")}</pre>' if chip else ""
        )
        tiles.append(
            f"""      <article class="square">
        <span class="idx">{html.escape(number)}</span>
        <h3>{html.escape(name)}</h3>
        <p class="sub">{inline(sub)}</p>
        <p class="desc">{inline(desc)}</p>{chip_html}
      </article>"""
        )
    return f"""  <!-- SQUARES -->
  <section id="{SECTION_IDS['squares']}">
    <div class="big-head">
      <h2>{heading(title)}</h2>
      <p>{inline(intro)}</p>
    </div>

    <div class="squares">
{"\n".join(tiles)}
    </div>
  </section>
"""


def render_syntax(title: str, blocks) -> str:
    preamble, chunks = split_blocks(blocks)
    intro = next((b[1] for b in preamble if b[0] == "p"), "")
    tabs = []
    panels = []
    for position, (heading_text, body) in enumerate(chunks):
        name, _, filename = heading_text.partition(" · ")
        desc = next((b[1] for b in body if b[0] == "p"), "")
        code = next((b for b in body if b[0] == "code"), None)
        tab_id = f"tab-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
        panel_id = f"syn-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
        active = position == 0
        tabs.append(
            f'<button class="api-tab" id="{tab_id}" role="tab" aria-selected="{"true" if active else "false"}" '
            f'aria-controls="{panel_id}"{" tabindex=\"-1\"" if not active else ""} type="button">'
            f'<span class="dot" aria-hidden="true"></span>{html.escape(name)}</button>'
        )
        panels.append(
            f"""        <div class="api-panel" id="{panel_id}" role="tabpanel" aria-labelledby="{tab_id}"{" hidden" if not active else ""}>
          <div class="api-head">
            <p>{inline(desc)}</p>
            <span class="api-file">{html.escape(filename)}</span>
          </div>
<pre>{code_html(code[1], "python") if code else ""}</pre>
        </div>"""
        )
    return f"""  <!-- SYNTAX -->
  <section id="{SECTION_IDS['syntax']}">
    <div class="big-head">
      <h2>{heading(title)}</h2>
      <p>{inline(intro)}</p>
    </div>

    <div class="syntax" data-tabs>
      <div class="api-tabs" role="tablist" aria-label="Syntax examples" aria-orientation="vertical">
        {"\n        ".join(tabs)}
      </div>

      <div class="api-panels">
{"".join(panels)}
      </div>
    </div>
  </section>
"""


def render_stdlib(title: str, blocks) -> str:
    preamble, chunks = split_blocks(blocks)
    note = next((b[1] for b in preamble if b[0] == "p"), "")
    if note.startswith("_") and note.endswith("_"):
        note = note[1:-1]
    tiles = []
    for name, body in chunks:
        rows = []
        for item in next((b[1] for b in body if b[0] == "list"), "").split("\n"):
            if not item:
                continue
            code, _, desc = item.partition(" — ")
            rows.append(f"          <li><code>{inline(code)}</code><span>{inline(desc)}</span></li>")
        rows = [row.replace("<code><code>", "<code>").replace("</code></code>", "</code>") for row in rows]
        tiles.append(
            f"""      <div class="std-block">
        <h3 class="std-label">{html.escape(name)}</h3>
        <ul>
{chr(10).join(rows)}
        </ul>
      </div>"""
        )
    return f"""  <!-- STANDARD LIBRARY -->
  <section class="stdlib" id="{SECTION_IDS['stdlib']}">
    <div class="std-head">
      <h2>{heading(title)}</h2>
      <p class="std-note">{inline(note)}</p>
    </div>

    <div class="std-grid">
{"\n".join(tiles)}
    </div>
  </section>
"""


def render_tradeoffs(title: str, blocks) -> str:
    preamble, chunks = split_blocks(blocks)
    intro = next((b[1] for b in preamble if b[0] == "p"), "")
    columns = []
    for name, body in chunks:
        kind = "plus" if name.lower().startswith("plus") else "minus"
        mark = "+" if kind == "plus" else "−"
        items = []
        for item in next((b[1] for b in body if b[0] == "list"), "").split("\n"):
            if not item:
                continue
            strong, _, rest = item.partition(" — ")
            detail = f"\n              <small>{inline(rest)}</small>" if rest else ""
            items.append(
                f"""          <li>
            <span class="mark" aria-hidden="true">{mark}</span>
            <span>{inline(strong)}{detail}</span>
          </li>"""
            )
        columns.append(
            f"""      <div class="tradeoff {kind}">
        <h3 class="tradeoff-label">{html.escape(name.lower())}</h3>
        <ul>
{chr(10).join(items)}
        </ul>
      </div>"""
        )
    return f"""  <!-- EARLY ALPHA -->
  <section id="{SECTION_IDS['tradeoffs']}">
    <div class="big-head">
      <h2>{heading(title)}</h2>
      <p>{inline(intro)}</p>
    </div>

    <div class="tradeoffs">
{"\n".join(columns)}
    </div>
  </section>
"""


def render_footer(meta) -> str:
    line = meta.get("footer", "")
    brand, _, rest = line.partition(" · ")
    links = "".join(
        f'\n      <a href="#{SECTION_IDS[key]}">{label}</a>'
        for key, label in FOOTER_LABELS.items()
    )
    return f"""<footer>
  <div class="inner">
    <p><strong>{html.escape(brand)}</strong> · {html.escape(rest)}</p>
    <nav aria-label="Footer">{links}
    </nav>
  </div>
</footer>
"""


# --------------------------------------------------------------------------- page


def render(meta, hero, sections) -> str:
    renderers = [render_squares, render_syntax, render_stdlib, render_tradeoffs]
    if len(sections) != len(renderers):
        raise SystemExit(f"expected {len(renderers)} H2 sections, found {len(sections)}")

    body = render_hero(hero, meta)
    for (title, blocks), renderer in zip(sections, renderers):
        body += renderer(title, blocks)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#ffffff">
<title>{html.escape(meta.get('title', 'Eel'))}</title>
<meta name="description" content="{html.escape(meta.get('description', ''))}">
<link rel="icon" href="assets/eel.png" type="image/png">
<link rel="stylesheet" href="style.css">
</head>
<body>

<header class="head">
  <div class="inner">
    <a class="logo" href="#top">
      <img class="mark" src="assets/eel.png" alt="" width="98" height="44">
      <span class="brand-text">
        <span class="wordmark">{html.escape(meta.get('brand', 'Eel'))}</span>
        <span class="status">{html.escape(meta.get('status', ''))}</span>
      </span>
    </a>
    <div class="head-end">
      <a class="gh" href="{html.escape(meta.get('github', '#'))}" aria-label="{html.escape(meta.get('brand', 'Eel'))} on GitHub" title="GitHub">
        <svg class="octocat" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="{GITHUB_PATH}"/></svg>
      </a>
    </div>
  </div>
</header>

<div class="frame">
<main id="top">
{body}</main>
</div>

{render_footer(meta)}
<p id="copy-status" class="sr-only" role="status" aria-live="polite"></p>

<script src="script.js" defer></script>
</body>
</html>
"""


GITHUB_PATH = (
    'M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04'
    '-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.8'
    '38 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5'
    '.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 '
    '1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.'
    '22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.5'
    '65 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12'
)


def main() -> None:
    meta, blocks = read_source(SOURCE.read_text())
    hero, sections = group_sections(blocks)
    TARGET.write_text(render(meta, hero, sections))
    print(f"wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    if not SOURCE.exists():
        sys.exit("index.md not found")
    main()
