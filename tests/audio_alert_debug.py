import argparse
import shutil
import subprocess
import time

from modules.module_audio_alert import AudioAlertConfig, AudioAlertNotifier
from modules.module_event_router import EventRouter


def parse_args():
    parser = argparse.ArgumentParser(description="Audio alert hardware debug utility")
    parser.add_argument(
        "--continuous-seconds",
        type=float,
        default=0.0,
        help="Play one continuous tone for the requested number of seconds.",
    )
    parser.add_argument(
        "--frequency-hz",
        type=int,
        default=880,
        help="Frequency to use for continuous tone mode.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=2.0,
        help="Duration for continuous tone mode.",
    )
    parser.add_argument(
        "--force-gpio",
        action="store_true",
        help="Force the tone pin to use GPIO bit-banging instead of PWM.",
    )
    parser.add_argument(
        "--force-pwm",
        action="store_true",
        help="Force PWM mode even if environment defaults differ.",
    )
    parser.add_argument(
        "--probe-pins",
        action="store_true",
        help="Hold SHDN enabled and log each tone transition for hardware probing.",
    )
    parser.add_argument(
        "--noise-seconds",
        type=float,
        default=0.0,
        help="Play randomized PWM noise for the requested number of seconds.",
    )
    parser.add_argument(
        "--noise-deviation",
        type=float,
        default=45.0,
        help="Maximum duty-cycle deviation from 50%% while playing PWM noise.",
    )
    parser.add_argument(
        "--noise-min-hz",
        type=int,
        default=220,
        help="Lowest PWM frequency to use while generating noise.",
    )
    parser.add_argument(
        "--noise-max-hz",
        type=int,
        default=4000,
        help="Highest PWM frequency to use while generating noise.",
    )
    parser.add_argument(
        "--board-pin",
        type=int,
        default=15,
        choices=(13, 15),
        help="BOARD header pin to test for PWM output.",
    )
    parser.add_argument(
        "--scope-test",
        action="store_true",
        help="Hold a steady 1 kHz 50%% duty PWM waveform for oscilloscope probing.",
    )
    parser.add_argument(
        "--scope-frequency-hz",
        type=int,
        default=1000,
        help="Frequency to use for --scope-test.",
    )
    parser.add_argument(
        "--scope-seconds",
        type=float,
        default=10.0,
        help="How long to hold --scope-test active.",
    )
    parser.add_argument(
        "--keep-shdn-enabled",
        action="store_true",
        help="Leave the amp shutdown pin asserted after the tone starts for troubleshooting.",
    )
    parser.add_argument(
        "--alsa-test",
        action="store_true",
        help="Try the system audio path with speaker-test before using GPIO/PWM.",
    )
    parser.add_argument(
        "--alsa-frequency-hz",
        type=int,
        default=1000,
        help="Tone frequency for --alsa-test.",
    )
    parser.add_argument(
        "--alsa-seconds",
        type=float,
        default=5.0,
        help="Duration for --alsa-test.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    router = EventRouter(source_id="jetson-01", producer="audio-debug", schema_version="1.0")
    config = AudioAlertConfig.from_env()
    config = config.__class__(**{**config.__dict__, "tone_pin": args.board_pin})
    if args.force_gpio:
        config = config.__class__(**{**config.__dict__, "force_gpio": True, "prefer_pwm": False})
    elif args.force_pwm:
        config = config.__class__(**{**config.__dict__, "force_gpio": False, "prefer_pwm": True})
    config = config.__class__(**{**config.__dict__, "startup_muted": False})

    print("Audio preflight:")
    print(f"  board tone pin: {config.tone_pin}")
    print(f"  board shutdown pin: {config.shutdown_pin}")
    print(f"  default frequency hz: {config.default_frequency_hz}")
    print(f"  prefer pwm: {config.prefer_pwm}")
    print(f"  force gpio: {config.force_gpio}")
    print(f"  audio output mode: {config.audio_output_mode}")
    print(f"  pwm carrier hz: {config.pwm_carrier_hz}")
    print(f"  pwm step hz: {config.pwm_step_hz}")
    print(f"  pwm noise range hz: {config.pwm_noise_min_hz}-{config.pwm_noise_max_hz}")
    print(f"  test board pin: {config.tone_pin}")
    notifier = AudioAlertNotifier(router=router, config=config)
    pwm_info = notifier.pwm_mapping_info()
    print(f"  pwm mapping: {pwm_info}")
    notifier.start()
    try:
        if args.keep_shdn_enabled:
            notifier._set_shutdown(True)
            print("Audio debug: SHDN held enabled for troubleshooting.")
        if args.alsa_test:
            run_alsa_test(args.alsa_frequency_hz, args.alsa_seconds)
            return
        if args.probe_pins:
            notifier.probe_pins(
                duration_s=args.continuous_seconds or args.duration_seconds,
                tone_frequency_hz=args.frequency_hz,
            )
            return
        if args.scope_test:
            duration_s = max(0.5, float(args.scope_seconds))
            frequency_hz = max(1, int(args.scope_frequency_hz))
            print(
                f"Holding steady PWM on BOARD {config.tone_pin} at {frequency_hz} Hz "
                f"for {duration_s:.1f} seconds..."
            )
            notifier.play_test_tone(
                frequency_hz=frequency_hz,
                duration_s=duration_s,
            )
            return
        if args.noise_seconds > 0.0:
            print(
                f"Playing PWM noise for {args.noise_seconds:.1f} seconds "
                f"with +/-{args.noise_deviation:.1f}% duty-cycle deviation "
                f"and {args.noise_min_hz}-{args.noise_max_hz} Hz sweep..."
            )
            notifier.play_pwm_noise(
                duration_s=args.noise_seconds,
                carrier_hz=args.noise_min_hz,
                deviation=args.noise_deviation,
                step_hz=config.pwm_step_hz,
                noise_min_hz=args.noise_min_hz,
                noise_max_hz=args.noise_max_hz,
            )
            return
        if args.continuous_seconds > 0.0:
            print(
                f"Playing continuous tone at {args.frequency_hz} Hz "
                f"for {args.continuous_seconds:.1f} seconds..."
            )
            notifier.play_test_tone(
                frequency_hz=args.frequency_hz,
                duration_s=args.continuous_seconds or args.duration_seconds,
            )
            return
        print("Playing drowsiness tone...")
        notifier.send_alert(2, "debug drowsiness", code="drowsiness_detected")
        time.sleep(2.0)
        print("Playing head inattention tone...")
        notifier.send_alert(2, "debug head", code="head_inattention_detected")
        time.sleep(2.0)
    finally:
        if args.keep_shdn_enabled:
            try:
                if notifier._use_pwm and notifier._pwm is not None:
                    notifier._pwm.ChangeDutyCycle(0.0)
                    notifier._pwm.stop()
                else:
                    notifier._gpio.output(notifier.config.tone_pin, notifier._gpio.LOW)
            except Exception:
                pass
        notifier.stop()
        print("Audio debug complete.")


def run_alsa_test(frequency_hz, duration_s):
    speaker_test = shutil.which("speaker-test")
    aplay = shutil.which("aplay")
    print("ALSA test:")
    print(f"  speaker-test: {speaker_test or 'not found'}")
    print(f"  aplay: {aplay or 'not found'}")
    duration_s = max(0.5, float(duration_s))
    frequency_hz = max(50, int(frequency_hz))
    if speaker_test:
        cmd = [
            speaker_test,
            "-t", "sine",
            "-f", str(frequency_hz),
            "-l", "1",
            "-D", "default",
        ]
        print(f"Running: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, timeout=duration_s + 5.0)
            return
        except subprocess.CalledProcessError as exc:
            print(f"speaker-test failed rc={exc.returncode}")
        except subprocess.TimeoutExpired:
            print("speaker-test timed out")
    if aplay:
        print("No usable speaker-test path found; ALSA device may be missing.")
    else:
        print("No ALSA tools found.")


if __name__ == "__main__":
    main()
