import logging
import os

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class JobsConfig(AppConfig):
    name = "jobs"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Print a startup banner reporting preflight status. Doesn't block —
        # we still let the server come up, but ops sees the warning in the
        # very first log line and the upload view will return 503 anyway.
        # Skip in management commands (migrate, makemigrations, test) so a
        # local dev environment without keys doesn't get noisy.
        if os.environ.get("PODCAST_PACK_SKIP_PREFLIGHT") == "1":
            return
        argv1 = (os.sys.argv[1] if len(os.sys.argv) > 1 else "").lower()
        if argv1 in {
            "migrate",
            "makemigrations",
            "collectstatic",
            "test",
            "shell",
            "showmigrations",
            "preflight",  # the dedicated command does its own reporting
        }:
            return

        # Import here so `manage.py` (which calls django.setup()) reaches us
        # without circular-import drama.
        from services.preflight import check_api_keys

        issues = check_api_keys(probe_network=False)
        if not issues:
            logger.info("preflight_ok", extra={"checked": "structural"})
            return
        for issue in issues:
            logger.warning(
                "preflight_issue",
                extra={"key": issue["key"], "reason": issue["reason"]},
            )
