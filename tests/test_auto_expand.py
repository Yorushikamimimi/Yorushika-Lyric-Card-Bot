import json

import pytest

import auto_expand as ae


def test_segments_cover_whole_video_within_scan_limit():
    parts = ae.segments(4749.267)
    assert parts[0] == (0, 120) and parts[-1][1] == 4746.0
    assert all(b - a <= 120 for a, b in parts)
    assert all(a == prev[1] for prev, (a, _) in zip(parts, parts[1:]))


def test_write_batch_marks_queue_and_never_accepts(tmp_path):
    out, batch = tmp_path / 'scan', tmp_path / 'auto-review-x'
    out.mkdir(); batch.mkdir()
    (out / 'scan-candidates.json').write_text(json.dumps({'source': {'path': '/v/live.mp4'}}))
    moments = [{'seconds': 10 + k * .75, 'file': f'n0003_{k + 1}.jpg', 'cut_before': k == 6} for k in range(9)]
    (out / 'moments.json').write_text(json.dumps([{'n': 3, 'seconds': 13, 'chapter': '晴る', 'moments': moments}]))
    manifest = ae.write_batch(out, batch, [{'n': 3, 'system_moment': 5, 'reasons': ['歌词完整']}], '待挑', '2024 前世')
    row = manifest['candidates'][0]
    assert manifest['review_queue'] is True and row['id'] == 'A001'
    assert row['timestamp'] == 13.75 and row['preview'] == 'moments/n0003_6.jpg'
    assert row['user_accepted'] is None and row['moments'][6]['cut_before']
    with pytest.raises(FileExistsError):
        ae.write_batch(out, batch, [{'n': 3, 'system_moment': 5, 'reasons': []}], '待挑', '2024 前世')
    (batch / 'manifest.json').unlink()
    with pytest.raises(ValueError):
        ae.write_batch(out, batch, [{'n': 3, 'system_moment': 9, 'reasons': []}], '待挑', '2024 前世')
