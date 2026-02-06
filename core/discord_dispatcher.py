import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

from .discord_sender import DiscordWebhookSender, DiscordMessage
from database.db import Database


logger = logging.getLogger(__name__)


@dataclass
class _MediaRef:
    path: str
    remaining: int
    lock: asyncio.Lock

    async def release(self) -> None:
        async with self.lock:
            self.remaining -= 1
            if self.remaining > 0:
                return
        try:
            Path(self.path).unlink(missing_ok=True)
        except Exception:
            pass


@dataclass
class DiscordJob:
    webhook_url: str
    webhook_name: str
    route_mapping_id: Optional[int]
    destination_type: str
    destination_name: Optional[str]
    channel_id: int
    message_id: int
    timestamp: datetime
    original_text: Optional[str]
    transformed_text: Optional[str]
    has_media: bool
    discord_message: DiscordMessage
    media_ref: Optional[_MediaRef] = None


class DiscordSendDispatcher:
    def __init__(
        self,
        *,
        sender: DiscordWebhookSender,
        db: Database,
        on_forward_callback: Optional[Callable[[dict], Any]] = None,
        max_in_flight: int = 5,
        per_webhook_queue_size: int = 500,
    ):
        self.sender = sender
        self.db = db
        self.on_forward_callback = on_forward_callback
        self._sema = asyncio.Semaphore(max_in_flight)
        self._per_webhook_queue_size = per_webhook_queue_size
        self._queues: dict[str, asyncio.Queue[DiscordJob]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._db_lock = asyncio.Lock()
        self._closed = False

    def make_media_ref(self, path: str, fanout: int) -> _MediaRef:
        return _MediaRef(path=path, remaining=fanout, lock=asyncio.Lock())

    async def enqueue(self, job: DiscordJob) -> None:
        if self._closed:
            return

        queue = self._queues.get(job.webhook_url)
        if queue is None:
            queue = asyncio.Queue(maxsize=self._per_webhook_queue_size)
            self._queues[job.webhook_url] = queue
            self._tasks[job.webhook_url] = asyncio.create_task(
                self._worker(job.webhook_url),
                name=f"discord-worker:{job.webhook_name}",
            )

        await queue.put(job)

    async def close(self) -> None:
        self._closed = True
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._queues.clear()

    async def _worker(self, webhook_url: str) -> None:
        queue = self._queues[webhook_url]
        while True:
            job = await queue.get()
            try:
                async with self._sema:
                    success, error = await self.sender.send(
                        job.webhook_url, job.discord_message
                    )
                await self._record_result(job, success, error)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._record_result(job, False, f"Dispatcher error: {e}")
            finally:
                try:
                    if job.media_ref is not None:
                        await job.media_ref.release()
                except Exception:
                    pass
                queue.task_done()

    async def _record_result(
        self, job: DiscordJob, success: bool, error: Optional[str]
    ) -> None:
        async with self._db_lock:
            if job.route_mapping_id is not None:
                self.db.add_forward_log_v2(
                    route_mapping_id=job.route_mapping_id,
                    telegram_message_id=job.message_id,
                    destination_type=job.destination_type,
                    destination_name=job.destination_name,
                    original_text=job.original_text[:1000] if job.original_text else None,
                    transformed_text=job.transformed_text[:1000]
                    if job.transformed_text
                    else None,
                    has_media=job.has_media,
                    status="success" if success else "error",
                    error_message=error,
                )

        if self.on_forward_callback:
            event = {
                "channel_id": job.channel_id,
                "message_id": job.message_id,
                "webhook_name": job.webhook_name,
                "destination_type": job.destination_type,
                "destination_name": job.destination_name,
                "success": success,
                "error": error,
                "timestamp": job.timestamp,
            }
            try:
                result = self.on_forward_callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Forward callback failed")

