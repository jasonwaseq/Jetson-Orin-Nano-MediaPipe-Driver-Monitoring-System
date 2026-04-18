import shutil
import subprocess
import threading
import time
from pathlib import Path


def _resolve_alarm_sound_path():
    sound_dir = Path(__file__).resolve().parent / "sound"
    candidate = sound_dir / "alarm_sound.wav"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Missing alarm sound: {candidate}")

class Alarm:
    def __init__(self):
        self.alarm_sound_path = _resolve_alarm_sound_path()
        self._player = shutil.which("aplay")
        self._thread = None
        self._stop_event = threading.Event()
        if self._player is None:
            print(f"Alarm disabled: missing 'aplay' for {self.alarm_sound_path}")

    def play_sound(self):
        if self._player is None:
            return

        while not self._stop_event.is_set():
            subprocess.run(
                [self._player, "-q", str(self.alarm_sound_path)],
                check=False,
            )
            break

    def start_background(self):
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.play_sound, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop_event.set()

if __name__ == '__main__':
    alert = Alarm()
    alert.start_background()
    time.sleep(1)
