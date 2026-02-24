import json

import redis.asyncio as aioredis

from .config import setup_logger

logger = setup_logger("redis_client")

QUEUE_KEY = "exec:queue"
DEDUP_TTL = 86400  # 24 hours

_client: aioredis.Redis | None = None


def get_client() -> aioredis.Redis:
    if _client is None:
        raise RuntimeError("Redis client not initialized")
    return _client


async def create_client(redis_url: str) -> aioredis.Redis:
    global _client
    _client = aioredis.from_url(redis_url, decode_responses=True)
    await _client.ping()
    logger.info("Redis connected")
    return _client


async def close_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        logger.info("Redis closed")


async def push_thread(channel_id: str, thread_ts: str, channel_name: str = "") -> bool:
    """Enqueue a thread for extraction. Returns False if already queued/processed."""
    client = get_client()
    dedup_key = f"processed:{channel_id}:{thread_ts}"
    already = await client.get(dedup_key)
    if already:
        return False
    await client.setex(dedup_key, DEDUP_TTL, "1")
    payload = json.dumps({
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "channel_name": channel_name,
    })
    await client.rpush(QUEUE_KEY, payload)
    logger.debug(f"Queued thread {thread_ts} from {channel_id}")
    return True


async def pop_thread(timeout: int = 1) -> dict | None:
    """Block-pop one thread from the queue. Returns None on timeout."""
    client = get_client()
    result = await client.blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    _, raw = result
    return json.loads(raw)
