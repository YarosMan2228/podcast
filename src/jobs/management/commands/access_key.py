"""Manage per-user access keys (multi-user mode, docs/SECURITY.md #6).

    python manage.py access_key create --name "Alice"   # prints the key ONCE
    python manage.py access_key list
    python manage.py access_key revoke <id-or-name>
"""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand, CommandError

from jobs.models import AccessKey
from services.access import create_access_key


class Command(BaseCommand):
    help = "Create / list / revoke per-user access keys (APP_MULTI_USER=1)."

    def add_arguments(self, parser) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        c = sub.add_parser("create", help="create a key and print it once")
        c.add_argument("--name", required=True, help="who this key belongs to")
        sub.add_parser("list", help="list keys")
        r = sub.add_parser("revoke", help="deactivate a key")
        r.add_argument("ref", help="key id (uuid) or exact name")

    def handle(self, *args, **options) -> None:
        action = options["action"]
        if action == "create":
            row, raw = create_access_key(options["name"])
            self.stdout.write(self.style.SUCCESS(f"created key for {row.name!r} (id {row.id})"))
            self.stdout.write("Give the user this secret — it is not stored and cannot be shown again:")
            self.stdout.write(f"  {raw}")
            return
        if action == "list":
            rows = AccessKey.objects.order_by("created_at")
            if not rows.exists():
                self.stdout.write("no access keys")
                return
            for k in rows:
                state = "active" if k.is_active else "REVOKED"
                used = k.last_used_at.isoformat(timespec="minutes") if k.last_used_at else "never"
                self.stdout.write(f"{k.id}  {state:8}  {k.name:30}  jobs={k.jobs.count():<4} last used {used}")
            return
        if action == "revoke":
            ref = options["ref"]
            row = AccessKey.objects.filter(id=ref).first() if _looks_uuid(ref) else None
            if row is None:
                matches = list(AccessKey.objects.filter(name=ref))
                if len(matches) != 1:
                    raise CommandError(f"{ref!r}: expected exactly one key, found {len(matches)}")
                row = matches[0]
            AccessKey.objects.filter(id=row.id).update(is_active=False)
            self.stdout.write(self.style.WARNING(f"revoked {row.name!r} ({row.id})"))
            return
        sys.exit(1)


def _looks_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False
