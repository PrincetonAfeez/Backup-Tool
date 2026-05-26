"""Tests for backup_tool.atomic."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backup_tool.atomic import atomic_write_json, atomic_write_text, fsync_directory


def test_atomic_write_text_creates_file(tmp_path: Path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_atomic_write_json_writes_sorted_json(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"b": 2, "a": 1})
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {"a": 1, "b": 2}


def test_fsync_directory_without_o_directory(tmp_path: Path):
    with patch("backup_tool.atomic.hasattr", return_value=False):
        fsync_directory(tmp_path)


def test_fsync_directory_swallows_open_errors(tmp_path: Path):
    if not hasattr(os, "O_DIRECTORY"):
        pytest.skip("O_DIRECTORY unavailable")
    with patch("backup_tool.atomic.os.open", side_effect=OSError("nope")):
        fsync_directory(tmp_path)
