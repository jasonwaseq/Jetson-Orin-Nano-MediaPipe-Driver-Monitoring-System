from dataclasses import dataclass


@dataclass
class _ConditionState:
    active: bool = False
    active_since: float | None = None
    last_update_time: float | None = None
    update_count: int = 0


class AlertContinuityTracker:
    """Emit throttled active/recovered events for ongoing unsafe conditions."""

    def __init__(self, router, update_interval_sec=1.0, emit_initial_active=False):
        self.router = router
        self.update_interval_sec = max(0.1, float(update_interval_sec))
        self.emit_initial_active = bool(emit_initial_active)
        self._conditions = {}

    def update_condition(
        self,
        *,
        condition,
        active,
        current_time,
        active_since=None,
        severity="warning",
        active_code=None,
        recovered_code=None,
        active_message=None,
        recovered_message=None,
        **data,
    ):
        state = self._conditions.setdefault(condition, _ConditionState())
        active_code = active_code or f"{condition}_active"
        recovered_code = recovered_code or f"{condition}_recovered"

        if active:
            if not state.active:
                state.active = True
                state.active_since = active_since if active_since is not None else current_time
                state.last_update_time = None if self.emit_initial_active else current_time
                state.update_count = 0

            since = state.active_since if state.active_since is not None else current_time
            duration = max(0.0, current_time - since)
            should_emit = (
                state.last_update_time is None
                or current_time - state.last_update_time >= self.update_interval_sec
            )
            if should_emit:
                state.update_count += 1
                state.last_update_time = current_time
                message = (
                    active_message(duration)
                    if callable(active_message)
                    else active_message or f"{condition} still active ({duration:.1f}s)"
                )
                self.router.emit_alert(
                    active_code,
                    message,
                    severity=severity,
                    condition=condition,
                    alert_status="active",
                    unrecovered=True,
                    active_duration_sec=round(duration, 3),
                    active_update_count=state.update_count,
                    **data,
                )
            return

        if not state.active:
            return

        since = state.active_since if state.active_since is not None else current_time
        duration = max(0.0, current_time - since)
        message = (
            recovered_message(duration)
            if callable(recovered_message)
            else recovered_message or f"{condition} recovered after {duration:.1f}s"
        )
        self.router.emit_alert(
            recovered_code,
            message,
            severity="info",
            condition=condition,
            alert_status="recovered",
            unrecovered=False,
            active_duration_sec=round(duration, 3),
            active_update_count=state.update_count,
            **data,
        )

        state.active = False
        state.active_since = None
        state.last_update_time = None
        state.update_count = 0


def update_driver_alert_continuity(
    *,
    tracker,
    current_time,
    drowsiness_active,
    drowsiness_since=None,
    drowsiness_event_count=0,
    head_inattention_active,
    head_inattention_since=None,
    head_inattention_count=0,
    out_of_frame_active,
    out_of_frame_since=None,
    out_of_frame_count=0,
):
    tracker.update_condition(
        condition="drowsiness",
        active=drowsiness_active,
        current_time=current_time,
        active_since=drowsiness_since,
        severity="critical",
        active_code="drowsiness_active",
        recovered_code="drowsiness_recovered",
        active_message=lambda duration: (
            f"DROWSINESS STILL ACTIVE (eyes closed {duration:.1f}s)"
        ),
        recovered_message=lambda duration: (
            f"DROWSINESS RECOVERED (eyes reopened after {duration:.1f}s)"
        ),
        event_count=drowsiness_event_count,
    )
    tracker.update_condition(
        condition="head_inattention",
        active=head_inattention_active,
        current_time=current_time,
        active_since=head_inattention_since,
        severity="high",
        active_code="head_inattention_active",
        recovered_code="head_inattention_recovered",
        active_message=lambda duration: (
            f"HEAD INATTENTION STILL ACTIVE (deviated {duration:.1f}s)"
        ),
        recovered_message=lambda duration: (
            f"HEAD INATTENTION RECOVERED (attentive after {duration:.1f}s)"
        ),
        event_count=head_inattention_count,
    )
    tracker.update_condition(
        condition="out_of_frame",
        active=out_of_frame_active,
        current_time=current_time,
        active_since=out_of_frame_since,
        severity="high",
        active_code="user_out_of_frame_active",
        recovered_code="user_out_of_frame_recovered",
        active_message=lambda duration: (
            f"USER STILL OUT OF FRAME (no face {duration:.1f}s)"
        ),
        recovered_message=lambda duration: (
            f"USER BACK IN FRAME (missing for {duration:.1f}s)"
        ),
        event_count=out_of_frame_count,
    )
