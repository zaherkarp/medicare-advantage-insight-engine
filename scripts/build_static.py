#!/usr/bin/env python
"""Build the static site for GitHub Pages.

Usage:
    python scripts/build_static.py [--base-path /my-repo] [--out site]

Reads config + the archive DB from the project root, renders all pages to flat
HTML, and writes them to the output directory.
"""

import argparse
import os
from pathlib import Path

from ma_signal_monitor.config import load_config
from ma_signal_monitor.static_export import build_site
from ma_signal_monitor.storage import StateStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static Pages site.")
    parser.add_argument(
        "--base-path",
        default=os.getenv("STATIC_BASE_PATH", ""),
        help="URL prefix for project Pages, e.g. /my-repo ('' for root/custom domain)",
    )
    parser.add_argument(
        "--out", default=os.getenv("STATIC_OUT", "site"), help="Output directory"
    )
    parser.add_argument("--root", default=".", help="Project root (config + data dir)")
    args = parser.parse_args()

    root = Path(args.root)
    config = load_config(root)
    store = StateStore(root / config.db_path)
    try:
        counts = build_site(store, config, Path(args.out), base_path=args.base_path)
    finally:
        store.close()
    print(f"Built static site at '{args.out}': {counts}")


if __name__ == "__main__":
    main()
