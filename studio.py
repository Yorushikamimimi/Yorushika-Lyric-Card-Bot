"""Loopback-only gallery and render queue for local Yorushika artwork."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import subprocess
import threading
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from PIL import Image, ImageOps, ImageStat
from live_wallpaper import color_plan, extract_frame, probe_video, run, timecode, WallpaperError

ROOT = Path(__file__).resolve().parent
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_TYPES = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv"}


def within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def download_name(candidate: dict, suffix: str) -> str:
    """Readable file name: source, picture title, timecode and size or kind."""
    title = re.sub(r"^\s*\d+\s*·\s*", "", str(candidate.get("title") or ""))
    timecode = str(candidate.get("timecode") or "").split(".")[0].replace(":", "-")
    parts = [re.sub(r'[\\/:*?"<>|\s·・]+', "", part) for part in (str(candidate.get("source_label") or ""), title, timecode, suffix)]
    return "_".join(part for part in parts if part)[:150] + ".png"


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    candidate_id: str = Field(min_length=1, max_length=64)
    width: int = Field(default=2560, ge=256, le=8192)
    height: int = Field(default=1440, ge=256, le=8192)
    fit: str | None = Field(default=None, pattern="^(contain|cover)$")
    focus_x: float = Field(default=.5, ge=0, le=1)
    focus_y: float = Field(default=.5, ge=0, le=1)
    exposure: float = Field(default=0, ge=-2, le=2)
    contrast: float = Field(default=1, ge=.5, le=1.5)
    sharpen: float = Field(default=.25, ge=0, le=1)

    @model_validator(mode="after")
    def bounded_pixels(self):
        if self.width * self.height > 34_000_000:
            raise ValueError("图片总像素不能超过 3400 万")
        if self.fit is None:
            self.fit = "cover" if self.height >= self.width else "contain"
        return self


Renderer = Callable[[dict, ExportRequest, Path], Awaitable[None]]
PreviewRenderer = Callable[[dict, Path], Awaitable[None]]
# (candidate, first timestamp, step, frame count, folder): writes 01.jpg, 02.jpg ...
StripRenderer = Callable[[dict, float, float, int, Path], None]
STRIP_WIDTH, STRIP_SEEK_STEP, STRIP_SEEK_WORKERS = 480, 5, 2


def power_status() -> dict:
    """Fail open to AC on unsupported platforms or unreadable power state."""
    battery = False
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["pmset", "-g", "ps"], capture_output=True, text=True, timeout=2)
            battery = result.returncode == 0 and "Now drawing from 'Battery Power'" in result.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return {"on_battery": battery, "prefetch_count": 1 if battery else 2}


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=64)
    priority: Literal["current", "prefetch"] = "current"


def clean_capture_name(value):
    """A user's own name for a capture: one line, no control characters; blank means the song name."""
    if not isinstance(value, str):
        raise ValueError("名字必须是文字")
    value = " ".join(value.split())
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("名字里有不能用的字符")
    if len(value) > CAPTURE_NAME_MAX:
        raise ValueError(f"名字最多 {CAPTURE_NAME_MAX} 个字")
    return value


CAPTURE_NAME_MAX = 40


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_id: str = Field(min_length=1, max_length=64)
    timestamp: float = Field(ge=0)
    favorite: bool = False
    name: str = Field(default="", max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def tidy_name(cls, value):
        return clean_capture_name(value)

    @field_validator("timestamp", mode="before")
    @classmethod
    def numeric_time(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("时间必须是秒数")
        return value

    @field_validator("timestamp")
    @classmethod
    def tenths(cls, value):
        if value != round(value, 1):
            raise ValueError("时间最多保留一位小数")
        return value


class CaptureStripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0)
    step: Literal[1, 2, 5] = 2
    count: int = Field(default=9, ge=1, le=9)

    @field_validator("start", mode="before")
    @classmethod
    def numeric_start(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("时间必须是秒数")
        return value

    @field_validator("start")
    @classmethod
    def start_tenths(cls, value):
        if value != round(value, 1):
            raise ValueError("时间最多保留一位小数")
        return value


class CapturePreviewRequest(CaptureRequest):
    priority: Literal["current", "prefetch"] = "current"
    generation: int | None = Field(default=None, ge=0)


class CaptureNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=64)
    name: str = Field(max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def tidy_name(cls, value):
        return clean_capture_name(value)


REVIEW_REASON_MAX = 60


class ReviewDecisionRequest(BaseModel):
    """One explicit per-image choice in the 待挑 queue; null decision undoes it."""
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=64)
    decision: Literal["keep", "drop", "moment", "skip"] | None
    moment: int | None = Field(default=None, ge=0, le=40)
    reason: str = Field(default="", max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def tidy_reason(cls, value):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("原因必须是文字")
        value = " ".join(value.split())
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value) > REVIEW_REASON_MAX:
            raise ValueError(f"原因最多 {REVIEW_REASON_MAX} 个字")
        return value


class FavoriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=64)
    favorite: bool


class AvatarExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    avatar_id: str = Field(min_length=1, max_length=64)
    size: Literal[512, 1024, "original", "1170x2532", "1284x2778", "2560x1440", "3456x2234", "3840x2160"] = "original"
    fit: Literal["contain", "cover"] = "cover"
    focus_x: float = Field(default=.5, ge=0, le=1)
    focus_y: float = Field(default=.5, ge=0, le=1)


def create_app(project_root: Path = ROOT, renderer: Renderer | None = None, preview_renderer: PreviewRenderer | None = None,
               strip_renderer: StripRenderer | None = None) -> FastAPI:
    root = project_root.resolve()
    output = root / "output"
    candidates: dict[str, dict] = {}
    media: dict[str, Path] = {}
    media_names: dict[str, str] = {}
    avatars: dict[str, dict] = {}
    jobs: dict[str, dict] = {}
    preview_jobs: dict[str, dict] = {}
    preview_pending: dict[str, tuple] = {}
    preview_worker: asyncio.Task | None = None
    current_revision = 0
    tasks: set[asyncio.Task] = set()
    preview_slot = asyncio.Semaphore(1)
    artwork_slots = asyncio.Semaphore(2)
    favorite_lock = asyncio.Lock()
    capture_lock = asyncio.Lock()
    review_lock = asyncio.Lock()
    strip_lock = asyncio.Lock()
    source_lock = asyncio.Lock()
    capture_sources: dict[str, dict] = {}
    lut_lock = threading.Lock()
    probe_cache: dict[str, tuple[tuple, dict]] = {}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tuple(tasks), return_exceptions=True)

    app = FastAPI(title="ヨルシカ · 留住这一帧", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "只允许本机访问"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != f"{request.url.scheme}://{host}":
                return JSONResponse({"detail": "请从本地工作室页面发起操作"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "需要 JSON 请求"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def register_image(path: Path) -> str | None:
        path = path.resolve()
        if not within(output, root) or not within(path, output) or path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
            return None
        key = opaque(str(path))
        media[key] = path
        return f"/media/{key}"

    def name_media(url: str | None, name: str) -> None:
        if url:
            media_names[url.removeprefix("/media/")] = name

    def read_json(path: Path) -> Any:
        if not within(output, root) or not within(path, output):
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def read_favorites() -> tuple[set[tuple[str, str]], str | None]:
        path = output / "favorites.json"
        if not path.exists():
            return set(), None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return set(), "收藏文件无法读取或不是有效 JSON，已保留原文件。"
        if not isinstance(data, dict) or data.get("version", 1) != 1 or not isinstance(data.get("items"), list):
            return set(), "收藏文件结构无效，已保留原文件。"
        items: set[tuple[str, str]] = set()
        for item in data["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("manifest"), str) or not isinstance(item.get("candidate_id"), str) or not item["candidate_id"]:
                return set(), "收藏文件条目无效，已保留原文件。"
            items.add((item["manifest"], item["candidate_id"]))
        return items, None

    def read_decisions() -> dict[tuple[str, str], dict]:
        data = read_json(output / "review-decisions.json")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return {}
        return {(item["manifest"], item["candidate_id"]): item for item in items
                if isinstance(item, dict) and isinstance(item.get("manifest"), str) and isinstance(item.get("candidate_id"), str)}

    def write_decisions(items: dict[tuple[str, str], dict]) -> None:
        path = output / "review-decisions.json"
        temporary = output / f".{uuid.uuid4().hex}.review.tmp"
        payload = {"version": 1, "note": "待挑逐张决定；keep 即本人逐张确认（user_accepted），原 manifest 不改",
                   "items": [items[key] for key in sorted(items)]}
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def review_entry(manifest: Path, row: dict, decision: dict | None) -> dict | None:
        moments = []
        for moment in row.get("moments", []):
            if not isinstance(moment, dict) or not isinstance(moment.get("preview"), str):
                return None
            url = register_image(manifest.parent / moment["preview"])
            try:
                seconds = float(moment["seconds"])
            except (KeyError, TypeError, ValueError):
                return None
            if not url or not math.isfinite(seconds) or seconds < 0:
                return None
            moments.append({"seconds": seconds, "timecode": timecode(seconds), "url": url,
                            "offset": round(seconds - float(row["timestamp"]), 2),
                            "cut_before": moment.get("cut_before") is True, "label": str(moment.get("label") or "")})
        system = row.get("system_moment")
        if not moments or not isinstance(system, int) or not 0 <= system < len(moments):
            return None
        state = decision.get("decision") if decision else None
        kept = decision.get("moment") if decision and state == "keep" else None
        if kept is not None and not (isinstance(kept, int) and 0 <= kept < len(moments)):
            kept = None
        reasons = row.get("reasons") if isinstance(row.get("reasons"), list) else []
        return {"moments": moments, "system_moment": system, "reasons": [str(r) for r in reasons][:5],
                "decision": state, "kept_moment": kept, "drop_reason": str(decision.get("reason") or "") if decision else ""}

    def library() -> dict:
        found: dict[str, dict] = {}
        rows = []
        selection = read_json(output / "selection.json")
        selection_items = selection.get("items", []) if isinstance(selection, dict) else []
        picks = {(str(x.get("manifest") or ""), str(x.get("candidate_id"))): x for x in selection_items if isinstance(x, dict)}
        favorite_keys, _favorite_error = read_favorites()
        # A scoped local overlay preserves original manifests and stable IDs.
        status = read_json(output / "candidate-status.json")
        status_items = status.get("items", []) if isinstance(status, dict) else []
        inactive = {(item.get("manifest"), item.get("candidate_id")) for item in status_items
                    if isinstance(item, dict) and item.get("active") is False
                    and isinstance(item.get("manifest"), str)
                    and isinstance(item.get("candidate_id"), str)} if isinstance(status_items, list) else set()
        selection_order = {id(item): index for index, item in enumerate(selection_items) if isinstance(item, dict)}
        decisions = read_decisions()
        manifests = sorted(output.glob("*/manifest.json")) if output.exists() and within(output, root) else []
        if within(output, root) and (output / "manifest.json").exists():
            manifests.insert(0, output / "manifest.json")
        batches = []
        short_id_counts = Counter()
        for manifest in manifests:
            data = read_json(manifest)
            if not isinstance(data, dict):
                continue
            batches.append((manifest, data))
            short_id_counts.update(str(row.get("id", row.get("timestamp"))) for row in data.get("candidates", []) if isinstance(row, dict))
        for manifest, data in batches:
            manifest_scope = manifest.relative_to(output).as_posix()
            # 待挑 batches stay out of the gallery until the user keeps a picture.
            queue = data.get("review_queue") is True
            videos = {v.get("id"): v for v in data.get("videos", []) if isinstance(v, dict)}
            for row in data.get("candidates", []):
                if not isinstance(row, dict):
                    continue
                video = videos.get(row.get("video_id"), {})
                source_value = row.get("source") or video.get("source")
                preview_value = row.get("preview")
                if not isinstance(source_value, str) or not isinstance(preview_value, str):
                    continue
                source = Path(source_value).expanduser()
                if not source.is_absolute():
                    source = manifest.parent / source
                source = source.resolve()
                if source.suffix.lower() not in VIDEO_TYPES:
                    continue
                preview = register_image(manifest.parent / preview_value)
                try:
                    timestamp = float(row["timestamp"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not preview or not math.isfinite(timestamp) or timestamp < 0:
                    continue
                original_id = str(row.get("id", row["timestamp"]))
                key = opaque(str(manifest) + "|" + original_id)
                review = None
                if queue:
                    review = review_entry(manifest, row, decisions.get((manifest_scope, original_id)))
                    if review is None:
                        continue
                    kept = review["decision"] == "keep"
                    if kept and review["kept_moment"] is not None:
                        # The kept moment replaces the system time: its frame, export and preview.
                        chosen = review["moments"][review["kept_moment"]]
                        timestamp, preview = chosen["seconds"], chosen["url"]
                pick = picks.get((manifest_scope, original_id))
                if pick is None and short_id_counts[original_id] == 1:
                    pick = picks.get(("", original_id))
                pick = pick or {}
                # Only the user's own captures carry a name; it replaces the song title, never the chapter.
                name = str(row.get("name") or "").strip() if data.get("source_kind") == "user" else ""
                public = {
                    "id": key, "candidate_id": original_id, "preview": preview,
                    "thumbnail": preview,
                    "manifest": manifest_scope,
                    "source_kind": data.get("source_kind", "live"),
                    "title": name or str(pick.get("title") or row.get("title") or row.get("chapter") or "视频片刻"),
                    "name": name,
                    "source_label": str(video.get("label") or source.stem),
                    "timecode": timecode(timestamp) if queue else str(row.get("timecode") or f"{int(timestamp)//60:02d}:{int(timestamp)%60:02d}"),
                    "timestamp": timestamp, "chapter": row.get("chapter"),
                    "selected": bool(pick), "reviewed": pick.get("reviewed", row.get("reviewed")) is True,
                    "favorite": (manifest_scope, original_id) in favorite_keys,
                    "active": (manifest_scope, original_id) not in inactive and (not queue or kept),
                    "selection_order": selection_order.get(id(pick)),
                    "note": str(pick.get("note") or row.get("note") or ""),
                    "recommended_for": pick.get("recommended_for", row.get("recommended_for", [])),
                    "source_available": source.is_file(),
                    "scores": row.get("scores", {}),
                }
                found[key] = {**public, "source": str(source), "source_metadata": video.get("metadata", {}), "original_kind": video.get("kind", data.get("source_kind", "live"))}
                if review is not None:
                    found[key]["review"] = {**review, "batch": str(data.get("label") or manifest_scope), "order": len(found)}
                if public["active"] or public["favorite"] or public["selected"]:
                    rows.append(public)
        candidates.clear()
        candidates.update(found)
        avatar_rows = []
        found_avatars = {}
        image_folders = sorted([*output.glob("avatar*"), output / "illustrations"]) if output.exists() and within(output, root) else []
        for folder in image_folders:
            paths = [folder] if folder.is_file() else sorted(folder.rglob("*"))
            for path in paths:
                image_url = register_image(path)
                if image_url:
                    try:
                        with Image.open(path) as picture:
                            width, height = picture.size
                    except (OSError, ValueError):
                        continue
                    metadata = read_json(path.with_suffix(".json"))
                    if not isinstance(metadata, dict):
                        metadata = {}
                    reference = str(metadata.get("reference_url") or "")
                    if urlsplit(reference).scheme not in {"http", "https"}:
                        reference = ""
                    key = opaque(str(path.resolve()))
                    uses = metadata.get("uses", ["avatar"] if folder.name.startswith("avatar") else ["phone-wallpaper" if height > width else "desktop-wallpaper"])
                    uses = [use for use in uses if use in {"avatar", "phone-wallpaper", "desktop-wallpaper"}] if isinstance(uses, list) else []
                    public = {"id": key, "title": str(metadata.get("title") or path.stem.replace("_", " ")), "preview": image_url, "download": image_url + "?download=true", "width": width, "height": height, "direction": str(metadata.get("direction") or "本地插画作品，参考待补充"), "reference_url": reference, "prompt": str(metadata.get("prompt") or ""), "kind": str(metadata.get("kind") or "local-artwork"), "uses": uses, "relative_path": path.relative_to(output).as_posix()}
                    found_avatars[key] = {**public, "path": path.resolve()}
                    avatar_rows.append(public)
        avatars.clear()
        avatars.update(found_avatars)
        rows.sort(key=lambda row: (not row["selected"], row["selection_order"] if row["selection_order"] is not None else len(selection_items), row["source_label"], row["timestamp"]))
        return {"candidates": rows, "avatars": avatar_rows, "manifest_count": len(manifests)}

    def favorite_set() -> set[tuple[str, str]]:
        items, error = read_favorites()
        if error:
            raise ValueError(error)
        return items

    def write_favorites(items: set[tuple[str, str]]) -> None:
        output.mkdir(parents=True, exist_ok=True)
        path = output / "favorites.json"
        temporary = path.with_suffix(".json.tmp")
        payload = {"version": 1, "items": [{"manifest": manifest, "candidate_id": candidate_id} for manifest, candidate_id in sorted(items)]}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    async def render_cli(candidate: dict, params: ExportRequest, destination: Path):
        command = [sys.executable, str(root / "live_wallpaper.py"), "render", "--source", candidate["source"], "--timestamp", str(candidate["timestamp"]), "--output", str(destination), "--size", f"{params.width}x{params.height}", "--fit", params.fit, "--focus-x", str(params.focus_x), "--focus-y", str(params.focus_y), "--exposure", str(params.exposure), "--contrast", str(params.contrast), "--sharpen", str(params.sharpen)]
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=root)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            logging.error("Render failed: %s", stderr.decode(errors="replace")[-2000:])
            raise RuntimeError("导出失败，请确认源视频仍在原位置，且视频工具可用。")

    def preview_signature(candidate: dict) -> tuple[str, dict]:
        source = Path(candidate["source"])
        stat = source.stat()
        pipeline = root / "live_wallpaper.py"
        pipeline_hash = hashlib.sha256(pipeline.read_bytes()).hexdigest() if pipeline.is_file() else "test-renderer"
        signature = {"version": 1, "source": str(source.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "timestamp": candidate["timestamp"], "source_metadata": candidate["source_metadata"], "pipeline_sha256": pipeline_hash}
        key = opaque(json.dumps(signature, sort_keys=True, ensure_ascii=False))
        return key, signature

    def cached_preview(cache_dir: Path, signature: dict, candidate: dict) -> dict | None:
        record = read_json(cache_dir / "cache.json")
        if not isinstance(record, dict) or record.get("signature") != signature or not isinstance(record.get("image"), str):
            return None
        path = cache_dir / record["image"]
        if not within(path, cache_dir):
            return None
        image_url = register_image(path)
        if not image_url:
            return None
        try:
            with Image.open(path) as picture:
                width, height = picture.size
        except (OSError, ValueError):
            return None
        name_media(image_url, download_name(candidate, "原帧"))
        return {"preview": image_url, "original": image_url, "download": image_url + "?download=true", "width": width, "height": height}

    def color_filters(metadata: dict) -> list[str]:
        # One shared LUT per HDR kind: building a 65³ table costs ~0.4 s of CPU.
        luts = output / "preview-cache" / "luts"
        with lut_lock:
            luts.mkdir(parents=True, exist_ok=True)
            return color_plan(metadata, luts, "auto", 1000, 65)[0]

    def extract_native(candidate: dict, destination: Path) -> None:
        # Same frame as `live_wallpaper.py render --native` (pixel-identical),
        # without its second full-size PNG pass, LUT rebuild and Python start.
        source = Path(candidate["source"])
        destination.mkdir(parents=True)
        extract_frame(source, candidate["timestamp"], destination / "original.png", color_filters(probe_video(source)))

    async def render_native(candidate: dict, destination: Path):
        try:
            await asyncio.to_thread(extract_native, candidate, destination)
        except (OSError, ValueError, WallpaperError):
            logging.exception("Native preview failed")
            raise RuntimeError("原帧暂未载入，可重新选择这一帧再试。") from None

    async def run_preview(job: dict, candidate: dict, signature: dict, cache_dir: Path):
        try:
            async with preview_slot:
                job["status"] = "running"
                destination = cache_dir / uuid.uuid4().hex
                cache_dir.mkdir(parents=True, exist_ok=True)
                await (preview_renderer or render_native)(candidate, destination)
                _, current_signature = preview_signature(candidate)
                if current_signature != signature:
                    raise RuntimeError("源视频刚刚变化，请刷新后重新选片。")
                record = {"signature": signature, "image": str((destination / "original.png").relative_to(cache_dir))}
                record_path = cache_dir / "cache.json"
                record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                result = cached_preview(cache_dir, signature, candidate)
                if result is None:
                    raise RuntimeError("未找到可读取的原始帧。")
                job.update(status="done", **result)
        except asyncio.CancelledError:
            job.update(status="failed", error="服务关闭，原帧载入已中止。")
            raise
        except asyncio.TimeoutError:
            job.update(status="failed", error="原帧读取超时，请重试。")
        except Exception as exc:
            job.update(status="failed", error=str(exc)[:500])

    async def drain_previews():
        # A single dispatcher chooses after each render, not semaphore FIFO order.
        while preview_pending:
            job_id = min(preview_pending, key=lambda key: preview_jobs[key]["priority"] != "current")
            candidate, signature, cache_dir = preview_pending.pop(job_id)
            await run_preview(preview_jobs[job_id], candidate, signature, cache_dir)

    @app.get("/api/power")
    async def get_power():
        return await asyncio.to_thread(power_status)

    @app.post("/api/previews")
    async def request_preview(params: PreviewRequest):
        candidate = candidates.get(params.candidate_id)
        if candidate is None:
            raise HTTPException(404, "候选画面不存在，请刷新选片。")
        return await enqueue_preview(candidate, params.priority)

    async def enqueue_preview(candidate: dict, priority: str, generation: int | None = None):
        nonlocal preview_worker, current_revision
        if priority == "current" and generation is None:
            current_revision += 1
        revision = current_revision if generation is None else generation
        try:
            key, signature = await asyncio.to_thread(preview_signature, candidate)
        except OSError:
            raise HTTPException(409, "源视频已移动，暂时只能查看缩略图。")
        cache_dir = output / "preview-cache" / key
        if not within(cache_dir, output) or not within(output, root):
            raise HTTPException(400, "预览缓存目录不在项目内。")
        result = await asyncio.to_thread(cached_preview, cache_dir, signature, candidate)
        # Disk checks can finish out of order. An obsolete request must not
        # invalidate newer work or reinsert itself after the user has moved on.
        if revision != current_revision:
            return {"id": key, "candidate_id": candidate["id"], "status": "cancelled", "generation": revision}
        if priority == "current":
            for job_id in list(preview_pending):
                if preview_jobs[job_id]["cache_key"] != key:
                    preview_jobs[job_id]["status"] = "cancelled"
                    del preview_pending[job_id]
        if result is not None:
            return {"id": key, "candidate_id": candidate["id"], "status": "done", "cached": True, "generation": revision, **result}
        existing = next((job for job in preview_jobs.values()
                         if job["cache_key"] == key and job["status"] in {"queued", "running"}), None)
        if existing is not None:
            if priority == "current":
                existing["priority"] = "current"
            return {**existing, "candidate_id": candidate["id"], "generation": revision}
        if sum(job["status"] in {"queued", "running"} for job in preview_jobs.values()) >= 8:
            raise HTTPException(429, "正在载入几张原帧，请稍后再选。")
        # A new attempt gets a new ID so cancelled pollers never follow a retry.
        job_id = key + "-" + uuid.uuid4().hex[:8]
        job = {"id": job_id, "cache_key": key, "candidate_id": candidate["id"],
               "status": "queued", "priority": priority, "cached": False, "generation": revision}
        preview_jobs[job_id] = job
        preview_pending[job_id] = (candidate.copy(), signature, cache_dir)
        for old in list(preview_jobs):
            if len(preview_jobs) <= 100:
                break
            if preview_jobs[old]["status"] in {"done", "failed", "cancelled"}:
                del preview_jobs[old]
        if preview_worker is None or preview_worker.done():
            preview_worker = asyncio.create_task(drain_previews())
            tasks.add(preview_worker)
            preview_worker.add_done_callback(tasks.discard)
        return job

    @app.get("/api/preview-jobs/{job_id}")
    async def get_preview_job(job_id: str):
        if job_id not in preview_jobs:
            raise HTTPException(404, "原帧载入任务不存在。")
        return preview_jobs[job_id]

    # 主页放映（控制台读）：只读，不截新原帧、不写收藏；只把已缓存的原帧缩成 2560 宽 JPG。
    SHOWCASE_WIDTH = 2560
    showcase_lock = threading.Lock()
    brightness_cache: dict[str, tuple[int, float]] = {}

    def thumbnail_brightness(url: str) -> float | None:
        path = media.get(url.removeprefix("/media/"))
        if path is None:
            return None
        try:
            mtime = path.stat().st_mtime_ns
            hit = brightness_cache.get(str(path))
            if hit and hit[0] == mtime:
                return hit[1]
            with Image.open(path) as picture:
                value = round(ImageStat.Stat(picture.convert("L").resize((64, 30))).mean[0] / 255, 3)
        except (OSError, ValueError):
            return None
        brightness_cache[str(path)] = (mtime, value)
        return value

    def showcase_items() -> list[dict]:
        library()
        rows = []
        for key, candidate in candidates.items():
            if not candidate["favorite"]:
                continue
            ready = False
            try:
                cache_key, signature = preview_signature(candidate)
                ready = cached_preview(output / "preview-cache" / cache_key, signature, candidate) is not None
            except OSError:
                pass
            rows.append({"id": key, "title": candidate["title"], "source_label": candidate["source_label"],
                         "timecode": str(candidate["timecode"]).split(".")[0], "thumbnail": candidate["thumbnail"],
                         "wide": f"/showcase/{key}.jpg" if ready else None,
                         "brightness": thumbnail_brightness(candidate["thumbnail"])})
        return rows

    def showcase_image(key: str) -> Path | None:
        candidate = candidates.get(key)
        if candidate is None or not candidate["favorite"]:
            return None
        try:
            cache_key, signature = preview_signature(candidate)
        except OSError:
            return None
        cached = cached_preview(output / "preview-cache" / cache_key, signature, candidate)
        if cached is None:
            return None
        original = media.get(cached["original"].removeprefix("/media/"))
        target = output / "preview-cache" / "showcase" / f"{cache_key}-{SHOWCASE_WIDTH}.jpg"
        if not within(target, output) or original is None:
            return None
        with showcase_lock:
            if not target.is_file() or target.stat().st_mtime_ns < original.stat().st_mtime_ns:
                target.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(original) as picture:
                    picture = picture.convert("RGB")
                    if picture.width > SHOWCASE_WIDTH:
                        picture = picture.resize((SHOWCASE_WIDTH, round(picture.height * SHOWCASE_WIDTH / picture.width)), Image.LANCZOS)
                    temp = target.with_suffix(".tmp")
                    picture.save(temp, "JPEG", quality=88, optimize=True, progressive=True)
                    os.replace(temp, target)
        return target

    @app.get("/api/showcase")
    async def get_showcase():
        items = await asyncio.to_thread(showcase_items)
        return {"items": items, "total": len(items), "ready": sum(item["wide"] is not None for item in items)}

    @app.get("/showcase/{name}")
    async def get_showcase_image(name: str):
        key = name.removesuffix(".jpg")
        if not name.endswith(".jpg") or not re.fullmatch(r"[0-9a-f]{24}", key):
            raise HTTPException(404, "图片不存在。")
        if key not in candidates:
            await asyncio.to_thread(library)
        try:
            path = await asyncio.to_thread(showcase_image, key)
        except (OSError, ValueError):
            logging.exception("Showcase image failed")
            path = None
        if path is None:
            raise HTTPException(404, "这张还没有原帧缓存。")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    async def run_job(job_id: str, candidate: dict, params: ExportRequest, destination: Path):
        job = jobs[job_id]
        job["status"] = "running"
        try:
            await (renderer or render_cli)(candidate, params, destination)
            image_url = register_image(destination / "wallpaper.png")
            original_url = register_image(destination / "original.png")
            if not image_url:
                raise RuntimeError("导出没有产生可读取的壁纸文件。")
            filename = download_name(candidate, f"{params.width}x{params.height}")
            name_media(image_url, filename)
            name_media(original_url, download_name(candidate, "原帧"))
            job.update(filename=filename, status="done", preview=image_url, download=image_url + "?download=true", original=original_url)
        except asyncio.CancelledError:
            job.update(status="failed", error="服务关闭，导出已中止。")
            raise
        except asyncio.TimeoutError:
            job.update(status="failed", error="导出超过三分钟，请重试。")
        except Exception as exc:
            job.update(status="failed", error=str(exc)[:500])

    @app.get("/api/library")
    async def get_library():
        return await asyncio.to_thread(library)

    @app.post("/api/favorites")
    async def update_favorite(params: FavoriteRequest):
        candidate = candidates.get(params.candidate_id)
        if candidate is None:
            raise HTTPException(404, "候选画面不存在，请刷新选片。")
        key = (str(candidate["manifest"]), str(candidate["candidate_id"]))
        await set_favorite(key, params.favorite)
        return {"candidate_id": params.candidate_id, "favorite": params.favorite}

    def review_items() -> list[dict]:
        rows = []
        for key, candidate in candidates.items():
            review = candidate.get("review")
            if not review:
                continue
            rows.append({"id": key, "candidate_id": candidate["candidate_id"], "manifest": candidate["manifest"],
                         "batch": review["batch"], "title": candidate["title"], "chapter": candidate["chapter"],
                         "source_label": candidate["source_label"], "reasons": review["reasons"],
                         "moments": review["moments"], "system_moment": review["system_moment"],
                         "decision": review["decision"], "kept_moment": review["kept_moment"],
                         "drop_reason": review["drop_reason"], "order": review["order"]})
        rows.sort(key=lambda row: row["order"])
        for row in rows:
            row.pop("order")
        return rows

    @app.get("/api/review-queue")
    async def get_review_queue():
        async with review_lock:
            await asyncio.to_thread(library)
            return {"items": review_items()}

    @app.post("/api/review-decisions")
    async def update_review_decision(params: ReviewDecisionRequest):
        async with review_lock:
            await asyncio.to_thread(library)
            candidate = candidates.get(params.candidate_id)
            if candidate is None or not candidate.get("review"):
                raise HTTPException(404, "这张不在待挑队列里，请刷新。")
            moments = candidate["review"]["moments"]
            if params.decision == "keep" and (params.moment is None or params.moment >= len(moments)):
                raise HTTPException(422, "请选一个时刻再留。")
            key = (str(candidate["manifest"]), str(candidate["candidate_id"]))
            decisions = await asyncio.to_thread(read_decisions)
            previous = decisions.get(key, {}).get("decision")
            if params.decision is None:
                decisions.pop(key, None)
            else:
                entry = {"manifest": key[0], "candidate_id": key[1], "decision": params.decision,
                         "decided_at": datetime.now(timezone.utc).isoformat()}
                if params.decision == "keep":
                    entry.update(moment=params.moment, seconds=moments[params.moment]["seconds"],
                                 system_moment=candidate["review"]["system_moment"], user_accepted=True)
                if params.decision in {"drop", "moment"} and params.reason:
                    entry["reason"] = params.reason
                decisions[key] = entry
            try:
                await asyncio.to_thread(write_decisions, decisions)
            except OSError:
                raise HTTPException(500, "保存失败，请检查本地图库目录权限。") from None
        # 留 = 收藏; undoing or changing a keep takes the favorite back off.
        if params.decision == "keep":
            await set_favorite(key, True)
        elif previous == "keep":
            await set_favorite(key, False)
        return {"candidate_id": params.candidate_id, "decision": params.decision}

    async def set_favorite(key: tuple[str, str], favorite: bool):
        async with favorite_lock:
            try:
                items = await asyncio.to_thread(favorite_set)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
            if favorite:
                items.add(key)
            else:
                items.discard(key)
            try:
                await asyncio.to_thread(write_favorites, items)
            except OSError:
                raise HTTPException(500, "收藏保存失败，请检查本地图库目录权限。")

    async def source_metadata(source: dict) -> dict:
        path = Path(source["source"])
        try:
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
            cached = probe_cache.get(str(path))
            if cached and cached[0] == stamp:
                return cached[1]
            metadata = await asyncio.to_thread(probe_video, path)
            probe_cache[str(path)] = (stamp, metadata)
            return metadata
        except (OSError, ValueError, WallpaperError):
            # Probe diagnostics may contain private paths: do not return them.
            raise HTTPException(409, "源视频不可用或无法读取，请检查本地视频。") from None

    @app.get("/api/capture-sources")
    async def get_capture_sources():
        async with source_lock:
            await asyncio.to_thread(library)
            sources = {}
            for candidate in list(candidates.values()):
                key = opaque(candidate["source"])
                kind = candidate["original_kind"]
                if kind not in {"live", "mv"}:
                    continue
                sources.setdefault(key, {"id": key, "label": candidate["source_label"],
                                         "kind": kind, "source": candidate["source"]})
            capture_sources.clear()
            capture_sources.update(sources)
            public = []
            for source in sources.values():
                item = {k: source[k] for k in ("id", "label", "kind")}
                try:
                    metadata = await source_metadata(source)
                    item.update(available=True, duration=metadata["duration"], chapters=metadata.get("chapters", []))
                except HTTPException:
                    item.update(available=False, duration=None, chapters=[])
                public.append(item)
            return {"sources": sorted(public, key=lambda item: (item["kind"], item["label"]))}

    async def capture_candidate(params: CaptureRequest) -> dict:
        source = capture_sources.get(params.source_id)
        if source is None:
            await get_capture_sources()
            source = capture_sources.get(params.source_id)
        if source is None:
            raise HTTPException(404, "视频不存在，请刷新视频列表。")
        async with source_lock:
            metadata = await source_metadata(source)
        if params.timestamp >= metadata["duration"]:
            raise HTTPException(422, "时间超出视频长度。")
        chapter = next((c["title"] for c in metadata.get("chapters", [])
                        if c["start"] <= params.timestamp < c["end"]), "")
        return {"id": opaque(f"capture|{source['id']}|{params.timestamp:.1f}"),
                "source": source["source"], "source_label": source["label"],
                "original_kind": source["kind"], "source_metadata": metadata,
                "timestamp": params.timestamp, "timecode": timecode(params.timestamp),
                "chapter": chapter, "title": chapter or "我截的画面"}

    @app.post("/api/capture-previews")
    async def capture_preview(params: CapturePreviewRequest):
        # Reserve the current revision before probing: slow old probes cannot
        # replace a more recent request. Prefetch carries its parent's revision.
        nonlocal current_revision
        if params.priority == "current":
            current_revision += 1
        generation = current_revision if params.priority == "current" or params.generation is None else params.generation
        candidate = await capture_candidate(params)
        if generation != current_revision:
            return {"status": "cancelled", "id": candidate["id"], "generation": generation}
        return await enqueue_preview(candidate, params.priority, generation)

    def render_strip(candidate: dict, start: float, step: float, count: int, folder: Path) -> None:
        # Small JPEG previews for choosing a moment; the main picture is still
        # the native frame. Dense strips decode once; wide strips seek per frame.
        metadata = candidate["source_metadata"]
        color = color_filters(metadata)
        ratio = metadata.get("display_aspect_ratio") or 16 / 9
        width, height = STRIP_WIDTH, max(2, 2 * round(STRIP_WIDTH / ratio / 2))

        def ffmpeg(seconds: float, frames: int, target: Path) -> None:
            # round=up keeps the frame at each tick; the default (near) keeps
            # the last frame before the next half tick, up to step/2 late.
            pick = [f"fps={1 / step:g}:round=up"] if frames > 1 else []
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-ss", f"{seconds:.6f}"]
            if frames > 1:
                command += ["-t", f"{(frames - 1 + .5) * step:.6f}"]
            tail = ["-i", candidate["source"], "-map", "0:v:0", "-an", "-sn", "-vf", None,
                    "-frames:v", str(frames), "-q:v", "4", "-pix_fmt", "yuvj420p", str(target)]
            cpu_scale = ",".join([*pick, f"scale={width}:{height}", *color])
            attempts = []
            if os.environ.get("YORUSHIKA_HWACCEL", "1") != "0":
                # Best: decode and shrink on the GPU, copy only small frames back.
                # The download format must match the decoder's: a list fails on HDR.
                deep = re.search(r"p1[0-6]", metadata.get("pixel_format") or "")
                gpu_scale = ",".join([*pick, f"scale_vt=w={width}:h={height}", "hwdownload", "format=p010le" if deep else "format=nv12", *color])
                attempts += [["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld", gpu_scale],
                             ["-hwaccel", "videotoolbox", cpu_scale]]
            attempts.append([cpu_scale])
            for index, attempt in enumerate(attempts):
                *options, vf = attempt
                try:
                    run(command + options + [vf if part is None else part for part in tail])
                    if frames > 1 or target.is_file():
                        return
                except WallpaperError:
                    if index == len(attempts) - 1:
                        raise
                # Only this attempt's partial output can exist here (-n above).
                for partial in folder.glob(target.name.replace("%02d", "*")):
                    partial.unlink()

        if step < STRIP_SEEK_STEP:
            ffmpeg(start, count, folder / "%02d.jpg")
            return
        # Wide steps seek per frame (a 5 s GOP makes one pass decode far more).
        with ThreadPoolExecutor(STRIP_SEEK_WORKERS) as pool:
            jobs = [pool.submit(ffmpeg, start + index * step, 1, folder / f"{index + 1:02d}.jpg") for index in range(count)]
            errors = [job.exception() for job in jobs]
        if all(errors):
            raise errors[0]

    def capture_strip_files(candidate: dict, times: list[float], step: float) -> dict[float, Path]:
        source = Path(candidate["source"])
        stat = source.stat()
        key = opaque(json.dumps({"version": 2, "source": str(source.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                                 "source_metadata": candidate["source_metadata"], "width": STRIP_WIDTH}, sort_keys=True, ensure_ascii=False))
        folder = output / "preview-cache" / "strips" / key
        if not within(folder, output) or not within(output, root):
            raise HTTPException(400, "预览缓存目录不在项目内。")
        files = {t: folder / f"{round(t * 10)}.jpg" for t in times}
        missing = [t for t in times if not files[t].is_file()]
        if missing:
            # Times share one grid, so one run from the first to the last
            # missing frame also covers any missing frames in between.
            start = min(missing)
            count = round((max(missing) - start) / step) + 1
            work = folder / f".{uuid.uuid4().hex}"
            work.mkdir(parents=True)
            try:
                (strip_renderer or render_strip)(candidate, start, step, count, work)
                for index in range(count):
                    timestamp = round((start + index * step) * 10) / 10
                    produced = work / f"{index + 1:02d}.jpg"
                    if timestamp in files and produced.is_file():
                        produced.replace(files[timestamp])
            finally:
                for leftover in work.glob("*"):
                    leftover.unlink()
                work.rmdir()
        return {t: path for t, path in files.items() if path.is_file()}

    @app.post("/api/capture-strip")
    async def capture_strip(params: CaptureStripRequest):
        candidate = await capture_candidate(CaptureRequest(source_id=params.source_id, timestamp=params.start))
        duration = candidate["source_metadata"]["duration"]
        times = [t for t in (round((params.start + i * params.step) * 10) / 10 for i in range(params.count)) if t < duration]
        async with strip_lock:
            try:
                files = await asyncio.to_thread(capture_strip_files, candidate, times, params.step)
            except OSError:
                raise HTTPException(409, "源视频不可用或无法读取，请检查本地视频。") from None
            except WallpaperError:
                # ffmpeg diagnostics may contain private paths: log, do not return.
                logging.exception("Capture strip failed")
                raise HTTPException(409, "附近帧暂时没有生成，可以直接用主图。") from None
        return {"frames": [{"timestamp": t, "url": register_image(files[t]) if t in files else None} for t in times]}

    def capture_manifest() -> tuple[Path, dict]:
        folder = output / "user-captures"
        manifest = folder / "manifest.json"
        if not all(within(path, output) for path in (folder, manifest, folder / "previews")) or not within(output, root):
            raise HTTPException(400, "截帧目录不在项目内。")
        data = {"version": 1, "source_kind": "user", "videos": [], "candidates": []}
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("source_kind") != "user" or not isinstance(data["videos"], list) or not isinstance(data["candidates"], list):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError):
                raise HTTPException(409, "我截的清单无法读取，已保留原文件。") from None
        return manifest, data

    def write_capture_manifest(manifest: Path, data: dict) -> None:
        temporary = manifest.parent / f".{uuid.uuid4().hex}.json.tmp"
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(manifest)
        finally:
            temporary.unlink(missing_ok=True)

    def set_capture_name(row: dict, name: str) -> None:
        if name:
            row["name"] = name
        else:
            row.pop("name", None)

    def rename_capture(capture_id: str, name: str) -> None:
        manifest, data = capture_manifest()
        row = next((row for row in data["candidates"] if isinstance(row, dict) and row.get("id") == capture_id), None)
        if row is None:
            raise HTTPException(404, "这张截图不在「我截的」清单里，请刷新图库。")
        set_capture_name(row, name)
        write_capture_manifest(manifest, data)

    def persist_capture(candidate: dict, image: Path, name: str = "") -> tuple[str, bool]:
        manifest, data = capture_manifest()
        folder = manifest.parent
        previews = folder / "previews"
        for row in data["candidates"]:
            if row["source"] == candidate["source"] and row["timestamp"] == candidate["timestamp"]:
                # Same frame again: a newly typed name still lands; a blank one keeps the old name.
                if name and row.get("name") != name:
                    set_capture_name(row, name)
                    write_capture_manifest(manifest, data)
                return row["id"], True
        number = max((int(row["id"][1:]) for row in data["candidates"]), default=0) + 1
        short_id = f"U{number:03d}"
        video_id = opaque(candidate["source"])
        if not any(video["id"] == video_id for video in data["videos"]):
            data["videos"].append({"id": video_id, "source": candidate["source"],
                                   "label": candidate["source_label"], "kind": candidate["original_kind"],
                                   "metadata": candidate["source_metadata"]})
        previews.mkdir(parents=True, exist_ok=True)
        thumbnail = previews / f"{short_id}.jpg"
        if thumbnail.exists():
            raise HTTPException(409, "截帧缩略图已存在但未入库，请保留现场后检查。")
        temporary = previews / f".{uuid.uuid4().hex}.jpg"
        with Image.open(image) as picture:
            picture = picture.convert("RGB")
            picture.resize((640, max(1, round(640 * picture.height / picture.width))), Image.Resampling.LANCZOS).save(temporary, quality=90)
        temporary.replace(thumbnail)
        row = {"id": short_id, "video_id": video_id, "source": candidate["source"],
               "timestamp": candidate["timestamp"], "timecode": candidate["timecode"],
               "chapter": candidate["chapter"], "created_at": datetime.now(timezone.utc).isoformat(),
               "preview": f"previews/{short_id}.jpg"}
        set_capture_name(row, name)
        data["candidates"].append(row)
        try:
            write_capture_manifest(manifest, data)
        except OSError:
            # Roll back only files created by this attempt, never old captures.
            thumbnail.unlink(missing_ok=True)
            raise
        return short_id, False

    @app.post("/api/captures")
    async def save_capture(params: CaptureRequest):
        # Serialize the full save operation, including duplicate cache checks.
        async with capture_lock:
            return await complete_capture(params)

    async def complete_capture(params: CaptureRequest):
        candidate = await capture_candidate(params)
        job = await enqueue_preview(candidate, "current")
        deadline = asyncio.get_running_loop().time() + 210
        while job["status"] in {"queued", "running"}:
            if asyncio.get_running_loop().time() > deadline:
                raise HTTPException(504, "截帧等待超时，请重试。")
            await asyncio.sleep(.05)
            job = preview_jobs.get(job["id"], job)
        if job["status"] != "done":
            raise HTTPException(409, "这一帧尚未完成，请停在所选时间后重试。")
        key, signature = await asyncio.to_thread(preview_signature, candidate)
        result = await asyncio.to_thread(cached_preview, output / "preview-cache" / key, signature, candidate)
        if not result:
            raise HTTPException(409, "源视频或原帧刚刚变化，请重新截取。")
        try:
            short_id, duplicate = await asyncio.to_thread(persist_capture, candidate, media[result["original"].removeprefix("/media/")], params.name)
        except OSError:
            raise HTTPException(500, "加入图库失败，请检查本地目录权限。") from None
        if params.favorite:
            await set_favorite(("user-captures/manifest.json", short_id), True)
        public_id = opaque(str(output / "user-captures" / "manifest.json") + "|" + short_id)
        return {"candidate_id": public_id, "capture_id": short_id, "duplicate": duplicate}

    @app.post("/api/capture-names")
    async def update_capture_name(params: CaptureNameRequest):
        candidate = candidates.get(params.candidate_id)
        if candidate is None or candidate.get("manifest") != "user-captures/manifest.json":
            raise HTTPException(404, "只有「我截的」可以改名，请刷新图库。")
        async with capture_lock:
            try:
                await asyncio.to_thread(rename_capture, candidate["candidate_id"], params.name)
            except OSError:
                raise HTTPException(500, "名字没有保存，请检查本地目录权限。") from None
        title = params.name or str(candidate.get("chapter") or "视频片刻")
        previous = download_name(candidate, "原帧")
        candidate.update(name=params.name, title=title)
        # An original frame already on screen downloads under the new name too.
        for path, existing in list(media_names.items()):
            if existing == previous:
                media_names[path] = download_name(candidate, "原帧")
        return {"candidate_id": params.candidate_id, "name": params.name, "title": title}

    @app.post("/api/exports", status_code=202)
    async def export_image(params: ExportRequest):
        candidate = candidates.get(params.candidate_id)
        if candidate is None:
            raise HTTPException(404, "候选画面不存在，请刷新选片。")
        if not Path(candidate["source"]).is_file():
            raise HTTPException(409, "源视频已移动，请重新扫描候选画面。")
        if sum(job["status"] in {"queued", "running"} for job in jobs.values()) >= 2:
            raise HTTPException(429, "已有两张壁纸正在导出，请稍等。")
        job_id = uuid.uuid4().hex
        destination = output / "exports" / job_id
        if not within(destination, output) or not within(output, root):
            raise HTTPException(400, "输出目录不在项目内。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        job = {"id": job_id, "status": "queued", "width": params.width, "height": params.height}
        jobs[job_id] = job
        while len(jobs) > 100:
            old_id = next((key for key, value in jobs.items() if value["status"] in {"done", "failed"}), None)
            if old_id is None:
                break
            del jobs[old_id]
        task = asyncio.create_task(run_job(job_id, candidate.copy(), params, destination))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return job

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, "导出任务不存在。")
        return jobs[job_id]

    @app.post("/api/avatar-exports")
    async def export_avatar(params: AvatarExportRequest):
        avatar = avatars.get(params.avatar_id)
        if avatar is None:
            raise HTTPException(404, "插画不存在，请刷新画廊。")
        source = avatar["path"]
        if not within(source, output) or not source.is_file():
            raise HTTPException(404, "插画原图已移动，请刷新画廊。")
        if params.size == "original":
            return {"preview": avatar["preview"], "download": avatar["download"], "width": avatar["width"], "height": avatar["height"], "original": True}
        target_size = tuple(map(int, params.size.split("x"))) if isinstance(params.size, str) else (params.size, params.size)
        ratios = [target_size[0] / avatar["width"], target_size[1] / avatar["height"]]
        scale = max(ratios) if params.fit == "cover" else min(ratios)
        destination = output / "exports" / ("avatar-" + uuid.uuid4().hex)
        if not within(destination, output) or not within(output, root):
            raise HTTPException(400, "输出目录不在项目内。")

        def resize_avatar():
            destination.mkdir(parents=True, exist_ok=False)
            with Image.open(source) as picture:
                picture = ImageOps.exif_transpose(picture).convert("RGBA")
                if params.fit == "cover":
                    result = ImageOps.fit(picture, target_size, method=Image.Resampling.LANCZOS, centering=(params.focus_x, params.focus_y))
                else:
                    content = ImageOps.contain(picture, target_size, method=Image.Resampling.LANCZOS)
                    result = Image.new("RGBA", target_size, (0, 0, 0, 0))
                    result.paste(content, (round((target_size[0]-content.width)*params.focus_x), round((target_size[1]-content.height)*params.focus_y)))
                result.save(destination / "avatar.png")
            (destination / "provenance.json").write_text(json.dumps({"kind": "local-artwork-resize", "source": str(source), "source_dimensions": [avatar["width"], avatar["height"]], "size": list(target_size), "fit": params.fit, "focus_x": params.focus_x, "focus_y": params.focus_y, "scale": scale, "upscaled": scale > 1, "reference_url": avatar["reference_url"], "generative_edit": False}, ensure_ascii=False, indent=2), encoding="utf-8")

        async with artwork_slots:
            await asyncio.to_thread(resize_avatar)
        image_url = register_image(destination / "avatar.png")
        return {"preview": image_url, "download": image_url + "?download=true", "width": target_size[0], "height": target_size[1], "original": False, "scale": round(scale, 3), "upscaled": scale > 1}

    @app.get("/media/{media_id}")
    async def get_media(media_id: str, download: bool = False):
        path = media.get(media_id)
        if path is None or not within(output, root) or not within(path, output) or not path.is_file():
            raise HTTPException(404, "图片不存在，请刷新。")
        filename = media_names.get(media_id, path.name) if download else None
        return FileResponse(path, filename=filename)

    @app.get("/")
    async def home():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="打开本机的夜鹿壁纸与头像工作室")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)
