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
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


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
        if key not in ("anim", "chart"):
            raise BakeError(f"media/{CONFIG}: unknown key `{key}` (expected `anim` or `chart`)")
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


def bake_names(card: Path) -> list[str]:
    """Every asset this card declares, whether by convention or in media/bake.yaml."""
    cfg = bake_config(card)
    return sorted(set(anim_names(card, cfg)) | set(chart_names(card, cfg)))


def bake_card(card: Path, only: set[str] | None = None) -> dict:
    """Bake every asset the card declares. Returns what was written, for the CLI's JSON output."""
    cfg = bake_config(card)
    anims, charts = anim_names(card, cfg), chart_names(card, cfg)
    for name in anims + charts:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise BakeError("media names may use letters, digits, dot, dash and underscore only")
    clash = sorted(set(anims) & set(charts))
    if clash:
        raise BakeError(f"`{clash[0]}` is declared as both an animation and a chart; rename one "
                        f"(the manifest is media/<name>.json for both)")
    (card / MEDIA_DIR).mkdir(exist_ok=True)
    written = {}
    for name in anims:
        if only is None or name in only:
            written[name] = bake_anim(card, name, (cfg.get("anim") or {}).get(name) or {})
    for name in charts:
        if only is None or name in only:
            written[name] = bake_chart(card, name, (cfg.get("chart") or {}).get(name) or {})
    if not written:
        raise BakeError("nothing to bake: add media/src/<name>/ frames, media/<name>.vl.json, or media/bake.yaml")
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
