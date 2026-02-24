import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from .telegram_client import TelegramClientWrapper


@dataclass
class TelegramOutgoingMessage:
    text: str
    file_path: Optional[str] = None
    topic_id: Optional[int] = None
    parse_mode: Optional[str] = None


class TelegramDestinationSender:
    def __init__(
        self,
        max_retries: int = 4,
        max_backoff_seconds: float = 20.0,
        *,
        bot_api_id: Optional[int] = None,
        bot_api_hash: Optional[str] = None,
        bot_token: Optional[str] = None,
        data_dir: Optional[Path] = None,
    ):
        self.max_retries = max_retries
        self.max_backoff_seconds = max_backoff_seconds
        self.bot_api_id = bot_api_id
        self.bot_api_hash = (bot_api_hash or "").strip() or None
        self.bot_token = (bot_token or "").strip() or None
        self.data_dir = (
            Path(data_dir)
            if data_dir is not None
            else (Path(__file__).parent.parent / "data")
        )
        self._bot_client: Optional[TelegramClient] = None
        self._bot_client_lock = asyncio.Lock()

    @property
    def uses_bot(self) -> bool:
        return bool(self.bot_token and self.bot_api_id and self.bot_api_hash)

    def _backoff_seconds(self, attempt: int) -> float:
        base = min(self.max_backoff_seconds, (2**attempt) * 0.5)
        return base + random.random() * 0.25

    def _ensure_private_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    async def _ensure_bot_client(self) -> Optional[TelegramClient]:
        if not self.uses_bot:
            return None

        client = self._bot_client
        if client is not None and client.is_connected():
            return client

        async with self._bot_client_lock:
            client = self._bot_client
            if client is None:
                self._ensure_private_dir(self.data_dir)
                session_path = str(self.data_dir / "teleforward-bot")
                client = TelegramClient(
                    session_path,
                    int(self.bot_api_id),  # guarded by uses_bot
                    str(self.bot_api_hash),
                )
                self._bot_client = client

            await client.start(bot_token=str(self.bot_token))
            return client

    async def close(self) -> None:
        client = self._bot_client
        if client is None:
            return
        self._bot_client = None
        disconnect_coro = client.disconnect()
        if disconnect_coro is not None:
            await disconnect_coro

    async def send(
        self,
        telegram: TelegramClientWrapper,
        chat_id: int,
        message: TelegramOutgoingMessage,
    ) -> tuple[bool, Optional[str]]:
        try:
            client = await self._ensure_bot_client() if self.uses_bot else telegram.client
        except Exception as e:
            return False, f"Telegram bot client error: {e}"
        if not client:
            if self.bot_token and not self.uses_bot:
                return False, "Telegram bot sender is misconfigured"
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
        if message.parse_mode:
            kwargs["parse_mode"] = message.parse_mode

        for attempt in range(self.max_retries + 1):
            try:
                await client.send_message(
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
