from dataclasses import dataclass


@dataclass
class DrowsinessState:
    eyes_closed_start: float | None = None
    alert_active: bool = False
    event_count: int = 0
    total_blinks: int = 0
    blink_counter: int = 0


def update_drowsiness_state(
    *,
    ear,
    current_time,
    state: DrowsinessState,
    ear_threshold,
    drowsy_time_threshold,
    blink_frame_threshold,
    router,
    alarm=None,
):
    """Update drowsiness state from one frame and emit alerts when needed."""
    if ear < ear_threshold:
        state.blink_counter += 1

        if state.eyes_closed_start is None:
            state.eyes_closed_start = current_time
        else:
            closed_duration = current_time - state.eyes_closed_start
            if closed_duration >= drowsy_time_threshold:
                if not state.alert_active:
                    state.event_count += 1
                    message = (
                        f"DROWSINESS DETECTED! Event #{state.event_count}"
                        f" (eyes closed {closed_duration:.1f}s)"
                    )
                    router.emit_log(message, level="warning")
                    router.emit_alert(
                        "drowsiness_detected",
                        message,
                        severity="critical",
                        event_count=state.event_count,
                        closed_duration_sec=round(closed_duration, 3),
                        ear=round(ear, 3),
                        blink_ms=int(closed_duration * 1000),
                    )
                    if alarm is not None:
                        alarm.start_background()
                state.alert_active = True
    else:
        if state.blink_counter >= blink_frame_threshold:
            state.total_blinks += 1
        state.blink_counter = 0
        state.eyes_closed_start = None
        state.alert_active = False

    return state
