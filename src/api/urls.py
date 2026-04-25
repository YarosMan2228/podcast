from django.urls import path

from api.views import health, jobs, upload

urlpatterns = [
    path("health", health.health, name="health"),
    path("jobs/upload", upload.upload, name="jobs_upload"),
    path("jobs/from_url", upload.from_url, name="jobs_from_url"),
    path("jobs/<str:job_id>", jobs.get_job, name="jobs_detail"),
    path("jobs/<str:job_id>/events", jobs.job_events, name="jobs_events"),
    path("jobs/<str:job_id>/download", jobs.download_package, name="jobs_download"),
    path("artifacts/<str:artifact_id>/regenerate", jobs.regenerate_artifact, name="artifact_regenerate"),
]
