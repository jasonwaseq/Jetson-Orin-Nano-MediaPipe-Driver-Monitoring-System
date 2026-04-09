import argparse
import time

from module_audio_alert import AudioAlertConfig, AudioAlertNotifier
from module_event_router import EventRouter


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
    return parser.parse_args()


def main():
    args = parse_args()
    router = EventRouter(source_id="jetson-01", producer="audio-debug", schema_version="1.0")
    config = AudioAlertConfig.from_env()
    notifier = AudioAlertNotifier(router=router, config=config)
    notifier.start()
    try:
        if args.continuous_seconds > 0.0:
            print(
                f"Playing continuous tone at {args.frequency_hz} Hz "
                f"for {args.continuous_seconds:.1f} seconds..."
            )
            notifier.play_test_tone(
                frequency_hz=args.frequency_hz,
                duration_s=args.continuous_seconds,
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
