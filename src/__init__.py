"""Marks `src` as a Python package.

The sample ships the same agent twice. Each implementation gets its own
subpackage, and `common/` holds what they share:

    src/common/    .env, the system prompt, the preflight check, the SQL trace
    src/maf/       implementation 1 - Microsoft Agent Framework
    src/foundry/   implementation 2 - Foundry Agent Service

Being a package lets every entry point be run the same way, from the repository
root, and lets the modules import each other without any `sys.path` tricks:

    python -m src.maf.main
    python -m src.maf.examples.read_only
    python -m src.foundry.sync
    python -m src.foundry.main

docs/implementations.md compares the two.
"""
