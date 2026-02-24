import asyncio
import re

from . import database as db
from .config import Settings, setup_logger

logger = setup_logger("drift")

STOPWORDS = {
    "the", "a", "an", "to", "for", "and", "or", "is", "will", "we",
    "it", "in", "on", "at", "be", "by", "up", "do", "this", "that",
    "with", "have", "has", "need", "should", "would", "could",
}


def extract_keywords(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())
    return [w for w in words if w not in STOPWORDS][:5]


async def run_drift_check(settings: Settings) -> int:
    """
    Check all open actions for drift.
    Returns the number of actions newly marked as drifted.
    """
    threshold = settings.drift_threshold_days

    open_actions = await db.fetch_all(
        """
        SELECT a.id, a.created_at, e.text, e.thread_id
        FROM actions a
        JOIN extractions e ON e.id = a.extraction_id
        WHERE a.status = 'open'
          AND a.created_at < NOW() - ($1 || ' days')::INTERVAL
        """,
        str(threshold),
    )

    if not open_actions:
        logger.debug("No open actions old enough to check for drift")
        return 0

    drifted = 0
    for action in open_actions:
        action_id = action["id"]
        keywords = extract_keywords(action["text"])
        if not keywords:
            continue

        thread_id = action["thread_id"]
        channel_row = await db.fetch_one(
            """
            SELECT c.slack_channel_id
            FROM threads t
            JOIN channels c ON c.id = t.channel_id
            WHERE t.id = $1
            """,
            thread_id,
        )
        if not channel_row:
            continue

        # Build a simple keyword OR pattern for the recent messages search.
        # We store messages in threads table via last_message_ts updates.
        # For keyword matching, check if any thread in the same channel has
        # been updated recently with matching content in extractions.
        keyword_conditions = " OR ".join(
            [f"e.text ILIKE '%{kw}%'" for kw in keywords]
        )
        recent_mention = await db.fetch_val(
            f"""
            SELECT COUNT(*)
            FROM extractions e
            JOIN threads t ON t.id = e.thread_id
            JOIN channels c ON c.id = t.channel_id
            WHERE c.slack_channel_id = $1
              AND t.processed_at > NOW() - ($2 || ' days')::INTERVAL
              AND ({keyword_conditions})
            """,
            channel_row["slack_channel_id"],
            str(threshold),
        )

        if recent_mention == 0:
            days_old = await db.fetch_val(
                "SELECT EXTRACT(DAY FROM NOW() - created_at)::INT FROM actions WHERE id = $1",
                action_id,
            )
            await db.execute(
                "UPDATE actions SET status = 'drifted' WHERE id = $1",
                action_id,
            )
            await db.execute(
                "INSERT INTO drift_events (action_id, days_overdue) VALUES ($1, $2)",
                action_id, days_old or threshold,
            )
            drifted += 1
            logger.info(f"Action {action_id} marked as drifted ({days_old} days old)")

    logger.info(f"Drift check complete: {drifted}/{len(open_actions)} actions drifted")
    return drifted


async def drift_loop(settings: Settings) -> None:
    """Run drift check every hour indefinitely."""
    while True:
        try:
            await run_drift_check(settings)
        except Exception as e:
            logger.error(f"Drift check failed: {e}", exc_info=True)
        await asyncio.sleep(3600)
