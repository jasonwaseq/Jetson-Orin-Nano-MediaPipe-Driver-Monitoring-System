import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.module_alert_continuity import AlertContinuityTracker


class DummyRouter:
    def __init__(self):
        self.alerts = []

    def emit_alert(self, code, message, severity="warning", **data):
        self.alerts.append((code, message, severity, data))


def test_active_updates_are_throttled_and_include_duration():
    router = DummyRouter()
    tracker = AlertContinuityTracker(router, update_interval_sec=1.0)

    tracker.update_condition(
        condition="drowsiness",
        active=True,
        current_time=10.0,
        active_since=8.5,
        severity="critical",
    )
    tracker.update_condition(
        condition="drowsiness",
        active=True,
        current_time=10.5,
        active_since=8.5,
        severity="critical",
    )
    tracker.update_condition(
        condition="drowsiness",
        active=True,
        current_time=11.0,
        active_since=8.5,
        severity="critical",
    )

    assert [alert[0] for alert in router.alerts] == [
        "drowsiness_active",
        "drowsiness_active",
    ]
    assert router.alerts[0][2] == "critical"
    assert router.alerts[0][3]["alert_status"] == "active"
    assert router.alerts[0][3]["unrecovered"] is True
    assert router.alerts[0][3]["active_duration_sec"] == 1.5
    assert router.alerts[1][3]["active_duration_sec"] == 2.5
    assert router.alerts[1][3]["active_update_count"] == 2


def test_recovery_event_emits_once_when_condition_clears():
    router = DummyRouter()
    tracker = AlertContinuityTracker(router, update_interval_sec=1.0)

    tracker.update_condition(
        condition="out_of_frame",
        active=True,
        current_time=5.0,
        active_since=3.0,
    )
    tracker.update_condition(
        condition="out_of_frame",
        active=False,
        current_time=8.0,
    )
    tracker.update_condition(
        condition="out_of_frame",
        active=False,
        current_time=9.0,
    )

    assert [alert[0] for alert in router.alerts] == [
        "out_of_frame_active",
        "out_of_frame_recovered",
    ]
    assert router.alerts[1][2] == "info"
    assert router.alerts[1][3]["alert_status"] == "recovered"
    assert router.alerts[1][3]["unrecovered"] is False
    assert router.alerts[1][3]["active_duration_sec"] == 5.0
