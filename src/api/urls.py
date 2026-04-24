from django.urls import path

from api.views import health, jobs, upload

urlpatterns = [
    path("health", health.health, name="health"),
    path("jobs/upload", upload.upload, name="jobs_upload"),
    path("jobs/<str:job_id>", jobs.get_job, name="jobs_detail"),
    path("jobs/<str:job_id>/events", jobs.job_events, name="jobs_events"),
]
