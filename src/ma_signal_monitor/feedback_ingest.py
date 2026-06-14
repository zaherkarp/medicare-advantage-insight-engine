"""Pull crowd feedback from giscus-backed GitHub Discussions.

The public static site mounts a giscus thread on each story page with
``mapping: specific`` and the term set to the story's stable ``item_id`` — so
giscus creates one Discussion per story whose **title is the item_id**. This
module reads those Discussions' reactions back via the GitHub GraphQL API and
records them as advisory (``channel="github"``) feedback rows.

Ingest is idempotent: each reaction is stored under a ``source_ref`` of
``reaction:<databaseId>``, and the unique index on ``(channel, source_ref)``
makes re-runs no-ops. Owner verdicts (weight 1.0) are unaffected.
"""

import json
import logging
import os

import requests

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.storage import VALID_VERDICTS, StateStore

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

    Args:
        config: App config (giscus repo/category settings).
        store: Open StateStore to write feedback into.
        token: GitHub token; falls back to the ``GITHUB_TOKEN`` env var.

    Returns:
        Summary counts: discussions scanned, reactions matched to stories,
        and rows newly recorded (idempotent — re-runs report 0 recorded).

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
    timeout = config.request_timeout
    scanned = matched = recorded = 0
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
                before = store.count_feedback()
                store.add_feedback(
                    item_id,
                    verdict,
                    channel="github",
                    voter_key=(r.get("user") or {}).get("login"),
                    source_ref=f"reaction:{r['databaseId']}",
                )
                if store.count_feedback() > before:
                    recorded += 1
        page = conn["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    summary = {
        "discussions": scanned,
        "reactions_matched": matched,
        "recorded": recorded,
    }
    logger.info("giscus feedback ingest: %s", summary)
    return summary


def ingest_ntfy_feedback(
    config: AppConfig,
    store: StateStore,
    since: str = "all",
) -> dict:
    """Pull owner votes from the ntfy feedback topic into ``feedback``.

    ntfy alerts carry 👍/👎 buttons that publish ``{item_id, verdict}`` to the
    configured feedback topic. This polls that topic's JSON endpoint and records
    each vote as an owner verdict (``channel="ntfy"``, weight 1.0).

    Args:
        config: App config (ntfy feedback settings).
        store: Open StateStore to write feedback into.
        since: ntfy ``since`` selector (timestamp, message id, duration, or
            "all"). Idempotency is by message id, so re-polling is safe.

    Returns:
        Summary counts: messages scanned and rows newly recorded.

    Raises:
        ValueError: If the ntfy feedback topic isn't configured.
    """
    if not config.ntfy_feedback_enabled:
        raise ValueError("ntfy feedback is not configured (set NTFY_FEEDBACK_TOPIC)")

    url = f"{config.ntfy_server.rstrip('/')}/{config.ntfy_feedback_topic}/json"
    resp = requests.get(
        url, params={"poll": "1", "since": since}, timeout=config.request_timeout
    )
    resp.raise_for_status()

    scanned = recorded = 0
    # ntfy's JSON endpoint streams one JSON object per line (not an array).
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("event") != "message":
            continue
        scanned += 1
        try:
            vote = json.loads(msg.get("message", ""))
            item_id, verdict = vote["item_id"], vote["verdict"]
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning(
                "Skipping unparseable ntfy feedback message %s", msg.get("id")
            )
            continue
        if verdict not in VALID_VERDICTS or store.get_story(item_id) is None:
            continue
        before = store.count_feedback()
        store.add_feedback(
            item_id,
            verdict,
            channel="ntfy",
            source_ref=f"ntfy:{msg.get('id')}",
        )
        if store.count_feedback() > before:
            recorded += 1

    summary = {"messages": scanned, "recorded": recorded}
    logger.info("ntfy feedback ingest: %s", summary)
    return summary
