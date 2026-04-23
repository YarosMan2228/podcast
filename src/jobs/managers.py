from __future__ import annotations

from django.db import models as djmodels

from jobs.enums import ArtifactStatus


class ArtifactManager(djmodels.Manager):
    def ready_for_job(self, job_id):
        return self.filter(job_id=job_id, status=ArtifactStatus.READY)

    def pending_for_job(self, job_id):
        return self.filter(
            job_id=job_id,
            status__in=[ArtifactStatus.QUEUED, ArtifactStatus.PROCESSING],
        )

    def failed_for_job(self, job_id):
        return self.filter(job_id=job_id, status=ArtifactStatus.FAILED)
