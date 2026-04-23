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
celery_app.autodiscover_tasks(["workers"])
