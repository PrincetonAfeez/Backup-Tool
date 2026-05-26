"""Tests for backup_tool package exports."""

from backup_tool import Repository, __all__, __version__


def test_version_is_string():
    assert isinstance(__version__, str)
    assert __version__ == "0.1.0"


def test_public_exports():
    assert __all__ == ["Repository"]
    assert Repository is not None
