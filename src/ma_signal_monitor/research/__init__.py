"""Retrieval-research subsystem.

A one-way dependency: research code may import the application, the
application never imports research code. Research code never writes to
``state.db`` (docs/goal.md Guardrail 7). Plans and decisions live in
``docs/research/``.
"""
