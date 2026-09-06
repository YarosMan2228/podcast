"""AccessKey — a named per-user credential (multi-user mode, SECURITY.md #6).

The raw key is shown once at creation and only its SHA-256 lives in the
DB. Every ``Job`` created with a key gets ``owner`` = that key, and every
job-scoped endpoint (list / get / delete / download / SSE / regenerate /
media) is filtered by it. The env ``APP_ACCESS_TOKEN`` stays the master
credential that sees everything (ops / demo driver).
"""
from __future__ import annotations

import uuid

from django.db import models as djmodels


class AccessKey(djmodels.Model):
    id = djmodels.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = djmodels.CharField(max_length=80)
    key_hash = djmodels.CharField(max_length=64, unique=True)
    is_active = djmodels.BooleanField(default=True)
    created_at = djmodels.DateTimeField(auto_now_add=True)
    last_used_at = djmodels.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "access_keys"

    def __str__(self) -> str:
        return f"AccessKey({self.name} · {'active' if self.is_active else 'revoked'})"
