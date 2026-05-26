"""Allow `python -m backup_tool` to run the CLI."""

from backup_tool.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
