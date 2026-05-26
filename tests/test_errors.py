"""Tests for custom exception hierarchy."""

import pytest

from backup_tool.errors import (
    BackupToolError,
    HashError,
    IntegrityError,
    LockError,
    ManifestError,
    RepositoryError,
    RestoreError,
    StoreError,
)


@pytest.mark.parametrize(
    "exc_type",
    [
        HashError,
        StoreError,
        ManifestError,
        RestoreError,
        IntegrityError,
        RepositoryError,
        LockError,
    ],
)
def test_expected_errors_inherit_from_base(exc_type):
    assert issubclass(exc_type, BackupToolError)
    exc = exc_type("message")
    assert str(exc) == "message"
    assert isinstance(exc, Exception)
