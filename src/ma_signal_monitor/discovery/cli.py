"""CLI entry points for source discovery.

- ``ma-signal-discover``    run feed autodiscovery over due candidate domains.
- ``ma-signal-candidates``  list / promote / reject candidates, ignore a domain,
                            or print a paste-ready sources.yaml block.

Promotion is a DB overlay: promoted feeds are merged into the live source list
at config load time (see ``config._merge_promoted_sources``), so no YAML edit is
required. Use ``export-yaml`` if you prefer to keep ``sources.yaml`` as truth.
"""

import sys
from pathlib import Path

from ma_signal_monitor.config import load_config
from ma_signal_monitor.discovery.runner import run_discovery
from ma_signal_monitor.storage import StateStore


def _open(root: Path) -> tuple:
    config = load_config(root)
    store = StateStore(root / config.db_path)
    return config, store


def discover_main() -> None:
    """Run autodiscovery on demand (``ma-signal-discover [project_root]``)."""
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    config, store = _open(root)
    try:
        if not config.discovery_enabled:
            print("Discovery is disabled. Set DISCOVERY_ENABLED=true to enable.")
            return
        summary = run_discovery(config, store)
        print(
            f"Discovery: checked {summary['domains_checked']} domain(s), "
            f"found {summary['feeds_found']} feed(s), "
            f"auto-promoted {summary['auto_promoted']}."
        )
    finally:
        store.close()


def _yaml_block(row) -> str:
    return (
        f'  - name: "{row["feed_title"] or row["domain"]}"\n'
        f"    type: rss\n"
        f'    url: "{row["feed_url"]}"\n'
        f"    priority: 2\n"
        f"    enabled: true\n"
        f'    tags: ["discovered"]\n'
        f'    homepage: "https://{row["domain"]}/"\n'
    )


def candidates_main() -> None:
    """List and manage candidate sources.

    Usage:
        ma-signal-candidates [list [status]]
        ma-signal-candidates promote <id>
        ma-signal-candidates reject <id>
        ma-signal-candidates ignore-domain <domain>
        ma-signal-candidates export-yaml <id>
    """
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    root = Path.cwd()
    config, store = _open(root)
    try:
        if cmd == "list":
            status = args[1] if len(args) > 1 else None
            rows = store.list_candidate_sources(status=status, limit=100)
            if not rows:
                print("No candidate sources.")
                return
            print(f"{'ID':>4}  {'SCORE':>6}  {'SEEN':>4}  {'STATUS':<13}  FEED")
            for r in rows:
                print(
                    f"{r['id']:>4}  {r['relevance_score'] or 0:>6.2f}  "
                    f"{r['times_seen']:>4}  {r['status']:<13}  "
                    f"{r['feed_title'] or r['domain']}  ({r['feed_url']})"
                )
        elif cmd in ("promote", "reject") and len(args) > 1:
            cid = int(args[1])
            if store.get_candidate_source(cid) is None:
                print(f"No candidate with id {cid}.")
                sys.exit(1)
            store.set_candidate_status(
                cid, "promoted" if cmd == "promote" else "rejected"
            )
            print(f"Candidate {cid} marked {cmd}d.")
            if cmd == "promote":
                print("It will be fetched on the next run (DISCOVERY_ENABLED=true).")
        elif cmd == "ignore-domain" and len(args) > 1:
            store.set_domain_status(args[1], "ignored")
            print(f"Domain {args[1]} will be ignored by discovery.")
        elif cmd == "export-yaml" and len(args) > 1:
            row = store.get_candidate_source(int(args[1]))
            if row is None:
                print(f"No candidate with id {args[1]}.")
                sys.exit(1)
            print(_yaml_block(row))
        else:
            print(candidates_main.__doc__)
            sys.exit(2)
    finally:
        store.close()
