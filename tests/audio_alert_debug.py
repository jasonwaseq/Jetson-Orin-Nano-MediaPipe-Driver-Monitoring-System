import argparse
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
    return parser.parse_args()


def main():
    args = parse_args()
    router = EventRouter(source_id="jetson-01", producer="audio-debug", schema_version="1.0")
    config = AudioAlertConfig.from_env()
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
    notifier = AudioAlertNotifier(router=router, config=config)
    notifier.start()
    try:
        if args.probe_pins:
            notifier.probe_pins(
                duration_s=args.continuous_seconds or args.duration_seconds,
                tone_frequency_hz=args.frequency_hz,
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
        notifier.stop()
        print("Audio debug complete.")


if __name__ == "__main__":
    main()
