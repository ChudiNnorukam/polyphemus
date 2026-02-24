import asyncio
from datetime import date
from pathlib import Path

import anthropic

from . import database as db
from . import redis_client
from . import slack_client
from .config import Settings, setup_logger

logger = setup_logger("extractor")

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_v1.txt"

EXTRACT_TOOL = {
    "name": "extract_execution_data",
    "description": "Extract decisions, actions, and risks from a Slack thread",
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text":        {"type": "string"},
                        "decided_by":  {"type": ["string", "null"]},
                        "confidence":  {"type": "number"},
                    },
                    "required": ["text", "confidence"],
                },
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text":        {"type": "string"},
                        "assigned_to": {"type": ["string", "null"]},
                        "due_date":    {"type": ["string", "null"]},
                        "confidence":  {"type": "number"},
                    },
                    "required": ["text", "confidence"],
                },
            },
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text":       {"type": "string"},
                        "raised_by":  {"type": ["string", "null"]},
                        "severity":   {"type": "string", "enum": ["low", "medium", "high"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["text", "confidence", "severity"],
                },
            },
        },
        "required": ["decisions", "actions", "risks"],
    },
}


class ExtractionWorker:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._running = False
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._system_prompt = PROMPT_PATH.read_text().strip()

    async def run(self) -> None:
        self._running = True
        logger.info("Extraction worker started")
        while self._running:
            try:
                item = await redis_client.pop_thread(timeout=1)
                if item is None:
                    continue
                await self._process(item)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
        logger.info("Extraction worker stopped")

    def stop(self) -> None:
        self._running = False

    async def _process(self, item: dict) -> None:
        channel_id = item["channel_id"]
        thread_ts = item["thread_ts"]
        channel_name = item.get("channel_name", channel_id)

        logger.info(f"Processing thread {thread_ts} in {channel_id}")

        if self._settings.dry_run:
            logger.info(f"[DRY RUN] Would extract from thread {thread_ts}")
            return

        try:
            messages = await slack_client.fetch_thread(channel_id, thread_ts)
        except Exception as e:
            logger.error(f"Failed to fetch thread {thread_ts}: {e}")
            return

        if len(messages) < 2:
            logger.debug(f"Thread {thread_ts} has < 2 messages, skipping")
            return

        transcript = slack_client.format_thread_for_llm(messages)
        last_ts = messages[-1].get("ts", thread_ts)

        db_channel_id = await db.upsert_channel(channel_id, channel_name)
        thread_id = await db.upsert_thread(db_channel_id, thread_ts, last_ts)

        extracted = await self._call_llm(transcript)
        if extracted is None:
            return

        await self._store(thread_id, extracted)
        await db.mark_thread_processed(thread_id, self._settings.prompt_version)
        logger.info(
            f"Thread {thread_ts}: "
            f"{len(extracted.get('decisions', []))} decisions, "
            f"{len(extracted.get('actions', []))} actions, "
            f"{len(extracted.get('risks', []))} risks"
        )

    async def _call_llm(self, transcript: str) -> dict | None:
        try:
            response = await self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=self._system_prompt,
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": "extract_execution_data"},
                messages=[{"role": "user", "content": transcript}],
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == "extract_execution_data":
                    return block.input
            logger.warning("LLM returned no tool_use block")
            return None
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return None

    async def _store(self, thread_id: int, extracted: dict) -> None:
        min_conf = self._settings.extraction_confidence_min
        pv = self._settings.prompt_version

        for item in extracted.get("decisions", []):
            if item["confidence"] < min_conf:
                continue
            await db.insert_extraction(
                thread_id, "decision",
                item["text"], item.get("decided_by"),
                item["confidence"], pv,
            )

        for item in extracted.get("actions", []):
            if item["confidence"] < min_conf:
                continue
            extraction_id = await db.insert_extraction(
                thread_id, "action",
                item["text"], item.get("assigned_to"),
                item["confidence"], pv,
            )
            due = None
            if item.get("due_date"):
                try:
                    due = date.fromisoformat(item["due_date"])
                except ValueError:
                    pass
            await db.insert_action(extraction_id, item.get("assigned_to"), due)

        for item in extracted.get("risks", []):
            if item["confidence"] < min_conf:
                continue
            await db.insert_extraction(
                thread_id, "risk",
                item["text"], item.get("raised_by"),
                item["confidence"], pv,
            )
