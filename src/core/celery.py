"""Celery app factory.

`celery -A core worker` discovers this module. Queues match
`.claude/rules/celery-tasks.md §2`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Same sys.path trick as manage.py so `celery -A core` works.
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

from celery import Celery  # noqa: E402

celery_app = Celery("podcastpack")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")

# `autodiscover_tasks` only scans ``<package>/<related_name>.py`` — our
# artifact workers live in dedicated modules (``video_clip_worker.py``
# etc.) and would not be registered at boot. Without these explicit
# scans the worker process boots with only ``workers/tasks.py`` known;
# any direct dispatch of an artifact task (e.g. POST /api/artifacts/:id
# /regenerate) would arrive as an unknown task name. ``autodiscover_tasks``
# is lazy — it hooks `django.setup()`, so this avoids the AppRegistryNotReady
# trap that a direct top-level ``import workers.video_clip_worker`` would hit.
celery_app.autodiscover_tasks(["workers"], related_name="tasks")
celery_app.autodiscover_tasks(["workers"], related_name="video_clip_worker")
celery_app.autodiscover_tasks(["workers"], related_name="text_artifact_worker")
celery_app.autodiscover_tasks(["workers"], related_name="quote_graphic_worker")
celery_app.autodiscover_tasks(["workers"], related_name="packager")
