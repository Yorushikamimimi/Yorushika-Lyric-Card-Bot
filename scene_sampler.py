"""Bounded, offline scene-sampling pilot. Never publishes a gallery manifest.

Analysis images are deliberately small; selected times still need native-frame
review. A cut time is only accurate to the analysis sampling grid, not a source
frame PTS. Uses existing FFmpeg/Pillow and the existing HDR conversion.
This pilot is not production selection: dense stage lighting causes false cuts,
and temporal stability alone can favor dark or defocused frames.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import time
import statistics
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

import live_wallpaper as live

SCAN_VERSION = 2


def dump(path: Path, value) -> None:
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def source_signature(source: Path) -> dict:
    stat = source.stat()
    return {'path': str(source.resolve()), 'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns}


def parse_scores(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        match = re.search(r'frame:(\d+).*pts_time:([\d.eE+-]+)', line)
        if match:
            records.append({'index': int(match[1]), 'relative_seconds': float(match[2])})
        elif line.startswith('lavfi.scd.score=') and records:
            records[-1]['score'] = float(line.split('=', 1)[1])
    return records


def scan(source: Path, start: float, end: float, cache: Path,
         fps: int = 12, width: int = 320, context_seconds: float = 0) -> tuple[dict, bool]:
    meta = live.probe_video(source)
    if not (math.isfinite(start) and math.isfinite(end) and
            0 <= start < end <= meta['duration'] and end - start <= 120):
        raise ValueError('试验片段须在源视频内，且每段不超过120秒')
    if not 4 <= fps <= 24 or not 96 <= width <= 320 or width % 2:
        raise ValueError('分析帧率须4–24，宽度须96–320的偶数')
    if not math.isfinite(context_seconds) or not 0 <= context_seconds <= 1:
        raise ValueError('边界参考画面须为0–1秒')
    signature = {'version': SCAN_VERSION, 'source': source_signature(source),
                 'start': start, 'end': end, 'fps': fps, 'width': width,
                 'color_pipeline': hashlib.sha256(Path(live.__file__).read_bytes()).hexdigest(),
                 'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]}
    # Whole analysis ticks keep the requested interval on the same grid when
    # there is enough source material before it. Never decode beyond the file.
    padding = math.ceil(context_seconds * fps) / fps
    scan_start, scan_end = max(0, start-padding), min(meta['duration'], end+padding)
    if context_seconds:
        signature['context_seconds'] = context_seconds
        signature['decoded_range'] = [scan_start, scan_end]
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
    folder = cache.resolve() / key
    if (folder / 'scan.json').is_file():
        result = json.loads((folder / 'scan.json').read_text())
        if result['signature'] != signature:
            raise ValueError('扫描缓存签名不一致')
        for frame in result['frames']:
            with Image.open(folder / frame['file']) as im:
                im.verify()
        return {**result, 'folder': str(folder)}, True
    folder.mkdir(parents=True, exist_ok=False)
    height = max(2, round(width / meta['display_aspect_ratio'] / 2) * 2)
    filters, color = live.color_plan(meta, folder, 'auto', 1000, 33)
    # Keep analysis PTS relative to the requested segment. round=up avoids the
    # half-frame timing shift observed in the existing capture-strip work.
    tail = ['setpts=PTS-STARTPTS', f'fps={fps}:round=up', 'format=yuv420p', 'scdet=t=0']
    base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
            '-threads', '2', '-filter_threads', '2', '-ss', str(scan_start)]
    swscale = [f'scale={width}:{height}', 'setsar=1']
    modes = [('software', [], swscale)]
    if os.environ.get('YORUSHIKA_HWACCEL', '1') != '0':
        download = 'p010le' if '10' in meta['pixel_format'] else 'nv12'
        modes.insert(0, ('videotoolbox', ['-hwaccel', 'videotoolbox',
                      '-hwaccel_output_format', 'videotoolbox_vld'],
                      [f'scale_vt=w={width}:h={height}', 'hwdownload',
                       f'format={download}', 'setsar=1']))
    begin = time.monotonic()
    attempts = []
    for mode, hw, scale in modes:
        # Own attempt-specific paths; a failed hardware attempt never overwrites
        # the user's source or an earlier completed analysis.
        raw = folder / f'{mode}.rgb'
        score_path = folder / f'{mode}-scores.txt'
        vf = scale + tail + [f'metadata=print:file={live.filter_path(score_path)}']
        vf += filters + ['format=rgb24']
        cmd = base + hw + ['-i', str(source), '-t', str(scan_end-scan_start), '-map', '0:v:0',
               '-an', '-sn', '-vf', ','.join(vf), '-fps_mode', 'passthrough',
               '-f', 'rawvideo', str(raw)]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        attempts.append({'mode': mode, 'command': cmd, 'returncode': done.returncode,
                         'stderr': done.stderr[-3000:]})
        if done.returncode == 0:
            break
    else:
        dump(folder / 'failure.json', attempts)
        raise RuntimeError('扫描解码失败，见缓存中的failure.json')
    records = parse_scores(score_path.read_text())
    frames = []
    frame_size = width * height * 3
    with raw.open('rb') as f:
        for i, record in enumerate(records):
            rel = record['relative_seconds']
            # FFmpeg can run the filter on the endpoint before the output -t
            # drops that frame. Do not expect bytes for out-of-range records.
            if rel >= scan_end-scan_start:
                break
            data = f.read(frame_size)
            if len(data) != frame_size:
                raise RuntimeError('分析帧与时间记录数量不一致')
            if not (0 <= rel < scan_end-scan_start):
                continue
            name = f'frame-{i:05}.png'
            Image.frombytes('RGB', (width, height), data).save(folder / name)
            frames.append({**record, 'seconds': scan_start + rel, 'file': name})
        if f.read(1):
            raise RuntimeError('存在未匹配时间记录的分析帧')
    if not frames or any('score' not in f for f in frames):
        raise RuntimeError('扫描未得到完整评分与图像')
    if source_signature(source) != signature['source']:
        raise RuntimeError('扫描过程中源文件变化，结果不可使用')
    result = {'signature': signature, 'metadata': meta, 'color': color,
              'attempts': attempts, 'frames': frames,
              'elapsed_seconds': time.monotonic()-begin,
              'time_notice': '分析网格时间，非精确源帧PTS；不保证捕获短于采样间隔的镜头'}
    dump(folder / 'scan.json', result)
    return {**result, 'folder': str(folder)}, False


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / (len(a.getbands()) * 255)


def normalized(image: Image.Image) -> Image.Image:
    gray = image.convert('L').resize((48, 24)).filter(ImageFilter.GaussianBlur(.6))
    stat = ImageStat.Stat(gray)
    mean, std = stat.mean[0], max(stat.stddev[0], 8)
    return gray.point([max(0, min(255, round(128 + (x-mean)*40/std))) for x in range(256)])


def local_structure(image: Image.Image) -> list[tuple[Image.Image, float]]:
    """Normalize small regions separately so a passing lamp does not dominate
    the whole picture. Textureless tiles cannot establish scene continuity.
    """
    gray = image.convert('L').resize((96, 48)).filter(ImageFilter.GaussianBlur(.6))
    tiles = []
    for y in range(3):
        for x in range(4):
            tile = gray.crop((x*24, y*16, (x+1)*24, (y+1)*16))
            stat = ImageStat.Stat(tile)
            mean, std = stat.mean[0], stat.stddev[0]
            tile = tile.point([max(0, min(255, round(128+(v-mean)*40/max(std, 8))))
                               for v in range(256)])
            tiles.append((tile, std))
    return tiles


def continuity_difference(a: list, b: list) -> float:
    distances = [difference(x, y) for (x, sx), (y, sy) in zip(a, b)
                 if min(sx, sy) >= 8]
    # A majority of textured regions must support the match; large flat dark
    # backgrounds alone must not merge two different shots.
    return statistics.median(distances) if len(distances) >= 6 else 1.


def detect(frames: list[dict], images: list[Image.Image], fps: int,
           threshold: float = 6) -> tuple[list[int], list[dict]]:
    """Compare two short windows, subtracting variation within each window.

    Brightness-normalized structure proposes cuts even when SCDet is low in a
    dark shot. A flash returning to the same scene has little persistent change.
    Stage-screen changes and moving cameras remain ambiguous, not semantic cuts.
    Threshold is the minimum window separation as a percentage of pixel range.
    """
    shapes = [normalized(im) for im in images]
    radius = max(2, round(fps * .4))
    distances = {}
    tiles = {}

    def local_frame(i):
        if i not in tiles:
            tiles[i] = local_structure(images[i])
        return tiles[i]

    def distance(a, b):
        key = tuple(sorted((a, b)))
        if key not in distances:
            distances[key] = difference(shapes[a], shapes[b])
        return distances[key]

    steps = [0.] + [distance(i-1, i) for i in range(1, len(frames))]
    audit = []
    for i in range(1, len(frames)):
        left = list(range(max(0, i-radius), i))
        right = list(range(i, min(len(frames), i+radius)))
        cross = statistics.median(distance(a, b) for a in left for b in right)
        within = statistics.mean(statistics.median(distance(a, b) for a in side for b in side)
                                 for side in (left, right))
        separation = cross-within
        step = steps[i]
        neighbors = steps[max(1, i-3):i] + steps[i+1:i+4]
        abruptness = step / max(.001, statistics.median(neighbors)) if neighbors else 0.
        # Require an abrupt step as well as sustained change; do not interpret
        # ordinary slow motion alone as a hard cut.
        proposed = separation >= threshold/100 and step >= .055
        continuity = None
        local_recovery = False
        right_stability = None
        if proposed or (separation >= threshold/100 * .65 and step >= .10):
            # Look across a brief light sweep for the same underlying scene.
            # This also tolerates modest camera drift; it is not optical flow
            # and does not prove identity during fast movement or occlusion.
            reach = max(2, round(fps * .5))
            left_indices = range(max(0, i-reach), i)
            right_indices = range(i, min(len(frames), i+reach))
            cross_tiles = [continuity_difference(local_frame(a), local_frame(b))
                           for a in left_indices for b in right_indices]
            continuity = min(cross_tiles)
            if not proposed and continuity >= .12:
                steady = [continuity_difference(local_frame(a), local_frame(b))
                          for a in right_indices for b in right_indices if a < b]
                valid = [d for d in steady if d < 1]
                right_stability = statistics.median(valid) if valid else None
                # A flash before a real cut can dilute the global separation.
                # Recover only with enough textured comparisons and a sustained,
                # internally stable new view; absence of texture is not proof.
                local_recovery = (sum(d < 1 for d in cross_tiles) >= 2*reach and
                                  len(valid) >= reach and right_stability < .06)
                proposed = local_recovery
        # A five-frame window can straddle both ends of a short shot. Consult
        # two frames per side only for a strong, isolated step with textured
        # evidence of a different view; do not relax the flash veto.
        short_separation = None
        short_continuity = None
        short_recovery = False
        if (not proposed and (continuity is None or continuity >= .09)
                and step >= .12 and abruptness >= 2 and i >= 2 and i+1 < len(frames)):
            short_separation = (statistics.median(distance(a, b)
                                for a in (i-2, i-1) for b in (i, i+1))
                                - (distance(i-2, i-1)+distance(i, i+1))/2)
            if short_separation >= threshold/100 * (5/12):
                short_continuity = min(continuity_difference(local_frame(a), local_frame(b))
                                       for a in (i-2, i-1) for b in (i, i+1))
                short_recovery = .12 <= short_continuity < 1
                proposed = short_recovery
        # Weak changes embedded in similarly large neighboring movements are
        # not isolated cuts (for example a silhouette raising an arm).
        continuous_motion = proposed and step < .09 and abruptness < 1.6
        decision = ('continuous_scene' if continuity is not None and continuity < .09 else
                    'continuous_motion' if continuous_motion else
                    'proposed' if proposed else 'insufficient_persistent_change')
        audit.append({'index': i, 'seconds': frames[i]['seconds'],
                      'score': frames[i]['score'], 'step_structure': step,
                      'window_separation': separation, 'within_window_change': within,
                      'strength': (short_separation if short_recovery else separation) * step,
                      'abruptness': abruptness, 'short_recovery': short_recovery,
                      'short_separation': short_separation, 'short_continuity': short_continuity,
                      'continuity_difference': continuity, 'local_recovery': local_recovery,
                      'right_stability': right_stability, 'decision': decision})
    # Neighboring proposals often describe one transition. Choose the strongest,
    # not simply the first flash; retain every decision for later inspection.
    cuts = []

    def separate_short_shot(a, b):
        a, b = sorted((a, b))
        if b-a < 3 or a < 2 or b+1 >= len(frames):
            return False
        sides = [list(range(a-2, a)), list(range(a, b)), list(range(b, b+2))]
        # Keep close boundaries only when an internally steady middle view is
        # clearly different from both neighbors. Lighting/motion alone often
        # creates several peaks without such a stable intervening shot.
        if any(max(distance(x, y) for x in side for y in side) >= .025 for side in sides):
            return False
        return all(statistics.median(distance(x, y) for x in left for y in right)
                   >= threshold/100 for left, right in zip(sides, sides[1:]))

    for row in sorted((r for r in audit if r['decision'] == 'proposed'),
                      key=lambda r: r['strength'], reverse=True):
        if any(abs(row['index']-cut) <= radius and not separate_short_shot(row['index'], cut)
               for cut in cuts):
            row['decision'] = 'nearby_stronger_event'
        else:
            row['decision'] = 'cut'
            cuts.append(row['index'])
    return sorted(cuts), audit


def frame_quality(image: Image.Image) -> dict:
    """Relative frame choice within a shot, never an aesthetic acceptance gate.

    Spatial detail, useful tonal range and visible area reduce the preference
    for empty dark frames. No absolute brightness rejection: silhouettes, text
    on black and all-dark shots remain available for visual review.
    """
    gray = image.convert('L').resize((160, 80))
    # Avoid encoded outer borders dominating these simple measurements.
    gray = gray.crop((8, 4, 152, 76))
    hist = gray.histogram()
    total = sum(hist)
    visible = sum(hist[20:246])/total
    clipped = sum(hist[246:])/total
    cumulative = 0
    quantiles = []
    for value, count in enumerate(hist):
        cumulative += count
        while len(quantiles) < 2 and cumulative >= total * (.05, .95)[len(quantiles)]:
            quantiles.append(value)
    tonal_range = (quantiles[1]-quantiles[0])/255
    detail = difference(gray, gray.filter(ImageFilter.GaussianBlur(1)))
    quality = .45*min(1, detail/.045) + .30*tonal_range + .25*visible - .25*clipped
    return {'quality': quality, 'detail': detail, 'tonal_range': tonal_range,
            'visible_fraction': visible, 'clipped_fraction': clipped}


def plan(frames: list[dict], images: list[Image.Image], cuts: list[int],
         fps: int, max_gap: float = 4) -> tuple[list[dict], list[dict]]:
    """Sample interiors, refine locally for temporal stability, merge only very
    similar images within the same detected shot. Never exclude dark/no-person
    scenes and never call automatic samples curated/accepted.
    """
    bounds = [0] + sorted(set(i for i in cuts if 0 < i < len(frames))) + [len(frames)]
    small = [im.resize((48, 24)) for im in images]
    movement = [0.] + [difference(small[i-1], small[i]) for i in range(1, len(small))]
    quality = [frame_quality(im) for im in images]
    picks, rejected = [], []
    for shot, (a, b) in enumerate(zip(bounds, bounds[1:])):
        amount = max(1, math.ceil((b-a) / fps / max_gap))
        margin = min(round(.3*fps), max(0, (b-a-1)//3))
        lo, hi = a+margin, b-1-margin
        kept = []
        for j in range(amount):
            center = min(hi, max(lo, round(a+(b-a)*(j+1)/(amount+1))))
            # Search each density bucket inside the shot, rather than just a
            # few neighbors around a possibly black midpoint.
            bucket_lo = max(lo, math.floor(a+(b-a)*j/amount))
            bucket_hi = min(hi, math.ceil(a+(b-a)*(j+1)/amount)-1)
            nearby = range(bucket_lo, max(bucket_lo, bucket_hi)+1)
            index = max(nearby, key=lambda i: quality[i]['quality'] -
                        .12*(movement[i] + (movement[i+1] if i+1 < b else movement[i])) -
                        .02*abs(i-center)/max(1, b-a))
            row = {'index': index, 'seconds': frames[index]['seconds'], 'shot': shot,
                   'reason': 'shot_interior' if amount == 1 else 'long_shot_density',
                   'file': frames[index]['file'], 'quality_metrics': quality[index],
                   'reviewed': False, 'user_accepted': None}
            duplicate = next((k for k in kept if difference(small[k], small[index]) < .018), None)
            if duplicate is not None:
                rejected.append({**row, 'reason': 'near_duplicate_within_shot', 'duplicate_index': duplicate})
            else:
                picks.append(row)
                kept.append(index)
    return picks, rejected


def combine_candidates(scene_picks: list[dict], uniform: list[dict],
                       images: list[Image.Image]) -> tuple[list[dict], list[dict]]:
    """Keep both search routes; only identical analysis indices are merged.

    A visually similar frame may belong to a missed short shot. Similarity is
    therefore a review hint, never a reason to remove a fallback frame.
    """
    combined, audit = {}, []
    for origin, rows in (('scene', scene_picks), ('uniform', uniform)):
        for row in rows:
            index = row['index']
            if index not in combined:
                combined[index] = {**row, 'origins': [], 'uniform_requested_seconds': [],
                                   'reviewed': False, 'user_accepted': None}
                if origin == 'uniform':
                    combined[index]['reason'] = 'uniform_fallback'
                    combined[index]['quality_metrics'] = frame_quality(images[index])
            else:
                audit.append({'index': index, 'origin': origin, 'action': 'merged_same_frame'})
            candidate = combined[index]
            if origin not in candidate['origins']:
                candidate['origins'].append(origin)
            if origin == 'uniform':
                candidate['uniform_requested_seconds'].append(row['requested_seconds'])
    picks = sorted(combined.values(), key=lambda row: row['index'])
    small = {row['index']: images[row['index']].resize((48, 24)) for row in picks}
    for position, row in enumerate(picks):
        for prior in reversed(picks[:position]):
            if row['seconds']-prior['seconds'] > 1:
                break
            delta = difference(small[row['index']], small[prior['index']])
            if delta < .018:
                row['similar_to_index'] = prior['index']
                audit.append({'index': row['index'], 'similar_to_index': prior['index'],
                              'pixel_difference': delta, 'action': 'kept_for_visual_review'})
                break
    return picks, audit


def contact_sheet(frames: list[dict], folder: Path, target: Path,
                  columns: int = 6) -> None:
    cell_w, cell_h = 320, 176
    sheet = Image.new('RGB', (cell_w*columns, cell_h*math.ceil(len(frames)/columns)), '#171a20')
    from PIL import ImageDraw
    draw = ImageDraw.Draw(sheet)
    for j, frame in enumerate(frames):
        x, y = (j % columns)*cell_w, (j//columns)*cell_h
        with Image.open(folder/frame['file']) as im:
            im.thumbnail((316, 146))
            sheet.paste(im, (x+2, y+2))
        draw.text((x+5, y+150), f'{frame["index"]:04} | {live.timecode(frame["seconds"])}', fill='white')
    sheet.save(target, quality=90)


def run_pilot(source: Path, start: float, end: float, output: Path, cache: Path,
              fps: int = 12, threshold: float = 6, max_gap: float = 4) -> dict:
    if not math.isfinite(threshold) or not 0 < threshold <= 100 or not math.isfinite(max_gap) or max_gap <= 0:
        raise ValueError('阈值须0–100，抽样间隔须为正数')
    output = live.create_output(output)
    begin = time.monotonic()
    analysis, hit = scan(source, start, end, cache, fps, context_seconds=1)
    context_frames, folder = analysis['frames'], Path(analysis['folder'])
    images = []
    for frame in context_frames:
        with Image.open(folder/frame['file']) as im:
            images.append(im.convert('RGB'))
    context_cuts, context_audit = detect(context_frames, images, fps, threshold)
    inside = [i for i, f in enumerate(context_frames) if start <= f['seconds'] < end]
    if not inside:
        raise ValueError('指定片段内没有分析帧，请扩大范围或提高分析帧率')
    offset = inside[0]
    frames = [{**context_frames[i], 'analysis_index': i, 'index': j}
              for j, i in enumerate(inside)]
    images = [images[i] for i in inside]
    cuts = [i-offset for i in context_cuts if offset < i <= inside[-1]]
    audit = [{**row, 'analysis_index': row['index'], 'index': row['index']-offset}
             for row in context_audit if offset < row['index'] <= inside[-1]]
    picks, duplicates = plan(frames, images, cuts, fps, max_gap)
    # Equal-count control: same formula as the old chapter-uniform sampler,
    # restricted to this pilot interval. Keep the absolute unrounded requested
    # times as well as nearest analysis-grid images used for visual comparison.
    times = live.choose_timestamps({'duration': end, 'chapters': [{'start': start, 'end': end}]}, len(picks), len(picks))
    uniform = [{**min(frames, key=lambda f: abs(f['seconds']-t)), 'requested_seconds': t} for t in times]
    hybrid, merge_audit = combine_candidates(picks, uniform, images)
    legacy = {str(n): [t for t in live.choose_timestamps(analysis['metadata'], n, n) if start <= t < end]
              for n in (2, 10)}
    result = {'source': analysis['signature']['source'], 'range': [start, end],
              'analysis_folder': str(folder), 'fps': fps, 'threshold': threshold, 'algorithm_version': 5,
              'context_seconds': 1, 'decoded_range': analysis['signature']['decoded_range'],
              'max_gap': max_gap, 'cache_hit': hit,
              'original_scan_seconds': analysis['elapsed_seconds'],
              'cut_events': audit, 'cuts': [frames[i]['seconds'] for i in cuts],
              'scene_picks': picks, 'uniform_equal_count': uniform,
              'hybrid_picks': hybrid, 'hybrid_merge_audit': merge_audit,
              'hybrid_policy': 'Union with equal-count uniform control; merge same analysis index only. '
                               'Near duplicates are review hints; no visual/brightness rejection.',
              'legacy_per_chapter_times': legacy, 'duplicate_candidates': duplicates,
              'pipeline_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'notice': '内部算法候选，未入库，非Agent精选/用户接受；小图不能代替原帧画质验收'}
    contact_sheet(picks, folder, output/'scene.jpg')
    contact_sheet(uniform, folder, output/'uniform.jpg')
    contact_sheet(hybrid, folder, output/'hybrid.jpg')
    for page, offset in enumerate(range(0, len(frames), 72), 1):
        contact_sheet(frames[offset:offset+72], folder, output/f'timeline-{page:02}.jpg')
    result['elapsed_seconds'] = time.monotonic()-begin
    dump(output/'result.json', result)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--start', type=live.timestamp, required=True)
    p.add_argument('--end', type=live.timestamp, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--fps', type=int, default=12)
    p.add_argument('--threshold', type=float, default=6)
    p.add_argument('--max-gap', type=float, default=4)
    args = p.parse_args(argv)
    try:
        r = run_pilot(**vars(args))
    except (ValueError, OSError, RuntimeError, live.WallpaperError, subprocess.SubprocessError) as e:
        p.exit(1, f'{e}\n')
    print(json.dumps({k: r[k] for k in ['range', 'cache_hit', 'cuts', 'elapsed_seconds']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
