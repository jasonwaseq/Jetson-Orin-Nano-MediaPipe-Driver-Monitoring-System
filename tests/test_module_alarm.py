import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.module_alarm as module_alarm


def test_alarm_starts_audio_playback(monkeypatch):
    monkeypatch.setattr(module_alarm, "_speaker_device_present", lambda: True)
    monkeypatch.setattr(module_alarm.shutil, "which", lambda name: "/usr/bin/aplay")
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    calls = []

    def fake_run(cmd, check=False):
        calls.append((cmd, check))

    monkeypatch.setattr(subprocess, "run", fake_run)

    alarm = module_alarm.Alarm()
    alarm.start_background()
    if alarm._thread is not None:
        alarm._thread.join(timeout=1)

    assert calls == [(['/usr/bin/aplay', '-q', str(alarm.alarm_sound_path)], False)]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
