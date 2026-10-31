import asyncio
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from studio import create_app


@pytest.fixture
def capture_app(tmp_path):
    source = tmp_path / 'private-video.mkv'
    source.write_bytes(b'video')
    batch = tmp_path / 'output' / 'old'
    batch.mkdir(parents=True)
    Image.new('RGB', (20, 10)).save(batch / 'thumb.jpg')
    manifest = {'videos': [{'id': 'v', 'label': 'Live', 'source': str(source)}], 'candidates': [{'id': 'a', 'video_id': 'v', 'source': str(source), 'timestamp': 1, 'preview': 'thumb.jpg'}]}
    (batch / 'manifest.json').write_text(json.dumps(manifest))
    metadata = {'duration': 60, 'chapters': [{'start': 0, 'end': 60, 'title': '山の草原'}], 'source': str(source)}
    async def render(candidate, destination):
        await asyncio.sleep(.05)
        destination.mkdir(parents=True)
        Image.new('RGB', (1280, 720), 'green').save(destination / 'original.png')
    with patch('studio.probe_video', return_value=metadata):
        app = create_app(tmp_path, preview_renderer=render)
        with TestClient(app, base_url='http://127.0.0.1:8766') as client:
            yield client, tmp_path, app


def post(client, route, body):
    return client.post(route, json=body, headers={'Origin': 'http://127.0.0.1:8766'})


def source_id(client):
    return client.get('/api/capture-sources').json()['sources'][0]['id']


def test_capture_sources_hide_paths_and_mark_missing(capture_app):
    client, root, _ = capture_app
    response = client.get('/api/capture-sources')
    assert str(root) not in response.text
    assert 'private-video' not in response.text
    assert response.json()['sources'][0]['available']
    (root / 'private-video.mkv').unlink()
    assert not client.get('/api/capture-sources').json()['sources'][0]['available']


def test_capture_writes_new_manifest_thumbnail_and_favorite_only(capture_app):
    client, root, _ = capture_app
    old = root / 'output/old/manifest.json'
    before = old.read_bytes()
    response = post(client, '/api/captures', {'source_id': source_id(client), 'timestamp': 35.1, 'favorite': True})
    assert response.status_code == 200, response.text
    data = json.loads((root / 'output/user-captures/manifest.json').read_text())
    assert data['source_kind'] == 'user'
    assert data['videos'][0]['kind'] == 'live'
    assert data['candidates'][0]['id'] == 'U001'
    assert data['candidates'][0]['chapter'] == '山の草原'
    assert data['candidates'][0]['created_at']
    assert Image.open(root / 'output/user-captures/previews/U001.jpg').width == 640
    assert old.read_bytes() == before
    candidate = next(c for c in client.get('/api/library').json()['candidates'] if c['id'] == response.json()['candidate_id'])
    assert candidate['favorite'] and candidate['source_kind'] == 'user'


def test_capture_duplicate_and_favorite_false(capture_app):
    client, root, _ = capture_app
    body = {'source_id': source_id(client), 'timestamp': 2.5, 'favorite': False}
    first = post(client, '/api/captures', body)
    assert first.status_code == 200, first.text
    assert not (root / 'output/favorites.json').exists()
    again = post(client, '/api/captures', body)
    assert again.json()['duplicate']
    assert again.json()['candidate_id'] == first.json()['candidate_id']
    assert len(json.loads((root / 'output/user-captures/manifest.json').read_text())['candidates']) == 1
    body['favorite'] = True
    assert post(client, '/api/captures', body).status_code == 200
    assert (root / 'output/favorites.json').exists()


@pytest.mark.parametrize('timestamp', [-.1, 60, 61, .11, '2', True])
def test_capture_rejects_invalid_time(capture_app, timestamp):
    client, root, _ = capture_app
    assert post(client, '/api/captures', {'source_id': source_id(client), 'timestamp': timestamp}).status_code == 422
    assert not (root / 'output/user-captures').exists()


def test_capture_rejects_unknown_source(capture_app):
    client, _, _ = capture_app
    assert post(client, '/api/captures', {'source_id': 'missing', 'timestamp': 1}).status_code == 404


def test_capture_stale_generation_cannot_revive_prefetch(capture_app):
    client, _, _ = capture_app
    source = source_id(client)
    first = post(client, '/api/capture-previews', {'source_id': source, 'timestamp': 1}).json()
    post(client, '/api/capture-previews', {'source_id': source, 'timestamp': 10})
    stale = post(client, '/api/capture-previews', {'source_id': source, 'timestamp': 1.5, 'priority': 'prefetch', 'generation': first['generation']})
    assert stale.json()['status'] == 'cancelled'


def test_capture_time_parser_node():
    source = (Path(__file__).parents[1] / 'web/app.js').read_text()
    functions = source[source.index('function parseCaptureTime'):source.index('const capture =')]
    checks = '''
const assert = require('node:assert/strict');
for (const value of ['35:08', '0:35:08', '2108', '35：08']) assert.equal(parseCaptureTime(value), 2108);
assert.equal(parseCaptureTime('1:02.5'), 62.5);
for (const value of ['abc', '', '-1', '1:60', '0:99:00', '1.23', '1:2:3:4', 'Infinity', '9'.repeat(60)]) assert.equal(parseCaptureTime(value), null, value);
assert.equal(captureTime(59.9), '0:59.9');
assert.equal(captureTime(60), '1:00');
'''
    subprocess.run(['node', '-e', functions + checks], check=True, capture_output=True, text=True)


def test_concurrent_capture_additions_are_idempotent(capture_app):
    from concurrent.futures import ThreadPoolExecutor
    client, root, _ = capture_app
    body = {'source_id': source_id(client), 'timestamp': 4.5, 'favorite': True}
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: post(client, '/api/captures', body), range(3)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()['candidate_id'] for r in responses}) == 1
    assert len(json.loads((root / 'output/user-captures/manifest.json').read_text())['candidates']) == 1


def test_capture_manifest_replace_failure_preserves_previous_files(capture_app):
    client, root, _ = capture_app
    source = source_id(client)
    assert post(client, '/api/captures', {'source_id': source, 'timestamp': 3}).status_code == 200
    manifest = root / 'output/user-captures/manifest.json'
    before = manifest.read_bytes()
    original_replace = Path.replace
    def fail_manifest(path, target):
        if Path(target) == manifest:
            raise OSError('fixture write failure')
        return original_replace(path, target)
    with patch.object(Path, 'replace', fail_manifest):
        response = post(client, '/api/captures', {'source_id': source, 'timestamp': 4})
    assert response.status_code == 500
    assert manifest.read_bytes() == before
    assert (manifest.parent / 'previews/U001.jpg').exists()
    assert not (manifest.parent / 'previews/U002.jpg').exists()
    assert not list(manifest.parent.glob('*.tmp'))


@pytest.fixture
def strip_app(tmp_path):
    source = tmp_path / 'private-video.mkv'
    source.write_bytes(b'video')
    batch = tmp_path / 'output' / 'old'
    batch.mkdir(parents=True)
    Image.new('RGB', (20, 10)).save(batch / 'thumb.jpg')
    manifest = {'videos': [{'id': 'v', 'label': 'Live', 'source': str(source)}], 'candidates': [{'id': 'a', 'video_id': 'v', 'source': str(source), 'timestamp': 1, 'preview': 'thumb.jpg'}]}
    (batch / 'manifest.json').write_text(json.dumps(manifest))
    metadata = {'duration': 60, 'chapters': [], 'source': str(source)}
    calls = []

    def strip(candidate, start, step, count, folder):
        calls.append((start, step, count))
        if step == 1:
            from live_wallpaper import WallpaperError
            raise WallpaperError(f'ffmpeg failed for {candidate["source"]}')
        for index in range(count):
            Image.new('RGB', (48, 27), 'blue').save(folder / f'{index + 1:02d}.jpg')

    async def render(candidate, destination):
        destination.mkdir(parents=True)
        Image.new('RGB', (64, 36)).save(destination / 'original.png')

    with patch('studio.probe_video', return_value=metadata):
        app = create_app(tmp_path, preview_renderer=render, strip_renderer=strip)
        with TestClient(app, base_url='http://127.0.0.1:8766') as client:
            yield client, tmp_path, calls


def test_capture_strip_renders_batches_and_reuses_cached_frames(strip_app):
    client, root, calls = strip_app
    source = source_id(client)
    first = post(client, '/api/capture-strip', {'source_id': source, 'start': 8, 'step': 2, 'count': 9}).json()['frames']
    assert [f['timestamp'] for f in first] == [8, 10, 12, 14, 16, 18, 20, 22, 24]
    assert all(f['url'].startswith('/media/') for f in first)
    assert calls == [(8, 2, 9)]
    # Extending to the right reuses 24 and only decodes the new frames.
    later = post(client, '/api/capture-strip', {'source_id': source, 'start': 24, 'step': 2, 'count': 9}).json()['frames']
    assert calls[1:] == [(26, 2, 8)] and all(f['url'] for f in later)
    # Frames are cached by time, so another step sharing a time reuses it.
    post(client, '/api/capture-strip', {'source_id': source, 'start': 10, 'step': 5, 'count': 3})
    assert calls[2:] == [(15, 5, 1)]
    assert client.get(first[0]['url']).status_code == 200
    assert not list((root / 'output/preview-cache/strips').rglob('.*'))


def test_capture_strip_clips_to_video_and_hides_failures(strip_app):
    client, root, calls = strip_app
    source = source_id(client)
    end = post(client, '/api/capture-strip', {'source_id': source, 'start': 40, 'step': 5, 'count': 9}).json()['frames']
    assert [f['timestamp'] for f in end] == [40, 45, 50, 55]
    failed = post(client, '/api/capture-strip', {'source_id': source, 'start': 30, 'step': 1, 'count': 3})
    assert failed.status_code == 409 and str(root) not in failed.text
    assert not list((root / 'output/preview-cache/strips').rglob('.*'))
    assert not (root / 'output/user-captures').exists()


@pytest.mark.parametrize('body', [{'start': 1, 'step': 3}, {'start': 1, 'count': 10}, {'start': 1, 'count': 0}, {'start': 1.25}, {'start': 60}, {'start': True}])
def test_capture_strip_rejects_bad_requests(strip_app, body):
    client, _, calls = strip_app
    assert post(client, '/api/capture-strip', {'source_id': source_id(client), **body}).status_code == 422
    assert calls == []


@pytest.mark.skipif(subprocess.run(['which', 'ffmpeg'], capture_output=True).returncode, reason='ffmpeg missing')
@pytest.mark.parametrize('start,step', [(1, 1), (0, 2)])
def test_capture_strip_real_ffmpeg_matches_frame_times(tmp_path, start, step):
    # A clock video: brightness grows with time, so each frame shows its time.
    source = tmp_path / 'clock.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=10:d=6',
                    '-vf', 'geq=lum=T*40:cb=128:cr=128', '-pix_fmt', 'yuv420p', str(source)], check=True)
    batch = tmp_path / 'output' / 'old'
    batch.mkdir(parents=True)
    Image.new('RGB', (20, 10)).save(batch / 'x.jpg')
    manifest = {'videos': [{'id': 'v', 'label': 'Clock', 'source': str(source)}], 'candidates': [{'id': 'a', 'video_id': 'v', 'source': str(source), 'timestamp': 1, 'preview': 'x.jpg'}]}
    (batch / 'manifest.json').write_text(json.dumps(manifest))
    from io import BytesIO
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8766') as client:
        frames = post(client, '/api/capture-strip', {'source_id': source_id(client), 'start': start, 'step': step, 'count': 9}).json()['frames']
        times = [f['timestamp'] for f in frames]
        assert times == [t for t in range(start, 6, step)]
        levels = []
        for frame in frames:
            picture = Image.open(BytesIO(client.get(frame['url']).content)).convert('L')
            assert picture.width == 480
            levels.append(picture.getpixel((10, 10)))
    # Limited-range luma T*40 in full-range JPEG; one 0.1 s frame is about 19 levels.
    expected = [max(0, round((t * 40 - 16) * 255 / 219)) for t in times]
    assert all(abs(a - b) <= 6 for a, b in zip(levels, expected)), (levels, expected)
