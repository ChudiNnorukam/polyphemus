from typing import Any

import asyncpg

from .config import setup_logger

logger = setup_logger("database")

_pool: asyncpg.Pool | None = None


async def create_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    logger.info("Database pool created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        logger.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool


async def fetch_all(sql: str, *args) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def fetch_one(sql: str, *args) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def execute(sql: str, *args) -> str:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def fetch_val(sql: str, *args) -> Any:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def upsert_channel(slack_channel_id: str, name: str) -> int:
    sql = """
        INSERT INTO channels (slack_channel_id, name)
        VALUES ($1, $2)
        ON CONFLICT (slack_channel_id) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """
    return await fetch_val(sql, slack_channel_id, name)


async def upsert_thread(channel_id: int, slack_thread_ts: str, last_message_ts: str) -> int:
    sql = """
        INSERT INTO threads (channel_id, slack_thread_ts, last_message_ts)
        VALUES ($1, $2, $3)
        ON CONFLICT (channel_id, slack_thread_ts)
        DO UPDATE SET last_message_ts = EXCLUDED.last_message_ts
        RETURNING id
    """
    return await fetch_val(sql, channel_id, slack_thread_ts, last_message_ts)


async def mark_thread_processed(thread_id: int, prompt_version: str) -> None:
    sql = """
        UPDATE threads
        SET processed_at = NOW(), prompt_version = $2
        WHERE id = $1
    """
    await execute(sql, thread_id, prompt_version)


async def insert_extraction(
    thread_id: int,
    type_: str,
    text: str,
    attributed_to: str | None,
    confidence: float,
    prompt_version: str,
) -> int:
    sql = """
        INSERT INTO extractions (thread_id, type, text, attributed_to, confidence, prompt_version)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    """
    return await fetch_val(sql, thread_id, type_, text, attributed_to, confidence, prompt_version)


async def insert_action(extraction_id: int, assignee_id: str | None, due_date) -> int:
    sql = """
        INSERT INTO actions (extraction_id, assignee_id, due_date)
        VALUES ($1, $2, $3)
        RETURNING id
    """
    return await fetch_val(sql, extraction_id, assignee_id, due_date)
