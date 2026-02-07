import asyncio
import json
import random
import httpx
import re
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class DiscordMessage:
    content: str
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    embeds: Optional[list[dict[str, Any]]] = None
    allowed_mentions: Optional[dict[str, Any]] = None


class DiscordWebhookSender:
    _WEBHOOK_TOKEN_RE = re.compile(
        r"(https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/)\S+",
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 6,
        max_backoff_seconds: float = 30.0,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_backoff_seconds = max_backoff_seconds
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def redact_webhook_url(cls, text: str) -> str:
        return cls._WEBHOOK_TOKEN_RE.sub(r"\1[REDACTED]", text)

    @classmethod
    def _redact(cls, text: str) -> str:
        return cls.redact_webhook_url(text)

    @staticmethod
    def is_discord_webhook_url(webhook_url: str) -> bool:
        try:
            parsed = urlparse(webhook_url)
        except Exception:
            return False

        if parsed.scheme != "https":
            return False

        host = (parsed.hostname or "").lower()
        if host not in {
            "discord.com",
            "discordapp.com",
            "canary.discord.com",
            "ptb.discord.com",
        }:
            return False

        if not parsed.path.startswith("/api/webhooks/"):
            return False

        return True

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def send(
        self, webhook_url: str, message: DiscordMessage
    ) -> tuple[bool, Optional[str]]:
        if not self.is_discord_webhook_url(webhook_url):
            return False, "Invalid Discord webhook URL"

        client = await self._get_client()

        try:
            if message.file_path and Path(message.file_path).exists():
                return await self._send_with_file(client, webhook_url, message)
            else:
                return await self._send_text(client, webhook_url, message)
        except httpx.TimeoutException:
            return False, "Request timed out"
        except httpx.HTTPError as e:
            return False, f"HTTP error: {self._redact(str(e))}"
        except Exception as e:
            return False, f"Unexpected error: {self._redact(str(e))}"

    async def _send_text(
        self, client: httpx.AsyncClient, webhook_url: str, message: DiscordMessage
    ) -> tuple[bool, Optional[str]]:
        payload = {"content": message.content[:2000]}

        if message.username:
            payload["username"] = message.username
        if message.avatar_url:
            payload["avatar_url"] = message.avatar_url
        if message.embeds:
            payload["embeds"] = message.embeds
        if message.allowed_mentions:
            payload["allowed_mentions"] = message.allowed_mentions

        return await self._post_with_retries(client, webhook_url, json=payload)

    async def _send_with_file(
        self, client: httpx.AsyncClient, webhook_url: str, message: DiscordMessage
    ) -> tuple[bool, Optional[str]]:
        if not message.file_path:
            return False, "No file path provided"
        file_path = Path(message.file_path)
        file_name = message.file_name or file_path.name

        payload_json = {}
        if message.content:
            payload_json["content"] = message.content[:2000]
        if message.username:
            payload_json["username"] = message.username
        if message.avatar_url:
            payload_json["avatar_url"] = message.avatar_url
        if message.embeds:
            payload_json["embeds"] = message.embeds
        if message.allowed_mentions:
            payload_json["allowed_mentions"] = message.allowed_mentions

        for attempt in range(self.max_retries + 1):
            try:
                with open(file_path, "rb") as f:
                    files = {"file": (file_name, f)}
                    data = (
                        {"payload_json": json.dumps(payload_json)}
                        if payload_json
                        else {}
                    )

                    response = await client.post(
                        webhook_url, files=files, data=data
                    )

                ok, err, retry_after = self._classify_response(response)
                if ok:
                    return True, None
                if retry_after is None or attempt >= self.max_retries:
                    return False, err
                if retry_after == 0.0:
                    await asyncio.sleep(self._backoff_seconds(attempt))
                else:
                    await asyncio.sleep(retry_after)
            except httpx.TimeoutException:
                if attempt >= self.max_retries:
                    return False, "Request timed out"
                await asyncio.sleep(self._backoff_seconds(attempt))
            except httpx.HTTPError as e:
                if attempt >= self.max_retries:
                    return False, f"HTTP error: {self._redact(str(e))}"
                await asyncio.sleep(self._backoff_seconds(attempt))
            except Exception as e:
                return False, f"Unexpected error: {self._redact(str(e))}"

        return False, "Failed after retries"

    def _backoff_seconds(self, attempt: int) -> float:
        base = min(self.max_backoff_seconds, (2**attempt) * 0.5)
        return base + random.random() * 0.25

    def _classify_response(
        self, response: httpx.Response
    ) -> tuple[bool, str, Optional[float]]:
        if response.status_code in (200, 204):
            return True, "", None

        text = (response.text or "").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        text = self._redact(text)

        # Rate limit
        if response.status_code == 429:
            retry_after: Optional[float] = None
            try:
                data = response.json()
                if isinstance(data, dict) and "retry_after" in data:
                    retry_after = float(data["retry_after"])
            except Exception:
                retry_after = None

            if retry_after is None:
                hdr = response.headers.get("retry-after") or response.headers.get(
                    "Retry-After"
                )
                if hdr:
                    try:
                        retry_after = float(hdr)
                    except ValueError:
                        retry_after = None

            retry_after = retry_after if retry_after is not None else 1.5
            return False, "Rate limited by Discord (429)", retry_after

        # Transient server errors (retry with backoff handled by caller)
        if response.status_code in (500, 502, 503, 504):
            return False, f"Discord server error {response.status_code}", 0.0

        return (
            False,
            f"Discord returned status {response.status_code}: {text}",
            None,
        )

    async def _post_with_retries(
        self,
        client: httpx.AsyncClient,
        webhook_url: str,
        *,
        json: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(webhook_url, json=json)
                ok, err, retry_after = self._classify_response(response)
                if ok:
                    return True, None
                if retry_after is None or attempt >= self.max_retries:
                    return False, err

                if retry_after == 0.0:
                    await asyncio.sleep(self._backoff_seconds(attempt))
                else:
                    await asyncio.sleep(retry_after)
            except httpx.TimeoutException:
                if attempt >= self.max_retries:
                    return False, "Request timed out"
                await asyncio.sleep(self._backoff_seconds(attempt))
            except httpx.HTTPError as e:
                if attempt >= self.max_retries:
                    return False, f"HTTP error: {self._redact(str(e))}"
                await asyncio.sleep(self._backoff_seconds(attempt))
            except Exception as e:
                return False, f"Unexpected error: {self._redact(str(e))}"
        return False, "Failed after retries"

    async def test_webhook(self, webhook_url: str) -> tuple[bool, Optional[str]]:
        if not self.is_discord_webhook_url(webhook_url):
            return False, "Invalid Discord webhook URL"

        client = await self._get_client()

        try:
            response = await client.get(webhook_url)
            if response.status_code == 200:
                data = response.json()
                return True, data.get("name", "Unknown Webhook")
            else:
                return False, f"Invalid webhook: status {response.status_code}"
        except Exception as e:
            return False, self._redact(str(e))


discord_sender = DiscordWebhookSender()
