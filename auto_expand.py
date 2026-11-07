"""Whole-show candidate scan for the "待挑" review queue.

Reuses the frozen scene_sampler algorithm (shot detection + uniform fallback,
<=120 s per segment) over an entire Live video. The source video is only read.
Outputs stay under one new folder; nothing here touches existing manifests,
favorites or selections, and no candidate is ever marked accepted.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from PIL import Image

import live_wallpaper as live
import scene_sampler as ss

SEGMENT = 120.0


TAIL = 3.0  # last seconds decode short at EOF (scan pads +1 s); credits only


def segments(duration: float, length: float = SEGMENT) -> list[tuple[float, float]]:
    usable = float(math.floor(duration - TAIL))  # whole seconds: fractional ends drop the last analysis frame
    count = math.ceil(usable / length)
    return [(i * length, min(usable, (i + 1) * length)) for i in range(count)]


def scan_segment(source: Path, start: float, end: float, cache: Path, fps: int = 12,
                 threshold: float = 6, max_gap: float = 4) -> dict:
    analysis, hit = ss.scan(source, start, end, cache, fps, context_seconds=1)
    folder = Path(analysis['folder'])
    for raw in folder.glob('*.rgb'):
        raw.unlink()  # decoded scratch only; the PNG frames and scan.json stay
    frames_all = analysis['frames']
    images = []
    for frame in frames_all:
        with Image.open(folder / frame['file']) as im:
            images.append(im.convert('RGB'))
    cuts_all, _ = ss.detect(frames_all, images, fps, threshold)
    inside = [i for i, f in enumerate(frames_all) if start <= f['seconds'] < end]
    offset = inside[0]
    frames = [{**frames_all[i], 'analysis_index': i, 'index': j} for j, i in enumerate(inside)]
    images = [images[i] for i in inside]
    cuts = [i - offset for i in cuts_all if offset < i <= inside[-1]]
    picks, _ = ss.plan(frames, images, cuts, fps, max_gap)
    times = live.choose_timestamps({'duration': end, 'chapters': [{'start': start, 'end': end}]},
                                   len(picks), len(picks))
    uniform = [{**min(frames, key=lambda f: abs(f['seconds'] - t)), 'requested_seconds': t} for t in times]
    hybrid, _ = ss.combine_candidates(picks, uniform, images)
    bounds = [0] + sorted(set(cuts)) + [len(frames)]
    shot_of = {}
    for shot, (a, b) in enumerate(zip(bounds, bounds[1:])):
        for k in range(a, b):
            shot_of[k] = (shot, frames[a]['seconds'], frames[b - 1]['seconds'])
    rows = []
    for row in hybrid:
        shot, s0, s1 = shot_of[row['index']]
        rows.append({'seconds': row['seconds'], 'segment': [start, end], 'shot_start': s0, 'shot_end': s1,
                     'origins': row['origins'], 'quality': row['quality_metrics'],
                     'analysis_file': str(folder / row['file']), 'similar_to_prior': 'similar_to_index' in row})
    return {'range': [start, end], 'cache_hit': hit, 'cuts': [frames[i]['seconds'] for i in cuts],
            'candidates': rows, 'scan_seconds': analysis['elapsed_seconds']}


def run(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'cache'
    meta = live.probe_video(source)
    before = ss.source_signature(source)
    progress = output / 'scan-progress.json'
    done = json.loads(progress.read_text()) if progress.exists() else {'segments': []}
    finished = {tuple(s['range']) for s in done['segments']}
    begin = time.monotonic()
    for start, end in segments(meta['duration']):
        if (start, end) in finished:
            continue
        done['segments'].append(scan_segment(source, start, end, cache))
        temporary = progress.with_suffix('.tmp')
        temporary.write_text(json.dumps(done, ensure_ascii=False))
        temporary.replace(progress)
        print(f'{live.timecode(end)} / {live.timecode(meta["duration"])}', flush=True)
    if ss.source_signature(source) != before:
        raise RuntimeError('扫描过程中源文件变化，结果不可使用')
    rows = []
    for seg in sorted(done['segments'], key=lambda s: s['range'][0]):
        for row in seg['candidates']:
            row['chapter'] = live.chapter_at(meta, row['seconds'])
            rows.append(row)
    result = {'source': before, 'duration': meta['duration'], 'candidate_count': len(rows),
              'elapsed_seconds': time.monotonic() - begin, 'candidates': rows,
              'notice': '算法候选，未审图、未入库、未被接受'}
    (output / 'scan-candidates.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def existing_times(output_root: Path, source: Path) -> list[float]:
    """Timestamps of every candidate already in the gallery for this source video."""
    times = []
    for manifest in output_root.glob('*/manifest.json'):
        try:
            data = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        for row in data.get('candidates', []):
            try:
                if Path(str(row.get('source'))).resolve() == source.resolve():
                    times.append(float(row['timestamp']))
            except (KeyError, TypeError, ValueError, OSError):
                continue
    return sorted(times)


def shortlist(output: Path, output_root: Path, gap: float = 4.0, min_quality: float = .12) -> dict:
    """Rule pass only removes the obviously unusable; looking at pictures decides the rest."""
    scan = json.loads((output / 'scan-candidates.json').read_text())
    source = Path(scan['source']['path'])
    taken = existing_times(output_root, source)
    kept, dropped = [], {'near_existing': 0, 'similar_to_prior': 0, 'low_quality': 0}
    for row in scan['candidates']:
        if any(abs(row['seconds'] - t) < gap for t in taken):
            dropped['near_existing'] += 1
        elif row['similar_to_prior']:
            dropped['similar_to_prior'] += 1
        elif row['quality']['quality'] < min_quality:
            dropped['low_quality'] += 1
        else:
            kept.append(row)
    # Lighting changes split a song into many short "shots"; keep the clearest
    # frame per shot (one more per 8 s of a long shot, >=3 s apart).
    shots: dict[tuple, list] = {}
    for row in kept:
        shots.setdefault((row['shot_start'], row['shot_end']), []).append(row)
    kept = []
    for (a, b), rows in shots.items():
        chosen = []
        for row in sorted(rows, key=lambda r: -r['quality']['quality']):
            if len(chosen) < max(1, math.ceil((b - a) / 8)) and all(abs(row['seconds'] - c['seconds']) >= 3 for c in chosen):
                chosen.append(row)
        dropped['same_shot'] = dropped.get('same_shot', 0) + len(rows) - len(chosen)
        kept += chosen
    kept.sort(key=lambda r: r['seconds'])
    for n, row in enumerate(kept):
        row['n'] = n
    sheets = output / 'review-sheets'
    sheets.mkdir(exist_ok=True)
    from PIL import ImageDraw
    per, cols, w = 48, 8, 320
    for page in range(math.ceil(len(kept) / per)):
        rows = kept[page * per:(page + 1) * per]
        h = 148
        sheet = Image.new('RGB', (cols * w, math.ceil(len(rows) / cols) * (h + 18)), '#15181a')
        draw = ImageDraw.Draw(sheet)
        for j, row in enumerate(rows):
            x, y = (j % cols) * w, (j // cols) * (h + 18)
            with Image.open(row['analysis_file']) as im:
                sheet.paste(im.convert('RGB').resize((w - 4, h)), (x + 2, y))
            draw.text((x + 4, y + h + 2), f"#{row['n']} {live.timecode(row['seconds'])[:8]} {row['chapter'] or ''}"[:44], fill='#d8dccf')
        sheet.save(sheets / f'sheet-{page + 1:02}.jpg', quality=82)
    result = {'existing_times': len(taken), 'dropped': dropped, 'count': len(kept), 'candidates': kept}
    (output / 'shortlist.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


MOMENT_OFFSETS = [-3, -2.25, -1.5, -.75, 0, .75, 1.5, 2.25, 3]


def extract_moments(source: Path, seconds: float, target: Path, filters: list[str], stem: str) -> list[dict]:
    """Nine frames across +-3 s, one decode pass; 1280 px wide previews."""
    start = max(0.0, seconds + MOMENT_OFFSETS[0])
    pattern = target / f'{stem}_%d.jpg'
    if any(target.glob(f'{stem}_*.jpg')):
        raise FileExistsError(stem)
    vf = ['setpts=PTS-STARTPTS', 'fps=4/3:round=up'] + filters + ['scale=1280:-2']
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-hwaccel', 'videotoolbox',
               '-ss', f'{start:.3f}', '-i', str(source), '-t', '6.6', '-map', '0:v:0', '-an', '-sn',
               '-vf', ','.join(vf), '-frames:v', '9', '-q:v', '3', str(pattern)]
    live.run(command)
    rows = []
    for k in range(9):
        file = target / f'{stem}_{k + 1}.jpg'
        if file.is_file():
            rows.append({'seconds': round(start + k * .75, 3), 'file': file.name})
    return rows


def build_moments(output: Path, batch: Path, picks: list[int]) -> list[dict]:
    short = json.loads((output / 'shortlist.json').read_text())
    scan = json.loads((output / 'scan-candidates.json').read_text())
    progress = json.loads((output / 'scan-progress.json').read_text())
    cuts = sorted(c for seg in progress['segments'] for c in seg['cuts'])
    source = Path(scan['source']['path'])
    meta = live.probe_video(source)
    folder = batch / 'moments'
    folder.mkdir(parents=True, exist_ok=True)
    filters, _ = live.color_plan(meta, output, 'auto', 1000, 65)
    by_n = {row['n']: row for row in short['candidates']}
    built = []
    for n in picks:
        row = by_n[n]
        stem = f'n{n:04}'
        moments = [] if any(folder.glob(f'{stem}_*.jpg')) else extract_moments(source, row['seconds'], folder, filters, stem)
        if not moments:
            moments = [{'seconds': round(max(0.0, row['seconds'] + MOMENT_OFFSETS[0]) + k * .75, 3), 'file': f'{stem}_{k + 1}.jpg'}
                       for k in range(9) if (folder / f'{stem}_{k + 1}.jpg').is_file()]
        previous = None
        for moment in moments:
            moment['cut_before'] = previous is not None and any(previous < c <= moment['seconds'] for c in cuts)
            previous = moment['seconds']
        built.append({'n': n, 'seconds': row['seconds'], 'chapter': row['chapter'], 'moments': moments})
        print(f'moments {len(built)}/{len(picks)}', flush=True)
    (output / 'moments.json').write_text(json.dumps(built, ensure_ascii=False, indent=1))
    return built


def write_batch(output: Path, batch: Path, reviews: list[dict], label: str, video_label: str) -> dict:
    """Queue manifest for 待挑: review_queue keeps it out of the gallery until kept.

    reviews: [{'n', 'system_moment' (index into the nine moments), 'reasons': [...]}],
    in the order the user should see them. Nothing here is marked accepted.
    """
    built = {row['n']: row for row in json.loads((output / 'moments.json').read_text())}
    scan = json.loads((output / 'scan-candidates.json').read_text())
    source = scan['source']['path']
    rows = []
    for number, review in enumerate(reviews, 1):
        item = built[review['n']]
        moments = [{'seconds': m['seconds'], 'preview': f"moments/{m['file']}", 'cut_before': m['cut_before']}
                   for m in item['moments']]
        system = review['system_moment']
        if not 0 <= system < len(moments):
            raise ValueError(f"#{review['n']} 的系统时刻越界")
        rows.append({'id': f'A{number:03}', 'video_id': 'v', 'source': source,
                     'timestamp': moments[system]['seconds'], 'timecode': live.timecode(moments[system]['seconds']),
                     'chapter': item['chapter'], 'title': item['chapter'] or '开场',
                     'preview': moments[system]['preview'],
                     'moments': moments, 'system_moment': system, 'reasons': review['reasons'][:5],
                     'scan_seconds': item['seconds'], 'shortlist_n': review['n'],
                     'reviewed_by_agent': True, 'user_accepted': None})
    manifest = {'review_queue': True, 'label': label, 'source_kind': 'live',
                'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'selection_process': 'auto_expand.py 全场扫描 → 规则粗筛 → Agent 看接触表与九格时刻条选出并定系统时刻；本人在待挑逐张决定',
                'user_acceptance': '见 output/review-decisions.json；本清单不写 user_accepted',
                'videos': [{'id': 'v', 'label': video_label, 'source': source}], 'candidates': rows}
    target = batch / 'manifest.json'
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    r = run(args.source, args.output)
    print(json.dumps({k: r[k] for k in ('candidate_count', 'elapsed_seconds')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
