from pathlib import Path

from audio.audio_bridge_server import _resolve_alarm_sound_path


def test_audio_bridge_resolves_packaged_alarm_sound():
    sound_path = _resolve_alarm_sound_path()

    assert sound_path == Path(__file__).resolve().parents[1] / "modules" / "sound" / "alarm_sound.wav"
    assert sound_path.is_file()
