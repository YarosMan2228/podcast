from django.urls import path

from api.views import health, upload

urlpatterns = [
    path("health", health.health, name="health"),
    path("jobs/upload", upload.upload, name="jobs_upload"),
]
