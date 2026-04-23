"""Overrides core.settings for tests — SQLite in-memory, no Postgres needed."""
from core.settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Eager Celery — tasks run inline, no Redis required.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# No Redis in unit tests; `publish()` short-circuits to a no-op.
EVENTS_ENABLED = False

# The Day-1 start_job stub sleeps 3s in prod; zero it so tests don't pause.
START_JOB_STUB_SLEEP_SEC = 0
