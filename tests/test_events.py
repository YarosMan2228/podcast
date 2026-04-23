"""services.events.publish — Redis pub/sub wrapper."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import redis
from django.test import override_settings

from services import events


@pytest.fixture(autouse=True)
def _reset_client():
    events.reset_client()
    yield
    events.reset_client()


def test_publish_returns_false_when_disabled() -> None:
    # Default test settings already have EVENTS_ENABLED=False.
    with patch.object(events, "_get_client") as client_factory:
        assert events.publish("abc", "status_changed", {"status": "PENDING"}) is False
        client_factory.assert_not_called()


@override_settings(EVENTS_ENABLED=True)
def test_publish_sends_json_on_job_channel() -> None:
    fake = MagicMock()
    with patch.object(events, "_get_client", return_value=fake):
        ok = events.publish("abc-123", "status_changed", {"status": "INGESTING"})

    assert ok is True
    fake.publish.assert_called_once()
    channel, message = fake.publish.call_args.args
    assert channel == "job:abc-123"
    body = json.loads(message)
    assert body == {"event": "status_changed", "data": {"status": "INGESTING"}}


@override_settings(EVENTS_ENABLED=True)
def test_publish_allows_empty_payload() -> None:
    fake = MagicMock()
    with patch.object(events, "_get_client", return_value=fake):
        assert events.publish("x", "completed") is True

    _, message = fake.publish.call_args.args
    assert json.loads(message) == {"event": "completed", "data": {}}


@override_settings(EVENTS_ENABLED=True)
def test_publish_swallows_redis_errors() -> None:
    fake = MagicMock()
    fake.publish.side_effect = redis.ConnectionError("redis down")
    with patch.object(events, "_get_client", return_value=fake):
        # Must never raise — a broken Redis cannot block the pipeline.
        assert events.publish("x", "status_changed", {"status": "FAILED"}) is False
