import hashlib
import json

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from studio import create_app

ORIGIN = {'Origin': 'http://127.0.0.1:8766'}


@pytest.fixture
def review_app(tmp_path):
    source = tmp_path / 'live.mp4'
    source.write_bytes(b'video')
    batch = tmp_path / 'output' / 'auto-review-test'
    (batch / 'moments').mkdir(parents=True)
    rows = []
    for n in range(2):
        moments = []
        for k in range(3):
            name = f'moments/A{n}_{k}.jpg'
            Image.new('RGB', (32, 18), (k * 80, 0, 0)).save(batch / name)
            moments.append({'seconds': 100 + n * 50 + k - 1, 'preview': name, 'cut_before': k == 2})
        rows.append({'id': f'A00{n + 1}', 'video_id': 'v', 'source': str(source), 'timestamp': 100 + n * 50,
                     'chapter': '晴る', 'preview': moments[1]['preview'], 'moments': moments,
                     'system_moment': 1, 'reasons': ['歌词完整', '舞台全景']})
    manifest = {'review_queue': True, 'label': '待挑 · 测试', 'videos': [{'id': 'v', 'label': '2024前世', 'source': str(source)}],
                'candidates': rows}
    (batch / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False))
    app = create_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1:8766') as client:
        yield client, tmp_path


def decide(client, body):
    return client.post('/api/review-decisions', json=body, headers=ORIGIN)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_queue_items_stay_out_of_gallery_until_kept(review_app):
    client, root = review_app
    assert client.get('/api/library').json()['candidates'] == []
    items = client.get('/api/review-queue').json()['items']
    assert [i['candidate_id'] for i in items] == ['A001', 'A002']
    first = items[0]
    assert first['system_moment'] == 1 and first['reasons'] == ['歌词完整', '舞台全景']
    assert [m['offset'] for m in first['moments']] == [-1, 0, 1] and first['moments'][2]['cut_before']
    assert str(root) not in json.dumps(items)


def test_keep_with_other_moment_favorites_and_moves_time(review_app):
    client, root = review_app
    manifest = root / 'output/auto-review-test/manifest.json'
    before = digest(manifest)
    item = client.get('/api/review-queue').json()['items'][0]
    response = decide(client, {'candidate_id': item['id'], 'decision': 'keep', 'moment': 2})
    assert response.status_code == 200, response.text
    card = client.get('/api/library').json()['candidates']
    assert len(card) == 1 and card[0]['favorite'] and card[0]['timestamp'] == 101
    assert card[0]['preview'] == item['moments'][2]['url']
    saved = json.loads((root / 'output/review-decisions.json').read_text())['items'][0]
    assert saved['user_accepted'] is True and saved['moment'] == 2 and saved['seconds'] == 101
    favorites = json.loads((root / 'output/favorites.json').read_text())['items']
    assert favorites == [{'manifest': 'auto-review-test/manifest.json', 'candidate_id': 'A001'}]
    assert digest(manifest) == before


def test_undo_keep_removes_favorite_and_drop_keeps_reason(review_app):
    client, root = review_app
    a, b = client.get('/api/review-queue').json()['items']
    decide(client, {'candidate_id': a['id'], 'decision': 'keep', 'moment': 1})
    assert decide(client, {'candidate_id': a['id'], 'decision': None}).status_code == 200
    assert json.loads((root / 'output/favorites.json').read_text())['items'] == []
    assert client.get('/api/library').json()['candidates'] == []
    assert decide(client, {'candidate_id': b['id'], 'decision': 'drop', 'reason': '  灯光 太乱 '}).status_code == 200
    assert decide(client, {'candidate_id': a['id'], 'decision': 'moment'}).status_code == 200
    items = {i['candidate_id']: i for i in client.get('/api/review-queue').json()['items']}
    assert items['A002']['decision'] == 'drop' and items['A002']['drop_reason'] == '灯光 太乱'
    assert items['A001']['decision'] == 'moment'
    assert client.get('/api/library').json()['candidates'] == []


def test_review_rejects_bad_input(review_app):
    client, _ = review_app
    item = client.get('/api/review-queue').json()['items'][0]
    assert decide(client, {'candidate_id': item['id'], 'decision': 'keep'}).status_code == 422
    assert decide(client, {'candidate_id': item['id'], 'decision': 'keep', 'moment': 9}).status_code == 422
    assert decide(client, {'candidate_id': item['id'], 'decision': 'drop', 'reason': 'x' * 61}).status_code == 422
    assert decide(client, {'candidate_id': item['id'], 'decision': 'drop', 'reason': 'a\x00'}).status_code == 422
    assert decide(client, {'candidate_id': 'nope', 'decision': 'skip'}).status_code == 404
    assert decide(client, {'candidate_id': item['id'], 'decision': 'love'}).status_code == 422
