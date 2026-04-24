"""``python manage.py run_pipeline <media_file>`` — demo smoke-test harness.

Bypasses the HTTP upload view and Celery broker: copies a local file into
``MEDIA_ROOT/uploads/<job_id>/`` the same way the upload view does, then
drives ``start_job`` in Celery's eager mode so the full chain runs inline
(ingestion → transcription → analysis → orchestrate → video clips).

Used for Day 3 end-to-end verification ("one real clip from one real
file") and demo prep before wifi dies. Requires ffmpeg on PATH and valid
``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` in the environment — this
command does NOT stub external APIs.
"""
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from jobs.models import Artifact, Job, JobStatus, SourceType


class Command(BaseCommand):
    help = "Run the full pipeline on a local media file (eager Celery, real APIs)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("media_path", help="Path to an audio/video file to process")
        parser.add_argument(
            "--mime",
            default=None,
            help="Override MIME type (defaults to guess-by-extension)",
        )

    def handle(self, *args, media_path: str, mime: str | None, **opts) -> None:
        src = Path(media_path).expanduser().resolve()
        if not src.exists():
            raise CommandError(f"File not found: {src}")
        if not src.is_file():
            raise CommandError(f"Not a regular file: {src}")

        # Flip Celery into inline execution for this process only — so we
        # don't need a running broker to validate the chain end-to-end.
        # Matches the pattern used by tests.settings_test.
        from core import celery as celery_module
        celery_module.celery_app.conf.update(
            task_always_eager=True, task_eager_propagates=True
        )

        job_id = uuid.uuid4()
        upload_dir = Path(settings.MEDIA_ROOT) / "uploads" / str(job_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / src.name
        shutil.copy2(src, dest)

        mime_type = mime or self._guess_mime(src)
        job = Job.objects.create(
            id=job_id,
            source_type=SourceType.FILE,
            original_filename=src.name,
            raw_media_path=str(dest),
            file_size_bytes=dest.stat().st_size,
            mime_type=mime_type,
        )
        self.stdout.write(self.style.NOTICE(f"Created Job {job.id} ({mime_type})"))

        # Import lazily: the Celery task module pulls in ffmpeg helpers
        # we don't need for ``--help``.
        from workers.tasks import start_job

        self.stdout.write("Running pipeline (eager)... this may take a few minutes.")
        start_job.apply_async(args=[str(job.id)])

        job.refresh_from_db()
        self._report(job)

        # Non-zero exit so CI / shell scripts can gate on the result.
        if job.status == JobStatus.FAILED:
            sys.exit(1)

    # ----- helpers -----

    @staticmethod
    def _guess_mime(path: Path) -> str:
        ext = path.suffix.lower().lstrip(".")
        return {
            "mp4": "video/mp4",
            "mov": "video/quicktime",
            "mkv": "video/x-matroska",
            "webm": "video/webm",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "m4a": "audio/mp4",
            "ogg": "audio/ogg",
            "flac": "audio/flac",
        }.get(ext, "application/octet-stream")

    def _report(self, job: Job) -> None:
        self.stdout.write("")
        self.stdout.write(f"Job status: {job.status}")
        if job.error:
            self.stdout.write(self.style.ERROR(f"Error: {job.error}"))

        artifacts = list(Artifact.objects.filter(job=job).order_by("type", "index"))
        if not artifacts:
            self.stdout.write("No artifacts created.")
            return

        self.stdout.write(f"Artifacts ({len(artifacts)}):")
        for art in artifacts:
            line = f"  [{art.status}] {art.type}#{art.index}"
            if art.file_path:
                line += f" → {art.file_path}"
            if art.error:
                line += f" ERROR: {art.error}"
            self.stdout.write(line)
