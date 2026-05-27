"""Optional in-tree lint gate (skipped when dev tools are not installed)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run_module(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which(sys.executable) is None, reason="python unavailable")
def test_ruff_clean_tree():
    if _run_module("ruff", "--version").returncode != 0:
        pytest.skip("ruff not installed")

    result = _run_module("ruff", "check", "backup_tool", "tests")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which(sys.executable) is None, reason="python unavailable")
def test_mypy_clean_backup_tool():
    if _run_module("mypy", "--version").returncode != 0:
        pytest.skip("mypy not installed")

    result = _run_module("mypy", "backup_tool")
    assert result.returncode == 0, result.stdout + result.stderr
