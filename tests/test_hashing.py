"""Tests for backup_tool.hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backup_tool.errors import HashError
from backup_tool.hashing import DEFAULT_CHUNK_SIZE, HashResult, hash_file


def test_default_chunk_size_is_one_megabyte():
    assert DEFAULT_CHUNK_SIZE == 1024 * 1024


def test_hash_result_fields():
    result = HashResult("abc", 3, 1)
    assert result.hash_hex == "abc"
    assert result.size == 3
    assert result.chunks_read == 1


def test_streaming_hash_matches_hashlib(tmp_path: Path):
    path = tmp_path / "big.txt"
    data = (b"abc123" * 10000) + b"tail"
    path.write_bytes(data)

    result = hash_file(path, chunk_size=17)

    assert result.hash_hex == hashlib.sha256(data).hexdigest()
    assert result.size == len(data)
    assert result.chunks_read > 1


def test_hash_file_empty_file(tmp_path: Path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    result = hash_file(path)
    assert result.hash_hex == hashlib.sha256(b"").hexdigest()
    assert result.size == 0
    assert result.chunks_read == 0


def test_hash_file_missing_path_raises(tmp_path: Path):
    with pytest.raises(HashError, match="Could not hash"):
        hash_file(tmp_path / "missing.bin")
