"""Structured error envelope per SPEC §1.6.

We prove each ApiError subclass renders through the DRF exception handler
into the exact shape documented in the spec:
    {"error": {"code": "...", "message": "...", "field": "..."}}
"""
from __future__ import annotations

import pytest
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.test import APIClient
from rest_framework.test import URLPatternsTestCase

from api.errors import (
    ApiError,
    NotFound,
    StorageError,
    UploadInvalidFormat,
    UploadNoFile,
    UploadTooLarge,
)


@api_view(["GET"])
def _raise_upload_no_file(request: Request):
    raise UploadNoFile()


@api_view(["GET"])
def _raise_upload_invalid(request: Request):
    raise UploadInvalidFormat(mime="application/pdf")


@api_view(["GET"])
def _raise_too_large(request: Request):
    raise UploadTooLarge(limit_mb=500)


@api_view(["GET"])
def _raise_storage(request: Request):
    raise StorageError()


@api_view(["GET"])
def _raise_not_found(request: Request):
    raise NotFound()


@api_view(["GET"])
def _raise_generic(request: Request):
    raise ApiError()


class ErrorEnvelopeTests(URLPatternsTestCase):
    """Swap URLconf to expose dummy views that raise each error class."""

    urlpatterns = [
        path("boom/upload-no-file", _raise_upload_no_file),
        path("boom/upload-invalid", _raise_upload_invalid),
        path("boom/too-large", _raise_too_large),
        path("boom/storage", _raise_storage),
        path("boom/not-found", _raise_not_found),
        path("boom/generic", _raise_generic),
    ]

    def setUp(self) -> None:
        self.client = APIClient()

    def test_upload_no_file_envelope(self) -> None:
        r = self.client.get("/boom/upload-no-file")
        assert r.status_code == 400
        assert r.json() == {
            "error": {
                "code": "UPLOAD_NO_FILE",
                "message": "Field `file` is required.",
                "field": "file",
            }
        }

    def test_upload_invalid_format_includes_mime(self) -> None:
        r = self.client.get("/boom/upload-invalid")
        assert r.status_code == 400
        body = r.json()
        assert body["error"]["code"] == "UPLOAD_INVALID_FORMAT"
        assert body["error"]["field"] == "file"
        assert "application/pdf" in body["error"]["message"]

    def test_upload_too_large_is_413(self) -> None:
        r = self.client.get("/boom/too-large")
        assert r.status_code == 413
        body = r.json()
        assert body["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert "500MB" in body["error"]["message"]
        assert body["error"]["field"] == "file"

    def test_storage_error_is_500(self) -> None:
        r = self.client.get("/boom/storage")
        assert r.status_code == 500
        assert r.json()["error"]["code"] == "STORAGE_ERROR"

    def test_not_found_is_404(self) -> None:
        r = self.client.get("/boom/not-found")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"
        # Optional field omitted when None.
        assert "field" not in r.json()["error"]

    def test_generic_api_error_defaults(self) -> None:
        r = self.client.get("/boom/generic")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "BAD_REQUEST"


def test_apierror_as_envelope_structure() -> None:
    err = ApiError(code="FOO", message="bar", field="baz", status_code=422)
    assert err.as_envelope() == {"error": {"code": "FOO", "message": "bar", "field": "baz"}}
    assert err.status_code == 422


def test_apierror_envelope_omits_field_when_none() -> None:
    err = ApiError(code="FOO", message="bar")
    assert err.as_envelope() == {"error": {"code": "FOO", "message": "bar"}}
