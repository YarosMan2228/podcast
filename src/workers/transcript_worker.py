"""TRANSCRIPT artifact worker — plain text + SRT + VTT exports.

Deterministic (no LLM): reads ``Transcript.segments_json`` and writes

* ``text_content``  — readable ``[MM:SS] paragraph`` transcript
* ``file_path``     — ``artifacts/<job>/transcript.srt``
* ``metadata_json.files`` — ``{"srt": <rel>, "vtt": <rel>}`` so the API can
  expose one URL per format and the packager can ship both.

Runs on the ``text_artifacts`` queue because it is I/O-light and must not
wait behind ffmpeg renders. Failure policy mirrors the text workers: a
missing transcript row is permanent, IO errors get the short retry ladder.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from core.celery import celery_app
from jobs.models import Artifact, ArtifactStatus
from pipeline.subtitles import build_plain_transcript, build_srt, build_vtt
from workers.text_artifact_worker import (
    _mark_failed,
    _mark_processing,
    _mark_ready,
    is_permanent_error,
    retry_countdown_sec,
)

logger = logging.getLogger(__name__)


def render_transcript_files(artifact: Artifact) -> tuple[str, dict[str, Any], str]:
    """Write SRT/VTT for *artifact*'s job; return ``(text, metadata, srt_rel)``."""
    transcript = artifact.job.transcript
    segments = list(transcript.segments_json or [])

    out_dir = Path(settings.ARTIFACTS_ROOT) / str(artifact.job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "transcript.srt"
    vtt_path = out_dir / "transcript.vtt"
    srt_path.write_text(build_srt(segments), encoding="utf-8")
    vtt_path.write_text(build_vtt(segments), encoding="utf-8")

    media_root = Path(settings.MEDIA_ROOT)
    srt_rel = srt_path.relative_to(media_root).as_posix()
    vtt_rel = vtt_path.relative_to(media_root).as_posix()

    text = build_plain_transcript(segments) or (transcript.full_text or "")
    metadata: dict[str, Any] = {
        "language": transcript.language,
        "word_count": len((transcript.full_text or "").split()),
        "segment_count": len(segments),
        "duration_sec": transcript.duration_sec,
        "files": {"srt": srt_rel, "vtt": vtt_rel},
    }
    return text, metadata, srt_rel


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=330,
    acks_late=True,
    queue="text_artifacts",
)
def generate_transcript(self, artifact_id: str) -> None:
    """Produce the TRANSCRIPT artifact for *artifact_id*."""
    logger.info(
        "task_started", extra={"task": "generate_transcript", "artifact_id": artifact_id}
    )
    artifact: Artifact | None = None
    try:
        artifact = Artifact.objects.select_related("job__transcript").get(id=artifact_id)
        _mark_processing(artifact)
        text, metadata, srt_rel = render_transcript_files(artifact)
        Artifact.objects.filter(id=artifact.id).update(file_path=srt_rel)
        _mark_ready(artifact, text, metadata)
        logger.info(
            "task_completed",
            extra={"task": "generate_transcript", "artifact_id": artifact_id},
        )
    except SoftTimeLimitExceeded:
        if artifact is not None:
            _mark_failed(artifact, "TRANSCRIPT_TIMEOUT: soft_time_limit exceeded")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "task_failed", extra={"task": "generate_transcript", "artifact_id": artifact_id}
        )
        is_final = self.request.retries >= self.max_retries or is_permanent_error(exc)
        if is_final:
            if artifact is not None:
                _mark_failed(artifact, f"{type(exc).__name__}: {exc}")
            return
        raise self.retry(exc=exc, countdown=retry_countdown_sec(self.request.retries))
