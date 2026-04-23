"""Sanity tests: settings, URLs and Celery app load without errors."""
from __future__ import annotations

from django.conf import settings
from django.urls import resolve


def test_settings_loaded() -> None:
    assert settings.ROOT_URLCONF == "core.urls"
    assert "api.apps.ApiConfig" in settings.INSTALLED_APPS
    assert "jobs.apps.JobsConfig" in settings.INSTALLED_APPS
    assert settings.MAX_UPLOAD_SIZE_BYTES == settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    assert settings.REST_FRAMEWORK["EXCEPTION_HANDLER"].endswith(
        "structured_exception_handler"
    )


def test_health_url_resolves() -> None:
    match = resolve("/api/health")
    assert match.url_name == "health"


def test_celery_app_importable() -> None:
    from core import celery_app

    assert celery_app.main == "podcastpack"


def test_jobs_app_models_registered() -> None:
    """All four models are visible in Django's registry under the `jobs` label."""
    from django.apps import apps

    registered = {m.__name__ for m in apps.get_app_config("jobs").get_models()}
    assert registered == {"Job", "Transcript", "Analysis", "Artifact"}
