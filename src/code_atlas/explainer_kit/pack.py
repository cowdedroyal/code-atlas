"""Lint a card directory and pack it into one self-contained HTML file.

A card source is `card.yaml` + `index.html`. The HTML refers to the kit through two fixed placeholders,

    <link rel="stylesheet" href="atlas-kit:1.css">
    <script src="atlas-kit:1.js"></script>

which `pack` replaces with the kit's text, and adds a Content-Security-Policy that forbids every network
request. Cards travel through git between people, so they are treated as untrusted: lint rejects anything
that could reach outside the sandbox, and the viewer still runs them in an iframe without `allow-same-origin`.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from . import KIT_VERSION

PKG = Path(__file__).parent
MAX_BYTES = 200_000
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "img-src data:; font-src data:; base-uri 'none'; form-action 'none'")
CSS_PLACEHOLDER = f"atlas-kit:{KIT_VERSION}.css"
JS_PLACEHOLDER = f"atlas-kit:{KIT_VERSION}.js"

FORBIDDEN_TAGS = {"iframe", "object", "embed", "form", "base", "frame", "frameset", "applet", "audio", "video", "source", "track"}
URL_ATTRS = {"src", "href", "action", "poster", "srcset", "data", "formaction", "xlink:href"}
SOURCES = {"real", "illustrative"}
CSS_EXTERNAL = re.compile(r"@import|url\(\s*['\"]?\s*(?:(?:[a-z][a-z0-9+.-]*:)?//|https?:|file:)", re.I)

_schema = Draft202012Validator(json.loads((PKG / "card.schema.json").read_text("utf-8")))


def card_dirs(path: Path) -> list[Path]:
    if (path / "card.yaml").exists():
        return [path]
    return sorted(p.parent for p in path.glob("*/card.yaml"))


def _url_allowed(attr: str, value: str) -> bool:
    """Only fragments, atlas:// links and inline images may appear in URL-bearing attributes."""
    v = value.strip()
    if v.startswith("#"):
        return True
    if attr == "href" and v.startswith("atlas://"):
        return True
    return attr in {"src", "srcset"} and v.lower().startswith("data:image/")


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.has_css = self.has_js = False
        self.in_style = False

    def err(self, msg: str):
        self.errors.append(f"index.html:{self.getpos()[0]}: {msg}")

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in FORBIDDEN_TAGS:
            self.err(f"<{tag}> is not allowed in a card")
        if tag == "meta" and a.get("http-equiv"):
            self.err("<meta http-equiv> is not allowed (pack adds the Content-Security-Policy)")
        for k in a:
            if k.startswith("on"):
                self.err(f"event handler attribute `{k}` is not allowed; cards run no custom scripts")
        if tag == "link":
            if a.get("href") == CSS_PLACEHOLDER and a.get("rel", "").lower() == "stylesheet":
                self.has_css = True
            else:
                self.err(f'<link> may only be `<link rel="stylesheet" href="{CSS_PLACEHOLDER}">`')
        elif tag == "script":
            if a.get("src") == JS_PLACEHOLDER:
                self.has_js = True
            else:
                self.err(f'cards run no custom scripts; the only <script> is `<script src="{JS_PLACEHOLDER}"></script>`')
        elif tag == "style":
            self.in_style = True
        else:
            for k, v in a.items():
                if k in URL_ATTRS and v and not _url_allowed(k, v):
                    self.err(f"<{tag} {k}=…> points outside the card (`{v[:60]}`); "
                             "only #anchors, atlas:// links and data:image URIs are allowed")
        if "style" in a and CSS_EXTERNAL.search(a["style"]):
            self.err("style attribute loads an external resource")
        if tag == "ex-table":
            src = a.get("source")
            if src not in SOURCES:
                self.err('<ex-table> needs source="real" or source="illustrative"')
            elif src == "real" and not a.get("origin"):
                self.err('<ex-table source="real"> needs origin="…" saying where the data was read from (file, command or log)')
        if tag == "ex-stage" and not a.get("title"):
            self.err("<ex-stage> needs a title")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style and CSS_EXTERNAL.search(data):
            self.err("<style> loads an external resource (@import / url(http…))")


def _structure_errors(html: str) -> list[str]:
    errs = []
    for m in re.finditer(r"<ex-flow\b.*?</ex-flow>", html, re.S | re.I):
        if "<ex-stage" not in m.group(0).lower():
            errs.append("<ex-flow> contains no <ex-stage>")
    for m in re.finditer(r"<ex-compare\b.*?</ex-compare>", html, re.S | re.I):
        body = m.group(0).lower()
        if "<ex-before" not in body or "<ex-after" not in body:
            errs.append("<ex-compare> needs both <ex-before> and <ex-after>")
    return errs


def load_manifest(card: Path) -> dict:
    return yaml.safe_load((card / "card.yaml").read_text("utf-8")) or {}


def lint_card(card: Path) -> list[str]:
    problems: list[str] = []
    try:
        manifest = load_manifest(card)
    except yaml.YAMLError as e:
        return [f"card.yaml: not valid YAML ({e})"]
    for e in sorted(_schema.iter_errors(manifest), key=lambda e: [str(p) for p in e.path]):
        where = ".".join(str(p) for p in e.path) or "(root)"
        problems.append(f"card.yaml: {where}: {e.message}")
    if isinstance(manifest, dict) and manifest.get("id") and manifest["id"] != card.name:
        problems.append(f"card.yaml: id `{manifest['id']}` must equal the directory name `{card.name}`")
    page = card / "index.html"
    if not page.exists():
        return problems + ["index.html is missing"]
    html = page.read_text("utf-8")
    scan = _Scan()
    scan.feed(html)
    problems += scan.errors
    if not scan.has_css:
        problems.append(f'index.html: missing `<link rel="stylesheet" href="{CSS_PLACEHOLDER}">`')
    if not scan.has_js:
        problems.append(f'index.html: missing `<script src="{JS_PLACEHOLDER}"></script>`')
    problems += ["index.html: " + e for e in _structure_errors(html)]
    for pat, what in ((r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b", "network API"),
                      (r"\beval\s*\(|new\s+Function\b", "dynamic code")):
        if re.search(pat, html):
            problems.append(f"index.html: contains a {what} reference")
    packed = len(pack_html(html, manifest if isinstance(manifest, dict) else {}).encode("utf-8"))
    if packed > MAX_BYTES:
        problems.append(f"packed card is {packed:,} bytes; the limit is {MAX_BYTES:,} (fewer rows, smaller images)")
    return problems


def pack_html(html: str, manifest: dict) -> str:
    css = (PKG / "kit" / f"v{KIT_VERSION}.css").read_text("utf-8")
    js = (PKG / "kit" / f"v{KIT_VERSION}.js").read_text("utf-8")
    html = re.sub(r"<link\b[^>]*" + re.escape(CSS_PLACEHOLDER) + r"[^>]*>", lambda _: f"<style>\n{css}</style>", html)
    html = re.sub(r"<script\b[^>]*" + re.escape(JS_PLACEHOLDER) + r"[^>]*>\s*</script>", lambda _: f"<script>\n{js}</script>", html)
    csp = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    if re.search(r"<head\b[^>]*>", html, re.I):
        html = re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + csp, html, count=1, flags=re.I)
    else:
        html = csp + html
    lang = manifest.get("language")
    if lang and not re.search(r"<html\b[^>]*\blang=", html, re.I):
        html = re.sub(r"<html\b", f'<html lang="{lang}"', html, count=1, flags=re.I)
    return html


def pack_card(card: Path, out: Path) -> dict:
    manifest = load_manifest(card)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(pack_html((card / "index.html").read_text("utf-8"), manifest), encoding="utf-8")
    (out / "card.yaml").write_text((card / "card.yaml").read_text("utf-8"), encoding="utf-8")
    return {k: manifest.get(k) for k in ("id", "title", "summary", "questions", "anchors", "tags", "language")}
