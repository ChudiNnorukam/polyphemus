from datetime import datetime

from slack_sdk.web.async_client import AsyncWebClient

from .config import setup_logger

logger = setup_logger("slack_client")

_client: AsyncWebClient | None = None


def init_client(bot_token: str) -> AsyncWebClient:
    global _client
    _client = AsyncWebClient(token=bot_token)
    return _client


def get_client() -> AsyncWebClient:
    if _client is None:
        raise RuntimeError("Slack client not initialized")
    return _client


async def fetch_thread(channel_id: str, thread_ts: str) -> list[dict]:
    """Fetch all messages in a thread, paginating as needed."""
    client = get_client()
    messages = []
    cursor = None

    while True:
        kwargs = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": 200,
            "inclusive": True,
        }
        if cursor:
            kwargs["cursor"] = cursor

        resp = await client.conversations_replies(**kwargs)
        messages.extend(resp["messages"])

        if not resp.get("has_more"):
            break
        cursor = resp["response_metadata"]["next_cursor"]

    return messages


async def get_channel_name(channel_id: str) -> str:
    """Look up the channel name from its ID."""
    client = get_client()
    try:
        resp = await client.conversations_info(channel=channel_id)
        return resp["channel"].get("name", channel_id)
    except Exception:
        return channel_id


def format_thread_for_llm(messages: list[dict]) -> str:
    """Convert raw Slack messages into a readable transcript for the LLM."""
    lines = []
    for msg in messages:
        user = msg.get("user", "unknown")
        text = msg.get("text", "").strip()
        if not text:
            continue
        ts = msg.get("ts", "")
        try:
            dt = datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            dt = ts
        lines.append(f"{user} ({dt}): {text}")
    return "\n".join(lines)
