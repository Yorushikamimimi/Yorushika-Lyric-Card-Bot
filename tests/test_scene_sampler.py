"""Offline pilot checks on constructed shots; never touch the real gallery."""
import hashlib
import copy
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import scene_sampler as scene


def picture(reverse=False):
    im = Image.new('RGB', (160, 80), '#244660')
    d = ImageDraw.Draw(im)
    d.rectangle((90 if reverse else 10, 10, 145 if reverse else 65, 70), fill='#e2b96b')
    return im


def test_persistence_rejects_flash_but_keeps_new_composition():
    a, b = picture(), picture(True)
    images = [a.copy() for _ in range(40)] + [b.copy() for _ in range(40)]
    images[16] = Image.new('RGB', a.size, 'white')
    frames = [{'index': i, 'seconds': i/8, 'score': 30 if i in (16, 17, 40) else 0,
               'file': f'{i}.png'} for i in range(len(images))]
    cuts, audit = scene.detect(frames, images, 8)
    assert cuts == [40]
    assert all(r['decision'] != 'cut' for r in audit if r['index'] in (16, 17))


def test_sampling_stays_inside_shots_and_deduplicates_static_long_shot():
    images = [picture() for _ in range(64)] + [picture(True) for _ in range(16)]
    frames = [{'index': i, 'seconds': 100+i/8, 'file': f'{i}.png'} for i in range(80)]
    picks, rejected = scene.plan(frames, images, [64], 8, max_gap=2)
    assert len(picks) == 2
    assert len(rejected) == 3
    assert 0 < picks[0]['index'] < 64 < picks[1]['index'] < 80
    assert all(p['user_accepted'] is None and not p['reviewed'] for p in picks)


def test_repeated_exposure_flashes_do_not_hide_a_dim_composition_change():
    images = []
    for i in range(80):
        frame = ImageEnhance.Brightness(picture(i >= 40)).enhance((.3, .6, 1)[i % 3])
        images.append(frame)
    frames = [{'index': i, 'seconds': i/12, 'score': 1, 'file': f'{i}.png'} for i in range(80)]
    cuts, _ = scene.detect(frames, images, 12)
    assert cuts == [40]  # SCDet alone would reject the low-scoring cut.


def test_quality_search_prefers_visible_detail_over_a_dark_or_blurred_midpoint():
    clear = picture()
    blurred = clear.filter(ImageFilter.GaussianBlur(7))
    images = [blurred.copy() for _ in range(48)]
    images[12] = clear
    for i in range(20, 29):
        images[i] = ImageEnhance.Brightness(clear).enhance(.04)
    frames = [{'index': i, 'seconds': i/12, 'file': f'{i}.png'} for i in range(48)]
    picks, _ = scene.plan(frames, images, [], 12)
    assert [p['index'] for p in picks] == [12]


def test_black_background_text_is_retained_without_a_brightness_rejection():
    im = Image.new('RGB', (160, 80), 'black')
    ImageDraw.Draw(im).text((100, 30), 'TEXT', fill='white')
    images = [im.copy() for _ in range(48)]
    frames = [{'index': i, 'seconds': i/12, 'file': f'{i}.png'} for i in range(48)]
    picks, _ = scene.plan(frames, images, [], 12)
    assert len(picks) == 1
    assert picks[0]['user_accepted'] is None


def test_fallback_keeps_missed_dark_shot_even_when_visually_similar():
    dark = Image.new('RGB', (160, 80), 'black')
    text = dark.copy()
    ImageDraw.Draw(text).text((80, 30), 'A', fill='#303030')
    images = [dark, text, picture(True)]
    scene_picks = [{'index': 0, 'seconds': 10., 'file': '0.png', 'reason': 'shot_interior'},
                   {'index': 2, 'seconds': 10.5, 'file': '2.png', 'reason': 'shot_interior'}]
    uniform = [{'index': 1, 'seconds': 10.25, 'requested_seconds': 10.26, 'file': '1.png'}]
    before = copy.deepcopy((scene_picks, uniform))
    combined, audit = scene.combine_candidates(scene_picks, uniform, images)
    assert [p['index'] for p in combined] == [0, 1, 2]
    assert combined[1]['origins'] == ['uniform']
    assert combined[1]['similar_to_index'] == 0
    assert audit[0]['action'] == 'kept_for_visual_review'
    assert all(p['user_accepted'] is None and not p['reviewed'] for p in combined)
    assert (scene_picks, uniform) == before


def test_same_grid_frame_merges_routes_but_retains_requested_times():
    frame = {'index': 0, 'seconds': 1., 'file': '0.png'}
    uniform = [{**frame, 'requested_seconds': t} for t in [1.01, 1.02]]
    picks, audit = scene.combine_candidates([frame], uniform, [picture()])
    assert len(picks) == 1
    assert picks[0]['origins'] == ['scene', 'uniform']
    assert picks[0]['uniform_requested_seconds'] == [1.01, 1.02]
    assert len(audit) == 2
    assert all(row['action'] == 'merged_same_frame' for row in audit)


def test_slow_camera_drift_with_local_light_sweeps_keeps_real_cut():
    images = []
    for i in range(80):
        im = Image.new('RGB', (192, 96), '#23455a')
        draw = ImageDraw.Draw(im)
        for y in range(4, 96, 12):
            for x in range(-10, 210, 18):
                shift = i * .18
                draw.rectangle((x+shift, y, x+8+shift, y+7), fill=(190, 130, 75))
        if 25 <= i <= 28 or 47 <= i <= 50:
            draw.rectangle((55, 0, 112, 96), fill='white')
        if i >= 60:
            im = im.transpose(Image.Transpose.ROTATE_180)
        images.append(im)
    frames = [{'index': i, 'seconds': i/12, 'score': 0, 'file': f'{i}.png'} for i in range(80)]
    cuts, audit = scene.detect(frames, images, 12)
    assert cuts == [60]
    assert any(row['decision'] == 'continuous_scene' for row in audit)


def textured_picture(seed):
    rng = random.Random(seed)
    im = Image.new('RGB', (192, 96), '#244660')
    draw = ImageDraw.Draw(im)
    for _ in range(80):
        x, y = rng.randrange(192), rng.randrange(96)
        draw.rectangle((x, y, x+8, y+8),
                       fill=tuple(rng.randrange(30, 230) for _ in range(3)))
    return im


@pytest.mark.parametrize('length', [3, 4, 5])
def test_two_close_cuts_preserve_a_stable_short_shot(length):
    images = [textured_picture(1)]*30 + [textured_picture(2)]*length + [textured_picture(3)]*30
    frames = [{'index': i, 'seconds': i/12, 'score': 0, 'file': f'{i}.png'}
              for i in range(len(images))]
    cuts, _ = scene.detect(frames, images, 12)
    assert cuts == [30, 30+length]
    picks, _ = scene.plan(frames, images, cuts, 12)
    assert any(30 <= p['index'] < 30+length for p in picks)


def test_continuous_foreground_motion_is_not_a_cut_but_next_camera_change_is():
    images = []
    for i in range(65):
        im = textured_picture(1)
        x = min(130, max(25, 25+(i-15)*20))
        ImageDraw.Draw(im).rectangle((x, 0, x+48, 96), fill='#051020')
        images.append(im)
    images += [textured_picture(2)]*20
    frames = [{'index': i, 'seconds': i/12, 'score': 0} for i in range(len(images))]
    cuts, audit = scene.detect(frames, images, 12)
    assert cuts == [65]
    assert any(row['decision'] == 'continuous_motion' for row in audit)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_boundary_context_preserves_short_final_shot_without_exporting_outside(tmp_path, monkeypatch):
    monkeypatch.setenv('YORUSHIKA_HWACCEL', '0')
    source = tmp_path/'boundary.mkv'
    images = [picture()]*24 + [picture(True)]*24 + [picture()]*16
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                    '-video_size', '160x80', '-framerate', '8', '-i', '-', '-c:v', 'ffv1', str(source)],
                   input=b''.join(im.tobytes() for im in images), check=True, capture_output=True)
    # Only one analyzed frame of the second shot is inside the requested range.
    result = scene.run_pilot(source, 1, 3.125, tmp_path/'tail', tmp_path/'cache', fps=8)
    assert result['cuts'] == [3.0]
    assert any(p['seconds'] == 3.0 for p in result['scene_picks'])
    assert all(1 <= p['seconds'] < 3.125 for key in ('scene_picks', 'uniform_equal_count')
               for p in result[key])
    assert result['decoded_range'] == [0, 4.125]
    # At the actual source boundaries padding is clamped; no made-up tail.
    edge = scene.run_pilot(source, 0, 8, tmp_path/'edges', tmp_path/'cache', fps=8)
    assert edge['decoded_range'] == [0, 8]
    assert edge['cuts'] == [3.0, 6.0]
    assert all(0 <= p['seconds'] < 8 for p in edge['scene_picks'])
    with pytest.raises(ValueError):
        scene.scan(source, 1, 3, tmp_path/'cache', context_seconds=2)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_real_scan_offset_cache_and_no_gallery_manifest(monkeypatch):
    monkeypatch.setenv('YORUSHIKA_HWACCEL', '0')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "pilot's, workspace"
        root.mkdir()
        source = root/'synthetic.mkv'
        a, b = picture(), picture(True)
        images = [a]*24 + [b]*24
        images[12] = Image.new('RGB', a.size, 'white')
        data = b''.join(im.tobytes() for im in images)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                        '-video_size', '160x80', '-framerate', '8', '-i', '-', '-c:v', 'ffv1', str(source)],
                       input=data, check=True, capture_output=True)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        first = scene.run_pilot(source, 1, 5, root/'first', root/'cache', fps=8)
        assert first['cache_hit'] is False
        assert len(first['cuts']) == 1
        assert abs(first['cuts'][0]-3) <= 1/8
        assert len(first['uniform_equal_count']) == len(first['scene_picks'])
        assert {p['index'] for p in first['hybrid_picks']} == {
            p['index'] for p in first['scene_picks']+first['uniform_equal_count']}
        assert (root/'first/hybrid.jpg').is_file()
        assert all(1 <= p['seconds'] < 5 for p in first['scene_picks'])
        second = scene.run_pilot(source, 1, 5, root/'second', root/'cache', fps=8)
        assert second['cache_hit'] is True
        assert second['cuts'] == first['cuts']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert not list(root.rglob('manifest.json'))
        with pytest.raises(scene.live.WallpaperError):
            scene.run_pilot(source, 1, 5, root/'first', root/'cache', fps=8)
        for start, end in [(4, 3), (-1, 4), (0, 7), (float('nan'), 3)]:
            with pytest.raises(ValueError):
                scene.scan(source, start, end, root/'cache', fps=8)
        # Corrupt/missing caches must not be quietly used as valid analyses.
        cached = json.loads((Path(first['analysis_folder'])/'scan.json').read_text())
        (Path(first['analysis_folder'])/cached['frames'][0]['file']).unlink()
        with pytest.raises(FileNotFoundError):
            scene.scan(source, 1, 5, root/'cache', fps=8, context_seconds=1)
