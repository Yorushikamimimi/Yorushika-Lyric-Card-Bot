"""Deterministic queue tests: block the running decoder rather than racing sleeps."""
import asyncio
import json
import subprocess
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from studio import create_app, power_status
import test_studio


class PreviewSchedulingTests(unittest.TestCase):
    tearDown = test_studio.StudioTests.tearDown
    write_manifest = test_studio.StudioTests.write_manifest
    wait_preview = test_studio.StudioTests.wait_preview
    # Reuse the fixture only; inherited tests remain in StudioTests.
    def setUp(self):
        test_studio.StudioTests.setUp(self)
        self.client.__exit__(None, None, None)
        self.manifest['candidates'] = [dict(self.manifest['candidates'][0], id=f'C{i}', timestamp=i + 1) for i in range(12)]
        self.write_manifest()
        self.gate = asyncio.Event()
        self.started = []

        async def render(candidate, destination):
            self.started.append(candidate['timestamp'])
            self.active_previews += 1
            self.max_active_previews = max(self.max_active_previews, self.active_previews)
            try:
                await self.gate.wait()
                destination.mkdir(parents=True)
                Image.new('RGB', (20, 10)).save(destination / 'original.png')
            finally:
                self.active_previews -= 1

        self.client = TestClient(create_app(self.root, preview_renderer=render), base_url='http://127.0.0.1:8765')
        self.client.__enter__()
        self.rows = self.client.get('/api/library').json()['candidates']

    def post(self, index, priority='current'):
        return self.client.post('/api/previews', json={'candidate_id': self.rows[index]['id'], 'priority': priority}, headers=self.headers)

    def release(self):
        async def release():
            self.gate.set()
        self.client.portal.call(release)

    def test_current_discards_waiting_work_without_interrupting_decoder(self):
        first = self.post(0).json()
        old_current = self.post(1).json()
        old_prefetch = self.post(2, 'prefetch').json()
        last = self.post(3).json()
        later_prefetch = self.post(4, 'prefetch').json()
        for old in (old_current, old_prefetch):
            self.assertEqual(self.client.get('/api/preview-jobs/' + old['id']).json()['status'], 'cancelled')
        self.assertEqual(len(self.started), 1)
        self.release()
        for job in (first, last, later_prefetch):
            self.assertEqual(self.wait_preview(job['id'])['status'], 'done')
        self.assertEqual(self.started, [self.rows[i]['timestamp'] for i in (0, 3, 4)])
        self.assertEqual(self.max_active_previews, 1)

    def test_prefetch_promotion_deduplicates_and_cancelled_retry_has_new_id(self):
        first = self.post(0).json()
        cancelled = self.post(1, 'prefetch').json()
        pending = self.post(2, 'prefetch').json()
        promoted = self.post(2).json()
        self.assertEqual(pending['id'], promoted['id'])
        self.assertEqual(promoted['priority'], 'current')
        retry = self.post(1, 'prefetch').json()
        self.assertNotEqual(cancelled['id'], retry['id'])
        self.assertEqual(self.client.get('/api/preview-jobs/' + cancelled['id']).json()['status'], 'cancelled')
        self.release()
        for job in (first, promoted, retry):
            self.assertEqual(self.wait_preview(job['id'])['status'], 'done')
        self.assertEqual(self.started, [self.rows[i]['timestamp'] for i in (0, 2, 1)])

    def test_queue_limit_429_and_current_reclaims_obsolete_slots(self):
        for index in range(8):
            self.assertEqual(self.post(index, 'prefetch').status_code, 200)
        self.assertEqual(self.post(8, 'prefetch').status_code, 429)
        current = self.post(9)
        self.assertEqual(current.status_code, 200)
        self.release()
        self.assertEqual(self.wait_preview(current.json()['id'])['status'], 'done')
        invalid = self.client.post('/api/previews', json={'candidate_id': self.rows[0]['id'], 'priority': 'urgent'}, headers=self.headers)
        self.assertEqual(invalid.status_code, 422)

    def test_cached_current_also_discards_old_queue(self):
        self.release()
        cached = self.post(0).json()
        self.wait_preview(cached['id'])
        async def block():
            self.gate.clear()
        self.client.portal.call(block)
        running = self.post(1).json()
        obsolete = self.post(2, 'prefetch').json()
        self.assertTrue(self.post(0).json()['cached'])
        self.assertEqual(self.client.get('/api/preview-jobs/' + obsolete['id']).json()['status'], 'cancelled')
        self.release()
        self.wait_preview(running['id'])

    def test_slow_old_request_cannot_invalidate_new_current(self):
        import httpx
        original = asyncio.to_thread

        async def scenario():
            old_waiting, release_old = asyncio.Event(), asyncio.Event()
            calls = 0

            async def delayed(func, *args, **kwargs):
                nonlocal calls
                if func.__name__ == "cached_preview":
                    calls += 1
                    if calls == 1:
                        old_waiting.set()
                        await release_old.wait()
                return await original(func, *args, **kwargs)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.client.app), base_url="http://127.0.0.1:8765", headers=self.headers) as client:
                with mock.patch("studio.asyncio.to_thread", side_effect=delayed):
                    old = asyncio.create_task(client.post("/api/previews", json={"candidate_id": self.rows[0]["id"]}))
                    await old_waiting.wait()
                    newer = await client.post("/api/previews", json={"candidate_id": self.rows[1]["id"]})
                    release_old.set()
                    obsolete = await old
                return obsolete.json(), newer.json()

        obsolete, newer = self.client.portal.call(scenario)
        self.assertEqual(obsolete["status"], "cancelled")
        self.release()
        self.assertEqual(self.wait_preview(newer["id"])["status"], "done")
        self.assertEqual(self.started, [self.rows[1]["timestamp"]])

    def test_power_endpoint_is_read_only(self):
        with mock.patch('studio.power_status', return_value={'on_battery': True, 'prefetch_count': 1}):
            response = self.client.get('/api/power')
        self.assertEqual(response.json(), {'on_battery': True, 'prefetch_count': 1})


def test_power_battery_ac_unknown_and_failures():
    with mock.patch('studio.sys.platform', 'darwin'):
        for stdout, code, expected in [
            ("Now drawing from 'Battery Power'\n -InternalBattery-0 52%; discharging", 0, True),
            ("Now drawing from 'AC Power'\n -InternalBattery-0 52%; charging", 0, False),
            ('unrecognized', 0, False),
            ("Now drawing from 'Battery Power'", 1, False),
        ]:
            with mock.patch('studio.subprocess.run', return_value=subprocess.CompletedProcess([], code, stdout)) as run:
                assert power_status()['on_battery'] is expected
                assert run.call_args.args[0] == ['pmset', '-g', 'ps']
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('pmset', 2)):
            with mock.patch('studio.subprocess.run', side_effect=error):
                assert power_status() == {'on_battery': False, 'prefetch_count': 2}
    with mock.patch('studio.sys.platform', 'linux'), mock.patch('studio.subprocess.run') as run:
        assert not power_status()['on_battery']
        run.assert_not_called()
