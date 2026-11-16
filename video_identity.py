"""认视频本身：挪文件夹、改文件名、换容器（mp4↔mkv 重新封装）都不变的指纹。

指纹 = 第一路视频流开头 48 个压缩包 + 中段（按 10 秒取整的半程处）24 个压缩包的 sha256。
只读压缩数据、不解码，4K 长片也只要几十毫秒；路径、mtime、容器、音轨、字幕、章节都不参与，
所以挪动、改名、重新封装后仍相同；画面内容一变（重新编码、剪掉一段）就不同。
中段那一截用来区分「开头相同、后面剪过」的版本（例如完整版和纯享版）。
ffmpeg 读不了的文件退回「大小 + 开头 1 MiB 内容」的哈希（同样不看路径和 mtime）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import threading

VIDEO_TYPES = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv"}
HEAD_PACKETS = 48
MIDDLE_PACKETS = 24
RAW_BYTES = 1 << 20


def _packet_hash(path: Path, packets: int, seek: float | None = None) -> str | None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if seek:
        command += ["-ss", f"{seek:.0f}"]
    command += ["-i", str(path), "-map", "0:v:0", "-c", "copy", "-frames:v", str(packets), "-f", "hash", "-hash", "sha256", "-"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = done.stdout.strip()
    return line.removeprefix("SHA256=") if done.returncode == 0 and line.startswith("SHA256=") else None


def _duration(path: Path) -> float | None:
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, timeout=30)
        value = float(done.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def fingerprint(path: Path) -> str:
    """Identity of the video content; raises OSError when the file is unreadable."""
    path = Path(path)
    size = path.stat().st_size
    head = _packet_hash(path, HEAD_PACKETS)
    if head:
        duration = _duration(path) or 0
        # 10 秒取整：mp4/mkv 报的时长差几十毫秒，取整后落在同一处。
        middle_at = math.floor(duration / 2 / 10) * 10
        middle = _packet_hash(path, MIDDLE_PACKETS, middle_at) if middle_at >= 10 else ""
        if middle is not None:
            return "vid1-" + hashlib.sha256(f"{head}|{middle_at}|{middle}".encode()).hexdigest()[:32]
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(RAW_BYTES))
    return "raw1-" + digest.hexdigest()[:32]


class IdentityIndex:
    """path → 指纹，按 (大小, mtime) 记住，存盘以便视频挪走后还知道它原来是谁。"""

    def __init__(self, store: Path, compute=fingerprint):
        self.store = store
        self.compute = compute
        self.lock = threading.Lock()
        self.rows: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self.rows is None:
            try:
                data = json.loads(self.store.read_text(encoding="utf-8"))
                rows = data.get("items") if isinstance(data, dict) else None
                self.rows = {k: v for k, v in rows.items() if isinstance(v, dict)} if isinstance(rows, dict) else {}
            except (OSError, ValueError):
                self.rows = {}
        return self.rows

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        temp = self.store.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "items": self.rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(temp, self.store)

    def of(self, path: Path) -> str:
        path = Path(path)
        stat = path.stat()
        key = str(path)
        with self.lock:
            row = self._load().get(key)
            if row and row.get("size") == stat.st_size and row.get("mtime_ns") == stat.st_mtime_ns:
                return row["identity"]
        identity = self.compute(path)
        with self.lock:
            self._load()[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "identity": identity}
            self._save()
        return identity

    def known(self, path: str) -> str | None:
        with self.lock:
            row = self._load().get(str(path))
        return row.get("identity") if row else None


def search_root(missing: Path, levels: int = 3) -> Path | None:
    """视频挪走后在哪找：原位置最近的仍存在的上级目录（最多往上 3 层，不会退到根或家目录）。"""
    home = Path.home()
    folder = Path(missing).parent
    for _ in range(levels):
        if folder.is_dir():
            return None if folder in {Path(folder.anchor), home} else folder
        folder = folder.parent
    return None


def scan_videos(root: Path, limit: int = 400, depth: int = 4) -> list[Path]:
    found: list[Path] = []
    base = len(root.parts)
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if len(Path(folder).parts) - base < depth else []
        for name in sorted(files):
            if not name.startswith(".") and Path(name).suffix.lower() in VIDEO_TYPES:
                found.append(Path(folder) / name)
                if len(found) >= limit:
                    return found
    return found
