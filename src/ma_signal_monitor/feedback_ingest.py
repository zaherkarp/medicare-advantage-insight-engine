"""Pull feedback from giscus-backed GitHub Discussions.

The public static site mounts a giscus thread on each story page with
``mapping: specific`` and the term set to the story's stable ``item_id`` — so
giscus creates one Discussion per story whose **title is the item_id**. This
module reads those Discussions' reactions back via the GitHub GraphQL API and
records them as feedback rows:

- a reaction by ``config.feedback_owner_login`` (case-insensitive) is an owner
  verdict: ``channel="giscus_owner"``, weight 1.0. This is how the owner votes
  on the public static site, which has no server to take a POST;
- every other reaction is advisory crowd signal: ``channel="github"``,
  weight 0.2.

Ingest is idempotent: each reaction is stored under a ``source_ref`` of
``reaction:<databaseId>``, and the unique index on ``(channel, source_ref)``
makes re-runs no-ops.

It runs in CI on every deploy (see deploy-pages.yml), against the production
archive, so votes land without anyone running a command.
"""

import logging
import os

import requests

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.storage import StateStore

logger = logging.getLogger("ma_signal_monitor.feedback_ingest")

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# giscus reaction content (GitHub ReactionContent enum) → our verdict. Reactions
# not listed (LAUGH, EYES) are intentionally ignored as low-signal.
REACTION_VERDICTS = {
    "THUMBS_UP": "relevant",
    "THUMBS_DOWN": "irrelevant",
    "CONFUSED": "wrong_category",
    "HEART": "great",
    "HOORAY": "great",
    "ROCKET": "great",
}

_DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $categoryId: ID, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 50, after: $cursor, categoryId: $categoryId) {
      pageInfo { hasNextPage endCursor }
      nodes {
        title
        reactions(first: 100) {
          nodes { content databaseId user { login } }
        }
      }
    }
  }
}
"""


def _post_graphql(query: str, variables: dict, token: str, timeout: int) -> dict:
    """Execute a GraphQL request, raising on transport or GraphQL errors."""
    resp = requests.post(
        GITHUB_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {body['errors']}")
    return body["data"]


def ingest_github_feedback(
    config: AppConfig,
    store: StateStore,
    token: str | None = None,
) -> dict:
    """Ingest giscus reactions for the configured repo/category into ``feedback``.

    Reactions by ``config.feedback_owner_login`` are recorded as owner
    verdicts (``giscus_owner``); all others as crowd (``github``).

    Args:
        config: App config (giscus repo/category settings).
        store: Open StateStore to write feedback into.
        token: GitHub token; falls back to the ``GITHUB_TOKEN`` env var.

    Returns:
        Summary counts: discussions scanned, reactions matched to stories,
        rows newly recorded (idempotent — re-runs report 0 recorded), and how
        many of the newly recorded rows were owner verdicts.

    Raises:
        ValueError: If giscus isn't configured or no token is available.
    """
    if not config.giscus_enabled:
        raise ValueError("giscus is not configured (set GISCUS_REPO/_ID/_CATEGORY_ID)")
    token = token or os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("No GitHub token (set GITHUB_TOKEN) for feedback ingest")
    if "/" not in config.giscus_repo:
        raise ValueError(
            f"GISCUS_REPO must be 'owner/name', got: {config.giscus_repo!r}"
        )

    owner, name = config.giscus_repo.split("/", 1)
    owner_login = config.feedback_owner_login.strip().lower()
    timeout = config.request_timeout
    scanned = matched = recorded = recorded_owner = 0
    cursor = None

    while True:
        data = _post_graphql(
            _DISCUSSIONS_QUERY,
            {
                "owner": owner,
                "name": name,
                "categoryId": config.giscus_category_id or None,
                "cursor": cursor,
            },
            token,
            timeout,
        )
        conn = data["repository"]["discussions"]
        for disc in conn["nodes"]:
            scanned += 1
            item_id = disc["title"]
            # The discussion title is the story item_id; skip threads that don't
            # map to a known story (e.g. ad-hoc Discussions in the category).
            if store.get_story(item_id) is None:
                continue
            for r in disc["reactions"]["nodes"]:
                verdict = REACTION_VERDICTS.get(r["content"])
                if verdict is None:
                    continue
                matched += 1
                login = (r.get("user") or {}).get("login")
                is_owner = bool(owner_login) and (login or "").lower() == owner_login
                source_ref = f"reaction:{r['databaseId']}"
                # One reaction is one row, whatever channel it was first filed
                # under; see StateStore.has_feedback_ref.
                if store.has_feedback_ref(source_ref):
                    continue
                before = store.count_feedback()
                store.add_feedback(
                    item_id,
                    verdict,
                    channel="giscus_owner" if is_owner else "github",
                    voter_key=login,
                    source_ref=source_ref,
                )
                if store.count_feedback() > before:
                    recorded += 1
                    if is_owner:
                        recorded_owner += 1
        page = conn["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    summary = {
        "discussions": scanned,
        "reactions_matched": matched,
        "recorded": recorded,
        "recorded_owner": recorded_owner,
    }
    logger.info("giscus feedback ingest: %s", summary)
    return summary
