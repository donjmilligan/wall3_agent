"""bin/* wrappers resolve the repo from their own location and exec `uv run`."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPERS = {
    "slack": ("clients/slack", "slack"),
    "clickup": ("clients/clickup", "clickup"),
    "inbox-rank": ("tools/messaging", "inbox-rank"),
    "check-draft": ("tools/messaging", "check-draft"),
}


@pytest.fixture
def fake_uv_path(tmp_path: Path) -> str:
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    uv.chmod(0o755)
    return f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin"


def run(program: Path, cwd: Path, path: str, *args: str) -> list[str]:
    proc = subprocess.run(
        [str(program), *args],
        cwd=cwd,
        env={**os.environ, "PATH": path},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_wrapper_execs_uv_with_project(name, tmp_path, fake_uv_path):
    project, script = WRAPPERS[name]
    argv = run(REPO / "bin" / name, tmp_path, fake_uv_path, "inbox", "--json", "a b")
    assert argv == [
        "run",
        "--quiet",
        "--project",
        str(REPO / project),
        script,
        "inbox",
        "--json",
        "a b",
    ]


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_wrapper_resolves_through_a_symlink(name, tmp_path, fake_uv_path):
    link = tmp_path / "elsewhere" / name
    link.parent.mkdir()
    link.symlink_to(REPO / "bin" / name)
    argv = run(link, tmp_path, fake_uv_path, "whoami")
    assert argv[3] == str(REPO / WRAPPERS[name][0])


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_wrapper_is_executable(name):
    path = REPO / "bin" / name
    assert os.access(path, os.X_OK)
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-s", f"bin/{name}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    if tracked:
        assert tracked.startswith("100755"), tracked
