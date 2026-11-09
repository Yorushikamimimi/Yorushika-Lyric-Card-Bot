"""主页图池的选镜头规则：收藏所在镜头跳过、「不要」只降分、片尾不进池、每镜头只留清楚的一张。"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import auto_pool


def row(seconds, shot, quality=.5, similar=False):
    return {"seconds": seconds, "shot_start": shot[0], "shot_end": shot[1], "similar_to_prior": similar,
            "quality": {"quality": quality}, "analysis_file": f"/f/{seconds}.png", "chapter": "歌"}


class ShotCandidateTests(unittest.TestCase):
    def scan(self, rows, duration=1000):
        folder = Path(self.temp.name)
        (folder / "scan-candidates.json").write_text(json.dumps({"source": {"path": "/v.mp4"}, "duration": duration, "candidates": rows}))
        return folder

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_rules(self):
        rows = [row(10, (8, 14)), row(12, (8, 14), quality=.9),       # 同镜头留更清楚的那张
                row(30, (28, 34)),                                     # 镜头里有收藏 → 跳过
                row(50, (48, 54)),                                     # 离「不要」2 秒 → 降分标记
                row(70, (68, 74), quality=.05), row(90, (88, 94), similar=True),
                row(950, (948, 954))]                                  # 片尾 2 分钟内
        taken = {"/v.mp4": [(31.0, 1.0), (52.0, 0.0)]}
        got = auto_pool.shot_candidates(self.scan(rows), "某场", taken)
        self.assertEqual([(c["seconds"], c["penalty"]) for c in got], [(12, False), (50, True)])
        self.assertEqual(got[0]["id"], auto_pool.pick_id("/v.mp4", 12))
        self.assertEqual(len(got[0]["id"]), 24)

    def test_film_detects_pillarbox_only(self):
        folder = Path(self.temp.name)
        frame = np.zeros((92, 200), dtype=np.uint8)
        frame[:, 40:160] = 120
        Image.fromarray(frame).save(folder / "film.png")
        Image.fromarray(np.full((92, 200), 120, dtype=np.uint8)).save(folder / "full.png")
        self.assertTrue(auto_pool.is_film(str(folder / "film.png")))
        self.assertFalse(auto_pool.is_film(str(folder / "full.png")))


if __name__ == "__main__":
    unittest.main()
