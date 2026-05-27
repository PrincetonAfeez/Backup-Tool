"""Tests for backup_tool package exports."""

from pathlib import Path

import tomllib

from backup_tool import Repository, __all__, __version__


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_version_is_string():
    assert isinstance(__version__, str)
    assert __version__ == _pyproject_version()


def test_public_exports():
    assert __all__ == ["Repository"]
    assert Repository is not None


def test_resolve_version_unknown_when_both_sources_fail(monkeypatch):
    import backup_tool

    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _name: (_ for _ in ()).throw(Exception("missing")),
    )
    monkeypatch.setattr(
        "tomllib.load",
        lambda _handle: (_ for _ in ()).throw(KeyError("version")),
    )
    assert backup_tool._resolve_version() == "0.0.0+unknown"
