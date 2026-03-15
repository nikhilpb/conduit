import os
from pathlib import Path
import tempfile


# `conduit.main` constructs the app at import time, so tests need this override
# in place before pytest imports the test modules themselves.
_DEFAULT_SCHEDULED_CONFIG_PATH = (
    Path(tempfile.mkdtemp(prefix="conduit-scheduled-config-"))
    / "scheduled_sessions.yaml"
)
_DEFAULT_SCHEDULED_CONFIG_PATH.write_text("scheduled_sessions: []\n")
os.environ["CONDUIT_SCHEDULED_SESSIONS_CONFIG_PATH"] = str(
    _DEFAULT_SCHEDULED_CONFIG_PATH
)
