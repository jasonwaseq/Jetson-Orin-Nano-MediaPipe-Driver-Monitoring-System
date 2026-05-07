import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.module_alarm import Alarm


def _resolve_alarm_sound_path():
    sound_dir = Path(__file__).resolve().parent.parent / "modules" / "sound"
    candidate = sound_dir / "alarm_sound.wav"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Missing alarm sound: {candidate}")


def main():
    sound_path = _resolve_alarm_sound_path()
    player = subprocess.Popen(["which", "aplay"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _stderr = player.communicate()
    aplay_path = stdout.decode("utf-8", errors="ignore").strip()
    if not aplay_path:
        print(f"Audio bridge disabled: missing 'aplay' for {sound_path}")
        return 1

    alarm = Alarm()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 8767))
    print(f"Audio bridge ready on 127.0.0.1:8767 using {aplay_path}")
    try:
        while True:
            data, _addr = sock.recvfrom(1024)
            _text = data.decode("utf-8", errors="ignore")
            # Every alert retriggers the alarm locally.
            alarm.play_once()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
