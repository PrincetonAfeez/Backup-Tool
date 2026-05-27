"""Content-addressable backup tool library."""

from __future__ import annotations

from pathlib import Path

from backup_tool.repository import Repository

__all__ = ["Repository"]


def _resolve_version() -> str:
    try:
        from importlib.metadata import version

        return version("backup-tool")
    except Exception:
        pass

    import tomllib

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


__version__ = _resolve_version()
