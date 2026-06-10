"""Iterative source discovery.

Mines the outbound links of stories the pipeline already ingests, ranks the
domains behind them by relevance-weighted frequency, autodiscovers RSS/Atom
feeds on the promising ones, and surfaces them as reviewable candidate sources.
Everything is local — only ``requests`` + ``feedparser`` (already core deps).
"""
