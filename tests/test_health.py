"""Health endpoint — smoke test of DRF routing + settings boot."""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_health_returns_200_and_ok(client: APIClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_only_get_allowed(client: APIClient) -> None:
    response = client.post("/api/health")

    assert response.status_code == 405
    # exception_handler wraps DRF's MethodNotAllowed into the SPEC envelope.
    assert "error" in response.json()
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_unknown_endpoint_returns_404(client: APIClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
