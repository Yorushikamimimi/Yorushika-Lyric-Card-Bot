"""主页放映接口：只读、只放收藏、只用已缓存的原帧。"""
import json
import unittest

from PIL import Image

import test_studio


class ShowcaseTests(unittest.TestCase):
    setUp = test_studio.StudioTests.setUp
    tearDown = test_studio.StudioTests.tearDown
    write_manifest = test_studio.StudioTests.write_manifest
    candidate = test_studio.StudioTests.candidate
    wait_preview = test_studio.StudioTests.wait_preview

    def favorite(self, candidate_id):
        response = self.client.post("/api/favorites", json={"candidate_id": candidate_id, "favorite": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_only_favorites_are_listed(self):
        self.assertEqual(self.client.get("/api/showcase").json(), {"items": [], "total": 0, "ready": 0})
        candidate = self.candidate()
        self.favorite(candidate["id"])
        body = self.client.get("/api/showcase").json()
        self.assertEqual((body["total"], body["ready"]), (1, 0))
        item = body["items"][0]
        self.assertEqual(item["title"], "一场雨")
        self.assertIsNone(item["wide"])
        self.assertNotIn("source", item)
        self.assertAlmostEqual(item["brightness"], 0.2, delta=0.05)
        self.assertEqual(self.client.get(f"/showcase/{candidate['id']}.jpg").status_code, 404)
        self.assertEqual(self.preview_calls, [])

    def test_cached_original_is_served_as_2560_jpeg(self):
        candidate = self.candidate()
        self.favorite(candidate["id"])
        job = self.client.post("/api/previews", json={"candidate_id": candidate["id"]}, headers=self.headers).json()
        self.wait_preview(job["id"])
        # 夹具原帧只有 1920 宽：不放大。再造一张 4090 宽的确认会缩到 2560。
        cache = next(self.output.glob("preview-cache/*/cache.json")).parent
        original = next(cache.glob("*/original.png"))
        Image.new("RGB", (4090, 1890), "green").save(original)
        item = self.client.get("/api/showcase").json()["items"][0]
        self.assertEqual(item["wide"], f"/showcase/{candidate['id']}.jpg")
        response = self.client.get(item["wide"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        target = next(self.output.glob("preview-cache/showcase/*.jpg"))
        with Image.open(target) as picture:
            self.assertEqual(picture.size, (2560, 1183))
        self.assertEqual(len(self.preview_calls), 1)

    def test_lyric_comes_from_local_file_when_present(self):
        candidate = self.candidate()
        self.favorite(candidate["id"])
        self.assertIsNone(self.client.get("/api/showcase").json()["items"][0]["lyric"])
        folder = self.output / "lyrics"
        folder.mkdir()
        (folder / "favorite-lyrics.json").write_text('{"%s": {"song": "一场雨", "line": " 雨の匂い "}}' % candidate["id"], encoding="utf-8")
        self.assertEqual(self.client.get("/api/showcase").json()["items"][0]["lyric"], "雨の匂い")
        (folder / "favorite-lyrics.json").write_text("not json", encoding="utf-8")
        self.assertIsNone(self.client.get("/api/showcase").json()["items"][0]["lyric"])

    def test_frames_without_a_line_fall_back_to_the_songs_chorus(self):
        candidate = self.candidate()
        self.favorite(candidate["id"])
        folder = self.output / "lyrics"
        folder.mkdir()
        song = "A1\nA2\n\nサビの一行目です\nサビ二行目\n\nB1\nB2\n\nサビの一行目です\nサビ二行目\n"
        (folder / "full-lyrics.json").write_text(json.dumps({"songs": {"一场雨": song}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.client.get("/api/showcase").json()["items"][0]["lyric"], "サビの一行目です")
        # 画面原词优先于副歌
        (folder / "favorite-lyrics.json").write_text(json.dumps({candidate["id"]: {"line": "画面里那句"}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.client.get("/api/showcase").json()["items"][0]["lyric"], "画面里那句")

    def test_chorus_falls_back_to_most_repeated_line_or_nothing(self):
        candidate = self.candidate()
        self.favorite(candidate["id"])
        folder = self.output / "lyrics"
        folder.mkdir()
        target = folder / "full-lyrics.json"
        target.write_text(json.dumps({"songs": {"一场雨": "短\n繰り返す言葉\nX\n\n短\n繰り返す言葉\nY\n"}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.client.get("/api/showcase").json()["items"][0]["lyric"], "繰り返す言葉\u3000X")
        target.write_text(json.dumps({"songs": {"一场雨": "一回だけの歌詞\n"}}, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(self.client.get("/api/showcase").json()["items"][0]["lyric"])

    def test_rejects_unknown_and_non_favorite_names(self):
        candidate = self.candidate()
        for name in ("../favorites.json", "x.jpg", f"{candidate['id']}.png", f"{candidate['id']}.jpg"):
            self.assertEqual(self.client.get(f"/showcase/{name}").status_code, 404, name)
        self.assertEqual(self.client.post("/api/showcase", headers=self.headers, json={}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
