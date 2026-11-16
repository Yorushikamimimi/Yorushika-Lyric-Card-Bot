"""原帧缓存认视频本身：挪动、改名、换容器后继续命中；旧缓存迁移；挪走的视频自动找回。"""
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from PIL import Image

import live_wallpaper
from studio import opaque
import test_studio
from video_identity import fingerprint, search_root

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_video(path: Path, pattern: str = "testsrc2", seconds: int = 4, codec: str = "libx264") -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                    f"{pattern}=size=320x180:rate=12:duration={seconds}", "-c:v", codec, "-pix_fmt", "yuv420p", str(path)], check=True)


def remux(source: Path, target: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
                    "-map", "0", "-c", "copy", str(target)], check=True)


class StudioCase(unittest.TestCase):
    setUp = test_studio.StudioTests.setUp
    tearDown = test_studio.StudioTests.tearDown
    write_manifest = test_studio.StudioTests.write_manifest
    candidate = test_studio.StudioTests.candidate
    wait_preview = test_studio.StudioTests.wait_preview

    def favorite(self, candidate_id):
        response = self.client.post("/api/favorites", json={"candidate_id": candidate_id, "favorite": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def cache_original(self):
        candidate = self.candidate()
        self.favorite(candidate["id"])
        job = self.client.post("/api/previews", json={"candidate_id": candidate["id"]}, headers=self.headers).json()
        self.assertEqual(self.wait_preview(job["id"])["status"], "done")
        self.assertEqual(len(self.preview_calls), 1)
        return candidate

    def wait_ready(self, attempts=250):
        for _ in range(attempts):
            body = self.client.get("/api/showcase").json()
            if body["items"] and body["ready"] == body["total"]:
                return body
            time.sleep(.02)
        self.fail("原帧没有重新命中")

    def restart(self):
        """模拟重启工房：新进程读同一个 output/，原帧仍用会记数的假渲染器。"""
        async def preview_renderer(candidate, destination):
            self.preview_calls.append((candidate, destination))
            destination.mkdir(parents=True)
            Image.new("RGB", (1920, 1080), "green").save(destination / "original.png")

        self.client.__exit__(None, None, None)
        self.client = test_studio.TestClient(test_studio.create_app(self.root, None, preview_renderer), base_url="http://127.0.0.1:8765")
        self.client.__enter__()


class MovedVideoTests(StudioCase):
    def test_renamed_into_another_folder_still_hits_without_touching_the_manifest(self):
        self.cache_original()
        moved = self.root / "整理后" / "2024 前世 改名.mp4"
        moved.parent.mkdir()
        os.rename(self.source, moved)
        os.utime(moved, (1, 1))  # 挪动后 mtime 变了也不算换片
        # 清单仍写旧路径：第一次请求排队找回，后台按记下的指纹在原位置附近找到它。
        body = self.wait_ready()
        self.assertEqual(body["ready"], 1)
        self.assertEqual(len(self.preview_calls), 1)
        moves = json.loads((self.output / "preview-cache" / "source-moves.json").read_text())["items"]
        self.assertEqual(moves[str(self.source.resolve())]["path"], str(moved.resolve()))
        self.assertEqual(moves[str(self.source.resolve())]["how"], "identity")
        self.assertTrue(self.candidate()["source_available"])
        self.assertEqual(self.client.get(body["items"][0]["wide"]).status_code, 200)
        self.assertEqual(json.loads(self.samples.joinpath("manifest.json").read_text()), self.manifest)

    def test_manifest_pointing_at_the_new_name_hits_immediately(self):
        self.cache_original()
        renamed = self.root / "renamed.mkv"
        os.rename(self.source, renamed)
        for row in [*self.manifest["videos"], *self.manifest["candidates"]]:
            row["source"] = str(renamed)
        self.write_manifest()
        self.assertEqual(self.client.get("/api/showcase").json()["ready"], 1)
        self.assertEqual(len(self.preview_calls), 1)

    def test_changed_content_at_the_same_path_is_a_new_video(self):
        self.cache_original()
        self.source.write_bytes(b"re-encoded fixture")
        self.assertEqual(self.client.get("/api/showcase").json()["ready"], 0)

    def test_unfound_video_stays_on_the_thumbnail(self):
        self.cache_original()
        self.source.unlink()
        for _ in range(10):
            body = self.client.get("/api/showcase").json()
            time.sleep(.02)
        self.assertEqual((body["total"], body["ready"]), (1, 0))
        self.assertFalse((self.output / "preview-cache" / "source-moves.json").exists())

    def test_old_path_and_mtime_caches_are_migrated_not_reextracted(self):
        candidate = self.candidate()
        self.favorite(candidate["id"])
        stat = self.source.stat()
        old = {"version": 1, "source": str(self.source.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
               "timestamp": 30.25, "source_metadata": {}, "pipeline_sha256": "old"}
        folder = self.output / "preview-cache" / opaque(json.dumps(old, sort_keys=True))
        (folder / "frame").mkdir(parents=True)
        Image.new("RGB", (1920, 1080), "blue").save(folder / "frame" / "original.png")
        (folder / "cache.json").write_text(json.dumps({"signature": old, "image": "frame/original.png"}))
        # 同一路径上被替换过的视频（大小或 mtime 不符）不迁移。
        stale = {**old, "timestamp": 60.0, "size": 1}
        stale_folder = self.output / "preview-cache" / "stale"
        (stale_folder / "frame").mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(stale_folder / "frame" / "original.png")
        (stale_folder / "cache.json").write_text(json.dumps({"signature": stale, "image": "frame/original.png"}))
        self.restart()
        body = self.wait_ready()
        self.assertEqual(self.preview_calls, [])
        self.assertFalse(folder.exists())
        self.assertTrue(stale_folder.exists())
        migrated = next(p for p in (self.output / "preview-cache").glob("*/cache.json") if p.parent != stale_folder)
        record = json.loads(migrated.read_text())
        self.assertEqual(record["signature"]["version"], 2)
        self.assertEqual(record["migrated_from"]["key"], folder.name)
        self.assertEqual(self.client.get(body["items"][0]["wide"]).status_code, 200)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe required")
class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_remux_and_rename_keep_the_fingerprint_new_content_does_not(self):
        original = self.root / "live.mp4"
        make_video(original, seconds=25)
        remuxed = self.root / "其他" / "改名 LIVE.mkv"
        remuxed.parent.mkdir()
        remux(original, remuxed)
        self.assertNotEqual(original.stat().st_size, remuxed.stat().st_size)
        self.assertTrue(fingerprint(original).startswith("vid1-"))
        self.assertEqual(fingerprint(original), fingerprint(remuxed))
        other = self.root / "other.mp4"
        make_video(other, "smptebars", seconds=25)
        self.assertNotEqual(fingerprint(original), fingerprint(other))
        # 开头相同、后段剪过（如纯享版）也不算同一个视频。
        cut = self.root / "cut.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(original), "-i", str(other),
                        "-filter_complex", "[0:v]trim=0:8,setpts=PTS-STARTPTS[a];[1:v]trim=0:17,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1[v]",
                        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cut)], check=True)
        self.assertNotEqual(fingerprint(original), fingerprint(cut))

    def test_unreadable_files_fall_back_to_content_hash(self):
        first, second = self.root / "a.mp4", self.root / "b.mp4"
        first.write_bytes(b"not a video")
        second.write_bytes(b"not a video")
        self.assertTrue(fingerprint(first).startswith("raw1-"))
        self.assertEqual(fingerprint(first), fingerprint(second))

    def test_search_never_climbs_to_home_or_root(self):
        self.assertIsNone(search_root(Path.home() / "gone" / "x.mp4", levels=1))
        self.assertEqual(search_root(self.root / "gone" / "deeper" / "x.mp4"), self.root)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe required")
class RemuxedRelinkTests(StudioCase):
    """旧缓存没记过指纹、视频又被重新封装成别的容器：拿旧原帧比画面找回。"""

    def test_old_frames_find_the_remuxed_video_and_move_over(self):
        old_path = self.root / "2024 前世" / "其他" / "live.mp4"
        old_path.parent.mkdir(parents=True)
        make_video(old_path, seconds=6)
        frames = []
        for seconds in (1.5, 4.0):
            folder = self.output / "preview-cache" / f"old{int(seconds * 10)}"
            (folder / "f").mkdir(parents=True)
            live_wallpaper.extract_frame(old_path, seconds, folder / "f" / "original.png", [])
            stat = old_path.stat()
            signature = {"version": 1, "source": str(old_path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                         "timestamp": seconds, "source_metadata": {}, "pipeline_sha256": "old"}
            (folder / "cache.json").write_text(json.dumps({"signature": signature, "image": "f/original.png"}))
            frames.append(folder)
        decoy = self.root / "a 别的场次.mkv"
        make_video(decoy, "smptebars", seconds=6)
        new_path = self.root / "视频 IINA" / "2024 前世.mkv"
        new_path.parent.mkdir()
        remux(old_path, new_path)
        shutil.rmtree(old_path.parent.parent)
        self.manifest["videos"][0]["source"] = str(old_path)
        self.manifest["candidates"][0].update(source=str(old_path), timestamp=4.0)
        self.write_manifest()
        self.restart()
        candidate = self.candidate()
        self.assertFalse(candidate["source_available"])
        self.favorite(candidate["id"])
        body = self.wait_ready(attempts=500)
        self.assertEqual(self.preview_calls, [])
        moves = json.loads((self.output / "preview-cache" / "source-moves.json").read_text())["items"]
        self.assertEqual(moves[str(old_path.resolve())], {**moves[str(old_path.resolve())], "path": str(new_path.resolve()), "how": "frames"})
        self.assertFalse(any(folder.exists() for folder in frames))
        self.assertFalse((self.output / "preview-cache" / "relink-tmp").exists() and any((self.output / "preview-cache" / "relink-tmp").iterdir()))
        self.assertEqual(self.client.get(body["items"][0]["wide"]).status_code, 200)


class FramePipelineVersionTests(unittest.TestCase):
    def test_frame_functions_are_pinned_to_the_pipeline_version(self):
        # 改到这些函数时：画面会变就把 live_wallpaper.FRAME_PIPELINE_VERSION 加一（原帧缓存整批作废），
        # 画面不变（注释、报错文字等）就只更新下面的哈希。改 live_wallpaper.py 其他部分不影响缓存。
        names = ["extract_frame", "color_plan", "make_hdr_lut", "hdr_to_srgb", "hdr_kind", "hable", "srgb", "filter_path"]
        source = "\n".join(inspect.getsource(getattr(live_wallpaper, name)) for name in names)
        self.assertEqual(live_wallpaper.FRAME_PIPELINE_VERSION, 1)
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest()[:16], "f7dabcf151a4506c")


if __name__ == "__main__":
    unittest.main()
