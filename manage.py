#!/usr/bin/env python
"""Django management entrypoint.

Adds `src/` to sys.path so apps are importable as `core`, `api`, etc.,
rather than `src.core`, `src.api`. This keeps imports short and matches
`celery -A core worker` in docker-compose.
"""
import os
import sys
from pathlib import Path


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    src_dir = base_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Install deps: pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
