"""Tests for python -m backup_tool entry point."""

from __future__ import annotations

import subprocess
import sys


def test_python_m_backup_tool_version():
    result = subprocess.run(
        [sys.executable, "-m", "backup_tool", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
