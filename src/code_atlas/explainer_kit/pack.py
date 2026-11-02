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
prose it illustrates, its size is budgeted separately from the page — and so is the kit, which the author does not
control: a kit update must never push cards that were fine yesterday over the page budget.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from . import KIT_VERSION
from .media import stale, validate_scene

PKG = Path(__file__).parent
MAX_BYTES = 120_000            # the page the author writes: prose, tags, inline images; the kit is budgeted separately
MAX_MEDIA_BYTES = 2_000_000    # baked animation/chart/narration media, inlined at pack time
MAX_KIT_BYTES = 90_000         # v1.css + v1.js, inlined into every page; currently ~83 KB (see test_pack)
MEDIA = "media"
MEDIA_SCHEME = "atlas-media:"
MEDIA_TAGS = {"ex-anim": "anim", "ex-chart": "chart", "ex-scene": "scene"}
NARRATION = "narration"        # optional media/narration.json: one spoken clip per frame
VENDOR = PKG / MEDIA / "vendor"
RUNTIME = [VENDOR / "vega.min.js", VENDOR / "vega-interpreter.min.js"]
LICENSES = [VENDOR / "LICENSE-vega.txt", VENDOR / "LICENSE-vega-interpreter.txt"]
# `{script_src}` is the hashes of exactly the inline scripts pack writes; author scripts have none, so they cannot run.
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src {script_src}; "
       "img-src data:; font-src data:; media-src data:; base-uri 'none'; form-action 'none'")
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


def _srcset_allowed(value: str) -> bool:
    """Every srcset candidate must be an inline image. A data: URL contains commas of its own."""
    rest = value.strip()
    while rest:
        m = re.match(r"\s*(\S+)", rest)
        url, rest = m.group(1), rest[m.end():]
        separated = url.endswith(",")             # a comma right after the URL separates candidates
        if separated:
            url = url[:-1]
        if url and not url.lower().startswith("data:image/"):
            return False
        if not separated:
            comma = rest.find(",")                # skip descriptors (`2x`, `100w`) up to the next candidate
            rest = "" if comma < 0 else rest[comma + 1:]
    return True


def _url_allowed(attr: str, value: str, tag: str = "") -> bool:
    """Only fragments, atlas:// links, baked media names and inline images may appear in URL-bearing attributes."""
    if attr == "srcset":
        return _srcset_allowed(value)
    v = value.strip()
    if v.startswith("#"):
        return True
    if v.startswith(MEDIA_SCHEME):
        return attr == "src" and tag in MEDIA_TAGS
    if attr == "href" and v.startswith("atlas://"):
        return True
    return attr == "src" and v.lower().startswith("data:image/")


FOREIGN_TAGS = {"svg", "math"}
SMIL_TAGS = {"animate", "animatemotion", "animatetransform", "discard", "set"}
CANONICAL_CSS = f'<link rel="stylesheet" href="{CSS_PLACEHOLDER}">'
CANONICAL_JS = f'<script src="{JS_PLACEHOLDER}">'


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.has_css = self.has_js = False
        self.in_style = False
        self.foreign = 0                              # open <svg>/<math> elements: HTML rawtext does not apply there
        self.script_body: str | None = None           # text of the kit placeholder script, which must be empty
        self.media: list[tuple[str, int, str]] = []   # (tag, line, name)
        self._support_cdata = False                   # 3.13: a CDATA declaration would hide the markup after it

    def reset(self):
        super().reset()
        self._support_cdata = False                   # feed()/close() may reset; keep CDATA disabled

    def set_cdata_mode(self, elem, *args, **kwargs):
        # HTMLParser's rawtext/RCDATA modes assume HTML content; in SVG/MathML the same children are markup.
        if not self.foreign:
            super().set_cdata_mode(elem, *args, **kwargs)

    def err(self, msg: str):
        self.errors.append(f"index.html:{self.getpos()[0]}: {msg}")

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, self_closing=True)

    def _start(self, tag, attrs, self_closing):
        seen = set()
        for k, _ in attrs:
            if k in seen:
                self.err(f"<{tag}> repeats the attribute `{k}`; browsers keep the first value")
            seen.add(k)
        a = {k: (v or "") for k, v in attrs}
        raw = (self.get_starttag_text() or "").strip()
        if tag in FORBIDDEN_TAGS:
            self.err(f"<{tag}> is not allowed in a card")
        if tag == "meta" and a.get("http-equiv"):
            self.err("<meta http-equiv> is not allowed (pack adds the Content-Security-Policy)")
        for k in a:
            if k.startswith("on"):
                self.err(f"event handler attribute `{k}` is not allowed; cards run no custom scripts")
        if tag in SMIL_TAGS and (tag == "set" or a.get("attributename", "").lower() in URL_ATTRS):
            self.err(f"<{tag}> is not allowed: SMIL can retarget a URL attribute after lint")
        if tag == "link":
            if not self_closing and raw == CANONICAL_CSS:
                self.has_css = True
            else:
                self.err(f'<link> may only be `{CANONICAL_CSS}`')
        elif tag == "script":
            if self.foreign:
                self.err("cards run no custom scripts; <script> is not allowed inside <svg> or <math>")
            elif self_closing or raw != CANONICAL_JS:
                self.err(f'cards run no custom scripts; the only <script> is `{CANONICAL_JS}</script>`')
            else:
                self.has_js = True
                self.script_body = ""
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
        if tag in {"ex-table", *MEDIA_TAGS}:
            src = a.get("source")
            if src not in SOURCES:
                self.err(f'<{tag}> needs source="real" or source="illustrative"')
            elif src == "real" and not a.get("origin"):
                self.err(f'<{tag} source="real"> needs origin="…" saying where the data was read from (file, command or log)')
        if tag == "ex-stage" and not a.get("title"):
            self.err("<ex-stage> needs a title")
        if tag in FOREIGN_TAGS and not self_closing:
            self.foreign += 1

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        if tag == "script" and self.script_body is not None:
            if self.script_body.strip():
                self.err(f"the kit `<script>` must be empty; inline script bodies are not allowed")
            self.script_body = None
        if tag in FOREIGN_TAGS and self.foreign:
            self.foreign -= 1

    def handle_data(self, data):
        if self.in_style and CSS_EXTERNAL.search(data):
            self.err("<style> loads an external resource (@import / url(http…))")
        if self.script_body is not None:
            self.script_body += data

    def feed(self, data):
        if "<![" in data.lower():
            self.err("CDATA sections are not allowed in a card")   # 3.12 parses them even with CDATA support off
        super().feed(data)


_STRUCTURE_TOKENS = re.compile(r"<(/?)ex-(flow|compare|stage|before|after)\b", re.I)


def _structure_errors(html: str) -> list[str]:
    """One linear pass over the structural tags; a regex from each opener to its closer is quadratic on bad input."""
    errs, flows, compares = [], [], []      # flows: has an <ex-stage>; compares: [has <ex-before>, has <ex-after>]
    for m in _STRUCTURE_TOKENS.finditer(html):
        closing, name = bool(m.group(1)), m.group(2).lower()
        if name == "flow":
            if not closing:
                flows.append(False)
            elif flows and not flows.pop():
                errs.append("<ex-flow> contains no <ex-stage>")
        elif name == "stage":
            if not closing and flows:
                flows[-1] = True
        elif name == "compare":
            if not closing:
                compares.append([False, False])
            elif compares and not all(compares.pop()):
                errs.append("<ex-compare> needs both <ex-before> and <ex-after>")
        elif name == "before":
            if not closing and compares:
                compares[-1][0] = True
        elif not closing and compares:
            compares[-1][1] = True           # ex-after
    return errs


def load_manifest(card: Path) -> dict:
    return yaml.safe_load((card / "card.yaml").read_text("utf-8")) or {}


def media_path(card: Path, name: str) -> Path:
    """Resolve a flat media filename without reading outside the card, including through symlinks."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("media filename must be a basename inside media/")
    base = card.resolve() / MEDIA
    path = base / name
    if not base.resolve().is_relative_to(card.resolve()) or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"media/{name}: path leaves the card's media directory")
    return path


def sheet_path(card: Path, m: dict) -> Path:
    sheet = media_path(card, m.get("sheet"))
    if sheet.suffix.lower() not in _MIME:
        raise ValueError("animation sheet must be a raster image (webp/png/jpeg/gif)")
    if not sheet.is_file():
        raise ValueError(f"media/{m['id']}.json names `{m.get('sheet')}`, which is missing")
    return sheet


SVG_TAGS = set("svg g defs path rect circle ellipse line polyline polygon text tspan title desc clipPath "
               "linearGradient radialGradient stop image use a".split())
SVG_NS = "http://www.w3.org/2000/svg"
SAFE_URL = re.compile(r"#[A-Za-z0-9_.:-]+|data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+", re.I)


def _svg_errors(svg: str) -> list[str]:
    """Accept only passive SVG drawing elements and local references; never trust a baked JSON file."""
    if "<!" in svg:
        return ["SVG declarations/entities are not allowed"]
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return ["invalid SVG"]
    if root.tag not in ("svg", f"{{{SVG_NS}}}svg"):
        return ["SVG root must be <svg>"]
    for node in root.iter():
        tag = node.tag.removeprefix(f"{{{SVG_NS}}}")
        if tag not in SVG_TAGS:
            return [f"SVG element <{tag}> is not allowed"]
        for attr, value in node.attrib.items():
            key = attr.rsplit("}", 1)[-1].lower()
            if key.startswith("on") or key in {"style", "base", "src", "srcset"}:
                return [f"SVG attribute `{key}` is not allowed"]
            if key == "href" and not SAFE_URL.fullmatch(value):
                return ["SVG href must be a fragment or inline raster image"]
            if "url(" in value.lower() and not re.fullmatch(r"url\(#[A-Za-z0-9_.:-]+\)", value):
                return ["SVG url() must refer to a local fragment"]
    return []


def _vega_errors(value) -> list[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "values":
                continue  # Inline rows may have ordinary fields called url/href; they are data, not loaders.
            if key in {"url", "href"} and (not isinstance(item, str) or not SAFE_URL.fullmatch(item)):
                return ["Vega URL must be a fragment or inline raster image; bake all data inline"]
            errors = _vega_errors(item)
            if errors:
                return errors
    elif isinstance(value, list):
        for item in value:
            errors = _vega_errors(item)
            if errors:
                return errors
    return []


def _narration_errors(m: dict) -> list[str]:
    frames = m.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 500:
        return ["narration needs 1–500 frames"]
    for i, f in enumerate(frames):
        if not isinstance(f, dict):
            return [f"narration frame {i + 1} is not an object"]
        text, audio = f.get("text"), f.get("audio")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            return [f"narration frame {i + 1} needs text (1–2000 characters)"]
        if not isinstance(audio, str) or not audio.startswith("data:audio/"):
            return [f"narration frame {i + 1} needs an inline audio clip (data:audio/…)"]
        ms = f.get("ms")
        if ms is not None and (type(ms) not in (int, float) or not math.isfinite(ms) or ms <= 0):
            return [f"narration frame {i + 1}: ms must be a positive number"]
    if m.get("voice") is not None and not isinstance(m.get("voice"), str):
        return ["narration voice must be a string"]
    return []


def _media_errors(m: dict) -> list[str]:
    if not isinstance(m, dict) or m.get("kind") not in ("anim", "chart", "scene", NARRATION):
        return ["not a baked asset (`kind` must be `anim`, `chart`, `scene` or `narration`); run `atlas-explainer bake`"]
    sources = m.get("sources", {})
    if not isinstance(sources, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in sources.items()):
        return ["sources must map card-relative filenames to hashes"]
    if m["kind"] == NARRATION:
        return _narration_errors(m)
    if m["kind"] == "scene":
        return validate_scene(m.get("scene"))
    if m["kind"] == "anim":
        for key in ("width", "height", "columns", "frames"):
            if type(m.get(key)) is not int or m[key] <= 0:
                return [f"animation {key} must be a positive integer"]
        if not isinstance(m.get("sheet"), str):
            return ["animation needs a sheet filename"]
        for key in ("fps", "ms"):
            if type(m.get(key)) not in (int, float) or not math.isfinite(m[key]) or m[key] <= 0:
                return [f"animation {key} must be a positive finite number"]
        if not isinstance(m.get("captions", []), list) or any(not isinstance(c, str) for c in m.get("captions", [])):
            return ["animation captions must be strings"]
        durations = m.get("durations")
        if durations is not None and (not isinstance(durations, list) or len(durations) != m["frames"] or any(
                type(t) not in (int, float) or not math.isfinite(t) or t <= 0 for t in durations)):
            return ["animation durations must contain one positive millisecond duration per frame"]
    else:
        svg = m.get("svg")
        if not isinstance(svg, dict) or set(svg) != {"light", "dark"} or any(not isinstance(s, str) for s in svg.values()):
            return ["chart needs SVG strings for light and dark themes"]
        for s in svg.values():
            errors = _svg_errors(s)
            if errors:
                return errors
        vega = m.get("vega")
        if vega is not None:
            if not isinstance(vega, dict) or set(vega) != {"light", "dark"} or any(not isinstance(v, dict) for v in vega.values()):
                return ["chart Vega specs must map light/dark to objects"]
            errors = _vega_errors(vega)
            if errors:
                return errors
        if not isinstance(m.get("tooltip", []), list) or any(not isinstance(f, str) for f in m.get("tooltip", [])):
            return ["chart tooltip fields must be strings"]
    return []


def load_media(card: Path) -> tuple[dict[str, dict], list[str]]:
    """Baked manifests in `media/`, by name, plus whatever is wrong with the files themselves."""
    out: dict[str, dict] = {}
    errors: list[str] = []
    d = card / MEDIA
    if not d.is_dir():
        return out, errors
    page = card / "index.html"
    try:
        names = set(media_refs(page.read_text("utf-8"))) if page.is_file() else set()
    except (OSError, UnicodeError) as e:
        return out, [f"index.html: unreadable ({e})"]
    if (d / f"{NARRATION}.json").is_file():
        names.add(NARRATION)             # narration is a card-level track, not a tag on the page
    # Only referenced names are required manifests. Other JSON files may be authored chart data.
    for name in sorted(names):
        try:
            p = media_path(card, name + ".json")
        except ValueError as e:
            errors.append(str(e))
            continue
        if p.name.endswith(".vl.json"):
            continue                     # an authored Vega-Lite spec, not a baked asset
        try:
            m = json.loads(p.read_text("utf-8"))
        except OSError as e:
            errors.append(f"media/{p.name}: not baked or unreadable ({e})")
            continue
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append(f"media/{p.name}: not valid JSON ({e})")
            continue
        except RecursionError:
            errors.append(f"media/{p.name}: nested too deeply")
            continue
        try:
            invalid = _media_errors(m)
        except RecursionError:
            errors.append(f"media/{p.name}: nested too deeply")
            continue
        if invalid:
            errors.extend(f"media/{p.name}: {e}" for e in invalid)
            continue
        if m.get("id") != name:
            errors.append(f"media/{p.name}: id `{m.get('id')}` must equal the file name `{name}`")
            continue
        if m["kind"] == "anim":
            try:
                sheet_path(card, m)
            except (ValueError, OSError) as e:
                errors.append(f"media/{p.name}: {e}")
                continue
        out[name] = m
    return out, errors


_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


def _data_uri(path: Path) -> str:
    return f"data:{_MIME.get(path.suffix.lower(), 'application/octet-stream')};base64," + \
        base64.b64encode(path.read_bytes()).decode("ascii")


def inline_media(card: Path, media: dict[str, dict], names) -> dict[str, dict]:
    """The manifests as the page carries them: the sprite sheet becomes a data URI, nothing points at a file."""
    out: dict[str, dict] = {}
    wanted = set(names)
    if NARRATION in media:
        wanted.add(NARRATION)
    for name in sorted(wanted):
        m = copy.deepcopy(media[name])
        if m["kind"] == "anim":
            sheet = sheet_path(card, m)
            m["sheet"] = _data_uri(sheet)
        out[name] = m
    return out


def media_size(card: Path, m: dict) -> int:
    """What the manifest costs once inlined: sprite sheet, SVG/Vega spec, or a scene's few kilobytes of JSON."""
    if m["kind"] == "anim":
        sheet = sheet_path(card, m)
        return len(json.dumps(m).encode("utf-8")) + 4 * ((sheet.stat().st_size + 2) // 3)
    return len(json.dumps(m, ensure_ascii=False).encode("utf-8"))


def lint_media(card: Path, scan: _Scan) -> list[str]:
    problems: list[str] = []
    media, errors = load_media(card)
    problems += errors
    if not scan.media and NARRATION not in media:
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
        if m["kind"] == "chart" and m.get("vega") and not all(p.is_file() for p in RUNTIME + LICENSES):
            problems.append("media/: the Vega runtime is missing from the tool "
                            f"({', '.join(str(p.name) for p in RUNTIME + LICENSES)}); reinstall atlas-explainer")
        changed = _stale(card, m)
        if changed:
            problems.append(f"media/{name}.json: stale — {', '.join(changed[:3])} changed after the last bake; "
                            "run `atlas-explainer bake`")
    used = {name for _, _, name in scan.media if name in media}
    if NARRATION in media:
        used.add(NARRATION)
        changed = _stale(card, media[NARRATION])
        if changed:
            problems.append("media/narration.json: stale - re-run the narration step")
    size = sum(media_size(card, media[n]) for n in used)
    if any(media[n].get("vega") for n in used):
        size += sum(p.stat().st_size for p in RUNTIME + LICENSES if p.is_file())
    if size > MAX_MEDIA_BYTES:
        problems.append(f"inlined media is {size:,} bytes; the limit is {MAX_MEDIA_BYTES:,} "
                        "(fewer frames, a smaller sheet width, or a static chart instead of an interactive one)")
    return problems


def _stale(card: Path, m: dict) -> list[str]:
    return stale(card, m)


def kit_bytes() -> int:
    """Size of the kit as inlined: it costs every page the same, so no author is charged for it."""
    return sum(len((PKG / "kit" / f"v{KIT_VERSION}.{ext}").read_text("utf-8").encode("utf-8"))
               for ext in ("css", "js"))


def _authored_size(html: str, manifest: dict) -> int:
    """The bytes `pack_html(..., media={}, kit=False)` would write, without its raw-text tag scans.

    Those scans (`<link[^>]*…`, `<script[^>]*…`) retry the tail for every prefix when a card has thousands of
    unterminated tags; this only handles the canonical placeholders, which lint has already enforced.
    """
    page = html.replace(CANONICAL_CSS, "<style>\n</style>")
    page, inlined = re.subn(re.escape(CANONICAL_JS) + r"\s*</script>", "<script>\n</script>", page)
    prefix = "<!doctype html>\n"
    lang = manifest.get("language")
    if lang and not _declares_lang(html):
        lang = str(lang).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        prefix += f'<html lang="{lang}">\n'
    prefix += ('<meta charset="utf-8">\n'
               f'<meta http-equiv="Content-Security-Policy" content="{_csp(["\n"] if inlined else [])}">\n')
    return len((prefix + page).encode("utf-8"))


def lint_card(card: Path) -> list[str]:
    problems: list[str] = []
    try:
        manifest = load_manifest(card)
    except UnicodeError:
        return ["card.yaml: not UTF-8"]
    except yaml.YAMLError as e:
        return [f"card.yaml: not valid YAML ({e})"]
    except (OSError, RecursionError) as e:
        return [f"card.yaml: unreadable or nested too deeply ({e})"]
    try:
        schema_errors = sorted(_schema.iter_errors(manifest), key=lambda e: [str(p) for p in e.path])
    except RecursionError:
        return ["card.yaml: nested too deeply"]
    for e in schema_errors:
        where = ".".join(str(p) for p in e.path) or "(root)"
        problems.append(f"card.yaml: {where}: {e.message}")
    if isinstance(manifest, dict) and manifest.get("id") and manifest["id"] != card.name:
        problems.append(f"card.yaml: id `{manifest['id']}` must equal the directory name `{card.name}`")
    page = card / "index.html"
    if not page.exists():
        return problems + ["index.html is missing"]
    try:
        html = page.read_text("utf-8")
    except UnicodeError:
        return problems + ["index.html: not UTF-8"]
    except OSError as e:
        return problems + [f"index.html: unreadable ({e})"]
    # Bound the work before any scan: a page over the budget cannot pass, and the old order let a 200 KB card spend
    # tens of seconds in quadratic raw-text scans first.
    raw = len(html.encode("utf-8"))
    if raw > MAX_BYTES:
        return problems + [f"index.html is {raw:,} bytes; the limit is {MAX_BYTES:,} "
                           "(fewer rows, smaller images)"]
    scan = _Scan()
    scan.feed(html)
    problems += scan.errors
    if not scan.has_css:
        problems.append(f'index.html: missing `{CANONICAL_CSS}`')
    # pack replaces the placeholder with a raw-text regex, so require the exact end tag the browser and pack see.
    if not scan.has_js or not re.search(re.escape(CANONICAL_JS) + r"\s*</script>", html):
        problems.append(f'index.html: missing `{CANONICAL_JS}</script>`')
    problems += ["index.html: " + e for e in _structure_errors(html)]
    for pat, what in ((r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b", "network API"),
                      (r"\beval\s*\(|new\s+Function\b", "dynamic code")):
        if re.search(pat, html):
            problems.append(f"index.html: contains a {what} reference")
    problems += lint_media(card, scan)
    authored = _authored_size(html, manifest if isinstance(manifest, dict) else {})
    if authored > MAX_BYTES:
        problems.append(f"card content is {authored:,} bytes once packed, without the kit; the limit is {MAX_BYTES:,} "
                        "(fewer rows, smaller images)")
    return problems


def _script_hash(src: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(src.encode("utf-8")).digest()).decode("ascii") + "'"


def _csp(scripts: list[str]) -> str:
    return CSP.format(script_src=" ".join(_script_hash(s) for s in scripts) or "'none'")


def _declares_lang(html: str) -> bool:
    """Whether the card's real <html> start tag declares lang; text and attribute values do not count."""
    class Scan(HTMLParser):
        found = False

        def handle_starttag(self, tag, attrs):
            if tag == "html" and any(k == "lang" for k, _ in attrs):
                self.found = True

    scan = Scan()
    scan.feed(html)
    return scan.found


def pack_html(html: str, manifest: dict, media: dict[str, dict] | None = None, kit: bool = True) -> str:
    """Inline the kit, the media registry and the CSP.

    `media` is the output of `inline_media`; pass `{}` to measure the page on its own. `None` means the caller does not
    handle media at all, so a card that uses some is an error rather than a page that silently loses its pictures.
    `kit=False` leaves the kit out, which is what the page budget measures: the kit and the Vega runtime are charged
    to their own budgets, not to the author.

    The packed document starts with its own doctype, charset and CSP, before any byte the author wrote, so no author
    markup can move or drop the policy; the author's own doctype/html/head tokens are then harmless parse errors. The
    CSP allows only the hashes of the inline scripts written here: the kit and, when a chart asks for it, the Vega
    runtime. Lint is still the first line of defence, but anything it misses (an event-handler attribute, a
    javascript: URL, an inline <script> in foreign content) cannot execute.
    """
    css = (PKG / "kit" / f"v{KIT_VERSION}.css").read_text("utf-8") if kit else ""
    js = (PKG / "kit" / f"v{KIT_VERSION}.js").read_text("utf-8") if kit else ""
    if media is None:
        if MEDIA_SCHEME in html:
            raise ValueError("this card uses baked media; pass `media=inline_media(...)` to pack_html")
        media = {}
    injected = ""
    scripts: list[str] = []       # the exact text of every inline script written below, hashed into script-src
    if media:
        for name, m in media.items():
            invalid = _media_errors(m)
            if invalid:
                raise ValueError(f"media/{name}: {'; '.join(invalid)}")
        if any(m.get("vega") for m in media.values()):
            notices = "\n\n".join(p.read_text("utf-8") for p in LICENSES)
            injected += "<!-- Third-party licenses\n" + notices.replace("--", "&#45;&#45;") + "\n-->\n"
            runtime = "\n".join(p.read_text("utf-8") for p in RUNTIME)
            injected += "<script>\n" + runtime + "\n</script>\n"
            scripts.append("\n" + runtime + "\n")
        # Every "<" escaped: "</script" would end the element and "<!--<script" would make it swallow the kit after it.
        payload = json.dumps(media, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        injected += f'<script type="application/json" id="atlas-media-registry">{payload}</script>\n'
    html = re.sub(r"<link\b[^>]*" + re.escape(CSS_PLACEHOLDER) + r"[^>]*>", lambda _: f"<style>\n{css}</style>", html)
    block = injected + f"<script>\n{js}</script>"
    html, inlined = re.subn(r"<script\b[^>]*" + re.escape(JS_PLACEHOLDER) + r"[^>]*>\s*</script>",
                            lambda _: block, html)
    if inlined:
        scripts.append("\n" + js)
    prefix = "<!doctype html>\n"
    lang = manifest.get("language")
    if lang and not _declares_lang(html):
        lang = str(lang).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        prefix += f'<html lang="{lang}">\n'
    prefix += ('<meta charset="utf-8">\n'
               f'<meta http-equiv="Content-Security-Policy" content="{_csp(scripts)}">\n')
    return prefix + html


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
