"""Overrides core.settings for tests — SQLite in-memory, no Postgres needed."""
import tempfile
from pathlib import Path

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

# In-process cache — the regenerate rate limiter must not need Redis in
# tests. conftest clears it between tests.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# Keep test output out of the repo's ``media/`` directory. Tests that
# exercise file-producing code either use ``tmp_path`` explicitly or fall
# through to this throwaway directory instead of littering ``media/packages``.
MEDIA_ROOT = tempfile.mkdtemp(prefix="podcast_pack_test_media_")
ARTIFACTS_ROOT = str(Path(MEDIA_ROOT) / "artifacts")
