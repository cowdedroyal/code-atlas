"""Lint a card directory and pack it into one self-contained HTML file.

A card source is `card.yaml` + `index.html`. The HTML refers to the kit through two fixed placeholders,

    <link rel="stylesheet" href="atlas-kit:1.css">
    <script src="atlas-kit:1.js"></script>

which `pack` replaces with the kit's text, and adds a Content-Security-Policy that forbids every network
request. Cards travel through git between people, so they are treated as untrusted: lint rejects anything
that could reach outside the sandbox, and the viewer still runs them in an iframe without `allow-same-origin`.

Cards may also show baked media — an animation or a chart — through a name `atlas-media:<name>`:

    <ex-anim src="atlas-media:flow" autoplay loop caption="…"></ex-anim>
    <ex-chart src="atlas-media:sales" caption="…"></ex-chart>

The files live in `media/` next to `card.yaml` and are produced by `atlas-explainer bake` (see media.py); `pack`
inlines them into the page, so the card is still one file with no network. Because media is far larger than the
prose it illustrates, its size is budgeted separately from the page.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from . import KIT_VERSION
from .media import stale

PKG = Path(__file__).parent
MAX_BYTES = 200_000            # the page itself: prose, tags, the kit
MAX_MEDIA_BYTES = 2_000_000    # baked animation/chart media, inlined at pack time
MEDIA = "media"
MEDIA_SCHEME = "atlas-media:"
MEDIA_TAGS = {"ex-anim": "anim", "ex-chart": "chart"}
VENDOR = PKG / MEDIA / "vendor"
RUNTIME = [VENDOR / "vega.min.js", VENDOR / "vega-interpreter.min.js"]
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


def _url_allowed(attr: str, value: str, tag: str = "") -> bool:
    """Only fragments, atlas:// links, baked media names and inline images may appear in URL-bearing attributes."""
    v = value.strip()
    if v.startswith("#"):
        return True
    if v.startswith(MEDIA_SCHEME):
        return attr == "src" and tag in MEDIA_TAGS
    if attr == "href" and v.startswith("atlas://"):
        return True
    return attr in {"src", "srcset"} and v.lower().startswith("data:image/")


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.has_css = self.has_js = False
        self.in_style = False
        self.media: list[tuple[str, int, str]] = []   # (tag, line, name)

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
                if k in URL_ATTRS and v and not _url_allowed(k, v, tag):
                    self.err(f"<{tag} {k}=…> points outside the card (`{v[:60]}`); "
                             "only #anchors, atlas:// links, atlas-media: names and data:image URIs are allowed")
        if tag in MEDIA_TAGS:
            src = a.get("src", "")
            if not src.startswith(MEDIA_SCHEME):
                self.err(f'<{tag}> needs src="{MEDIA_SCHEME}<name>" naming a baked asset in media/')
            else:
                name = src[len(MEDIA_SCHEME):]
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
                    self.err(f'<{tag} src="{src}">: the name may use letters, digits, dot, dash and underscore only')
                else:
                    self.media.append((tag, self.getpos()[0], name))
            if not a.get("caption"):
                self.err(f"<{tag}> needs a caption: it is the reader's only text when the picture fails to load")
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


def load_media(card: Path) -> tuple[dict[str, dict], list[str]]:
    """Baked manifests in `media/`, by name, plus whatever is wrong with the files themselves."""
    out: dict[str, dict] = {}
    errors: list[str] = []
    d = card / MEDIA
    if not d.is_dir():
        return out, errors
    for p in sorted(d.glob("*.json")):
        if p.name.endswith(".vl.json"):
            continue                     # an authored Vega-Lite spec, not a baked asset
        name = p.name[: -len(".json")]
        try:
            m = json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append(f"media/{p.name}: not valid JSON ({e})")
            continue
        if not isinstance(m, dict) or m.get("kind") not in ("anim", "chart"):
            errors.append(f"media/{p.name}: not a baked asset (`kind` must be `anim` or `chart`); run `atlas-explainer bake`")
            continue
        if m.get("id") != name:
            errors.append(f"media/{p.name}: id `{m.get('id')}` must equal the file name `{name}`")
            continue
        out[name] = m
    return out, errors


_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".svg": "image/svg+xml"}


def _data_uri(path: Path) -> str:
    return f"data:{_MIME.get(path.suffix.lower(), 'application/octet-stream')};base64," + \
        base64.b64encode(path.read_bytes()).decode("ascii")


def inline_media(card: Path, media: dict[str, dict], names) -> dict[str, dict]:
    """The manifests as the page carries them: the sprite sheet becomes a data URI, nothing points at a file."""
    out: dict[str, dict] = {}
    for name in sorted(set(names)):
        m = copy.deepcopy(media[name])
        if m["kind"] == "anim":
            sheet = card / MEDIA / str(m.get("sheet") or "")
            if not sheet.is_file():
                raise ValueError(f"media/{name}.json names `{m.get('sheet')}`, which is missing")
            m["sheet"] = _data_uri(sheet)
        out[name] = m
    return out


def media_size(card: Path, m: dict) -> int:
    """What the manifest costs once inlined: sprite sheet, or the SVG/Vega spec and the runtime for charts."""
    if m["kind"] == "anim":
        sheet = card / MEDIA / str(m.get("sheet") or "")
        return sheet.stat().st_size if sheet.is_file() else 0
    total = sum(len(s) for s in (m.get("svg") or {}).values()) + len(json.dumps(m.get("vega") or {}))
    if m.get("vega"):
        total += sum(p.stat().st_size for p in RUNTIME if p.is_file())
    return total


def lint_media(card: Path, scan: _Scan) -> list[str]:
    problems: list[str] = []
    media, errors = load_media(card)
    problems += errors
    if not scan.media:
        return problems
    for tag, line, name in scan.media:
        m = media.get(name)
        if not m:
            problems.append(f"index.html:{line}: <{tag}> uses `{name}`, which is not baked; run `atlas-explainer bake`")
            continue
        if m["kind"] != MEDIA_TAGS[tag]:
            problems.append(f"index.html:{line}: <{tag}> needs a `{MEDIA_TAGS[tag]}` asset but media/{name}.json is a "
                            f"`{m['kind']}`")
            continue
        if m["kind"] == "chart" and m.get("vega") and not all(p.is_file() for p in RUNTIME):
            problems.append("media/: the Vega runtime is missing from the tool "
                            f"({', '.join(str(p.name) for p in RUNTIME)}); reinstall atlas-explainer")
        changed = _stale(card, m)
        if changed:
            problems.append(f"media/{name}.json: stale — {', '.join(changed[:3])} changed after the last bake; "
                            "run `atlas-explainer bake`")
    used = {name for _, _, name in scan.media if name in media}
    size = sum(media_size(card, media[n]) for n in used)
    if size > MAX_MEDIA_BYTES:
        problems.append(f"inlined media is {size:,} bytes; the limit is {MAX_MEDIA_BYTES:,} "
                        "(fewer frames, a smaller sheet width, or a static chart instead of an interactive one)")
    return problems


def _stale(card: Path, m: dict) -> list[str]:
    return stale(card, m)


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
    problems += lint_media(card, scan)
    packed = len(pack_html(html, manifest if isinstance(manifest, dict) else {}, media={}).encode("utf-8"))
    if packed > MAX_BYTES:
        problems.append(f"packed card is {packed:,} bytes; the limit is {MAX_BYTES:,} (fewer rows, smaller images)")
    return problems


def pack_html(html: str, manifest: dict, media: dict[str, dict] | None = None) -> str:
    """Inline the kit, the media registry and the CSP.

    `media` is the output of `inline_media`; pass `{}` to measure the page on its own. `None` means the caller does not
    handle media at all, so a card that uses some is an error rather than a page that silently loses its pictures.
    """
    css = (PKG / "kit" / f"v{KIT_VERSION}.css").read_text("utf-8")
    js = (PKG / "kit" / f"v{KIT_VERSION}.js").read_text("utf-8")
    if media is None:
        if MEDIA_SCHEME in html:
            raise ValueError("this card uses baked media; pass `media=inline_media(...)` to pack_html")
        media = {}
    injected = ""
    if media:
        if any(m.get("vega") for m in media.values()):
            injected += "<script>\n" + "\n".join(p.read_text("utf-8") for p in RUNTIME if p.is_file()) + "\n</script>\n"
        payload = json.dumps(media, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        injected += f'<script type="application/json" id="atlas-media-registry">{payload}</script>\n'
    html = re.sub(r"<link\b[^>]*" + re.escape(CSS_PLACEHOLDER) + r"[^>]*>", lambda _: f"<style>\n{css}</style>", html)
    html = re.sub(r"<script\b[^>]*" + re.escape(JS_PLACEHOLDER) + r"[^>]*>\s*</script>",
                  lambda _: injected + f"<script>\n{js}</script>", html)
    csp = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    if re.search(r"<head\b[^>]*>", html, re.I):
        html = re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + csp, html, count=1, flags=re.I)
    else:
        html = csp + html
    lang = manifest.get("language")
    if lang and not re.search(r"<html\b[^>]*\blang=", html, re.I):
        html = re.sub(r"<html\b", f'<html lang="{lang}"', html, count=1, flags=re.I)
    return html


def media_refs(html: str) -> list[str]:
    scan = _Scan()
    scan.feed(html)
    return [name for _, _, name in scan.media]


def pack_card(card: Path, out: Path) -> dict:
    manifest = load_manifest(card)
    html = (card / "index.html").read_text("utf-8")
    manifests, errors = load_media(card)
    if errors:
        raise ValueError("; ".join(errors))
    page = pack_html(html, manifest, inline_media(card, manifests, media_refs(html)))
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "card.yaml").write_text((card / "card.yaml").read_text("utf-8"), encoding="utf-8")
    return {k: manifest.get(k) for k in ("id", "title", "summary", "questions", "anchors", "tags", "language")}
