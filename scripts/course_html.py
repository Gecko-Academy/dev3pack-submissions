"""Render the course as the static site a learner reads.

    uv run --extra site python scripts/course_html.py            # build into site/
    uv run --extra site python scripts/course_html.py --serve     # build, then serve it

WHY THIS EXISTS. The pages are MDX carrying `<Question>` blocks, which is the
Hugging Face course format, and nothing renders that by reading the file. GitHub's
preview shows the headings and then prints

    <Question choices={[{ text: "An AI model that can reason...", explain: ...

as a paragraph — the same on Hugging Face's own repository, so it is the format,
not our authoring. The explanations are the teaching, and that is exactly what is
lost. So the pages get rendered rather than read raw.

TRANSPORT ONLY, like every other generator here. `units/en/_toctree.yml` is the
single ordering source, and this file reads it rather than recomputing the order:
a second opinion about what comes after what is how a table of contents starts
disagreeing with the course. Whatever the toctree says, the sidebar says.

The output is `site/`, which is generated and NOT committed — there is no honest
`--check` for a tree of HTML, and a stale committed site that looks current is
worse than no site. Rebuild it; it takes under a second.
"""

from __future__ import annotations

import argparse
import html
import http.server
import json
import shutil
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bootcamp_agent.read import (  # noqa: E402
    Entry,
    Group,
    Page,
    ReadError,
    render,
    toctree,
)

OUT = ROOT / "site"


# The page model -- parsing `<Question>`, rendering markdown, reading the
# toctree -- lives in `bootcamp_agent.read`, because it is what a page IS and
# three surfaces need it: the notebook, this site, and whatever comes later.
# What stays here is the site AROUND a page: the shell, the sidebar, the files.


# --------------------------------------------------------------------------
# The page shell
# --------------------------------------------------------------------------

STYLE = """
/* Dark by design, light as a faithful counterpart — a reader on a light system
   should not be handed a black page, and a reader on a dark one should get the
   page this was drawn for. */
:root{
--bg:#fff;--panel:#fafafa;--fg:#10121a;--muted:#5d6373;--line:#e4e6ee;
--accent:#005efa;--accent-ink:#fff;--code:#f3f4f8;--ok:#005efa;--dot:rgba(16,18,26,.08)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#0a0c14;--panel:#0e111b;--fg:#eef1f8;--muted:#8b93a7;--line:#1d2231;
--accent:#2b7bff;--accent-ink:#fff;--code:#12161f;--ok:#2b7bff;--dot:rgba(255,255,255,.07)}}
:root[data-theme="dark"]{
--bg:#0a0c14;--panel:#0e111b;--fg:#eef1f8;--muted:#8b93a7;--line:#1d2231;
--accent:#2b7bff;--accent-ink:#fff;--code:#12161f;--ok:#2b7bff;--dot:rgba(255,255,255,.07)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.68 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,
Helvetica,Arial,sans-serif;
-webkit-font-smoothing:antialiased}
a{color:var(--accent)}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
.layout{display:grid;grid-template-columns:296px minmax(0,1fr) 232px;min-height:100vh}
nav.side{background:var(--panel);border-right:1px solid var(--line);padding:24px 16px;
overflow-y:auto;max-height:100vh;position:sticky;top:0}
nav.side h1{font-size:14px;margin:0 0 20px;letter-spacing:-.01em}
nav.side h1 a{color:var(--fg);text-decoration:none}
nav.side h1 a::before{content:"";display:inline-block;width:9px;height:9px;
background:var(--accent);margin-right:9px;vertical-align:2px}
nav.side details.group{margin:2px 0}
nav.side details.group>summary{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
margin:15px 0 6px;cursor:pointer;list-style:none;line-height:1.5}
nav.side details.group>summary::-webkit-details-marker{display:none}
nav.side details.group>summary::before{content:"\\25b8";display:inline-block;width:1em;
font-size:9px;transition:transform .12s}
nav.side details.group[open]>summary::before{transform:rotate(90deg)}
nav.side details.group>summary:hover{color:var(--accent)}
nav.side a.page{display:block;padding:5px 10px;border-radius:7px;color:var(--fg);
text-decoration:none;font-size:13.5px;opacity:.82}
nav.side a.page:hover{background:var(--line);opacity:1}
nav.side a.page[aria-current]{background:var(--line);opacity:1;font-weight:600;
box-shadow:inset 2px 0 0 var(--accent)}
nav.side details.group>a.page{margin-left:1em}
nav.side details.group.d1>summary{text-transform:none;letter-spacing:0;font-family:inherit;
font-size:12.5px;margin:9px 0 4px .7em}
nav.side details.group.d1>a.page{margin-left:1.8em}
nav.side details.group.d2>summary{margin-left:1.4em}
nav.side details.group.d2>a.page{margin-left:2.5em}
main{padding:44px 56px 96px;max-width:868px}
aside.onthispage .onthispage-title{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;
letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
aside.onthispage{padding:44px 18px;font-size:13px;position:sticky;top:0;
max-height:100vh;overflow-y:auto}
aside.onthispage a{display:block;color:var(--muted);text-decoration:none;padding:3px 0}
aside.onthispage a:hover{color:var(--accent)}
aside.onthispage a.h3{padding-left:12px}
h1,h2,h3{line-height:1.2;letter-spacing:-.02em}
h1{font-size:2.1rem;margin:.1em 0 .6em}
h2{margin-top:2.3em;padding-top:.45em;border-top:1px solid var(--line)}
code{background:var(--code);padding:.13em .38em;border-radius:5px;font-size:.89em;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--code);padding:15px 17px;border-radius:10px;overflow-x:auto;
border:1px solid var(--line)}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:8px 11px;text-align:left}
th{background:var(--panel)}
blockquote{margin:1em 0;padding:.5em 1.1em;border-left:3px solid var(--accent);color:var(--muted)}
img{max-width:100%}
/* The quiz: choose, and the reason appears. No script — the same markup runs in
   a notebook, where a script inserted through innerHTML never executes. */
.question{margin:1.3em 0 2em}
.choices{list-style:none;margin:0;padding:0}
.choice{margin:8px 0}
.choice input{position:absolute;opacity:0;width:0;height:0}
.choice label{display:flex;gap:.75em;align-items:flex-start;cursor:pointer;
padding:11px 14px;border:1px solid var(--line);border-radius:10px;
background:var(--panel);transition:border-color .12s,background .12s}
.choice label:hover{border-color:var(--accent)}
.choice .box{flex:0 0 auto;width:16px;height:16px;margin-top:.2em;border-radius:4px;
border:1.5px solid var(--muted);transition:background .12s,border-color .12s}
.choice input:focus-visible+label{outline:2px solid var(--accent);outline-offset:2px}
.choice input:checked+label{border-color:var(--accent)}
.choice input:checked+label .box{background:var(--accent);border-color:var(--accent)}
.choice.right input:checked+label{border-color:var(--ok)}
.choice.right input:checked+label .box{background:var(--ok);border-color:var(--ok)}
.choice .explain{display:none;margin:8px 0 0 2.6em;padding:9px 14px;font-size:14px;
color:var(--muted);border-left:2px solid var(--line)}
.choice input:checked~.explain{display:block}
.choice.right input:checked~.explain{border-left-color:var(--ok);color:var(--fg)}
.pager{display:flex;justify-content:space-between;gap:16px;margin-top:68px;
padding-top:22px;border-top:1px solid var(--line);font-size:14px}
.pager a{text-decoration:none}
.crumb{font-family:ui-monospace,Menlo,monospace;color:var(--muted);font-size:11px;
letter-spacing:.07em;text-transform:uppercase;margin-bottom:10px}
@media(max-width:1100px){.layout{grid-template-columns:264px minmax(0,1fr)}
aside.onthispage{display:none}}
@media(max-width:760px){.layout{grid-template-columns:1fr}
nav.side{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}
main{padding:28px 20px 72px}}
"""

#: The page needs no JavaScript at all. The quiz is a radio group and CSS, the
#: sidebar is `<details>`, and both work with scripts blocked — which is also
#: why the same markup renders in a notebook, where a script inserted through
#: `innerHTML` never executes. There is nothing left for a script to add.
SCRIPT = ""


def _depth(local: str) -> str:
    """The `../` prefix that reaches the site root from this page."""
    return "../" * local.count("/")


def _group_html(group: Group, here: str, up: str, depth: int) -> str:
    """One unit, its pages, and the units under it — open only on the path.

    A session's pages are a level deeper than the session, which is a level
    deeper than its unit, so a shut sidebar is four units and a reader opens the
    week they are in. `<details>` throughout: no JavaScript, so it works with
    the script blocked and reads the same everywhere.
    """
    # Open every group on the path to the current page, and nothing else — so
    # the unit, the session inside it and that session's pages are all visible
    # at once, while the other fourteen sessions stay shut.
    on_path = any(entry.local == here for entry in group.walk())
    rows = [
        f'<details class="group d{min(depth, 2)}"{" open" if on_path else ""}>',
        f"<summary>{html.escape(group.title)}</summary>",
    ]
    for entry in group.entries:
        current = ' aria-current="page"' if entry.local == here else ""
        rows.append(
            f'<a class="page" href="{up}{entry.local}.html"{current}>{html.escape(entry.title)}</a>'
        )
    for child in group.groups:
        rows.append(_group_html(child, here, up, depth + 1))
    rows.append("</details>")
    return "\n".join(rows)


def _sidebar(groups: list[Group], here: str) -> str:
    up = _depth(here)
    out = [f'<h1><a href="{up}">Dev3Pack AI-Engineering Bootcamp</a></h1>']
    for group in groups:
        if group.walk():
            out.append(_group_html(group, here, up, 0))
    return "\n".join(out)


def _onthispage(page: Page) -> str:
    links = [
        f'<a class="h{level}" href="#{anchor}">{html.escape(shown)}</a>'
        for level, shown, anchor in page.headings
        if 2 <= level <= 3
    ]
    if not links:
        return ""
    return '<p class="onthispage-title">On this page</p>' + "".join(links)


def _pager(entries: list[Entry], position: int) -> str:
    here = entries[position]
    up = _depth(here.local)
    left = right = ""
    if position > 0:
        previous = entries[position - 1]
        left = f'<a href="{up}{previous.local}.html">← {html.escape(previous.title)}</a>'
    if position + 1 < len(entries):
        following = entries[position + 1]
        right = f'<a href="{up}{following.local}.html">{html.escape(following.title)} →</a>'
    return f'<div class="pager"><span>{left}</span><span>{right}</span></div>'


def shell(page: Page, entry: Entry, sidebar: str, pager: str) -> str:
    up = _depth(entry.local)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(page.title)} — Dev3Pack</title>
<link rel="stylesheet" href="{up}style.css">
</head><body>
<div class="layout">
<nav class="side">{sidebar}</nav>
<main>
<p class="crumb">{html.escape(entry.group)}</p>
{page.body}
{pager}
</main>
<aside class="onthispage">{_onthispage(page)}</aside>
</div>
</body></html>
"""


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


LANDING_STYLE = """
.top{position:sticky;top:0;z-index:9;display:flex;align-items:center;justify-content:space-between;
padding:14px 28px;background:var(--panel);border-bottom:1px solid var(--line)}
.top .brand{font-weight:700;letter-spacing:-.01em;text-decoration:none;color:var(--fg)}
.top .brand::before{content:"";display:inline-block;width:9px;height:9px;
background:var(--accent);margin-right:10px;vertical-align:1px}
.hero{position:relative;padding:104px 24px 92px;text-align:center;
border-bottom:1px solid var(--line);
background-image:radial-gradient(var(--dot) 1px,transparent 1px);background-size:22px 22px}
.eyebrow{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;letter-spacing:.14em;
text-transform:uppercase;color:var(--accent);margin:0 0 26px}
.hero h1{font-size:clamp(2.1rem,5.4vw,3.5rem);line-height:1.08;letter-spacing:-.035em;
margin:0 auto .5em;max-width:15ch;font-weight:700}
.hero .lede{font-size:clamp(1rem,1.9vw,1.18rem);color:var(--muted);max-width:44ch;
margin:0 auto 2.1em;line-height:1.55}
.cta{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.cta a{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
letter-spacing:.09em;
text-transform:uppercase;text-decoration:none;padding:13px 24px;border-radius:7px;
border:1px solid var(--accent);transition:opacity .12s,background .12s}
.cta .primary{background:var(--accent);color:var(--accent-ink)}
.cta .primary:hover{opacity:.88}
.cta .ghost{color:var(--fg);border-color:var(--line)}
.cta .ghost:hover{border-color:var(--accent)}
.meta{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;letter-spacing:.07em;
text-transform:uppercase;color:var(--muted);margin:36px 0 0}
.units{max-width:1020px;margin:0 auto;padding:64px 24px 96px;
display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.unit{border:1px solid var(--line);border-radius:13px;padding:22px 24px;background:var(--panel)}
.unit h2{font-size:1.02rem;margin:0 0 .3em;border:0;padding:0;letter-spacing:-.01em}
.unit p{margin:.2em 0 1em;color:var(--muted);font-size:.92rem}
.unit ol{margin:0;padding-left:1.2em;font-size:.92rem;line-height:1.75}
.unit li{margin:.1em 0}
.unit a{color:var(--fg);text-decoration:none;border-bottom:1px solid transparent}
.unit a:hover{color:var(--accent);border-bottom-color:var(--accent)}
.unit li::marker{color:var(--muted);font-family:ui-monospace,Menlo,monospace;font-size:.85em}
.unit li.pending{color:var(--muted)}
.unit li .when{font-family:ui-monospace,Menlo,monospace;color:var(--muted);font-size:.84em}
.foot{max-width:1020px;margin:0 auto;padding:0 24px 72px;border-top:1px solid var(--line);
font-size:.88rem;color:var(--muted)}
.foot a{margin-right:1.3em;display:inline-block;padding-top:20px}
"""


def _landing(published: set[str]) -> str:
    """The front door: what this is, when it runs, and where to start.

    It replaced a `<meta http-equiv=refresh>` stub that bounced straight into
    unit 0 -- which works, and tells a visitor nothing about what they have
    opened or what is in it.

    WHAT IS PUBLISHED IS READ FROM DISK, never from the calendar. A session
    whose week has not shipped has no page to link to, so it is listed with its
    date and no link. That keeps this honest in the cohort repository, where
    later weeks genuinely are not there yet.
    """
    from bootcamp_agent.curriculum import CHAPTERS, WEEK_TITLES

    def link(local: str, text: str) -> str:
        safe = html.escape(text)
        return f'<a href="{local}.html">{safe}</a>' if local in published else safe

    blocks = []
    unit0 = [
        ("unit0/introduction", "Welcome"),
        ("unit0/onboarding", "Onboarding"),
        ("unit0/runtime-lanes", "Runtime lanes"),
        ("unit0/how-to-submit", "Handing work in"),
        ("unit0/week0", "Week 0 — the prerequisite"),
    ]
    items = "".join(f"<li>{link(local, text)}</li>" for local, text in unit0)
    blocks.append(
        '<section class="unit"><h2>Unit 0 — before we start</h2>'
        "<p>Self-paced, unmarked, and the reason week 1 does not begin with an "
        "install problem.</p>"
        f"<ol>{items}</ol></section>"
    )

    for week in sorted(WEEK_TITLES):
        rows = []
        for chapter in CHAPTERS:
            if chapter.module != week:
                continue
            local = f"{chapter.directory.parent.name}/{chapter.directory.name}/introduction"
            shown = f"Session {chapter.number}. {chapter.title}"
            if local in published:
                rows.append(
                    f"<li>{link(local, shown)} "
                    f'<span class="when">— {html.escape(chapter.weekday)}</span></li>'
                )
            else:
                rows.append(
                    f'<li class="pending">{html.escape(shown)} '
                    f'<span class="when">— arrives {html.escape(chapter.weekday)}</span></li>'
                )
        blocks.append(
            f'<section class="unit"><h2>Unit {week} — {html.escape(WEEK_TITLES[week])}</h2>'
            f"<ol>{''.join(rows)}</ol></section>"
        )

    start = "unit0/introduction" if "unit0/introduction" in published else None
    cta = (
        f'<a class="primary" href="{start}.html">Start here &gt;</a>'
        f'<a class="ghost" href="https://github.com/Gecko-Academy/dev3pack-cohort-2026-09">'
        "Read the repository</a>"
        if start
        else "<em>Nothing is published yet.</em>"
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dev3Pack AI-Engineering Bootcamp</title>
<meta name="description" content="Three weeks, fifteen sessions, one source-grounded
research assistant you can test, cite and defend.">
<link rel="stylesheet" href="style.css">
<style>{LANDING_STYLE}</style>
</head><body>
<header class="top">
  <a class="brand" href="#">Dev3Pack</a>
  <a class="brand" style="font-size:12.5px;font-weight:500"
     href="https://github.com/Gecko-Academy/dev3pack-cohort-2026-09">GitHub</a>
</header>
<section class="hero">
  <p class="eyebrow">Fifteen live sessions · 14 Sep – 2 Oct 2026</p>
  <h1>Three weeks in. One agent you built, tested, and can defend.</h1>
  <p class="lede">Contracts, tools, retrieval and evaluation — a source-grounded
  research assistant, and no API key required.</p>
  <div class="cta">{cta}</div>
  <p class="meta">Monday to Friday, two hours a day · every scored notebook runs offline</p>
</section>
<div class="units">{"".join(blocks)}</div>
<footer class="foot">
<a href="https://github.com/Gecko-Academy/dev3pack-cohort-2026-09">The repository</a>
<a href="https://github.com/Gecko-Academy/dev3pack-submissions">Hand work in</a>
<a href="llms.txt">For agents</a>
</footer>
</body></html>
"""


def build(out: Path = OUT) -> tuple[int, list[str]]:
    """Write the whole site. Returns how many pages, and what was missing."""
    groups = toctree()
    flat = [entry for group in groups for entry in group.walk()]
    missing = [entry.local for entry in flat if not entry.source.is_file()]
    present = [entry for entry in flat if entry.source.is_file()]

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "style.css").write_text(STYLE, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # The agent-facing files belong at the SITE root, not only the repo root.
    # An agent follows `<site>/llms.txt`; it never reads raw.githubusercontent.
    # The Pages workflow uploads `site/` alone, so a file that is not copied
    # here is a 404 to every reader that matters.
    for name in ("llms.txt", "AGENTS.md"):
        source = ROOT / name
        if source.is_file():
            shutil.copyfile(source, out / name)

    # Only pages that exist go in the sidebar: a week that has not been
    # published yet has no file, and a link to it would 404 rather than teach.
    def prune(group: Group) -> Group:
        return Group(
            title=group.title,
            entries=tuple(e for e in group.entries if e.source.is_file()),
            groups=tuple(prune(child) for child in group.groups),
        )

    shown = [prune(group) for group in groups]

    for position, entry in enumerate(present):
        page = render(entry.source)
        destination = out / f"{entry.local}.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            shell(page, entry, _sidebar(shown, entry.local), _pager(present, position)),
            encoding="utf-8",
        )

    if present:
        (out / "index.html").write_text(
            _landing({entry.local for entry in present}), encoding="utf-8"
        )
    (out / "pages.json").write_text(
        json.dumps([{"local": e.local, "title": e.title} for e in present], indent=2),
        encoding="utf-8",
    )
    return len(present), missing


def serve(out: Path, port: int) -> int:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(out), **kwargs)  # type: ignore[arg-type]

        def log_message(self, *args: object) -> None:  # keep the console quiet
            return

    with socketserver.TCPServer(("127.0.0.1", port), Handler) as server:
        print(f"the course is at http://127.0.0.1:{port}/  (ctrl-c to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(OUT), help="where to write the site")
    parser.add_argument("--serve", action="store_true", help="serve it after building")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    out = Path(args.out)
    try:
        count, missing = build(out)
    except ReadError as error:
        print(f"{error}", file=sys.stderr)
        return 1

    print(f"built {count} pages -> {out}")
    if missing:
        print(
            f"  {len(missing)} not published yet: {', '.join(missing[:4])}"
            + (" ..." if len(missing) > 4 else "")
        )
    if args.serve:
        return serve(out, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
