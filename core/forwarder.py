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
from .telegram_sender import (
    TelegramDestinationSender,
    TelegramOutgoingMessage,
    telegram_destination_sender,
)
from .transformer import MessageTransformer, TransformRule as TransformerRule
from database.db import Database
from database.models import TransformType, DestinationType


logger = logging.getLogger(__name__)


class Forwarder:
    def __init__(
        self,
        db: Database,
        telegram: Optional[TelegramClientWrapper] = None,
        discord: Optional[DiscordWebhookSender] = None,
        telegram_sender: Optional[TelegramDestinationSender] = None,
        allow_mass_mentions: bool = False,
        suppress_url_embeds: bool = True,
        strip_urls: bool = False,
        include_telegram_link: bool = True,
    ):
        self.db = db
        self.telegram = telegram
        self.discord = discord or discord_sender
        self.telegram_sender = telegram_sender or telegram_destination_sender
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

        self._load_mappings_v2()

        for telegram_channel_id in self._channel_webhook_map.keys():
            transformer = self._build_transformer_for_channel(telegram_channel_id)
            self._channel_transformer_map[telegram_channel_id] = transformer

    def _load_mappings_v2(self) -> bool:
        all_route_rows = self.db.get_route_rows(active_only=True)
        if not all_route_rows:
            return False

        all_groups = self.db.get_forwarding_groups()
        active_groups = {g.id: g for g in all_groups if g.is_active}

        for row in all_route_rows:
            group_id = row.get("group_id")
            if group_id and group_id not in active_groups:
                continue

            if row.get("destination_type") != DestinationType.DISCORD_WEBHOOK.value:
                if row.get("destination_type") != DestinationType.TELEGRAM_CHAT.value:
                    continue
                telegram_chat_id = row.get("telegram_chat_id")
                if telegram_chat_id is None:
                    continue
                webhook_url: Optional[str] = None
            else:
                webhook_url = row.get("discord_webhook_url")
                if not webhook_url:
                    continue
                telegram_chat_id = None

            telegram_channel_id = row["source_channel_id"]
            if telegram_channel_id not in self._channel_webhook_map:
                self._channel_webhook_map[telegram_channel_id] = []

            self._channel_webhook_map[telegram_channel_id].append(
                {
                    "webhook_id": row["destination_id"],
                    "webhook_url": webhook_url,
                    "webhook_name": row["destination_name"],
                    "rule_mapping_id": row.get("legacy_channel_mapping_id"),
                    "route_mapping_id": row["route_id"],
                    "destination_type": row["destination_type"],
                    "destination_name": row["destination_name"],
                    "telegram_chat_id": telegram_chat_id,
                    "telegram_topic_id": row.get("telegram_topic_id"),
                    "group_id": group_id,
                    "channel_name": row["source_channel_name"],
                    "channel_username": row["source_channel_username"],
                }
            )

        return bool(self._channel_webhook_map)

    def _build_transformer_for_channel(
        self, telegram_channel_id: int
    ) -> MessageTransformer:
        transformer = MessageTransformer()

        webhooks_info = self._channel_webhook_map.get(telegram_channel_id, [])
        group_ids = set(w["group_id"] for w in webhooks_info if w["group_id"])
        mapping_ids = set(
            w["rule_mapping_id"] for w in webhooks_info if w.get("rule_mapping_id")
        )

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
        if getattr(message, "out", False):
            # Prevent loops when forwarding into chats where this account also listens.
            return

        chat_id = getattr(message, "chat_id", None) or getattr(
            message.peer_id, "channel_id", None
        )
        if not chat_id:
            return
        if not self.telegram:
            return

        routes = self._channel_webhook_map.get(chat_id, [])
        if not routes:
            return
        telegram_routes = [
            r
            for r in routes
            if r.get("destination_type") == DestinationType.TELEGRAM_CHAT.value
        ]
        discord_routes = [
            r
            for r in routes
            if r.get("destination_type") == DestinationType.DISCORD_WEBHOOK.value
        ]

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
        if media_path and self._dispatcher and discord_routes:
            media_ref = self._dispatcher.make_media_ref(
                path=media_path,
                fanout=len(discord_routes),
            )

        transformed = self._neutralize_mass_mentions(transform_result.transformed_text)

        for route_info in telegram_routes:
            destination_chat_id = route_info.get("telegram_chat_id")
            if destination_chat_id is None:
                continue
            if destination_chat_id == chat_id:
                logger.debug("Skipping loop route source=%s destination=%s", chat_id, destination_chat_id)
                continue

            channel_name = route_info.get("channel_name") or str(chat_id)
            channel_username = route_info.get("channel_username")
            resolved_sender = sender_name or channel_name

            telegram_link: Optional[str] = None
            if channel_username and getattr(message, "id", None):
                telegram_link = f"https://t.me/{channel_username}/{message.id}"

            outgoing_text = self._build_telegram_text(
                channel_name=channel_name,
                sender_name=resolved_sender,
                text=transformed,
                telegram_link=telegram_link,
            )

            success, error = await self.telegram_sender.send(
                telegram=self.telegram,
                chat_id=int(destination_chat_id),
                message=TelegramOutgoingMessage(
                    text=outgoing_text,
                    file_path=media_path,
                    topic_id=route_info.get("telegram_topic_id"),
                ),
            )
            await self._record_direct_result(
                route_info=route_info,
                source_channel_id=chat_id,
                message_id=message.id,
                original_text=transform_result.original_text,
                transformed_text=transform_result.transformed_text,
                has_media=media_path is not None,
                success=success,
                error=error,
            )

        attachment_name = Path(media_path).name if media_path else None
        for route_info in discord_routes:
            channel_name = route_info.get("channel_name") or str(chat_id)
            channel_username = route_info.get("channel_username")
            resolved_sender = sender_name or channel_name

            telegram_link: Optional[str] = None
            if channel_username and getattr(message, "id", None):
                telegram_link = f"https://t.me/{channel_username}/{message.id}"

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
                webhook_url=route_info["webhook_url"],
                webhook_name=route_info["webhook_name"],
                route_mapping_id=route_info.get("route_mapping_id"),
                destination_type=route_info.get(
                    "destination_type", DestinationType.DISCORD_WEBHOOK.value
                ),
                destination_name=route_info.get("destination_name"),
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

        if media_path and (not self._dispatcher or not discord_routes):
            try:
                Path(media_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _build_telegram_text(
        self,
        channel_name: str,
        sender_name: str,
        text: str,
        telegram_link: Optional[str],
    ) -> str:
        lines = [f"[{channel_name}]"]
        if sender_name and sender_name != channel_name:
            lines.append(sender_name)
        lines.append(text.strip() if text.strip() else "(no text)")
        if telegram_link and self.include_telegram_link:
            lines.append(telegram_link)
        out = "\n\n".join(line for line in lines if line)
        if len(out) > 4096:
            out = out[:4093] + "..."
        return out

    async def _record_direct_result(
        self,
        *,
        route_info: dict,
        source_channel_id: int,
        message_id: int,
        original_text: Optional[str],
        transformed_text: Optional[str],
        has_media: bool,
        success: bool,
        error: Optional[str],
    ) -> None:
        route_mapping_id = route_info.get("route_mapping_id")

        if route_mapping_id is not None:
            self.db.add_forward_log_v2(
                route_mapping_id=route_mapping_id,
                telegram_message_id=message_id,
                destination_type=route_info.get(
                    "destination_type", DestinationType.TELEGRAM_CHAT.value
                ),
                destination_name=route_info.get("destination_name"),
                original_text=original_text[:1000] if original_text else None,
                transformed_text=transformed_text[:1000] if transformed_text else None,
                has_media=has_media,
                status="success" if success else "error",
                error_message=error,
            )

        if self._on_forward_callback:
            event = {
                "channel_id": source_channel_id,
                "message_id": message_id,
                "webhook_name": route_info.get("destination_name") or "?",
                "destination_type": route_info.get(
                    "destination_type", DestinationType.TELEGRAM_CHAT.value
                ),
                "destination_name": route_info.get("destination_name"),
                "success": success,
                "error": error,
                "timestamp": datetime.now(),
            }
            try:
                callback_result = self._on_forward_callback(event)
                if asyncio.iscoroutine(callback_result):
                    await callback_result
            except Exception:
                logger.exception("Forward callback failed")

    async def start(self):
        if not self.telegram:
            self.telegram = get_telegram_client()
        if not self.telegram:
            raise RuntimeError("Telegram client not initialized")

        self._load_mappings()

        channel_ids = list(self._channel_webhook_map.keys())
        if not channel_ids:
            logger.warning("No active v2 routes configured")
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
