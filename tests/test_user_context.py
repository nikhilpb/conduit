import os
import time
from datetime import UTC
from datetime import datetime

from conduit.user_context import CURRENT_TIME_STATE_KEY
from conduit.user_context import build_current_time_state_delta


def test_build_current_time_state_delta_preserves_aware_timezone():
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Zurich"
    if hasattr(time, "tzset"):
        time.tzset()

    try:
        state_delta = build_current_time_state_delta(
            datetime(2026, 3, 10, 8, 0, 0, tzinfo=UTC)
        )
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        if hasattr(time, "tzset"):
            time.tzset()

    assert state_delta == {
        CURRENT_TIME_STATE_KEY: "2026-03-10 08:00:00 UTC (UTC+00:00)"
    }
