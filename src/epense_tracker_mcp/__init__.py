"""Expense Tracker MCP Server — package entry point."""

import subprocess
import sys
import os


def main() -> None:
    """Launch the MCP server via the top-level main.py."""
    main_py = os.path.join(os.path.dirname(__file__), "..", "..", "main.py")
    main_py = os.path.abspath(main_py)
    subprocess.run([sys.executable, main_py], check=True)
