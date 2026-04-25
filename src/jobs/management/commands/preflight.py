"""Preflight check — run BEFORE bringing the worker/web up.

Exits 0 when ready, 1 when any issue is found. Useful as a docker-compose
healthcheck, a CI smoke test, or just a quick local sanity check:

    docker compose run --rm app python manage.py preflight
    docker compose run --rm app python manage.py preflight --probe
"""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from services.preflight import check_api_keys


class Command(BaseCommand):
    help = "Validate external API keys (placeholder + optional vendor probe)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Also make a tiny call to OpenAI/Anthropic to verify the key works.",
        )

    def handle(self, *args, **options) -> None:
        probe = bool(options["probe"])
        issues = check_api_keys(probe_network=probe)

        if not issues:
            mode = "structural + network" if probe else "structural"
            self.stdout.write(
                self.style.SUCCESS(f"preflight OK ({mode}) — API keys look valid")
            )
            return

        self.stderr.write(self.style.ERROR(f"preflight FAILED — {len(issues)} issue(s):"))
        for issue in issues:
            self.stderr.write(f"  - {issue['key']}: {issue['reason']}")
        sys.exit(1)
