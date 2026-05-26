"""Tests for repo.json metadata validation."""

from __future__ import annotations

from datetime import UTC, datetime

from backup_tool.repo_metadata import (
    CHUNKING,
    default_repo_metadata,
    format_created_at,
    validate_repo_metadata,
)


def test_format_created_at_uses_microsecond_precision():
    ts = format_created_at(datetime(2026, 1, 15, 12, 30, 45, 123456, tzinfo=UTC))
    assert ts == "2026-01-15T12:30:45.123456Z"


def test_default_repo_metadata_includes_expected_fields():
    metadata = default_repo_metadata()
    assert metadata["chunking"] == CHUNKING
    assert validate_repo_metadata(metadata) == []


def test_validate_repo_metadata_reports_all_mismatches():
    errors = validate_repo_metadata(
        {
            "version": 99,
            "hash_algorithm": "md5",
            "storage": "filesystem",
            "object_layout": "flat",
            "chunking": "none",
        }
    )
    assert len(errors) == 5


def test_validate_repo_metadata_rejects_non_object_root():
    errors = validate_repo_metadata([])
    assert errors == ["Repository metadata root must be an object"]
