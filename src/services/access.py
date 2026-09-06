"""Who is calling, and which jobs may they touch (SECURITY.md #3 #6 #19).

Three modes, chosen by env:

* **open** — neither ``APP_ACCESS_TOKEN`` nor ``APP_MULTI_USER`` set. Every
  request is an anonymous admin (localhost dev).
* **single token** — ``APP_ACCESS_TOKEN`` set, ``APP_MULTI_USER`` off. One
  shared secret, everyone sees everything.
* **multi-user** — ``APP_MULTI_USER=1``. Callers present a per-user key
  (``manage.py access_key create --name …``); jobs are owned and scoped.
  ``APP_ACCESS_TOKEN`` (optional) is the master key that sees all jobs.

``Principal`` is attached to the request by ``api.middleware`` and consumed
by the views through :func:`owner_for_new_job`, :func:`scope_jobs` and
:func:`can_access_job`.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from jobs.models import AccessKey, Job


@dataclass(frozen=True)
class Principal:
    """Resolved caller. ``key`` is None for the master token / open mode."""

    key: AccessKey | None = None
    is_admin: bool = True

    @property
    def name(self) -> str:
        if self.key is not None:
            return self.key.name
        return "admin" if master_token() else "anonymous"


ADMIN = Principal(key=None, is_admin=True)


def master_token() -> str:
    return (getattr(settings, "APP_ACCESS_TOKEN", "") or "").strip()


def multi_user_enabled() -> bool:
    return bool(getattr(settings, "APP_MULTI_USER", False))


def gating_enabled() -> bool:
    return bool(master_token()) or multi_user_enabled()


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def create_access_key(name: str) -> tuple[AccessKey, str]:
    """Create a key; return ``(row, raw_secret)`` — the secret is never stored."""
    raw = "pk_" + secrets.token_urlsafe(32)
    row = AccessKey.objects.create(name=name.strip()[:80], key_hash=hash_key(raw))
    return row, raw


def resolve_principal(candidate: str | None) -> Principal | None:
    """Map a presented secret to a Principal, or None if it's not valid."""
    if not candidate:
        return None
    candidate = candidate.strip()
    expected = master_token()
    if expected and hmac.compare_digest(candidate, expected):
        return ADMIN
    if not multi_user_enabled():
        return None
    row = AccessKey.objects.filter(key_hash=hash_key(candidate), is_active=True).first()
    if row is None:
        return None
    # Cheap audit trail; throttled to once a minute per key.
    now = timezone.now()
    if row.last_used_at is None or (now - row.last_used_at).total_seconds() > 60:
        AccessKey.objects.filter(id=row.id).update(last_used_at=now)
    return Principal(key=row, is_admin=False)


def principal_of(request: Any) -> Principal:
    return getattr(request, "principal", None) or ADMIN


def owner_for_new_job(request: Any) -> AccessKey | None:
    return principal_of(request).key


def scope_jobs(request: Any, qs: QuerySet[Job]) -> QuerySet[Job]:
    p = principal_of(request)
    if p.is_admin:
        return qs
    return qs.filter(owner=p.key)


def can_access_job(request: Any, job: Job) -> bool:
    p = principal_of(request)
    if p.is_admin:
        return True
    return job.owner_id == p.key.id if p.key is not None else False


def can_access_media_path(request: Any, rel_path: str) -> bool:
    """Ownership check for ``/media/<rel_path>``.

    Layout: ``uploads/<job_id>/…``, ``artifacts/<job_id>/…``,
    ``packages/<zip>`` (matched against ``Job.package_path``). Anything
    else under MEDIA_ROOT is admin-only in multi-user mode.
    """
    p = principal_of(request)
    if p.is_admin:
        return True
    parts = PurePosixPath(rel_path.replace("\\", "/")).parts
    if len(parts) >= 2 and parts[0] in {"uploads", "artifacts"}:
        return Job.objects.filter(id=parts[1], owner=p.key).exists() if _is_uuid(parts[1]) else False
    if len(parts) == 2 and parts[0] == "packages":
        return Job.objects.filter(package_path=f"packages/{parts[1]}", owner=p.key).exists()
    return False


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False
