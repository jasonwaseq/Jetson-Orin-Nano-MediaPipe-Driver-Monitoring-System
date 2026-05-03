import shutil
import subprocess
import threading
import time
import sounddevice as sd
from pathlib import Path

ALARM_SOUND_FILE = "alarm_sound.wav"

USB_FOUND = False

def search_for_USB_audio():
    global USB_FOUND
    USB_FOUND = False
    sd_out = str(sd.query_devices())
    sd_list = sd_out.split('\n')
    for device in sd_list:
        if "USB" in device:
            USB_FOUND = True
            break

class SoundFinder:
    def __init__(self):
        self.is_found = USB_FOUND

    def search(self):
        search_for_USB_audio()
        self.is_found = USB_FOUND

def _resolve_alarm_sound_path():
    sound_dir = Path(__file__).resolve().parent / "sound"
    candidate = sound_dir / ALARM_SOUND_FILE
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Missing alarm sound: {candidate}")

#=========================================================================#
# SoundDeviceNotFoundError                                                #
#     An exception that's called when there is no Sound Device connected  #
#=========================================================================#
class SoundDeviceNotFoundError(Exception):
    def __init__(self):
        super().__init__("Error: No sound device found, cannot play alarm")

# Tests if a sound device is present, if not raises the error above
def _speaker_device_present():
    sound = SoundFinder()
    sound.search()
    return sound.is_found

#=========================================================================#
# Alarm                                                                   #
#     This class is used for initiating the alarm so that the sound plays #
#=========================================================================#
class Alarm:
    def __init__(self):
        self.alarm_sound_path = _resolve_alarm_sound_path()
        self._player = shutil.which("aplay")
        self._thread = None
        self._process = None
        self._process_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._speaker_found = _speaker_device_present()
        if self._player is None:
            print(f"Alarm disabled: missing 'aplay' for {self.alarm_sound_path}")

    def search_for_alarm(self):
        self._speaker_found = _speaker_device_present()

    def play_sound(self):
        self.play_sound_forever()

    def play_once(self):
        if self._player is None:
            return

        subprocess.Popen(
            [self._player, "-q", str(self.alarm_sound_path)],
        ).wait()

    def _stop_current_process(self):
        with self._process_lock:
            process = self._process

        if process is None or process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def play_sound_forever(self):
        if self._player is None:
            return

        while not self._stop_event.is_set():
            process = subprocess.Popen(
                [self._player, "-q", str(self.alarm_sound_path)],
            )
            with self._process_lock:
                self._process = process

            while process.poll() is None:
                if self._stop_event.wait(timeout=0.1):
                    self._stop_current_process()
                    break

    def stop_playing_sound(self):
        self._stop_event.set()
        self._stop_current_process()
        if (
            self._thread is not None
            and self._thread.is_alive()
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=1)

    def start_background(self):
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.play_sound_forever, daemon=True)
        self._thread.start()
        return self._thread

    def set_active(self, active):
        if active:
            self.start_background()
        else:
            self.stop()

    def stop(self):
        self.stop_playing_sound()

if __name__ == '__main__':
    alert = Alarm()
    alert.play_sound_forever()
    time.sleep(10)
    alert.stop_playing_sound()
    if not alert._speaker_found:
        print("Warning: Speaker Not Found")
    time.sleep(1)
