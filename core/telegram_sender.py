import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon.errors import FloodWaitError, RPCError

from .telegram_client import TelegramClientWrapper


@dataclass
class TelegramOutgoingMessage:
    text: str
    file_path: Optional[str] = None
    topic_id: Optional[int] = None


class TelegramDestinationSender:
    def __init__(self, max_retries: int = 4, max_backoff_seconds: float = 20.0):
        self.max_retries = max_retries
        self.max_backoff_seconds = max_backoff_seconds

    def _backoff_seconds(self, attempt: int) -> float:
        base = min(self.max_backoff_seconds, (2**attempt) * 0.5)
        return base + random.random() * 0.25

    async def send(
        self,
        telegram: TelegramClientWrapper,
        chat_id: int,
        message: TelegramOutgoingMessage,
    ) -> tuple[bool, Optional[str]]:
        if not telegram.client:
            return False, "Telegram client is not connected"

        text = (message.text or "").strip()
        if len(text) > 4096:
            text = text[:4093] + "..."

        file_path = message.file_path
        if file_path and not Path(file_path).exists():
            file_path = None

        if not text and not file_path:
            return False, "Telegram destination message is empty"

        kwargs = {"link_preview": False}
        if message.topic_id is not None:
            kwargs["reply_to"] = int(message.topic_id)

        for attempt in range(self.max_retries + 1):
            try:
                await telegram.client.send_message(
                    entity=chat_id,
                    message=text or None,
                    file=file_path,
                    **kwargs,
                )
                return True, None
            except FloodWaitError as e:
                wait_seconds = int(getattr(e, "seconds", 0) or 0)
                if attempt >= self.max_retries:
                    return False, f"Telegram flood-wait: retry after {wait_seconds}s"
                await asyncio.sleep(max(1, wait_seconds))
            except RPCError as e:
                if attempt >= self.max_retries:
                    return False, f"Telegram RPC error: {e.__class__.__name__}: {e}"
                await asyncio.sleep(self._backoff_seconds(attempt))
            except Exception as e:
                return False, f"Telegram send error: {e}"

        return False, "Failed after retries"


telegram_destination_sender = TelegramDestinationSender()

