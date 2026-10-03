#!/usr/bin/env python3
"""Local, source-traceable Live stills. Requires ffmpeg, ffprobe and Pillow.

No source files are changed. Every command creates a new output directory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageStat

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi", ".ts"}
HDR_TRANSFERS = {"arib-std-b67": "hlg", "smpte2084": "pq"}
SCHEMA_VERSION = 1


class WallpaperError(Exception):
    pass


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise WallpaperError(f"命令失败 ({Path(command[0]).name}):\n{result.stderr[-5000:]}")
    return result.stdout


def require_tools() -> None:
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise WallpaperError(f"找不到 {name}，请先配置已有工具的 PATH。")


def timestamp(value: str | float) -> float:
    try:
        parts = str(value).split(":")
        if not 1 <= len(parts) <= 3:
            raise ValueError()
        values = [float(part) for part in parts]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError()
        if len(values) > 1 and any(v >= 60 for v in values[1:]):
            raise ValueError()
        seconds = sum(v * 60 ** i for i, v in enumerate(reversed(values)))
        return seconds
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("时间必须为非负秒数或 HH:MM:SS.mmm") from None


def timecode(seconds: float) -> str:
    total = round(seconds * 1000)
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def size_arg(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)[xX×](\d+)", value)
    if not match:
        raise argparse.ArgumentTypeError("尺寸示例：3840x2160 或 1440x3200")
    width, height = map(int, match.groups())
    if min(width, height) < 16 or max(width, height) > 16384 or width * height > 80_000_000:
        raise argparse.ArgumentTypeError("每边须为16～16384像素，总像素不超过8000万")
    return width, height


def bounded_number(low: float, high: float):
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError("请输入数字") from None
        if not math.isfinite(number) or not low <= number <= high:
            raise argparse.ArgumentTypeError(f"范围须为 {low}～{high}")
        return number
    return parse


def positive_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("请输入整数") from None
    if not 1 <= count <= 200:
        raise argparse.ArgumentTypeError("数量须为1～200")
    return count


def discover_videos(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    root = root.expanduser().resolve()
    if not root.exists():
        raise WallpaperError(f"输入路径不存在：{root}")
    files = [root] if root.is_file() else sorted(root.rglob("*"))
    groups: dict[tuple[str, str], list[Path]] = {}
    aliases: list[dict[str, str]] = []
    seen: dict[tuple[int, int], Path] = {}
    for path in files:
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            original_path = path
            path = path.resolve()
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                if original_path != seen[identity]:
                    aliases.append({"source": str(original_path), "preferred": str(seen[identity]),
                                    "reason": "同一物理文件的符号链接或硬链接，仅处理一次"})
                continue
            seen[identity] = path
            if original_path != path:
                aliases.append({"source": str(original_path), "preferred": str(path),
                                "reason": "符号链接已解析为真实视频，仅处理一次"})
            stem = re.sub(r"\s*\[chapters\]\s*", "", path.stem, flags=re.I).strip().casefold()
            groups.setdefault((str(path.parent), stem), []).append(path)
    selected, skipped = [], aliases
    for paths in groups.values():
        preferred = sorted(paths, key=lambda p: ("[chapters]" not in p.stem.lower(), str(p)))[0]
        selected.append(preferred)
        skipped.extend({"source": str(p), "preferred": str(preferred), "reason": "同目录同名版本优先 Chapters 文件"}
                       for p in paths if p != preferred)
    if not selected:
        raise WallpaperError(f"未找到支持的视频：{root}")
    return sorted(selected), skipped


def ratio(value: str | None, fallback: float = 1.0) -> float:
    try:
        numerator, denominator = map(float, (value or "").replace("/", ":").split(":"))
        parsed = numerator / denominator
        return parsed if parsed > 0 and math.isfinite(parsed) else fallback
    except (ValueError, ZeroDivisionError):
        return fallback


def probe_video(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    raw = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                          "-show_format", "-show_chapters", "-of", "json", str(path)]))
    if not raw.get("streams"):
        raise WallpaperError(f"文件没有可读取的视频流：{path}")
    stream = raw["streams"][0]
    duration = float(stream.get("duration") or raw.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise WallpaperError(f"无法确认视频时长：{path}")
    width, height = int(stream["width"]), int(stream["height"])
    sar = ratio(stream.get("sample_aspect_ratio"))
    dar = ratio(stream.get("display_aspect_ratio"), width * sar / height)
    rotation = 0
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(side["rotation"]) % 360
    # ffmpeg autorotates by default; use that displayed geometry downstream.
    if rotation in (90, 270):
        width, height, dar = height, width, 1 / dar
    chapters = [{"title": c.get("tags", {}).get("title", f"Chapter {i + 1}"),
                 "start": float(c["start_time"]), "end": min(duration, float(c["end_time"]))}
                for i, c in enumerate(raw.get("chapters", []))]
    return {"source": str(path), "file_size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
            "duration": duration, "width": width, "height": height, "rotation": rotation,
            "coded_width": int(stream["width"]), "coded_height": int(stream["height"]),
            "sample_aspect_ratio": stream.get("sample_aspect_ratio", "unknown"),
            "display_aspect_ratio": dar, "codec": stream.get("codec_name"),
            "video_bit_rate": int(stream["bit_rate"]) if stream.get("bit_rate", "").isdigit() else None,
            "container_bit_rate": int(raw["format"]["bit_rate"]) if str(raw.get("format", {}).get("bit_rate", "")).isdigit() else None,
            "pixel_format": stream.get("pix_fmt"), "color_transfer": stream.get("color_transfer", "unknown"),
            "color_primaries": stream.get("color_primaries", "unknown"),
            "color_space": stream.get("color_space", "unknown"),
            "color_range": stream.get("color_range", "unknown"), "chapters": chapters,
            "frame_rate": stream.get("avg_frame_rate"),
            "dolby_vision_metadata": any("DOVI" in s.get("side_data_type", "") for s in stream.get("side_data_list", []))}


def create_output(path: Path) -> Path:
    path = path.expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise WallpaperError(f"输出目录已存在，拒绝覆盖：{path}。请换一个新目录。") from None
    return path


def write_json(path: Path, data: Any) -> None:
    with path.open("x", encoding="utf-8") as target:
        json.dump(data, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def catalog_data(root: Path) -> dict[str, Any]:
    paths, skipped = discover_videos(root)
    return {"schema_version": SCHEMA_VERSION, "kind": "live-wallpaper-catalog",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "input": str(root.expanduser().resolve()), "skipped_duplicates": skipped,
            "videos": [{"id": f"V{i:02}", "source": str(path), "label": path.parent.name,
                        "metadata": probe_video(path)} for i, path in enumerate(paths, 1)]}


def chapter_at(metadata: dict[str, Any], seconds: float) -> str | None:
    for chapter in metadata["chapters"]:
        if chapter["start"] <= seconds < chapter["end"]:
            return chapter["title"]
    return None


def choose_timestamps(metadata: dict[str, Any], per_chapter: int, count: int,
                      explicit: list[float] | None = None) -> list[float]:
    duration = metadata["duration"]
    if explicit is not None:
        if any(t >= duration for t in explicit):
            raise WallpaperError(f"采样时间必须小于视频时长 {timecode(duration)}")
        return sorted(set(explicit))
    intervals = metadata["chapters"] or [{"start": 0, "end": duration}]
    chosen = []
    for chapter in intervals:
        start, end = max(0, chapter["start"]), min(duration, chapter["end"])
        if end <= start:
            continue
        amount = per_chapter if metadata["chapters"] else count
        chosen.extend(start + (end - start) * (i + 1) / (amount + 1) for i in range(amount))
    return sorted(set(round(t, 3) for t in chosen if t < duration))


def hdr_kind(metadata: dict[str, Any], requested: str) -> str:
    return HDR_TRANSFERS.get(metadata["color_transfer"], "sdr") if requested == "auto" else requested


def hable(x: float) -> float:
    return ((x * (0.15 * x + 0.05) + 0.004) / (x * (0.15 * x + 0.5) + 0.06)) - 1 / 15


def srgb(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def hdr_to_srgb(red: float, green: float, blue: float, kind: str, peak: float) -> tuple[float, float, float]:
    """BT.2100 nominal display transform, BT.2020 gamut, luminance Hable map.

    HLG uses a 1000-nit reference display (or the supplied peak) and its
    system gamma. PQ follows ST 2084 absolute luminance. No Dolby Vision RPU.
    """
    if kind == "pq":
        def decode(value: float) -> float:
            p = value ** (1 / (2523 / 32))
            return (max(p - 3424 / 4096, 0) / max(2413 / 128 - (2392 / 128) * p, 1e-12)) ** (1 / (2610 / 16384)) * 10000
        r, g, b = map(decode, (red, green, blue))
    else:
        def decode(value: float) -> float:
            return value * value / 3 if value <= .5 else (math.exp((value - .55991073) / .17883277) + .28466892) / 12
        r, g, b = map(decode, (red, green, blue))
        luminance = .2627 * r + .6780 * g + .0593 * b
        # ITU-R BT.2100-2 Note 5f, footnote 2: the logarithmic expression covers
        # 400..2000 nits; use the extension for higher reference peaks.
        gamma = (1.2 + .42 * math.log10(peak / 1000) if peak <= 2000
                 else 1.2 * 1.111 ** math.log2(peak / 1000))
        multiplier = peak * max(luminance, 1e-12) ** (gamma - 1)
        r, g, b = r * multiplier, g * multiplier, b * multiplier
    luminance = .2627 * r + .6780 * g + .0593 * b
    mapped = hable(luminance / 100) / hable(peak / 100)
    multiplier = mapped / max(luminance, 1e-12)
    # Linear-light BT.2020 -> BT.709/D65; final gamut clipping is deliberate.
    rr = (1.660491 * r - .587641 * g - .072850 * b) * multiplier
    gg = (-.124550 * r + 1.132900 * g - .008349 * b) * multiplier
    bb = (-.018151 * r - .100579 * g + 1.118730 * b) * multiplier
    return srgb(rr), srgb(gg), srgb(bb)


def make_hdr_lut(path: Path, kind: str, peak: float, resolution: int) -> None:
    with path.open("x", encoding="ascii") as target:
        target.write(f'TITLE "{kind.upper()} BT2020 to sRGB Hable {peak:g}nit"\nLUT_3D_SIZE {resolution}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for b in range(resolution):
            for g in range(resolution):
                for r in range(resolution):
                    mapped = hdr_to_srgb(r / (resolution - 1), g / (resolution - 1), b / (resolution - 1), kind, peak)
                    target.write("%.8f %.8f %.8f\n" % mapped)


def filter_path(path: Path) -> str:
    # Two parser layers: filter option and filter graph. The double escapes
    # protect literal apostrophes/backslashes in user-controlled directory names.
    value = str(path).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    value = value.replace("\\", "\\\\").replace("'", "\\'").replace(",", "\\,").replace(";", "\\;").replace("[", "\\[").replace("]", "\\]")
    return value


def color_plan(metadata: dict[str, Any], output: Path, mode: str, peak: float,
               resolution: int) -> tuple[list[str], dict[str, Any]]:
    kind = hdr_kind(metadata, mode)
    if kind == "sdr":
        return [], {"mode": "sdr", "source_transfer": metadata["color_transfer"],
                    "note": "普通 SDR 解码；未知色彩元数据由 ffmpeg 处理。"}
    if metadata["color_primaries"] not in {"bt2020", "unknown"}:
        raise WallpaperError("HDR 源并非 BT.2020 色域，当前转换器不支持；请勿强行导出。")
    if metadata["color_space"] not in {"bt2020nc", "unknown"}:
        raise WallpaperError("HDR 源并非 BT.2020 非恒定亮度 YCbCr，当前转换器不支持该色彩矩阵。")
    lut = output / f"{kind}-{peak:g}nit-{resolution}.cube"
    if not lut.exists():
        make_hdr_lut(lut, kind, peak, resolution)
    source_range = "pc" if metadata["color_range"] == "pc" else "tv"
    # swscale first interprets the BT.2020 YCbCr matrix/range, yielding encoded
    # RGB. The LUT then decodes PQ/HLG, maps gamut and luminance, and encodes sRGB.
    filters = [f"scale=in_color_matrix=bt2020:in_range={source_range}:out_range=pc",
               "format=gbrpf32le", f"lut3d=file={filter_path(lut)}:interp=tetrahedral", "format=rgb24",
               "setparams=range=full:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=gbr"]
    return filters, {"mode": kind, "source_transfer": metadata["color_transfer"],
                     "method": "BT.2020 YCbCr decode -> BT.2100 HLG/PQ EOTF -> linear BT.2020/BT.709 matrix -> luminance Hable -> sRGB",
                     "reference_display_peak_nits": peak, "lut_resolution": resolution,
                     "lut_file": lut.name, "interpolation": "tetrahedral",
                     "transfer_reference": "https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.2100-2-201807-S!!PDF-E.pdf",
                     "assumptions": ["BT.2020 primaries and non-constant luminance YCbCr", f"{source_range} source range",
                                     "D65 white point", "HLG reference display black level = 0 nits"],
                     "note": "可查看的 SDR 色调映射；默认1000nit参考峰值并非测得的母版峰值。不应用 Dolby Vision 动态元数据，超出 sRGB 色域的颜色会裁切。"}


def extract_frame(source: Path, seconds: float, target: Path, filters: list[str],
                  jpeg: bool = False) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
               "-ss", f"{seconds:.6f}", "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-an", "-sn"]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += ["-update", "1"]
    if jpeg:
        command += ["-q:v", "2", "-pix_fmt", "yuvj420p"]
    else:
        command += ["-pix_fmt", "rgb24"]
    command += [str(target)]
    run(command)
    if not target.is_file() or target.stat().st_size == 0:
        raise WallpaperError(f"该时间未提取到视频帧：{timecode(seconds)}")


def image_scores(path: Path) -> dict[str, float]:
    with Image.open(path) as image:
        gray = image.convert("L")
        gray.thumbnail((256, 256))
        width, height = gray.size
        pixels = list(gray.get_flattened_data() if hasattr(gray, "get_flattened_data") else gray.getdata())
        laplacian = []
        for y in range(1, height - 1):
            for x in range(1, width - 1):
                i = y * width + x
                laplacian.append(4 * pixels[i] - pixels[i - 1] - pixels[i + 1] - pixels[i - width] - pixels[i + width])
        mean = sum(laplacian) / len(laplacian) if laplacian else 0
        sharpness = sum((v - mean) ** 2 for v in laplacian) / max(1, len(laplacian))
        return {"sharpness": round(sharpness, 3), "brightness": round(ImageStat.Stat(gray).mean[0] / 255, 4),
                "clipped_white_fraction": round(sum(p >= 250 for p in pixels) / len(pixels), 4),
                "near_black_fraction": round(sum(p <= 8 for p in pixels) / len(pixels), 4)}


def contact_sheets(candidates: list[dict[str, Any]], output: Path) -> list[dict[str, str]]:
    folder = output / "contact_sheets"
    folder.mkdir()
    records = []
    font = ImageFont.load_default(size=17)
    for video_id in dict.fromkeys(c["video_id"] for c in candidates):
        group = [c for c in candidates if c["video_id"] == video_id]
        for page, offset in enumerate(range(0, len(group), 16), 1):
            batch = group[offset:offset + 16]
            columns, cell_w, cell_h = 4, 320, 200
            sheet = Image.new("RGB", (columns * cell_w, math.ceil(len(batch) / columns) * cell_h), "#171a20")
            draw = ImageDraw.Draw(sheet)
            for i, candidate in enumerate(batch):
                x, y = (i % columns) * cell_w, (i // columns) * cell_h
                with Image.open(output / candidate["preview"]) as thumbnail:
                    thumbnail.thumbnail((cell_w - 12, 151))
                    sheet.paste(thumbnail, (x + (cell_w - thumbnail.width) // 2, y + 4))
                draw.text((x + 8, y + 158), candidate["id"], font=font, fill="white")
                draw.text((x + 8, y + 179), candidate["timecode"], font=font, fill="#c3cad8")
            relative = f"contact_sheets/{video_id}-{page:02}.jpg"
            sheet.save(output / relative, quality=92)
            records.append({"video_id": video_id, "path": relative})
    return records


def write_index(output: Path, manifest: dict[str, Any]) -> None:
    sections = []
    for video in manifest["videos"]:
        cards = []
        for c in manifest["candidates"]:
            if c["video_id"] != video["id"]:
                continue
            cards.append(f'<article><a href="{html.escape(c["preview"])}" target="_blank"><img loading="lazy" src="{html.escape(c["preview"])}" alt="{c["id"]}"></a><h3>{c["id"]} · {c["timecode"]}</h3><p>{html.escape(c["chapter"] or "均匀抽样")}</p><small>清晰度参考 {c["scores"]["sharpness"]} · 亮度 {c["scores"]["brightness"]}</small></article>')
        sections.append(f'<section><h2>{video["id"]} · {html.escape(video["label"])}</h2><div class="grid">{"".join(cards)}</div></section>')
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Yorushika Live 候选帧</title><style>body{background:#13161b;color:#e8e8e8;font:16px system-ui;margin:30px auto;padding:0 22px;max-width:1500px}h1,h2{font-weight:500}p,small{color:#aeb8c7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}article{background:#20252e;padding:12px;border-radius:12px}img{width:100%;height:auto}h3{font-size:15px}a{color:inherit}</style><h1>Yorushika Live 候选帧</h1><p>选择喜欢的画面 ID，再查看原尺寸导出。缩略图经过色彩转换；评分只反映画面统计，不能判断 suis 是否在唱、歌词是否完整或现场是否热闹。</p>' + ''.join(sections) + '</html>'
    (output / "index.html").write_text(document, encoding="utf-8")


def sample(args: argparse.Namespace) -> Path:
    catalog = catalog_data(args.input)
    if args.video:
        requested = {str(p.expanduser().resolve()) for p in args.video}
        catalog["videos"] = [v for v in catalog["videos"] if v["source"] in requested]
        if {v["source"] for v in catalog["videos"]} != requested:
            raise WallpaperError("--video 必须是目录扫描选中的完整视频路径（同名优先 Chapters 版）")
    explicit = [timestamp(value.strip()) for value in args.timestamps.split(",")] if args.timestamps else None
    plans = [(v, choose_timestamps(v["metadata"], args.per_chapter, args.count, explicit)) for v in catalog["videos"]]
    output = create_output(args.output)
    (output / "previews").mkdir()
    manifest = {**catalog, "kind": "live-wallpaper-samples", "candidates": [], "contact_sheets": [],
                "score_notice": "清晰度与亮度仅供筛选参考，未经人工确认人物身份、演唱状态、现场气氛与歌词。",
                "selection": {"per_chapter": args.per_chapter, "uniform_count": args.count, "explicit_timestamps": explicit}}
    for video, times in plans:
        meta = video["metadata"]
        filters, conversion = color_plan(meta, output, args.hdr_mode, args.hdr_peak, 33)
        video["color_conversion"] = conversion
        thumb_h = max(2, round(args.thumb_width / meta["display_aspect_ratio"] / 2) * 2)
        thumb_w = max(2, round(args.thumb_width / 2) * 2)
        for i, seconds in enumerate(times, 1):
            identifier = f'{video["id"]}-C{i:03}'
            relative = f"previews/{identifier}.jpg"
            extract_frame(Path(video["source"]), seconds, output / relative,
                          [f"scale={thumb_w}:{thumb_h}", "setsar=1"] + filters, jpeg=True)
            manifest["candidates"].append({"id": identifier, "video_id": video["id"], "source": video["source"],
                                           "timestamp": seconds, "timecode": timecode(seconds),
                                           "chapter": chapter_at(meta, seconds), "preview": relative,
                                           "scores": image_scores(output / relative)})
            print(f"{identifier} {timecode(seconds)}", flush=True)
    manifest["contact_sheets"] = contact_sheets(manifest["candidates"], output)
    write_json(output / "manifest.json", manifest)
    write_index(output, manifest)
    return output


def render(args: argparse.Namespace) -> Path:
    source = args.source.expanduser().resolve()
    meta = probe_video(source)
    if args.timestamp >= meta["duration"]:
        raise WallpaperError(f"截图时间必须小于视频时长 {timecode(meta['duration'])}")
    output = create_output(args.output)
    color_filters, conversion = color_plan(meta, output, args.hdr_mode, args.hdr_peak, 65)
    original = output / "original.png"
    extract_frame(source, args.timestamp, original, color_filters)
    dar = meta["display_aspect_ratio"]
    native = getattr(args, "native", False)
    width, height = (round(meta["height"] * dar), meta["height"]) if native else args.size
    if args.fit == "contain":
        scaled_w = min(width, max(1, round(height * dar)))
        scaled_h = min(height, max(1, round(width / dar)))
        placement = f"pad={width}:{height}:x=(ow-iw)*{args.focus_x}:y=(oh-ih)*{args.focus_y}:color={args.background}"
    else:
        scaled_w = max(width, math.ceil(height * dar))
        scaled_h = max(height, math.ceil(width / dar))
        placement = f"crop={width}:{height}:x=(iw-ow)*{args.focus_x}:y=(ih-oh)*{args.focus_y}"
    resize_needed = (scaled_w, scaled_h) != (meta["width"], meta["height"])
    geometry = ([f"scale={scaled_w}:{scaled_h}:flags=lanczos"] if resize_needed else []) + ["setsar=1", placement]
    # Exposure is applied in the viewing (sRGB encoded) space with a gamma
    # approximation; provenance explicitly avoids claiming linear RAW grading.
    adjustment = []
    if args.exposure:
        multiplier = 2 ** (args.exposure / 2.2)
        adjustment += [f"lutrgb=r=val*{multiplier}:g=val*{multiplier}:b=val*{multiplier}"]
    if args.contrast != 1:
        adjustment += [f"eq=contrast={args.contrast}"]
    if args.sharpen:
        adjustment += [f"unsharp=5:5:{args.sharpen}:5:5:0"]
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(original),
         "-vf", ",".join(geometry + adjustment + ["format=rgb24"]), "-frames:v", "1", "-update", "1", str(output / "wallpaper.png")])
    with Image.open(output / "wallpaper.png") as result:
        if result.size != (width, height):
            raise WallpaperError("导出尺寸校验失败")
    scaled = scaled_w > meta["width"] or scaled_h > meta["height"]
    provenance = {"schema_version": SCHEMA_VERSION, "kind": "live-wallpaper-render",
                  "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "source": str(source), "source_metadata": meta, "timestamp": args.timestamp,
                  "timecode": timecode(args.timestamp), "chapter": chapter_at(meta, args.timestamp),
                  "color_conversion": conversion, "original": "original.png", "wallpaper": "wallpaper.png",
                  "parameters": {"size": [width, height], "fit": args.fit, "focus_x": args.focus_x,
                                 "focus_y": args.focus_y, "background": args.background, "exposure": args.exposure,
                                 "contrast": args.contrast, "sharpen": args.sharpen, "native": native},
                  "upscaled": scaled, "interpolation": "Lanczos",
                  "render_pipeline": {"input": "native video decoded frame", "intermediate": "lossless PNG, native pixel dimensions",
                                      "source_pixel_dimensions": [meta["width"], meta["height"]],
                                      "scaled_dimensions": [scaled_w, scaled_h], "resize_passes": int(resize_needed),
                                      "horizontal_scale": round(scaled_w / meta["width"], 6),
                                      "vertical_scale": round(scaled_h / meta["height"], 6),
                                      "thumbnail_used_for_render": False},
                  "notices": ["original.png 为源视频解码帧，保留显示方向；HDR 会转换为 SDR，未添加锐化。",
                              "非方形像素源 original.png 保留像素栅格，wallpaper.png 按源显示宽高比校正。",
                              "放大为插值，不是真实细节恢复；锐化无法修复运动模糊。",
                              "曝光为 sRGB 显示域近似档位调整，非 RAW 线性调色。",
                              "时间戳为请求的解码定位点，实际画面取该点可用帧，不宣称精确帧时间戳。",
                              "原片未修改；保留原片内已有字幕，不烧录外部字幕。",
                              "人物身份、演唱状态、歌词与氛围需人工看图确认。"]}
    write_json(output / "provenance.json", provenance)
    return output


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Yorushika Live 本地壁纸：先选帧，再导出。原片只读，输出必须为新目录。",
                                  formatter_class=argparse.RawDescriptionHelpFormatter,
                                  epilog='''示例：
  python3 live_wallpaper.py catalog --input "/path/to/Yorushika_Live" --output output/catalog-01
  python3 live_wallpaper.py sample --input "/path/to/Yorushika_Live" --per-chapter 1 --output output/sample-01
  python3 live_wallpaper.py sample --input "/path/to/live.mp4" --timestamps 120,240,360 --output output/refine-01
  python3 live_wallpaper.py render --source "/path/to/live.mp4" --timestamp 00:25:30 --size 3840x2160 --output output/desktop-01
  python3 live_wallpaper.py render --source "/path/to/live.mp4" --timestamp 00:25:30 --size 1440x3200 --fit cover --focus-x .65 --output output/phone-01
  python3 live_wallpaper.py render --source "/path/to/live.mp4" --timestamp 00:25:30 --native --sharpen 0 --output output/native-preview-01

contain 默认保留整幅舞台画面并补边；cover 铺满并裁切，请查看人物与日文歌词是否被截掉。
清晰度分数不判断人物或氛围。插值与轻锐化不是 AI 超分辨率，也不恢复真实细节。''')
    commands = cli.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="只读元数据与章节，自动优先 Chapters 版本")
    sampler = commands.add_parser("sample", help="有限抽帧，生成候选网页与分场拼图")
    renderer = commands.add_parser("render", help="导出原帧、壁纸与来源参数")
    for sub in (catalog, sampler):
        sub.add_argument("--input", type=Path, required=True, help="本地视频目录或单个文件")
    for sub in (catalog, sampler, renderer):
        sub.add_argument("--output", type=Path, required=True, help="新输出目录；已存在时拒绝覆盖")
    sampler.add_argument("--video", type=Path, action="append", help="只处理目录内指定完整路径，可重复")
    sampler.add_argument("--per-chapter", type=positive_count, default=1, help="每章等距取帧数，默认1")
    sampler.add_argument("--count", type=positive_count, default=12, help="无章节时每场等距取帧数，默认12")
    sampler.add_argument("--timestamps", help="逗号分隔秒数或 HH:MM:SS，覆盖自动抽样")
    sampler.add_argument("--thumb-width", type=int, choices=range(160, 1921), metavar="160..1920", default=640)
    renderer.add_argument("--source", type=Path, required=True)
    renderer.add_argument("--timestamp", type=timestamp, required=True)
    dimensions = renderer.add_mutually_exclusive_group()
    dimensions.add_argument("--size", type=size_arg, default=(3840, 2160))
    dimensions.add_argument("--native", action="store_true", help="按源显示尺寸导出高清预览；不以增加像素冒充细节")
    renderer.add_argument("--fit", choices=("contain", "cover"), default="contain")
    renderer.add_argument("--focus-x", type=bounded_number(0, 1), default=.5, help="水平裁切或补边位置，0左/0.5居中/1右")
    renderer.add_argument("--focus-y", type=bounded_number(0, 1), default=.5, help="垂直裁切或补边位置，0上/0.5居中/1下")
    renderer.add_argument("--background", default="0x101216", type=lambda value: validate_background(value), help="补边颜色，0xRRGGBB")
    renderer.add_argument("--exposure", type=bounded_number(-2, 2), default=0, help="近似曝光档位，-2～2")
    renderer.add_argument("--contrast", type=bounded_number(.5, 1.5), default=1)
    renderer.add_argument("--sharpen", type=bounded_number(0, 1), default=.25, help="轻锐化强度，0关闭，默认0.25")
    for sub in (sampler, renderer):
        sub.add_argument("--hdr-mode", choices=("auto", "sdr", "hlg", "pq"), default="auto", help="按元数据自动识别；仅在已知元数据错误时覆盖")
        sub.add_argument("--hdr-peak", type=bounded_number(400, 10000), default=1000, help="HDR参考显示峰值nit，默认1000（并非测量值）")
    return cli


def validate_background(value: str) -> str:
    if not re.fullmatch(r"0x[0-9a-fA-F]{6}", value):
        raise argparse.ArgumentTypeError("补边颜色必须为 0xRRGGBB")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        require_tools()
        if args.command == "catalog":
            data = catalog_data(args.input)
            output = create_output(args.output)
            write_json(output / "catalog.json", data)
        elif args.command == "sample":
            output = sample(args)
        else:
            output = render(args)
        print(f"完成：{output}")
        return 0
    except (WallpaperError, OSError, ValueError, argparse.ArgumentTypeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
