"""jobs.management.commands.run_pipeline — local smoke-test harness.

The command itself is exercised manually against real media during demo
prep; these tests just validate argparse plumbing + mime detection so a
typo in the command doesn't block that flow.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from jobs.management.commands.run_pipeline import Command


# ---------- argparse / existence checks ----------


def test_missing_path_raises_command_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(CommandError, match="File not found"):
        call_command("run_pipeline", str(missing))


def test_directory_argument_raises_command_error(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="Not a regular file"):
        call_command("run_pipeline", str(tmp_path))


# ---------- mime guessing ----------


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        ("mp4", "video/mp4"),
        ("mov", "video/quicktime"),
        ("mp3", "audio/mpeg"),
        ("wav", "audio/wav"),
        ("m4a", "audio/mp4"),
        ("xyz", "application/octet-stream"),  # unknown → generic
    ],
)
def test_guess_mime_by_extension(ext: str, expected: str) -> None:
    assert Command._guess_mime(Path(f"clip.{ext}")) == expected
