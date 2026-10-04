import asyncio
import json
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from studio import create_app


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.samples = self.output / "samples"
        (self.samples / "previews").mkdir(parents=True)
        self.source = self.root / "concert.mp4"
        self.source.write_bytes(b"fixture")
        Image.new("RGB", (160, 90), "#30382a").save(self.samples / "previews" / "frame.jpg")
        self.manifest = {"videos": [{"id": "V01", "source": str(self.source), "label": "Live 1"}], "candidates": [{"id": "V01-C001", "video_id": "V01", "source": str(self.source), "timestamp": 30.25, "timecode": "00:00:30.250", "preview": "previews/frame.jpg"}]}
        self.write_manifest()
        (self.output / "selection.json").write_text(json.dumps({"items": [{"candidate_id": "V01-C001", "title": "一场雨", "reviewed": True, "note": "现场大屏"}]}))
        self.calls = []
        self.preview_calls = []
        self.active_previews = 0
        self.max_active_previews = 0

        async def renderer(candidate, params, destination):
            self.calls.append((candidate, params, destination))
            destination.mkdir(parents=True, exist_ok=False)
            await asyncio.sleep(.02)
            Image.new("RGB", (params.width, params.height), "#30382a").save(destination / "wallpaper.png")
            Image.new("RGB", (160, 90)).save(destination / "original.png")

        async def preview_renderer(candidate, destination):
            self.preview_calls.append((candidate, destination))
            self.active_previews += 1
            self.max_active_previews = max(self.max_active_previews, self.active_previews)
            await asyncio.sleep(.04)
            destination.mkdir(parents=True, exist_ok=False)
            Image.new("RGB", (1920, 1080), "green").save(destination / "original.png")
            self.active_previews -= 1

        self.client = TestClient(create_app(self.root, renderer, preview_renderer), base_url="http://127.0.0.1:8765")
        self.client.__enter__()
        self.headers = {"Origin": "http://127.0.0.1:8765"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def write_manifest(self):
        (self.samples / "manifest.json").write_text(json.dumps(self.manifest))

    def candidate(self):
        return self.client.get("/api/library").json()["candidates"][0]

    def test_library_maps_known_assets_and_selection(self):
        candidate = self.candidate()
        self.assertEqual(candidate["title"], "一场雨")
        self.assertTrue(candidate["reviewed"])
        self.assertNotIn("source", candidate)
        self.assertEqual(len(candidate["id"]), 24)
        self.assertEqual(self.client.get(candidate["preview"]).status_code, 200)
        self.assertEqual(self.client.get("/media/unknown").status_code, 404)

    def test_favorites_persist_are_manifest_scoped_and_do_not_change_selection(self):
        candidate = self.candidate()
        self.assertFalse(candidate["favorite"])
        response = self.client.post("/api/favorites", json={"candidate_id": candidate["id"], "favorite": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/library").json()["candidates"][0]["favorite"])
        stored = json.loads((self.output / "favorites.json").read_text())
        self.assertEqual(stored["items"], [{"manifest": "samples/manifest.json", "candidate_id": "V01-C001"}])
        self.assertTrue(self.client.get("/api/library").json()["candidates"][0]["selected"])
        self.client.post("/api/favorites", json={"candidate_id": candidate["id"], "favorite": False}, headers=self.headers)
        self.assertFalse(self.client.get("/api/library").json()["candidates"][0]["favorite"])

    def test_favorites_do_not_cross_contaminate_duplicate_candidate_ids(self):
        second_batch = self.output / "later-scan"
        (second_batch / "previews").mkdir(parents=True)
        Image.new("RGB", (160, 90), "blue").save(second_batch / "previews" / "frame.jpg")
        other_source = self.root / "different-concert.mp4"
        other_source.write_bytes(b"different fixture")
        other_manifest = json.loads(json.dumps(self.manifest))
        other_manifest["videos"][0]["source"] = str(other_source)
        other_manifest["candidates"][0]["source"] = str(other_source)
        (second_batch / "manifest.json").write_text(json.dumps(other_manifest))
        rows = self.client.get("/api/library").json()["candidates"]
        self.assertEqual(len(rows), 2)
        self.client.post("/api/favorites", json={"candidate_id": rows[0]["id"], "favorite": True}, headers=self.headers)
        rows = self.client.get("/api/library").json()["candidates"]
        self.assertEqual(sum(row["favorite"] for row in rows), 1)
        self.assertTrue(next(row for row in rows if row["manifest"] == rows[0]["manifest"])["favorite"])

    def test_favorites_reject_unknown_candidate_and_empty_filter_data(self):
        self.assertEqual(self.client.post("/api/favorites", json={"candidate_id": "missing", "favorite": True}, headers=self.headers).status_code, 404)
        self.assertFalse((self.output / "favorites.json").exists())
        self.assertFalse(any(row["favorite"] for row in self.client.get("/api/library").json()["candidates"]))

    def test_invalid_favorites_file_is_preserved_and_library_stays_available(self):
        favorites = self.output / "favorites.json"
        for raw in ["not json", json.dumps({"version": 1, "items": None}), json.dumps({"version": 1, "items": [{"manifest": "x"}]})]:
            favorites.write_text(raw)
            library = self.client.get("/api/library")
            self.assertEqual(library.status_code, 200)
            self.assertIn("candidates", library.json())
            before = favorites.read_bytes()
            response = self.client.post("/api/favorites", json={"candidate_id": self.candidate()["id"], "favorite": True}, headers=self.headers)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(favorites.read_bytes(), before)

    def test_favorites_write_failure_preserves_existing_file(self):
        favorites = self.output / "favorites.json"
        favorites.write_text(json.dumps({"version": 1, "items": []}))
        original = favorites.read_bytes()
        favorites.chmod(0o400)
        try:
            with mock.patch("studio.Path.write_text", side_effect=OSError("read-only")):
                response = self.client.post("/api/favorites", json={"candidate_id": self.candidate()["id"], "favorite": True}, headers=self.headers)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(favorites.read_bytes(), original)
        finally:
            favorites.chmod(0o600)

    def test_duplicate_short_ids_require_manifest_scoped_selection(self):
        second_batch = self.output / "later-scan"
        (second_batch / "previews").mkdir(parents=True)
        Image.new("RGB", (160, 90), "blue").save(second_batch / "previews" / "frame.jpg")
        other_source = self.root / "different-concert.mp4"
        other_source.write_bytes(b"different fixture")
        other_manifest = json.loads(json.dumps(self.manifest))
        other_manifest["videos"][0].update(source=str(other_source), label="Different Live")
        other_manifest["candidates"][0].update(source=str(other_source), timestamp=80)
        (second_batch / "manifest.json").write_text(json.dumps(other_manifest))
        rows = self.client.get("/api/library").json()["candidates"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertTrue(all(not row["selected"] and not row["reviewed"] for row in rows))
        (self.output / "selection.json").write_text(json.dumps({"items": [{"manifest": "samples/manifest.json", "candidate_id": "V01-C001", "title": "只推荐旧场景", "reviewed": True}]}))
        rows = self.client.get("/api/library").json()["candidates"]
        by_manifest = {row["manifest"]: row for row in rows}
        self.assertTrue(by_manifest["samples/manifest.json"]["selected"])
        self.assertEqual(by_manifest["samples/manifest.json"]["title"], "只推荐旧场景")
        self.assertFalse(by_manifest["later-scan/manifest.json"]["selected"])
        self.assertFalse(by_manifest["later-scan/manifest.json"]["reviewed"])

    def test_selection_follows_curated_file_order(self):
        earlier = {**self.manifest["candidates"][0], "id": "V01-C002", "timestamp": 5}
        self.manifest["candidates"].append(earlier)
        self.write_manifest()
        (self.output / "selection.json").write_text(json.dumps({"items": [{"manifest": "samples/manifest.json", "candidate_id": "V01-C001", "title": "01"}, {"manifest": "samples/manifest.json", "candidate_id": "V01-C002", "title": "02"}]}))
        rows = self.client.get("/api/library").json()["candidates"]
        self.assertEqual([row["title"] for row in rows], ["01", "02"])

    def test_async_export_download_and_exact_parameters(self):
        response = self.client.post("/api/exports", json={"candidate_id": self.candidate()["id"], "width": 512, "height": 768, "fit": "cover", "focus_x": .7, "exposure": .3}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["id"]
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "done":
                break
            time.sleep(.02)
        self.assertEqual(job["status"], "done")
        download = self.client.get(job["download"])
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(self.calls[0][1].focus_x, .7)
        self.assertEqual(self.calls[0][0]["timestamp"], 30.25)
        self.assertTrue(self.calls[0][2].is_relative_to(self.output.resolve()))

    def test_cross_origin_missing_origin_and_untrusted_host_rejected(self):
        payload = {"candidate_id": self.candidate()["id"]}
        self.assertEqual(self.client.post("/api/exports", json=payload).status_code, 403)
        self.assertEqual(self.client.post("/api/exports", json=payload, headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.get("/api/library", headers={"Host": "evil.test"}).status_code, 403)

    def test_export_rejects_arbitrary_paths_and_unbounded_parameters(self):
        candidate_id = self.candidate()["id"]
        for extra in [{"source": "/tmp/any.mp4"}, {"output": "/tmp"}, {"width": 8192, "height": 8192}, {"focus_x": 2}, {"sharpen": 2}, {"fit": "stretch"}, {"width": 0}]:
            response = self.client.post("/api/exports", json={"candidate_id": candidate_id, **extra}, headers=self.headers)
            self.assertEqual(response.status_code, 422, extra)
        self.assertEqual(self.client.post("/api/exports", json={"candidate_id": "../../secret"}, headers=self.headers).status_code, 404)
        self.assertEqual(self.calls, [])

    def test_moved_source_keeps_preview_but_cannot_render(self):
        self.source.unlink()
        candidate = self.candidate()
        self.assertFalse(candidate["source_available"])
        self.assertEqual(self.client.post("/api/exports", json={"candidate_id": candidate["id"]}, headers=self.headers).status_code, 409)

    def test_preview_traversal_and_symlink_escape_are_not_served(self):
        outside = self.root / "outside.png"
        Image.new("RGB", (10, 10)).save(outside)
        self.manifest["candidates"][0]["preview"] = str(outside)
        self.write_manifest()
        self.assertEqual(self.client.get("/api/library").json()["candidates"], [])
        (self.samples / "previews" / "escape.png").symlink_to(outside)
        self.manifest["candidates"][0]["preview"] = "previews/escape.png"
        self.write_manifest()
        self.assertEqual(self.client.get("/api/library").json()["candidates"], [])

    def test_avatar_assets_and_static_page(self):
        folder = self.output / "avatars"
        folder.mkdir()
        Image.new("RGB", (64, 64)).save(folder / "portrait.png")
        folder.joinpath("secret.txt").write_text("not a public image")
        avatars = self.client.get("/api/library").json()["avatars"]
        self.assertEqual(len(avatars), 1)
        self.assertEqual(self.client.get(avatars[0]["preview"]).status_code, 200)
        for url in ["/", "/static/style.css", "/static/app.js"]:
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200)
            self.assertIn("frame-ancestors 'none'", result.headers["content-security-policy"])

    def test_avatar_sizes_metadata_and_download_boundaries(self):
        folder = self.output / "avatars"
        folder.mkdir()
        Image.new("RGBA", (1254, 1254), (50, 60, 70, 120)).save(folder / "portrait.png")
        (folder / "portrait.json").write_text(json.dumps({"title": "小小的人", "width": 1024, "reference_url": "https://yorushika.com/", "direction": "粉丝二创"}))
        avatar = self.client.get("/api/library").json()["avatars"][0]
        self.assertEqual(avatar["width"], 1254)
        self.assertEqual(avatar["title"], "小小的人")
        self.assertEqual(avatar["reference_url"], "https://yorushika.com/")
        for size in [512, 1024, "original"]:
            response = self.client.post("/api/avatar-exports", json={"avatar_id": avatar["id"], "size": size}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            download = self.client.get(response.json()["download"])
            expected = 1254 if size == "original" else size
            with Image.open(io.BytesIO(download.content)) as picture:
                self.assertEqual(picture.size, (expected, expected))
                self.assertEqual(picture.mode, "RGBA")
            self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(len(self.client.get("/api/library").json()["avatars"]), 1)
        self.assertEqual(self.client.post("/api/avatar-exports", json={"avatar_id": avatar["id"], "size": 9000}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/api/avatar-exports", json={"avatar_id": "../../secret"}, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/api/avatar-exports", json={"avatar_id": avatar["id"]}).status_code, 403)

    def wait_preview(self, job_id):
        for _ in range(80):
            job = self.client.get(f"/api/preview-jobs/{job_id}").json()
            if job["status"] in {"done", "failed"}:
                return job
            time.sleep(.02)
        self.fail("Preview did not finish")

    def test_native_preview_cache_deduplication_and_source_invalidation(self):
        candidate = self.candidate()
        payload = {"candidate_id": candidate["id"]}
        first = self.client.post("/api/previews", json=payload, headers=self.headers).json()
        duplicate = self.client.post("/api/previews", json=payload, headers=self.headers).json()
        self.assertEqual(first["id"], duplicate["id"])
        result = self.wait_preview(first["id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual((result["width"], result["height"]), (1920, 1080))
        self.assertNotEqual(result["preview"], candidate["thumbnail"])
        cached = self.client.post("/api/previews", json=payload, headers=self.headers).json()
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.preview_calls), 1)
        self.source.write_bytes(b"changed fixture content")
        changed = self.client.post("/api/previews", json=payload, headers=self.headers).json()
        self.assertNotEqual(first["id"], changed["id"])
        self.assertEqual(self.wait_preview(changed["id"])["status"], "done")
        self.assertEqual(len(self.preview_calls), 2)

    def test_preview_queue_is_bounded_to_one_decoder_and_validates_requests(self):
        self.manifest["candidates"].append({**self.manifest["candidates"][0], "id": "V01-C002", "timestamp": 60})
        self.write_manifest()
        rows = self.client.get("/api/library").json()["candidates"]
        started = [self.client.post("/api/previews", json={"candidate_id": row["id"]}, headers=self.headers).json() for row in rows]
        for job in started:
            self.assertEqual(self.wait_preview(job["id"])["status"], "done")
        self.assertEqual(self.max_active_previews, 1)
        payload = {"candidate_id": rows[0]["id"]}
        self.assertEqual(self.client.post("/api/previews", json=payload).status_code, 403)
        self.assertEqual(self.client.post("/api/previews", json={**payload, "source": "/tmp/arbitrary.mp4"}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/api/previews", json={"candidate_id": "../../secret"}, headers=self.headers).status_code, 404)

    def test_illustration_wallpapers_cover_without_stretching_or_padding(self):
        folder = self.output / "illustrations"
        folder.mkdir()
        picture = Image.new("RGB", (512, 512), "black")
        ImageDraw.Draw(picture).ellipse((192, 192, 320, 320), fill="white")
        picture.save(folder / "round-shape.png")
        (folder / "round-shape.json").write_text(json.dumps({"uses": ["phone-wallpaper", "desktop-wallpaper"], "title": "插画壁纸"}))
        artwork = self.client.get("/api/library").json()["avatars"][0]
        self.assertEqual(artwork["uses"], ["phone-wallpaper", "desktop-wallpaper"])
        for size, expected in [("1170x2532", (1170, 2532)), ("2560x1440", (2560, 1440))]:
            response = self.client.post("/api/avatar-exports", json={"avatar_id": artwork["id"], "size": size}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["upscaled"])
            raw = self.client.get(response.json()["download"]).content
            with Image.open(io.BytesIO(raw)) as result:
                self.assertEqual(result.size, expected)
                self.assertEqual(result.getchannel("A").getextrema(), (255, 255))
                box = result.convert("L").point(lambda value: 255 if value > 200 else 0).getbbox()
                self.assertLessEqual(abs((box[2] - box[0]) - (box[3] - box[1])), 2)

    def test_portrait_wallpaper_defaults_to_cover_but_explicit_contain_is_kept(self):
        from studio import ExportRequest
        self.assertEqual(ExportRequest(candidate_id="x", width=1170, height=2532).fit, "cover")
        self.assertEqual(ExportRequest(candidate_id="x", width=1170, height=2532, fit="contain").fit, "contain")

    def test_mv_source_kind_and_square_screenshot_default(self):
        from studio import ExportRequest
        self.assertEqual(self.candidate()["source_kind"], "live")
        self.manifest["source_kind"] = "mv"
        self.write_manifest()
        self.assertEqual(self.candidate()["source_kind"], "mv")
        self.assertEqual(ExportRequest(candidate_id="x", width=1024, height=1024).fit, "cover")


if __name__ == "__main__":
    unittest.main()
