import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable, Any
from pathlib import Path
import zlib
import re
import colorsys

from telethon.tl.types import Message

from .telegram_client import TelegramClientWrapper, get_telegram_client
from .discord_sender import DiscordWebhookSender, DiscordMessage, discord_sender
from .discord_dispatcher import DiscordSendDispatcher, DiscordJob
from .transformer import MessageTransformer, TransformRule as TransformerRule
from database.db import Database
from database.models import TransformType


logger = logging.getLogger(__name__)


class Forwarder:
    def __init__(
        self,
        db: Database,
        telegram: Optional[TelegramClientWrapper] = None,
        discord: Optional[DiscordWebhookSender] = None,
        allow_mass_mentions: bool = False,
        suppress_url_embeds: bool = True,
        strip_urls: bool = False,
        include_telegram_link: bool = True,
    ):
        self.db = db
        self.telegram = telegram
        self.discord = discord or discord_sender
        self.allow_mass_mentions = allow_mass_mentions
        self.suppress_url_embeds = suppress_url_embeds
        self.strip_urls = strip_urls
        self.include_telegram_link = include_telegram_link
        self._is_running = False
        self._channel_webhook_map: dict[int, list[dict]] = {}
        self._channel_transformer_map: dict[int, MessageTransformer] = {}
        self._on_forward_callback: Optional[Callable[[dict], Any]] = None
        self._dispatcher: Optional[DiscordSendDispatcher] = None

    def _neutralize_mass_mentions(self, text: str) -> str:
        if self.allow_mass_mentions:
            return text
        return text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")

    def _suppress_url_unfurls(self, text: str) -> str:
        if self.strip_urls:
            url_pattern = r"https?://[^\s<>()\[\]{}]+"
            return re.sub(url_pattern, "", text).strip()

        if not self.suppress_url_embeds:
            return text

        url_pattern = r"https?://[^\s<>()\\[\\]{}]+"
        def wrap(match: re.Match[str]) -> str:
            url = match.group(0)
            if url.startswith("<") and url.endswith(">"):
                return url
            return f"<{url}>"

        return re.sub(url_pattern, wrap, text)

    def _discord_allowed_mentions(self) -> dict[str, Any]:
        parse = ["users", "roles"]
        if self.allow_mass_mentions:
            parse.append("everyone")
        return {"parse": parse, "replied_user": False}

    @staticmethod
    def _embed_color(key: str) -> int:
        seed = zlib.crc32(key.encode("utf-8"))
        hue = (seed % 360) / 360.0
        # Keep these fairly saturated/bright so the embed border is visible
        r, g, b = colorsys.hsv_to_rgb(hue, 0.68, 0.90)
        return (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)

    def _build_embed(
        self,
        channel_name: str,
        sender_name: str,
        text: str,
        timestamp: Optional[datetime],
        telegram_link: Optional[str],
        attachment_name: Optional[str],
    ) -> dict[str, Any]:
        cleaned = self._suppress_url_unfurls(text.strip())
        if not cleaned:
            description_body = "(media only)" if attachment_name else "(no text)"
        else:
            description_body = cleaned

        if sender_name and sender_name != channel_name:
            description = f"**{sender_name}**\n\n{description_body}"
        else:
            description = description_body

        if len(description) > 4096:
            description = description[:4093] + "..."

        embed: dict[str, Any] = {
            "title": channel_name,
            "description": description,
            "color": self._embed_color(channel_name),
            "footer": {"text": "TeleForward"},
        }

        if telegram_link and self.include_telegram_link:
            embed["url"] = telegram_link

        if timestamp:
            embed["timestamp"] = timestamp.astimezone(timezone.utc).isoformat()

        if attachment_name and attachment_name.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        ):
            embed["image"] = {"url": f"attachment://{attachment_name}"}

        return embed

    def set_on_forward_callback(self, callback: Callable[[dict], Any]):
        self._on_forward_callback = callback

    def _load_mappings(self):
        self._channel_webhook_map.clear()
        self._channel_transformer_map.clear()

        all_mappings = self.db.get_channel_mappings(active_only=True)
        all_groups = self.db.get_forwarding_groups()
        all_channels = self.db.get_telegram_channels()
        all_webhooks = self.db.get_discord_webhooks()

        active_groups = {g.id: g for g in all_groups if g.is_active}
        channel_map = {c.id: c for c in all_channels}
        webhook_map = {w.id: w for w in all_webhooks}

        for mapping in all_mappings:
            if mapping.group_id and mapping.group_id not in active_groups:
                continue

            channel = channel_map.get(mapping.channel_id)
            webhook = webhook_map.get(mapping.webhook_id)

            if not channel or not channel.is_active:
                continue
            if not webhook or not webhook.is_active:
                continue

            telegram_channel_id = channel.channel_id
            if telegram_channel_id not in self._channel_webhook_map:
                self._channel_webhook_map[telegram_channel_id] = []

            self._channel_webhook_map[telegram_channel_id].append(
                {
                    "webhook_id": webhook.id,
                    "webhook_url": webhook.url,
                    "webhook_name": webhook.name,
                    "mapping_id": mapping.id,
                    "group_id": mapping.group_id,
                    "channel_name": channel.name,
                    "channel_username": channel.username,
                }
            )

        for telegram_channel_id in self._channel_webhook_map.keys():
            transformer = self._build_transformer_for_channel(telegram_channel_id)
            self._channel_transformer_map[telegram_channel_id] = transformer

    def _build_transformer_for_channel(
        self, telegram_channel_id: int
    ) -> MessageTransformer:
        transformer = MessageTransformer()

        webhooks_info = self._channel_webhook_map.get(telegram_channel_id, [])
        group_ids = set(w["group_id"] for w in webhooks_info if w["group_id"])
        mapping_ids = set(w["mapping_id"] for w in webhooks_info)

        for group_id in group_ids:
            db_rules = self.db.get_transform_rules(group_id=group_id)
            for db_rule in db_rules:
                rule = self._convert_db_rule(db_rule)
                if rule:
                    transformer.add_rule(rule)

        for mapping_id in mapping_ids:
            db_rules = self.db.get_transform_rules(mapping_id=mapping_id)
            for db_rule in db_rules:
                rule = self._convert_db_rule(db_rule)
                if rule:
                    transformer.add_rule(rule)

        return transformer

    def _convert_db_rule(self, db_rule) -> Optional[TransformerRule]:
        type_mapping = {
            TransformType.KEYWORD_WHITELIST.value: "whitelist",
            TransformType.KEYWORD_BLACKLIST.value: "blacklist",
            TransformType.TEXT_REPLACE.value: "replace",
            TransformType.ADD_PREFIX.value: "prefix",
            TransformType.ADD_SUFFIX.value: "suffix",
            TransformType.STRIP_LINKS.value: "strip_links",
            TransformType.STRIP_MENTIONS.value: "strip_mentions",
        }

        rule_type = type_mapping.get(db_rule.transform_type)
        if not rule_type:
            return None

        return TransformerRule(
            rule_type=rule_type,
            pattern=db_rule.pattern or "",
            replacement=db_rule.replacement or "",
            is_regex=False,
            enabled=db_rule.is_active,
        )

    async def _handle_message(self, message: Message):
        chat_id = getattr(message, "chat_id", None) or getattr(
            message.peer_id, "channel_id", None
        )
        if not chat_id:
            return

        webhooks = self._channel_webhook_map.get(chat_id, [])
        if not webhooks:
            return

        text = getattr(message, "text", "") or getattr(message, "message", "") or ""
        if getattr(message, "media", None) and not text.strip():
            logger.debug("Skipping media-only message chat=%s msg=%s", chat_id, message.id)
            return

        transformer = self._channel_transformer_map.get(chat_id, MessageTransformer())
        transform_result = transformer.transform(text)

        if not transform_result.should_forward:
            logger.debug(f"Message blocked: {transform_result.blocked_by}")
            return

        media_path: Optional[str] = None
        if message.media and self.telegram:
            try:
                media_path = await self.telegram.download_media(message)
            except Exception as e:
                logger.warning(f"Failed to download media: {e}")

        sender_name = None
        sender = getattr(message, "sender", None)
        if sender:
            sender_name = getattr(sender, "first_name", None) or getattr(
                sender, "title", "Unknown"
            )

        media_ref = None
        if media_path and self._dispatcher and webhooks:
            media_ref = self._dispatcher.make_media_ref(
                path=media_path,
                fanout=len(webhooks),
            )

        for webhook_info in webhooks:
            channel_name = webhook_info.get("channel_name") or str(chat_id)
            channel_username = webhook_info.get("channel_username")
            resolved_sender = sender_name or channel_name

            telegram_link: Optional[str] = None
            if channel_username and getattr(message, "id", None):
                telegram_link = f"https://t.me/{channel_username}/{message.id}"

            transformed = self._neutralize_mass_mentions(
                transform_result.transformed_text
            )

            attachment_name = Path(media_path).name if media_path else None

            embed = self._build_embed(
                channel_name=channel_name,
                sender_name=resolved_sender,
                text=transformed,
                timestamp=getattr(message, "date", None),
                telegram_link=telegram_link,
                attachment_name=attachment_name,
            )

            discord_message = DiscordMessage(
                content="",
                username="TeleForward",
                embeds=[embed],
                allowed_mentions=self._discord_allowed_mentions(),
                file_path=media_path,
                file_name=attachment_name,
            )

            if not self._dispatcher:
                logger.warning("Dispatcher not initialized; dropping message")
                continue

            job = DiscordJob(
                webhook_url=webhook_info["webhook_url"],
                webhook_name=webhook_info["webhook_name"],
                mapping_id=webhook_info["mapping_id"],
                channel_id=chat_id,
                message_id=message.id,
                timestamp=datetime.now(),
                original_text=transform_result.original_text,
                transformed_text=transform_result.transformed_text,
                has_media=media_path is not None,
                discord_message=discord_message,
                media_ref=media_ref,
            )
            await self._dispatcher.enqueue(job)

    async def start(self):
        if not self.telegram:
            self.telegram = get_telegram_client()
        if not self.telegram:
            raise RuntimeError("Telegram client not initialized")

        self._load_mappings()

        channel_ids = list(self._channel_webhook_map.keys())
        if not channel_ids:
            logger.warning("No channel mappings configured")
            return

        self.telegram.set_message_handler(self._handle_message)
        self._dispatcher = DiscordSendDispatcher(
            sender=self.discord,
            db=self.db,
            on_forward_callback=self._on_forward_callback,
        )
        self._is_running = True

        logger.info(f"Starting forwarder for {len(channel_ids)} channels")
        await self.telegram.run_until_disconnected(channel_ids)

    async def stop(self):
        self._is_running = False
        if self.telegram:
            await self.telegram.stop()
        if self._dispatcher:
            await self._dispatcher.close()
            self._dispatcher = None
        await self.discord.close()

    def reload_mappings(self):
        self._load_mappings()
        logger.info("Reloaded channel mappings")

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def monitored_channels(self) -> list[int]:
        return list(self._channel_webhook_map.keys())


async def backfill_messages(
    db: Database,
    telegram: TelegramClientWrapper,
    discord: DiscordWebhookSender,
    channel_id: int,
    webhook_url: str,
    mapping_id: int,
    limit: int = 100,
    progress_callback: Optional[Callable[[int, int], Any]] = None,
) -> tuple[int, int]:
    success_count = 0
    fail_count = 0

    db_rules = db.get_transform_rules(mapping_id=mapping_id)
    transformer = MessageTransformer()
    for db_rule in db_rules:
        type_mapping = {
            TransformType.KEYWORD_WHITELIST.value: "whitelist",
            TransformType.KEYWORD_BLACKLIST.value: "blacklist",
            TransformType.TEXT_REPLACE.value: "replace",
            TransformType.ADD_PREFIX.value: "prefix",
            TransformType.ADD_SUFFIX.value: "suffix",
            TransformType.STRIP_LINKS.value: "strip_links",
            TransformType.STRIP_MENTIONS.value: "strip_mentions",
        }
        rule_type = type_mapping.get(db_rule.transform_type)
        if rule_type:
            transformer.add_rule(
                TransformerRule(
                    rule_type=rule_type,
                    pattern=db_rule.pattern or "",
                    replacement=db_rule.replacement or "",
                    is_regex=False,
                    enabled=db_rule.is_active,
                )
            )

    messages = []
    async for msg in telegram.iter_messages(channel_id, limit=limit, reverse=True):
        messages.append(msg)

    total = len(messages)
    for i, message in enumerate(messages):
        text = getattr(message, "text", "") or getattr(message, "message", "") or ""
        if not text:
            continue

        transform_result = transformer.transform(text)
        if not transform_result.should_forward:
            continue

        discord_message = DiscordMessage(content=transform_result.transformed_text)
        success, error = await discord.send(webhook_url, discord_message)

        db.add_forward_log(
            mapping_id=mapping_id,
            telegram_message_id=message.id,
            original_text=text[:1000],
            transformed_text=transform_result.transformed_text[:1000],
            status="success" if success else "error",
            error_message=error,
        )

        if success:
            success_count += 1
        else:
            fail_count += 1

        if progress_callback:
            try:
                callback_result = progress_callback(i + 1, total)
                if asyncio.iscoroutine(callback_result):
                    await callback_result
            except Exception:
                pass

        await asyncio.sleep(0.5)

    return success_count, fail_count
