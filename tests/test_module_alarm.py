import sys
import time
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
    processes = []

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(cmd):
        process = FakeProcess()
        calls.append(cmd)
        processes.append(process)
        return process

    monkeypatch.setattr(module_alarm.subprocess, "Popen", fake_popen)

    alarm = module_alarm.Alarm()
    alarm.start_background()

    deadline = time.time() + 1
    while not calls and time.time() < deadline:
        time.sleep(0.01)

    alarm.stop()

    assert calls == [['/usr/bin/aplay', '-q', str(alarm.alarm_sound_path)]]
    assert processes[0].terminated
    assert alarm._thread is not None
    assert not alarm._thread.is_alive()


def test_alarm_restarts_audio_until_stopped(monkeypatch):
    monkeypatch.setattr(module_alarm, "_speaker_device_present", lambda: True)
    monkeypatch.setattr(module_alarm.shutil, "which", lambda name: "/usr/bin/aplay")
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    calls = []

    class FinishedProcess:
        def poll(self):
            return 0

        def terminate(self):
            raise AssertionError("finished process should not be terminated")

        def wait(self, timeout=None):
            return 0

    alarm = module_alarm.Alarm()

    def fake_popen(cmd):
        calls.append(cmd)
        if len(calls) == 3:
            alarm.stop_playing_sound()
        return FinishedProcess()

    monkeypatch.setattr(module_alarm.subprocess, "Popen", fake_popen)

    alarm.play_sound_forever()

    assert calls == [
        ['/usr/bin/aplay', '-q', str(alarm.alarm_sound_path)],
        ['/usr/bin/aplay', '-q', str(alarm.alarm_sound_path)],
        ['/usr/bin/aplay', '-q', str(alarm.alarm_sound_path)],
    ]


def test_alarm_set_active_starts_and_stops(monkeypatch):
    monkeypatch.setattr(module_alarm, "_speaker_device_present", lambda: True)
    monkeypatch.setattr(module_alarm.shutil, "which", lambda name: "/usr/bin/aplay")
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    alarm = module_alarm.Alarm()
    actions = []

    monkeypatch.setattr(alarm, "start_background", lambda: actions.append("start"))
    monkeypatch.setattr(alarm, "stop", lambda: actions.append("stop"))

    alarm.set_active(True)
    alarm.set_active(False)

    assert actions == ["start", "stop"]


def test_alarm_set_active_times_out_until_recovered(monkeypatch):
    monkeypatch.setattr(module_alarm, "_speaker_device_present", lambda: True)
    monkeypatch.setattr(module_alarm.shutil, "which", lambda name: "/usr/bin/aplay")
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    now = 0.0
    alarm = module_alarm.Alarm(max_active_seconds=7, clock=lambda: now)
    actions = []

    monkeypatch.setattr(alarm, "start_background", lambda: actions.append("start"))
    monkeypatch.setattr(alarm, "stop", lambda: actions.append("stop"))

    alarm.set_active(True)
    now = 6.9
    alarm.set_active(True)
    now = 7.0
    alarm.set_active(True)
    now = 8.0
    alarm.set_active(True)
    alarm.set_active(False)
    now = 9.0
    alarm.set_active(True)

    assert actions == ["start", "stop", "stop", "start"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
