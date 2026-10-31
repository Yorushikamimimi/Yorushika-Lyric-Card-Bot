import subprocess
from unittest import mock

import pytest
import live_wallpaper as live


@pytest.mark.parametrize('failure', ['exit', 'missing', 'empty'])
def test_hardware_failure_retries_software_once(tmp_path, failure):
    target = tmp_path / 'frame.png'
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            if failure != 'missing':
                target.write_bytes(b'partial' if failure == 'exit' else b'')
            return subprocess.CompletedProcess(command, 1 if failure == 'exit' else 0, '', 'failed')
        assert not target.exists()
        target.write_bytes(b'software')
        return subprocess.CompletedProcess(command, 0, '', '')
    with mock.patch.dict('os.environ', {'YORUSHIKA_HWACCEL': '1'}), mock.patch('live_wallpaper.subprocess.run', side_effect=run):
        live.extract_frame(tmp_path / 'source.mp4', 3.5, target, ['format=rgb24'])
    assert len(calls) == 2
    assert calls[0].index('-hwaccel') < calls[0].index('-i')
    assert '-hwaccel_output_format' not in calls[0]
    assert '-hwaccel' not in calls[1]
    assert calls[0][-1] == calls[1][-1] == str(target)
    assert target.read_bytes() == b'software'


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_decoder_switch_and_success_do_not_retry(tmp_path, enabled):
    target = tmp_path / 'frame.png'
    def run(command, **kwargs):
        target.write_bytes(b'image')
        return subprocess.CompletedProcess(command, 0, '', '')
    with mock.patch.dict('os.environ', {'YORUSHIKA_HWACCEL': enabled}), mock.patch('live_wallpaper.subprocess.run', side_effect=run) as process:
        live.extract_frame(tmp_path / 'source.mp4', 0, target, [])
    assert process.call_count == 1
    assert ('-hwaccel' in process.call_args.args[0]) == (enabled == '1')


def test_existing_output_is_never_removed(tmp_path):
    target = tmp_path / 'frame.png'
    target.write_bytes(b'keep')
    with mock.patch('live_wallpaper.subprocess.run') as process, pytest.raises(live.WallpaperError):
        live.extract_frame(tmp_path / 'source.mp4', 0, target, [])
    process.assert_not_called()
    assert target.read_bytes() == b'keep'


def test_both_decoders_fail_reports_original_error(tmp_path):
    with mock.patch.dict('os.environ', {'YORUSHIKA_HWACCEL': '1'}), mock.patch('live_wallpaper.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'decode failed')) as process:
        with pytest.raises(live.WallpaperError, match='decode failed'):
            live.extract_frame(tmp_path / 'source.mp4', 0, tmp_path / 'frame.png', [])
    assert process.call_count == 2
