# Release checklist

Version is defined once in `pyproject.toml` under `[project].version`.
`backup_tool.__version__` reads that value via package metadata (or falls back
to `pyproject.toml` when running from an uninstalled source tree).

Before tagging a release:

1. Bump `version` in `pyproject.toml`.
2. Run `pip install -e .` (or rebuild your environment).
3. Confirm `backup-tool version` matches.
4. Run `pytest`, `ruff check backup_tool tests`, and `mypy backup_tool`.

Regression coverage for edge-case behavior lives in `tests/test_edge_cases.py`.
