"""pytest bootstrap — puts src/ on sys.path so `core.settings` imports cleanly."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402  (needs sys.path tweaks above)


@pytest.fixture(autouse=True)
def _fake_api_keys(settings):
    """Default to real-looking keys for all tests so the upload-view
    preflight gate doesn't reject every fixture as ``SERVICE_NOT_CONFIGURED``.

    Tests that *want* to exercise the preflight path (test_preflight,
    test_upload_preflight_gating) override these explicitly via
    ``@override_settings``.
    """
    settings.OPENAI_API_KEY = "sk-test-fixture-key-not-real"
    settings.ANTHROPIC_API_KEY = "sk-ant-test-fixture-key-not-real"
    # The structural check looks for placeholder substrings; "fixture" is not
    # in the blacklist so these pass.
    yield
