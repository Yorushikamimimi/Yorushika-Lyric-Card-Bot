"""Small synthetic-video checks; never depend on the user's Live footage."""
import argparse
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import live_wallpaper as live


class ParameterTests(unittest.TestCase):
    def test_time_and_dimension_validation(self):
        self.assertEqual(live.timestamp("01:02:03.25"), 3723.25)
        self.assertEqual(live.timecode(3723.25), "01:02:03.250")
        self.assertEqual(live.size_arg("1440x3200"), (1440, 3200))
        for invalid in ("nan", "inf", "-1", "00:61:00", "1:2:3:4"):
            with self.assertRaises(argparse.ArgumentTypeError):
                live.timestamp(invalid)
        for invalid in ("0x100", "3840", "99999x10", "10000x10000"):
            with self.assertRaises(argparse.ArgumentTypeError):
                live.size_arg(invalid)
        for invalid in ("nan", "2", "-0.1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                live.bounded_number(0, 1)(invalid)

    def test_chapters_version_only_replaces_its_matching_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Live.mp4", "Live [Chapters].mp4", "different.mp4", "note.txt"):
                (root / name).touch()
            (root / "01 - video alias.mp4").symlink_to(root / "Live [Chapters].mp4")
            selected, skipped = live.discover_videos(root)
            self.assertEqual({p.name for p in selected}, {"Live [Chapters].mp4", "different.mp4"})
            self.assertEqual(len(skipped), 2)
            self.assertTrue(any(s["source"].endswith("Live.mp4") for s in skipped))

    def test_sampling_stays_inside_chapters_and_rejects_out_of_range(self):
        meta = {"duration": 100, "chapters": [{"start": 0, "end": 20}, {"start": 20, "end": 100}]}
        self.assertEqual(live.choose_timestamps(meta, 1, 12), [10, 60])
        self.assertEqual(live.choose_timestamps(meta, 2, 12, [40, 10, 40]), [10, 40])
        with self.assertRaises(live.WallpaperError):
            live.choose_timestamps(meta, 1, 12, [100])

    def test_color_transform_has_neutral_black_white_and_monotonic_gray(self):
        for kind in ("hlg", "pq"):
            black = live.hdr_to_srgb(0, 0, 0, kind, 1000)
            self.assertTrue(all(abs(channel) < 1e-7 for channel in black))
            white = live.hdr_to_srgb(1, 1, 1, kind, 1000)
            self.assertTrue(all(channel > .999 for channel in white))
            grays = [live.hdr_to_srgb(v, v, v, kind, 1000) for v in (.1, .3, .5, .7, .9)]
            self.assertEqual(sorted(x[0] for x in grays), [x[0] for x in grays])
            for rgb in grays:
                self.assertLess(max(rgb) - min(rgb), 1e-5)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe required")
class SyntheticIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=6:duration=2", "-vf", "setsar=4/3",
                        "-c:v", "mpeg4", "-q:v", "2", str(self.source)], check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return live.main(list(arguments))

    def test_catalog_sample_render_and_read_only_source(self):
        before = (self.source.stat().st_size, self.source.stat().st_mtime_ns)
        catalog_output = self.root / "catalog"
        self.assertEqual(self.command("catalog", "--input", str(self.root), "--output", str(catalog_output)), 0)
        catalog = json.loads((catalog_output / "catalog.json").read_text())
        self.assertEqual(catalog["videos"][0]["metadata"]["sample_aspect_ratio"], "4:3")
        sample_output = self.root / "samples"
        self.assertEqual(self.command("sample", "--input", str(self.source), "--timestamps", ".5,1",
                                      "--thumb-width", "320", "--output", str(sample_output)), 0)
        manifest = json.loads((sample_output / "manifest.json").read_text())
        self.assertEqual(len(manifest["candidates"]), 2)
        candidate = manifest["candidates"][0]
        self.assertEqual(candidate["source"], str(self.source.resolve()))
        self.assertEqual(candidate["timestamp"], .5)
        with Image.open(sample_output / candidate["preview"]) as preview:
            self.assertEqual(preview.size, (320, 136))
        self.assertTrue((sample_output / manifest["contact_sheets"][0]["path"]).is_file())
        self.assertIn("V01-C001", (sample_output / "index.html").read_text())
        output = self.root / "wallpaper"
        self.assertEqual(self.command("render", "--source", str(self.source), "--timestamp", ".5",
                                      "--size", "200x200", "--sharpen", "0", "--output", str(output)), 0)
        with Image.open(output / "wallpaper.png") as result:
            self.assertEqual(result.size, (200, 200))
            self.assertEqual(result.getpixel((0, 0)), (16, 18, 22))
            self.assertNotEqual(result.getpixel((100, 100)), (16, 18, 22))
        with Image.open(output / "original.png") as original:
            self.assertEqual(original.size, (320, 180))
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertEqual(provenance["source_metadata"]["mtime_ns"], before[1])
        self.assertEqual(provenance["parameters"]["fit"], "contain")
        self.assertEqual(provenance["timestamp"], .5)
        self.assertFalse(provenance["upscaled"])
        self.assertEqual(provenance["render_pipeline"]["resize_passes"], 1)
        self.assertFalse(provenance["render_pipeline"]["thumbnail_used_for_render"])
        self.assertGreater(provenance["source_metadata"]["video_bit_rate"], 0)
        self.assertEqual((self.source.stat().st_size, self.source.stat().st_mtime_ns), before)
        unchanged = (output / "wallpaper.png").read_bytes()
        self.assertEqual(self.command("render", "--source", str(self.source), "--timestamp", "1",
                                      "--output", str(output)), 2)
        self.assertEqual((output / "wallpaper.png").read_bytes(), unchanged)

    def test_cover_focus_changes_crop_and_exact_dimensions(self):
        results = []
        for label, focus in (("left", "0"), ("right", "1")):
            output = self.root / label
            self.assertEqual(self.command("render", "--source", str(self.source), "--timestamp", "0.5",
                                          "--size", "100x200", "--fit", "cover", "--focus-x", focus,
                                          "--output", str(output)), 0)
            with Image.open(output / "wallpaper.png") as image:
                self.assertEqual(image.size, (100, 200))
                results.append(image.tobytes())
        self.assertNotEqual(*results)

    def test_hdr_conversion_works_with_spaces_and_quote_in_output_path(self):
        for kind in ("hlg", "pq"):
            # Explicit HDR mode tests the conversion machinery with a tiny
            # synthetic signal, not a claim that SDR testsrc is true HDR footage.
            output = self.root / f"{kind} preview's [test]"
            code = self.command("sample", "--input", str(self.source), "--timestamps", ".5",
                                "--hdr-mode", kind, "--thumb-width", "160", "--output", str(output))
            self.assertEqual(code, 0)
            manifest = json.loads((output / "manifest.json").read_text())
            conversion = manifest["videos"][0]["color_conversion"]
            self.assertEqual(conversion["mode"], kind)
            self.assertTrue((output / conversion["lut_file"]).is_file())

    def test_invalid_time_does_not_create_output(self):
        output = self.root / "invalid"
        self.assertEqual(self.command("render", "--source", str(self.source), "--timestamp", "10",
                                      "--output", str(output)), 2)
        self.assertFalse(output.exists())

    def test_native_preview_preserves_square_pixel_frame_and_avoids_resize(self):
        source = self.root / "square.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(self.source),
                        "-vf", "setsar=1", "-c:v", "mpeg4", "-q:v", "2", str(source)], check=True)
        output = self.root / "native"
        self.assertEqual(self.command("render", "--source", str(source), "--timestamp", ".5",
                                      "--native", "--sharpen", "0", "--output", str(output)), 0)
        with Image.open(output / "original.png") as original, Image.open(output / "wallpaper.png") as result:
            self.assertEqual(result.size, (320, 180))
            self.assertEqual(result.tobytes(), original.tobytes())
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertTrue(provenance["parameters"]["native"])
        self.assertEqual(provenance["render_pipeline"]["resize_passes"], 0)
        self.assertEqual(provenance["render_pipeline"]["horizontal_scale"], 1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            live.parser().parse_args(["render", "--source", str(source), "--timestamp", "0", "--native",
                                      "--size", "100x100", "--output", str(output)])

    def test_native_preview_corrects_non_square_pixel_display_ratio(self):
        output = self.root / "native-sar"
        self.assertEqual(self.command("render", "--source", str(self.source), "--timestamp", ".5",
                                      "--native", "--sharpen", "0", "--output", str(output)), 0)
        with Image.open(output / "wallpaper.png") as result:
            self.assertEqual(result.size, (427, 180))
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertEqual(provenance["render_pipeline"]["resize_passes"], 1)

    def test_auto_hdr_detection_and_png_srgb_metadata(self):
        for kind, transfer in (("hlg", "arib-std-b67"), ("pq", "smpte2084")):
            source = self.root / f"tagged-{kind}.mkv"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                            "testsrc2=size=96x64:rate=2:duration=1", "-c:v", "ffv1",
                            "-vf", f"setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc",
                            "-color_primaries", "bt2020", "-color_trc", transfer, "-colorspace", "bt2020nc",
                            str(source)], check=True)
            output = self.root / f"auto-{kind}"
            self.assertEqual(self.command("render", "--source", str(source), "--timestamp", "0",
                                          "--size", "96x64", "--output", str(output)), 0)
            provenance = json.loads((output / "provenance.json").read_text())
            self.assertEqual(provenance["color_conversion"]["mode"], kind)
            for filename in ("original.png", "wallpaper.png"):
                metadata = json.loads(subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output / filename)], text=True))["streams"][0]
                self.assertEqual(metadata["color_transfer"], "iec61966-2-1")
                self.assertEqual(metadata["color_primaries"], "bt709")
                self.assertEqual(metadata["color_range"], "pc")


if __name__ == "__main__":
    unittest.main()
