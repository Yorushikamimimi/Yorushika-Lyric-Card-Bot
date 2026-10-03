"""Loopback-only gallery and render queue for local Yorushika artwork."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_TYPES = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv"}


def within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


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


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=64)


class AvatarExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    avatar_id: str = Field(min_length=1, max_length=64)
    size: Literal[512, 1024, "original", "1170x2532", "1284x2778", "2560x1440", "3456x2234", "3840x2160"] = "original"
    fit: Literal["contain", "cover"] = "cover"
    focus_x: float = Field(default=.5, ge=0, le=1)
    focus_y: float = Field(default=.5, ge=0, le=1)


def create_app(project_root: Path = ROOT, renderer: Renderer | None = None, preview_renderer: PreviewRenderer | None = None) -> FastAPI:
    root = project_root.resolve()
    output = root / "output"
    candidates: dict[str, dict] = {}
    media: dict[str, Path] = {}
    avatars: dict[str, dict] = {}
    jobs: dict[str, dict] = {}
    preview_jobs: dict[str, dict] = {}
    tasks: set[asyncio.Task] = set()
    preview_slot = asyncio.Semaphore(1)
    artwork_slots = asyncio.Semaphore(2)

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

    def read_json(path: Path) -> Any:
        if not within(output, root) or not within(path, output):
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def library() -> dict:
        found: dict[str, dict] = {}
        rows = []
        selection = read_json(output / "selection.json")
        selection_items = selection.get("items", []) if isinstance(selection, dict) else []
        picks = {(str(x.get("manifest") or ""), str(x.get("candidate_id"))): x for x in selection_items if isinstance(x, dict)}
        selection_order = {id(item): index for index, item in enumerate(selection_items) if isinstance(item, dict)}
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
                pick = picks.get((manifest_scope, original_id))
                if pick is None and short_id_counts[original_id] == 1:
                    pick = picks.get(("", original_id))
                pick = pick or {}
                public = {
                    "id": key, "candidate_id": original_id, "preview": preview,
                    "thumbnail": preview,
                    "manifest": manifest_scope,
                    "source_kind": data.get("source_kind", "live"),
                    "title": str(pick.get("title") or row.get("chapter") or "视频片刻"),
                    "source_label": str(video.get("label") or source.stem),
                    "timecode": str(row.get("timecode") or f"{int(timestamp)//60:02d}:{int(timestamp)%60:02d}"),
                    "timestamp": timestamp, "chapter": row.get("chapter"),
                    "selected": bool(pick), "reviewed": pick.get("reviewed") is True,
                    "selection_order": selection_order.get(id(pick)),
                    "note": str(pick.get("note") or ""),
                    "recommended_for": pick.get("recommended_for", []),
                    "source_available": source.is_file(),
                    "scores": row.get("scores", {}),
                }
                found[key] = {**public, "source": str(source), "source_metadata": video.get("metadata", {})}
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

    def cached_preview(cache_dir: Path, signature: dict) -> dict | None:
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
        return {"preview": image_url, "original": image_url, "download": image_url + "?download=true", "width": width, "height": height}

    async def render_native(candidate: dict, destination: Path):
        command = [sys.executable, str(root / "live_wallpaper.py"), "render", "--source", candidate["source"], "--timestamp", str(candidate["timestamp"]), "--output", str(destination), "--native", "--sharpen", "0"]
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=root)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            logging.error("Native preview failed: %s", stderr.decode(errors="replace")[-2000:])
            raise RuntimeError("原帧暂未载入，可重新选择这一帧再试。")

    async def run_preview(key: str, candidate: dict, signature: dict, cache_dir: Path):
        job = preview_jobs[key]
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
                result = cached_preview(cache_dir, signature)
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

    @app.post("/api/previews")
    async def request_preview(params: PreviewRequest):
        candidate = candidates.get(params.candidate_id)
        if candidate is None:
            raise HTTPException(404, "候选画面不存在，请刷新选片。")
        try:
            key, signature = await asyncio.to_thread(preview_signature, candidate)
        except OSError:
            raise HTTPException(409, "源视频已移动，暂时只能查看缩略图。")
        cache_dir = output / "preview-cache" / key
        if not within(cache_dir, output) or not within(output, root):
            raise HTTPException(400, "预览缓存目录不在项目内。")
        result = await asyncio.to_thread(cached_preview, cache_dir, signature)
        if result is not None:
            return {"id": key, "candidate_id": params.candidate_id, "status": "done", "cached": True, **result}
        existing = preview_jobs.get(key)
        if existing is not None and existing["status"] in {"queued", "running"}:
            return {**existing, "candidate_id": params.candidate_id}
        if sum(job["status"] in {"queued", "running"} for job in preview_jobs.values()) >= 8:
            raise HTTPException(429, "正在载入几张原帧，请稍后再选。")
        job = {"id": key, "candidate_id": params.candidate_id, "status": "queued", "cached": False}
        preview_jobs[key] = job
        for old in list(preview_jobs):
            if len(preview_jobs) <= 100:
                break
            if preview_jobs[old]["status"] in {"done", "failed"}:
                del preview_jobs[old]
        task = asyncio.create_task(run_preview(key, candidate.copy(), signature, cache_dir))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return job

    @app.get("/api/preview-jobs/{job_id}")
    async def get_preview_job(job_id: str):
        if job_id not in preview_jobs:
            raise HTTPException(404, "原帧载入任务不存在。")
        return preview_jobs[job_id]

    async def run_job(job_id: str, candidate: dict, params: ExportRequest, destination: Path):
        job = jobs[job_id]
        job["status"] = "running"
        try:
            await (renderer or render_cli)(candidate, params, destination)
            image_url = register_image(destination / "wallpaper.png")
            original_url = register_image(destination / "original.png")
            if not image_url:
                raise RuntimeError("导出没有产生可读取的壁纸文件。")
            job.update(status="done", preview=image_url, download=image_url + "?download=true", original=original_url)
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
        filename = path.name if download else None
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
