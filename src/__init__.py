"""Marks `src` as a Python package.

That lets every entry point be run the same way, from the repository root:

    python -m src.main
    python -m src.examples.read_only

and lets the modules import each other with plain `from src.config import ...`
without any `sys.path` tricks.
"""
