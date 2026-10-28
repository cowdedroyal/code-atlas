"""Bake authored media into something a card can carry.

Two kinds of explanation need more than HTML: a short animation ("what happens between these two states") and a
chart of real data ("how often, how much, compared to what"). Neither library may run in the reader — a card is one
self-contained page with no network and no scripts of its own — so the heavy tools run here, at authoring time:

| Written with | Runs during | Becomes |
|---|---|---|
| [Motion Canvas](https://github.com/motion-canvas/motion-canvas) (MIT), Manim (MIT), or any tool that writes frames | `atlas-explainer bake` | one sprite sheet (`media/<name>.webp`) + frame grid |
| [Vega-Lite](https://github.com/vega/vega-lite) (BSD-3) | `atlas-explainer bake` | inline SVG per theme, plus a compiled Vega spec for interaction |

`pack` inlines the baked files, so the card stays a single file that opens anywhere. Baking is never implicit: a
card with no `media/` directory is unaffected, and the tools are optional extras (`uv sync --extra media`).

Source layout (everything except the two baked outputs is optional):

    media/bake.yaml            how to produce each asset, when convention is not enough
    media/src/<name>/…         the animation project (Motion Canvas), the scene (Manim), or frames
    media/<name>.vl.json       a Vega-Lite spec; `data.url` files next to it are inlined
    media/<name>.csv|json      data that spec reads
    media/<name>.json          BAKED manifest — commit it
    media/<name>.webp          BAKED sprite sheet — commit it
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from bisect import bisect_right
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

MAX_FRAMES = 240              # longer animations are subsampled; a card is a gloss, not a film
SHEET_MAX_WIDTH = 2048        # keeps the sheet inside what every browser will decode
SHEET_MAX_HEIGHT = 16383      # WebP's hard per-dimension limit
SHEET_MAX_PIXELS = 16_000_000 # bound decoded memory as well as encoded bytes
SHEET_BUDGET = 900_000        # bytes; quality steps down until the sheet fits
DEFAULT_FPS = 12
DEFAULT_WIDTH = 560
CHART_WIDTH = 620             # the card's column is ~46rem; a chart baked this wide reads well and still scales down
CHART_HEIGHT = 280
MEDIA_DIR = "media"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".gif"}
TEXT_SUFFIXES = {".yaml", ".yml", ".json", ".csv", ".tsv", ".ts", ".tsx", ".js", ".mjs", ".py", ".md", ".txt"}
MANIFEST_SUFFIX = ".json"
SPEC_SUFFIX = ".vl.json"
CONFIG = "bake.yaml"

# The kit's palette, so a chart looks like the card around it and follows the reader's theme. A card may override any
# of it: its own `config` in the Vega-Lite spec is merged on top.
BASE_CONFIG = {
    "background": "transparent",
    "font": 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    "view": {"stroke": None},
    "axis": {"labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 500, "grid": True},
    "legend": {"labelFontSize": 11, "titleFontSize": 11},
    "title": {"fontSize": 13, "anchor": "start"},
}
THEME_CONFIG = {
    "light": {
        "axis": {"labelColor": "#667085", "titleColor": "#667085", "domainColor": "#dcdcd4",
                 "gridColor": "#e8e8e3", "tickColor": "#dcdcd4"},
        "legend": {"labelColor": "#667085", "titleColor": "#667085"},
        "title": {"color": "#1d2127"},
        "range": {"category": ["#2a6f97", "#e07a5f", "#7f9c6f", "#9a6700", "#7b6d8d", "#3d5a80", "#b3402f", "#5f8a8b"]},
    },
    "dark": {
        "axis": {"labelColor": "#98a2b3", "titleColor": "#98a2b3", "domainColor": "#2f353d",
                 "gridColor": "#272c33", "tickColor": "#2f353d"},
        "legend": {"labelColor": "#98a2b3", "titleColor": "#98a2b3"},
        "title": {"color": "#e4e6ea"},
        "range": {"category": ["#6aa9cf", "#ec8b7a", "#a3c46a", "#e0b04a", "#b3a1c9", "#8fb3d9", "#e17f6d", "#8fc7c8"]},
    },
}


class BakeError(Exception):
    """A source the author controls is missing, unreadable, or produced nothing usable."""


def _hash(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")   # a Windows checkout must not make every card stale
    return hashlib.sha256(data).hexdigest()[:16]


def _rel(card: Path, path: Path) -> str:
    return path.resolve().relative_to(card.resolve()).as_posix()


def _sources(card: Path, paths: list[Path]) -> dict[str, str]:
    seen: dict[str, str] = {}
    for p in paths:
        if p.is_file():
            seen[_rel(card, p)] = _hash(p)
    return seen


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def bake_config(card: Path) -> dict:
    """`media/bake.yaml`, or `{}` when the card relies on conventions alone."""
    p = card / MEDIA_DIR / CONFIG
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text("utf-8")) or {}
    except yaml.YAMLError as e:
        raise BakeError(f"media/{CONFIG}: not valid YAML ({e})") from e
    if not isinstance(data, dict):
        raise BakeError(f"media/{CONFIG}: must be a mapping of `anim:` / `chart:` entries")
    for key in data:
        if key not in ("anim", "chart", "scene"):
            raise BakeError(f"media/{CONFIG}: unknown key `{key}` (expected `anim`, `chart` or `scene`)")
        if not isinstance(data[key], dict):
            raise BakeError(f"media/{CONFIG}: `{key}` must map names to settings")
    return data


def anim_names(card: Path, cfg: dict) -> list[str]:
    names = set(cfg.get("anim") or {})
    src = card / MEDIA_DIR / "src"
    if src.is_dir():
        names.update(d.name for d in src.iterdir() if d.is_dir() and _auto_frames(d))
    return sorted(names)


def chart_names(card: Path, cfg: dict) -> list[str]:
    names = set(cfg.get("chart") or {})
    media = card / MEDIA_DIR
    if media.is_dir():
        names.update(p.name[: -len(SPEC_SUFFIX)] for p in media.glob("*" + SPEC_SUFFIX))
    return sorted(names)


def _auto_frames(project: Path) -> list[Path]:
    return sorted(p for p in project.glob("frames/*") if p.suffix.lower() in IMAGE_SUFFIXES)


def _frames_from_dir(card: Path, pattern: str) -> list[Path]:
    matches = sorted(card.glob(pattern)) if pattern else []
    frames = [p for p in matches if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    if not frames:
        raise BakeError(f"no frames match `{pattern}` (expected png/jpg/webp stills)")
    return frames


def _frames_from_video(path: Path, tmp: Path) -> tuple[list[Path], list[float]]:
    """Decode a video (Manim's output is one) into stills, without requiring ffmpeg on PATH."""
    try:
        import imageio.v3 as iio
        from PIL import Image
    except ImportError as e:  # pragma: no cover - depends on the optional extra
        raise BakeError("reading a video needs the media extra: `uv sync --extra media`") from e
    try:
        meta = iio.immeta(path)
        fps = float(meta.get("fps") or 0)
        if path.suffix.lower() != ".gif" and (not math.isfinite(fps) or fps <= 0):
            raise BakeError(f"{path.name}: video does not report a valid frame rate")
        durations = []
        for i, frame in enumerate(iio.imiter(path)):
            Image.fromarray(frame).save(tmp / f"{i:05d}.png")
            duration = (float(iio.immeta(path, index=i).get("duration", 100))
                        if path.suffix.lower() == ".gif" else 1000 / fps)
            if not math.isfinite(duration) or duration <= 0:
                raise BakeError(f"{path.name}: invalid duration for frame {i}")
            durations.append(duration)
        if not durations:
            raise BakeError(f"{path.name} decoded to no frames")
        return sorted(tmp.glob("*.png")), durations
    except BakeError:
        raise
    except Exception as e:  # imageio raises plugin-specific errors
        raise BakeError(f"cannot read {path.name}: {e}") from e


def _run(card: Path, command: str) -> None:
    """Run the author's declared render command. Only `bake` does this, and only when asked."""
    if not command:
        return
    args = command if isinstance(command, str) else " ".join(shlex.quote(a) for a in command)
    r = subprocess.run(args, cwd=card, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-8:]
        raise BakeError(f"`{args}` failed (exit {r.returncode}):\n" + "\n".join(tail))


def _sheet(frames: list[Path], width: int):
    """Lay the frames out in a grid and encode it small enough to inline."""
    from PIL import Image

    first = Image.open(frames[0])
    w, h = first.size
    first.close()
    if width and width != w:
        h = max(1, round(h * width / w))
        w = width
    # Reducing JPEG/WebP quality cannot fix an oversized image. Shrink cells until the entire grid fits.
    while True:
        columns = max(1, min(len(frames), SHEET_MAX_WIDTH // max(w, 1)))
        rows = (len(frames) + columns - 1) // columns
        if columns * w <= SHEET_MAX_WIDTH and rows * h <= SHEET_MAX_HEIGHT and columns * w * rows * h <= SHEET_MAX_PIXELS:
            break
        w, h = max(1, int(w * .9)), max(1, int(h * .9))
    sheet = Image.new("RGBA", (columns * w, rows * h), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        with Image.open(f) as source:
            im = source.convert("RGBA")
        if im.size != (w, h):
            im = im.resize((w, h), Image.LANCZOS)
        sheet.paste(im, ((i % columns) * w, (i // columns) * h))
        im.close()
    data = b""
    for quality in (80, 65, 50, 40):
        import io

        buf = io.BytesIO()
        try:
            sheet.save(buf, format="WEBP", quality=quality, method=6)
        except (ValueError, OSError) as e:
            raise BakeError(f"cannot encode animation sheet ({sheet.width}x{sheet.height}): {e}") from e
        data = buf.getvalue()
        if len(data) <= SHEET_BUDGET:
            break
    return w, h, columns, data


def _subsample(frames: list[Path], fps: float) -> tuple[list[Path], float]:
    if len(frames) <= MAX_FRAMES:
        return frames, fps
    step = len(frames) / MAX_FRAMES
    picked = [frames[min(len(frames) - 1, int(i * step))] for i in range(MAX_FRAMES)]
    return picked, fps / step


def _sample_video(frames: list[Path], durations: list[float], fps: float) -> tuple[list[Path], list[float]]:
    """Sample by source time, retaining the complete clip duration at any target FPS or frame limit."""
    total = sum(durations)
    count = min(MAX_FRAMES, max(1, math.ceil(total * fps / 1000 - 1e-8)))
    starts, at = [], 0.0
    for duration in durations:
        starts.append(at)
        at += duration
    picked = [frames[min(len(frames) - 1, bisect_right(starts, i * total / count) - 1)] for i in range(count)]
    return picked, [total / count] * count


def bake_anim(card: Path, name: str, cfg: dict | None = None) -> dict:
    """Sprite-sheet one animation: run its declared command, collect frames, write `media/<name>.*`."""
    cfg = cfg or {}
    if cfg.get("run"):
        _run(card, cfg["run"])
    sources: list[Path] = []
    fps = float(cfg.get("fps") or DEFAULT_FPS)
    if not math.isfinite(fps) or fps <= 0:
        raise BakeError(f"{name}: fps must be positive and finite")
    durations = None
    video = cfg.get("video")
    tmp = tempfile.TemporaryDirectory(prefix="atlas-frames-")   # decoded video frames never live in the card
    try:
        if video:
            paths = sorted(p for p in card.glob(video) if p.suffix.lower() in VIDEO_SUFFIXES)
            if not paths:
                raise BakeError(f"no video matches `{video}`")
            frames, durations = _frames_from_video(paths[0], Path(tmp.name))
            frames, durations = _sample_video(frames, durations, fps)
            fps = 1000 / durations[0]
            sources += paths
        elif cfg.get("frames"):
            frames = _frames_from_dir(card, cfg["frames"])
        else:
            project = card / MEDIA_DIR / "src" / name
            frames = _auto_frames(project)
            if not frames:
                raise BakeError(
                    f"{name}: no frames. Render the scene to stills (`frames/` under media/src/{name}/), or set "
                    f"`anim.{name}.frames` / `anim.{name}.video` in media/bake.yaml")
        sheet_sources = list(frames) if not video else []
        original_count = len(frames)
        frames, fps = _subsample(frames, fps)
        width, height, columns, data = _sheet(frames, int(cfg.get("width") or DEFAULT_WIDTH))
    finally:
        tmp.cleanup()
    sheet = card / MEDIA_DIR / f"{name}.webp"
    sheet.write_bytes(data)
    captions = [str(c) for c in (cfg.get("captions") or [])][: len(frames)]
    if not video and original_count > len(frames):
        authored = [str(c) for c in (cfg.get("captions") or [])]
        captions = [authored[int(i * original_count / len(frames))] if int(i * original_count / len(frames)) < len(authored)
                    else "" for i in range(len(frames))]
    manifest = {
        "kind": "anim",
        "id": name,
        "sheet": f"{name}.webp",
        "width": width,
        "height": height,
        "columns": columns,
        "frames": len(frames),
        "fps": round(fps, 3),
        "ms": 1000 / fps,
        "captions": captions,
        "baked_at": _now(),
        "sources": _sources(card, sources + sheet_sources + ([card / MEDIA_DIR / CONFIG] if (card / MEDIA_DIR / CONFIG).exists() else [])),
    }
    if durations is not None:
        manifest["durations"] = durations
    (card / MEDIA_DIR / f"{name}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return {"kind": "anim", "frames": len(frames), "bytes": len(data), "sheet": f"media/{name}.webp"}


def _inline_data(spec: dict, base: Path, used: list[Path]) -> dict:
    """Replace `data.url` with the file's values, so the card never needs to fetch anything."""
    if isinstance(spec, dict):
        out = {}
        for k, v in spec.items():
            if k == "data" and isinstance(v, dict) and isinstance(v.get("url"), str) and "values" not in v:
                out[k] = _load_data(v["url"], base)
                used.append((base / v["url"]).resolve())
            else:
                out[k] = _inline_data(v, base, used)
        return out
    if isinstance(spec, list):
        return [_inline_data(v, base, used) for v in spec]
    return spec


def _number(s: str):
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _load_data(url: str, base: Path) -> dict:
    if "://" in url or url.startswith("data:"):
        raise BakeError(f"data url `{url}` is remote; download the data next to the spec so it can be inlined")
    path = (base / url).resolve()
    if not path.is_file():
        raise BakeError(f"data file `{url}` is missing next to the spec")
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = [{k: _number(v) for k, v in row.items()} for row in csv.DictReader(f)]
        return {"values": rows}
    if path.suffix.lower() in (".json", ".geojson"):
        return {"values": json.loads(path.read_text("utf-8"))}
    raise BakeError(f"`{url}`: only csv and json data can be inlined")


def _tooltip_fields(spec: dict) -> list[str]:
    """The fields the spec asks to show on hover, so the reader's tooltip needs no guesswork."""
    fields: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            enc = node.get("encoding")
            if isinstance(enc, dict):
                tip = enc.get("tooltip")
                items = tip if isinstance(tip, list) else ([tip] if isinstance(tip, dict) else [])
                for it in items:
                    if isinstance(it, dict) and isinstance(it.get("field"), str) and it["field"] not in fields:
                        fields.append(it["field"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(spec)
    return fields[:8]


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def bake_chart(card: Path, name: str, cfg: dict | None = None) -> dict:
    """Compile one Vega-Lite spec into per-theme SVG, and into per-theme Vega specs for interaction."""
    cfg = cfg or {}
    try:
        import vl_convert
    except ImportError as e:
        raise BakeError("charts need the media extra: `uv sync --extra media`") from e
    spec_path = card / MEDIA_DIR / str(cfg.get("spec") or f"{name}{SPEC_SUFFIX}")
    if not spec_path.is_file():
        raise BakeError(f"media/{spec_path.name} is missing")
    try:
        spec = json.loads(spec_path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise BakeError(f"media/{spec_path.name}: not valid JSON ({e})") from e
    if not isinstance(spec, dict):
        raise BakeError(f"media/{spec_path.name}: a Vega-Lite spec is a JSON object")
    used: list[Path] = []
    author_config = spec.get("config") if isinstance(spec.get("config"), dict) else {}
    body = _inline_data({k: v for k, v in spec.items() if k != "config"}, spec_path.parent, used)
    svg, vega = {}, {}
    for theme, colors in THEME_CONFIG.items():
        themed = dict(body, config=_merge(_merge(BASE_CONFIG, colors), author_config))
        # Size is a design decision the spec may make; otherwise fill the card's column instead of Vega-Lite's
        # 20px-per-band default, which would bake a 60px-wide chart and then be stretched.
        themed.setdefault("width", CHART_WIDTH)
        themed.setdefault("height", CHART_HEIGHT)
        if themed["width"] == "container":
            themed["width"] = CHART_WIDTH
        try:
            svg[theme] = vl_convert.vegalite_to_svg(themed)
            if cfg.get("interactive"):
                vega[theme] = vl_convert.vegalite_to_vega(themed)
        except Exception as e:  # vl-convert reports compile errors as ValueError
            raise BakeError(f"media/{spec_path.name}: {e}") from e
    manifest = {
        "kind": "chart",
        "id": name,
        "svg": svg,
        "vega": vega or None,
        "tooltip": _tooltip_fields(spec),
        "baked_at": _now(),
        "sources": _sources(card, used + [spec_path] + ([card / MEDIA_DIR / CONFIG] if (card / MEDIA_DIR / CONFIG).exists() else [])),
    }
    (card / MEDIA_DIR / f"{name}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return {"kind": "chart", "svg_bytes": sum(len(s) for s in svg.values()),
            "interactive": bool(vega), "bytes": (card / MEDIA_DIR / f"{name}.json").stat().st_size}


# ---- scenes: a click-through diagram, drawn live ------------------------------------------------
#
# A frame animation is a video: the explanation is baked into pixels, cannot be searched or translated, and the reader
# can only watch it. A scene says the same thing as data instead — labelled boxes, arrows and values — and the reader
# clicks through the steps, each of which names what changed and why. The kit draws it as SVG from these numbers, so it
# follows the theme, the language and the reader's text size, and costs a few kilobytes instead of a few hundred.

SCENE_KINDS = {"box", "chip", "label", "dot", "bar"}
SCENE_TONES = {"default", "accent", "own", "ok", "warn", "del", "muted"}
SCENE_DEFAULT_SIZE = [720, 300]
SCENE_NODE_SIZE = {"box": [128, 56], "chip": [56, 32], "label": [0, 0], "dot": [14, 14], "bar": [180, 16]}
SCENE_MAX_NODES, SCENE_MAX_EDGES, SCENE_MAX_STEPS = 40, 60, 12


def _pair(value) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value):
        return [float(value[0]), float(value[1])]
    return None


def _text(value, limit: int) -> str | None:
    if isinstance(value, str) and len(value) <= limit:
        return value
    return None


def normalize_scene(raw: dict) -> dict:
    """Fill in what an author may leave out, so validation and the kit see one shape."""
    scene = {"size": _pair(raw.get("size")) or list(SCENE_DEFAULT_SIZE), "nodes": [], "edges": [], "steps": []}
    for node in raw.get("nodes") or []:
        if not isinstance(node, dict):
            scene["nodes"].append(node)
            continue
        kind = node.get("kind", "box")
        scene["nodes"].append({
            "id": node.get("id"),
            "kind": kind,
            "at": _pair(node.get("at")) or [0, 0],
            "size": _pair(node.get("size")) or list(SCENE_NODE_SIZE.get(kind, SCENE_NODE_SIZE["box"])),
            "text": node.get("text", ""),
            "sub": node.get("sub"),
            "tone": node.get("tone", "default"),
            "value": node.get("value", 1 if kind == "bar" else None),
        })
    for edge in raw.get("edges") or []:
        if not isinstance(edge, dict):
            scene["edges"].append(edge)
            continue
        scene["edges"].append({"from": edge.get("from"), "to": edge.get("to"), "label": edge.get("label"),
                               "dashed": bool(edge.get("dashed"))})
    for step in raw.get("steps") or []:
        if not isinstance(step, dict):
            scene["steps"].append(step)
            continue
        scene["steps"].append({
            "title": step.get("title", ""),
            "detail": step.get("detail"),
            "show": list(step.get("show") or []),
            "hide": list(step.get("hide") or []),
            "focus": list(step["focus"]) if "focus" in step else None,
            "move": step.get("move") or {},
            "text": step.get("text") or {},
            "sub": step.get("sub") or {},
            "value": step.get("value") or {},
            "tone": step.get("tone") or {},
        })
    return scene


def validate_scene(scene) -> list[str]:
    """Everything a baked scene must satisfy for the kit to draw it without guessing."""
    if not isinstance(scene, dict):
        return ["a scene is an object with size, nodes, edges and steps"]
    errors: list[str] = []
    size = _pair(scene.get("size"))
    if not size or not all(100 <= v <= 4000 for v in size):
        errors.append("size must be [width, height] between 100 and 4000")
        size = SCENE_DEFAULT_SIZE
    nodes = scene.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= SCENE_MAX_NODES:
        return errors + [f"nodes must be a list of 1–{SCENE_MAX_NODES} entries"]
    ids: set[str] = set()
    for i, node in enumerate(nodes):
        where = f"nodes[{i}]"
        if not isinstance(node, dict):
            errors.append(f"{where}: not an object")
            continue
        name = node.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,23}", name):
            errors.append(f"{where}: id must start with a letter and use up to 24 letters, digits, `_` or `-`")
        elif name in ids:
            errors.append(f"{where}: duplicate id `{name}`")
        else:
            ids.add(name)
        if node.get("kind") not in SCENE_KINDS:
            errors.append(f"{where}: kind must be one of {', '.join(sorted(SCENE_KINDS))}")
        at, box = _pair(node.get("at")), _pair(node.get("size"))
        if not at or not (0 <= at[0] <= size[0] and 0 <= at[1] <= size[1]):
            errors.append(f"{where}: at must be a point inside {size[0]}×{size[1]}")
        if not box or box[0] < 0 or box[1] < 0:
            errors.append(f"{where}: size must be a non-negative pair")
        if _text(node.get("text", ""), 160) is None:
            errors.append(f"{where}: text must be a string of at most 160 characters")
        if node.get("sub") is not None and _text(node.get("sub"), 120) is None:
            errors.append(f"{where}: sub must be a string of at most 120 characters")
        if node.get("tone") not in SCENE_TONES:
            errors.append(f"{where}: tone must be one of {', '.join(sorted(SCENE_TONES))}")
        value = node.get("value")
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not 0 <= float(value) <= 1):
            errors.append(f"{where}: value must be a number between 0 and 1")
    edges = scene.get("edges")
    if not isinstance(edges, list) or len(edges) > SCENE_MAX_EDGES:
        errors.append(f"edges must be a list of at most {SCENE_MAX_EDGES} entries")
        edges = []
    for i, edge in enumerate(edges):
        where = f"edges[{i}]"
        if not isinstance(edge, dict):
            errors.append(f"{where}: not an object")
            continue
        for end in ("from", "to"):
            if edge.get(end) not in ids:
                errors.append(f"{where}: {end} must name a node id")
        if edge.get("label") is not None and _text(edge.get("label"), 80) is None:
            errors.append(f"{where}: label must be a string of at most 80 characters")
    steps = scene.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= SCENE_MAX_STEPS:
        return errors + [f"steps must be a list of 2–{SCENE_MAX_STEPS} entries: a scene is read click by click"]
    for i, step in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(step, dict):
            errors.append(f"{where}: not an object")
            continue
        title = step.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            errors.append(f"{where}: every step needs a title (≤ 120 characters) — it is what the click explains")
        if step.get("detail") is not None and _text(step.get("detail"), 400) is None:
            errors.append(f"{where}: detail must be a string of at most 400 characters")
        for key in ("show", "hide"):
            value = step.get(key)
            if not isinstance(value, list) or any(v not in ids for v in value):
                errors.append(f"{where}: {key} must name node ids")
        if step.get("focus") is not None and (not isinstance(step["focus"], list)
                                              or any(v not in ids for v in step["focus"])):
            errors.append(f"{where}: focus must name node ids (an empty list clears the spotlight)")
        for key in ("move", "text", "sub", "value", "tone"):
            value = step.get(key)
            if value is None:
                continue                    # a manifest baked before this key existed
            if not isinstance(value, dict):
                errors.append(f"{where}: {key} must map node ids to changes")
                continue
            for node_id, change in value.items():
                if node_id not in ids:
                    errors.append(f"{where}: {key} names unknown node `{node_id}`")
                elif key == "move" and _pair(change) is None:
                    errors.append(f"{where}: move[{node_id}] must be a point")
                elif key in ("text", "sub") and _text(change, 160) is None:
                    errors.append(f"{where}: {key}[{node_id}] must be a string of at most 160 characters")
                elif key == "value" and (not isinstance(change, (int, float)) or isinstance(change, bool)
                                         or not 0 <= float(change) <= 1):
                    errors.append(f"{where}: value[{node_id}] must be between 0 and 1")
                elif key == "tone" and change not in SCENE_TONES:
                    errors.append(f"{where}: tone[{node_id}] must be one of {', '.join(sorted(SCENE_TONES))}")
        changes = any(step.get(k) for k in ("show", "hide", "focus", "move", "text", "sub", "value", "tone"))
        if not changes and not (step.get("detail") or "").strip():
            errors.append(f"{where}: a step must change something or explain something, or the click is pointless")
    return errors


def scene_names(card: Path, cfg: dict) -> list[str]:
    names = set(cfg.get("scene") or {})
    d = card / MEDIA_DIR / "scenes"
    if d.is_dir():
        names.update(p.stem for p in d.iterdir()
                     if p.is_file() and p.suffix.lower() in (".yaml", ".yml", ".json") and not p.name.startswith("."))
    return sorted(names)


def bake_scene(card: Path, name: str, cfg: dict | None = None) -> dict:
    """Read an authored scene, check it, and write the manifest the kit draws from."""
    cfg = cfg or {}
    src = card / MEDIA_DIR / str(cfg.get("source") or f"scenes/{name}.yaml")
    if not src.is_file():
        for suffix in (".yaml", ".yml", ".json"):
            candidate = card / MEDIA_DIR / "scenes" / f"{name}{suffix}"
            if candidate.is_file():
                src = candidate
                break
        else:
            raise BakeError(f"{name}: no scene source. Write media/scenes/{name}.yaml "
                            f"(a `size`, `nodes`, `edges` and at least two `steps`)")
    try:
        raw = json.loads(src.read_text("utf-8")) if src.suffix.lower() == ".json" else yaml.safe_load(src.read_text("utf-8"))
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as e:
        raise BakeError(f"media/{src.name}: not valid YAML/JSON ({e})") from e
    if not isinstance(raw, dict):
        raise BakeError(f"media/{src.name}: a scene is a mapping of size, nodes, edges and steps")
    scene = normalize_scene(raw)
    errors = validate_scene(scene)
    if errors:
        raise BakeError(f"media/{src.name}: " + "\n".join(errors[:8]))
    manifest = {
        "kind": "scene",
        "id": name,
        "scene": scene,
        "baked_at": _now(),
        "sources": _sources(card, [src] + ([card / MEDIA_DIR / CONFIG] if (card / MEDIA_DIR / CONFIG).exists() else [])),
    }
    (card / MEDIA_DIR / f"{name}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return {"kind": "scene", "steps": len(scene["steps"]), "nodes": len(scene["nodes"]),
            "bytes": (card / MEDIA_DIR / f"{name}.json").stat().st_size}


def bake_names(card: Path) -> list[str]:
    """Every asset this card declares, whether by convention or in media/bake.yaml."""
    cfg = bake_config(card)
    return sorted(set(anim_names(card, cfg)) | set(chart_names(card, cfg)) | set(scene_names(card, cfg)))


def _one_kind(kinds: dict[str, str], name: str, kind: str) -> None:
    """A name may only mean one thing: all three kinds write `media/<name>.json`."""
    if name in kinds:
        words = {"anim": "an animation", "chart": "a chart", "scene": "a scene"}
        raise BakeError(f"`{name}` is declared as both {words[kinds[name]]} and {words[kind]}; rename one "
                        f"(every kind bakes to media/<name>.json)")
    kinds[name] = kind


def bake_card(card: Path, only: set[str] | None = None) -> dict:
    """Bake every asset the card declares. Returns what was written, for the CLI's JSON output."""
    cfg = bake_config(card)
    anims, charts, scenes = anim_names(card, cfg), chart_names(card, cfg), scene_names(card, cfg)
    kinds: dict[str, str] = {}
    for name, kind in [(n, "anim") for n in anims] + [(n, "chart") for n in charts] + [(n, "scene") for n in scenes]:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise BakeError("media names may use letters, digits, dot, dash and underscore only")
        _one_kind(kinds, name, kind)
    (card / MEDIA_DIR).mkdir(exist_ok=True)
    written = {}
    for name in anims:
        if only is None or name in only:
            written[name] = bake_anim(card, name, (cfg.get("anim") or {}).get(name) or {})
    for name in charts:
        if only is None or name in only:
            written[name] = bake_chart(card, name, (cfg.get("chart") or {}).get(name) or {})
    for name in scenes:
        if only is None or name in only:
            written[name] = bake_scene(card, name, (cfg.get("scene") or {}).get(name) or {})
    if not written:
        raise BakeError("nothing to bake: add media/scenes/<name>.yaml, media/<name>.vl.json, media/src/<name>/ "
                        "frames, or media/bake.yaml")
    return {"ok": True, "card": card.name, "media": written}


def stale(card: Path, manifest: dict) -> list[str]:
    """Source files that are still here but changed since `bake`.

    A source that no longer exists is not stale: an applied card keeps only the baked result, and its sources stay
    with the author. What matters is a file that is present and different — that is a picture of yesterday's code.
    """
    changed = []
    for rel, digest in (manifest.get("sources") or {}).items():
        p = card / rel
        if not p.resolve().is_relative_to(card.resolve()):
            changed.append(f"invalid source path: {rel}")
            continue
        if p.is_file() and _hash(p) != digest:
            changed.append(rel)
    return changed
