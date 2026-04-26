import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.module_drowsiness import DrowsinessState, update_drowsiness_state


class DummyRouter:
    def __init__(self):
        self.logs = []
        self.alerts = []

    def emit_log(self, message, level="info", **data):
        self.logs.append((message, level, data))

    def emit_alert(self, code, message, severity="warning", **data):
        self.alerts.append((code, message, severity, data))


class DummyAlarm:
    def __init__(self):
        self.calls = 0

    def start_background(self):
        self.calls += 1


def test_no_false_alarm_with_open_eyes():
    state = DrowsinessState()
    router = DummyRouter()
    alarm = DummyAlarm()

    for i in range(100):
        update_drowsiness_state(
            ear=0.30,
            current_time=float(i) * 0.1,
            state=state,
            ear_threshold=0.21,
            drowsy_time_threshold=1.5,
            blink_frame_threshold=2,
            router=router,
            alarm=alarm,
        )

    assert router.logs == []
    assert router.alerts == []
    assert alarm.calls == 0
    assert state.total_blinks == 0


def test_drowsiness_triggers_after_threshold():
    state = DrowsinessState()
    router = DummyRouter()
    alarm = DummyAlarm()

    update_drowsiness_state(
        ear=0.30,
        current_time=0.0,
        state=state,
        ear_threshold=0.21,
        drowsy_time_threshold=1.5,
        blink_frame_threshold=2,
        router=router,
        alarm=alarm,
    )

    for i in range(1, 15):
        update_drowsiness_state(
            ear=0.10,
            current_time=float(i) * 0.2,
            state=state,
            ear_threshold=0.21,
            drowsy_time_threshold=1.5,
            blink_frame_threshold=2,
            router=router,
            alarm=alarm,
        )

    assert len(router.logs) == 1
    assert router.logs[0][1] == "warning"
    assert len(router.alerts) == 1
    assert router.alerts[0][0] == "drowsiness_detected"
    assert alarm.calls == 1
    assert state.event_count == 1
    assert state.alert_active is True


def test_short_eye_closure_does_not_trigger_alarm():
    state = DrowsinessState()
    router = DummyRouter()
    alarm = DummyAlarm()

    update_drowsiness_state(
        ear=0.30,
        current_time=0.0,
        state=state,
        ear_threshold=0.21,
        drowsy_time_threshold=1.5,
        blink_frame_threshold=2,
        router=router,
        alarm=alarm,
    )

    for i in range(1, 6):
        update_drowsiness_state(
            ear=0.10,
            current_time=float(i) * 0.2,
            state=state,
            ear_threshold=0.21,
            drowsy_time_threshold=1.5,
            blink_frame_threshold=2,
            router=router,
            alarm=alarm,
        )

    assert router.logs == []
    assert router.alerts == []
    assert alarm.calls == 0
    assert state.event_count == 0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
