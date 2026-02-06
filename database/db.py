from typing import Optional
from sqlalchemy.orm import Session, sessionmaker
from .models import (
    init_db,
    _sync_v2_from_v1,
    TelegramChannel,
    DiscordWebhook,
    ForwardingGroup,
    ChannelMapping,
    Destination,
    DestinationDiscord,
    DestinationTelegram,
    RouteMapping,
    DestinationType,
    TransformRule,
    ForwardLog,
    ForwardLogV2,
    AppSettings,
)


class Database:
    def __init__(self, database_path: Optional[str] = None):
        self.engine = init_db(database_path=database_path)
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False
        )

    def _get_session(self) -> Session:
        return self._session_factory()

    def _sync_v2_compat(self) -> None:
        _sync_v2_from_v1(self.engine)

    def get_legacy_migration_report(self) -> dict[str, object]:
        """Return a compatibility report for legacy v1 -> v2 mirrored rows."""
        self._sync_v2_compat()
        with self._get_session() as session:
            webhook_ids = {
                int(row[0]) for row in session.query(DiscordWebhook.id).all()
            }
            mirrored_webhook_ids = {
                int(row[0])
                for row in session.query(DestinationDiscord.legacy_webhook_id)
                .filter(DestinationDiscord.legacy_webhook_id.isnot(None))
                .all()
            }

            mapping_ids = {
                int(row[0]) for row in session.query(ChannelMapping.id).all()
            }
            mirrored_mapping_ids = {
                int(row[0])
                for row in session.query(RouteMapping.legacy_channel_mapping_id)
                .filter(RouteMapping.legacy_channel_mapping_id.isnot(None))
                .all()
            }

            transform_mapping_ids = {
                int(row[0])
                for row in session.query(TransformRule.mapping_id)
                .filter(TransformRule.mapping_id.isnot(None))
                .all()
            }

            missing_webhook_ids = sorted(webhook_ids - mirrored_webhook_ids)
            orphaned_webhook_mirror_ids = sorted(mirrored_webhook_ids - webhook_ids)
            missing_mapping_ids = sorted(mapping_ids - mirrored_mapping_ids)
            orphaned_mapping_mirror_ids = sorted(mirrored_mapping_ids - mapping_ids)
            unmatched_transform_mapping_ids = sorted(
                transform_mapping_ids - mapping_ids
            )

            return {
                "legacy_webhooks_total": len(webhook_ids),
                "legacy_mappings_total": len(mapping_ids),
                "legacy_transform_rules_linked_total": len(transform_mapping_ids),
                "mirrored_destinations_total": len(mirrored_webhook_ids),
                "mirrored_routes_total": len(mirrored_mapping_ids),
                "missing_webhook_ids": missing_webhook_ids,
                "orphaned_webhook_mirror_ids": orphaned_webhook_mirror_ids,
                "missing_mapping_ids": missing_mapping_ids,
                "orphaned_mapping_mirror_ids": orphaned_mapping_mirror_ids,
                "unmatched_transform_mapping_ids": unmatched_transform_mapping_ids,
            }

    def add_telegram_channel(
        self, channel_id: int, name: str, username: Optional[str] = None
    ) -> TelegramChannel:
        with self._get_session() as session:
            existing = (
                session.query(TelegramChannel).filter_by(channel_id=channel_id).first()
            )
            if existing:
                existing.name = name
                existing.username = username
                session.commit()
                session.refresh(existing)
                return existing

            channel = TelegramChannel(
                channel_id=channel_id, name=name, username=username
            )
            session.add(channel)
            session.commit()
            session.refresh(channel)
            return channel

    def get_telegram_channels(self, active_only: bool = False) -> list[TelegramChannel]:
        with self._get_session() as session:
            query = session.query(TelegramChannel)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()

    def get_telegram_channel(self, channel_id: int) -> Optional[TelegramChannel]:
        with self._get_session() as session:
            return (
                session.query(TelegramChannel).filter_by(channel_id=channel_id).first()
            )

    def delete_telegram_channel(self, db_id: int) -> bool:
        with self._get_session() as session:
            channel = session.query(TelegramChannel).filter_by(id=db_id).first()
            if channel:
                session.delete(channel)
                session.commit()
                return True
            return False

    def toggle_telegram_channel(self, db_id: int, is_active: bool) -> bool:
        with self._get_session() as session:
            channel = session.query(TelegramChannel).filter_by(id=db_id).first()
            if channel:
                channel.is_active = is_active
                session.commit()
                return True
            return False

    def update_telegram_channel(
        self,
        db_id: int,
        name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> bool:
        with self._get_session() as session:
            channel = session.query(TelegramChannel).filter_by(id=db_id).first()
            if not channel:
                return False
            if name is not None:
                channel.name = name
            if username is not None:
                channel.username = username
            session.commit()
            return True

    def add_discord_webhook(self, name: str, url: str) -> DiscordWebhook:
        with self._get_session() as session:
            webhook = DiscordWebhook(name=name, url=url)
            session.add(webhook)
            session.commit()
            session.refresh(webhook)
            self._sync_v2_compat()
            return webhook

    def get_discord_webhooks(self, active_only: bool = False) -> list[DiscordWebhook]:
        with self._get_session() as session:
            query = session.query(DiscordWebhook)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()

    def delete_discord_webhook(self, db_id: int) -> bool:
        with self._get_session() as session:
            mirrored_destination_ids = [
                row.destination_id
                for row in session.query(DestinationDiscord)
                .filter_by(legacy_webhook_id=db_id)
                .all()
            ]
            webhook = session.query(DiscordWebhook).filter_by(id=db_id).first()
            if webhook:
                session.delete(webhook)
                session.commit()
                with self._get_session() as cleanup_session:
                    for destination_id in mirrored_destination_ids:
                        destination = cleanup_session.query(Destination).filter_by(
                            id=destination_id
                        ).first()
                        if destination:
                            cleanup_session.delete(destination)
                    cleanup_session.commit()
                self._sync_v2_compat()
                return True
            return False

    def toggle_discord_webhook(self, db_id: int, is_active: bool) -> bool:
        with self._get_session() as session:
            webhook = session.query(DiscordWebhook).filter_by(id=db_id).first()
            if webhook:
                webhook.is_active = is_active
                session.commit()
                self._sync_v2_compat()
                return True
            return False

    def update_discord_webhook(
        self,
        db_id: int,
        name: Optional[str] = None,
        url: Optional[str] = None,
    ) -> bool:
        with self._get_session() as session:
            webhook = session.query(DiscordWebhook).filter_by(id=db_id).first()
            if not webhook:
                return False
            if name is not None:
                webhook.name = name
            if url is not None:
                webhook.url = url
            session.commit()
            self._sync_v2_compat()
            return True

    def add_forwarding_group(
        self, name: str, delay_seconds: int = 2
    ) -> ForwardingGroup:
        with self._get_session() as session:
            group = ForwardingGroup(name=name, delay_seconds=delay_seconds)
            session.add(group)
            session.commit()
            session.refresh(group)
            return group

    def get_forwarding_groups(self, active_only: bool = False) -> list[ForwardingGroup]:
        with self._get_session() as session:
            query = session.query(ForwardingGroup)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()

    def delete_forwarding_group(self, db_id: int) -> bool:
        with self._get_session() as session:
            group = session.query(ForwardingGroup).filter_by(id=db_id).first()
            if group:
                session.delete(group)
                session.commit()
                return True
            return False

    def add_channel_mapping(
        self, channel_db_id: int, webhook_db_id: int, group_db_id: Optional[int] = None
    ) -> ChannelMapping:
        with self._get_session() as session:
            existing = (
                session.query(ChannelMapping)
                .filter_by(
                    channel_id=channel_db_id,
                    webhook_id=webhook_db_id,
                    group_id=group_db_id,
                )
                .first()
            )
            if existing:
                return existing

            mapping = ChannelMapping(
                channel_id=channel_db_id, webhook_id=webhook_db_id, group_id=group_db_id
            )
            session.add(mapping)
            session.commit()
            session.refresh(mapping)
            self._sync_v2_compat()
            return mapping

    def get_channel_mappings(self, active_only: bool = False) -> list[ChannelMapping]:
        with self._get_session() as session:
            query = session.query(ChannelMapping)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()

    def get_mappings_for_channel(
        self, telegram_channel_id: int
    ) -> list[ChannelMapping]:
        with self._get_session() as session:
            channel = (
                session.query(TelegramChannel)
                .filter_by(channel_id=telegram_channel_id)
                .first()
            )
            if not channel:
                return []
            return (
                session.query(ChannelMapping)
                .filter_by(channel_id=channel.id, is_active=True)
                .all()
            )

    def delete_channel_mapping(self, db_id: int) -> bool:
        with self._get_session() as session:
            legacy_route_ids = [
                row.id
                for row in session.query(RouteMapping)
                .filter_by(legacy_channel_mapping_id=db_id)
                .all()
            ]
            mapping = session.query(ChannelMapping).filter_by(id=db_id).first()
            if mapping:
                session.delete(mapping)
                session.commit()
                with self._get_session() as cleanup_session:
                    if legacy_route_ids:
                        cleanup_session.query(RouteMapping).filter(
                            RouteMapping.id.in_(legacy_route_ids)
                        ).delete(synchronize_session=False)
                    cleanup_session.commit()
                self._sync_v2_compat()
                return True
            return False

    def toggle_channel_mapping(self, db_id: int, is_active: bool) -> bool:
        with self._get_session() as session:
            mapping = session.query(ChannelMapping).filter_by(id=db_id).first()
            if mapping:
                mapping.is_active = is_active
                session.commit()
                self._sync_v2_compat()
                return True
            return False

    def update_channel_mapping_webhook(self, db_id: int, webhook_db_id: int) -> bool:
        with self._get_session() as session:
            mapping = session.query(ChannelMapping).filter_by(id=db_id).first()
            if not mapping:
                return False
            mapping.webhook_id = webhook_db_id
            session.commit()
            self._sync_v2_compat()
            return True

    def add_transform_rule(
        self,
        transform_type: str,
        pattern: str,
        replacement: Optional[str] = None,
        group_id: Optional[int] = None,
        mapping_id: Optional[int] = None,
        priority: int = 0,
    ) -> TransformRule:
        with self._get_session() as session:
            rule = TransformRule(
                transform_type=transform_type,
                pattern=pattern,
                replacement=replacement,
                group_id=group_id,
                mapping_id=mapping_id,
                priority=priority,
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            return rule

    def get_transform_rules(
        self,
        group_id: Optional[int] = None,
        mapping_id: Optional[int] = None,
        active_only: bool = True,
    ) -> list[TransformRule]:
        with self._get_session() as session:
            query = session.query(TransformRule)
            if active_only:
                query = query.filter_by(is_active=True)
            if group_id is not None:
                query = query.filter_by(group_id=group_id)
            if mapping_id is not None:
                query = query.filter_by(mapping_id=mapping_id)
            return query.order_by(TransformRule.priority.desc()).all()

    def delete_transform_rule(self, db_id: int) -> bool:
        with self._get_session() as session:
            rule = session.query(TransformRule).filter_by(id=db_id).first()
            if rule:
                session.delete(rule)
                session.commit()
                return True
            return False

    def add_forward_log(
        self,
        mapping_id: int,
        telegram_message_id: int,
        original_text: Optional[str] = None,
        transformed_text: Optional[str] = None,
        has_media: bool = False,
        status: str = "success",
        error_message: Optional[str] = None,
    ) -> ForwardLog:
        with self._get_session() as session:
            log = ForwardLog(
                mapping_id=mapping_id,
                telegram_message_id=telegram_message_id,
                original_text=original_text,
                transformed_text=transformed_text,
                has_media=has_media,
                status=status,
                error_message=error_message,
            )
            session.add(log)
            session.commit()
            session.refresh(log)
            return log

    def add_forward_log_v2(
        self,
        route_mapping_id: int,
        telegram_message_id: int,
        destination_type: str,
        destination_name: Optional[str] = None,
        original_text: Optional[str] = None,
        transformed_text: Optional[str] = None,
        has_media: bool = False,
        status: str = "success",
        error_message: Optional[str] = None,
    ) -> ForwardLogV2:
        with self._get_session() as session:
            log = ForwardLogV2(
                route_mapping_id=route_mapping_id,
                telegram_message_id=telegram_message_id,
                destination_type=destination_type,
                destination_name=destination_name,
                original_text=original_text,
                transformed_text=transformed_text,
                has_media=has_media,
                status=status,
                error_message=error_message,
            )
            session.add(log)
            session.commit()
            session.refresh(log)
            return log

    def get_forward_logs(self, limit: int = 100, offset: int = 0) -> list[ForwardLog]:
        with self._get_session() as session:
            return (
                session.query(ForwardLog)
                .order_by(ForwardLog.forwarded_at.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )

    def get_forward_logs_v2(
        self, limit: int = 100, offset: int = 0
    ) -> list[ForwardLogV2]:
        with self._get_session() as session:
            return (
                session.query(ForwardLogV2)
                .order_by(ForwardLogV2.forwarded_at.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_session() as session:
            setting = session.query(AppSettings).filter_by(key=key).first()
            return setting.value if setting else default

    def set_setting(self, key: str, value: str) -> AppSettings:
        with self._get_session() as session:
            setting = session.query(AppSettings).filter_by(key=key).first()
            if setting:
                setting.value = value
            else:
                setting = AppSettings(key=key, value=value)
                session.add(setting)
            session.commit()
            session.refresh(setting)
            return setting

    def get_active_source_channel_ids(self) -> list[int]:
        with self._get_session() as session:
            mappings = session.query(ChannelMapping).filter_by(is_active=True).all()
            channel_ids = set()
            for mapping in mappings:
                channel = (
                    session.query(TelegramChannel)
                    .filter_by(id=mapping.channel_id)
                    .first()
                )
                if channel and channel.is_active:
                    channel_ids.add(channel.channel_id)
            return list(channel_ids)

    def add_destination(
        self, name: str, destination_type: str, is_active: bool = True
    ) -> Destination:
        with self._get_session() as session:
            destination = Destination(
                name=name,
                destination_type=destination_type,
                is_active=is_active,
            )
            session.add(destination)
            session.commit()
            session.refresh(destination)
            return destination

    def add_discord_destination(
        self,
        name: str,
        webhook_url: str,
        is_active: bool = True,
        legacy_webhook_id: Optional[int] = None,
    ) -> Destination:
        with self._get_session() as session:
            if legacy_webhook_id is not None:
                existing_config = (
                    session.query(DestinationDiscord)
                    .filter_by(legacy_webhook_id=legacy_webhook_id)
                    .first()
                )
                if existing_config:
                    destination = (
                        session.query(Destination)
                        .filter_by(id=existing_config.destination_id)
                        .first()
                    )
                    if destination:
                        destination.name = name
                        destination.destination_type = DestinationType.DISCORD_WEBHOOK.value
                        destination.is_active = is_active
                        existing_config.webhook_url = webhook_url
                        session.commit()
                        session.refresh(destination)
                        return destination

            destination = Destination(
                name=name,
                destination_type=DestinationType.DISCORD_WEBHOOK.value,
                is_active=is_active,
            )
            session.add(destination)
            session.flush()

            cfg = DestinationDiscord(
                destination_id=destination.id,
                webhook_url=webhook_url,
                legacy_webhook_id=legacy_webhook_id,
            )
            session.add(cfg)
            session.commit()
            session.refresh(destination)
            return destination

    def add_telegram_destination(
        self,
        name: str,
        chat_id: int,
        topic_id: Optional[int] = None,
        is_active: bool = True,
    ) -> Destination:
        with self._get_session() as session:
            destination = Destination(
                name=name,
                destination_type=DestinationType.TELEGRAM_CHAT.value,
                is_active=is_active,
            )
            session.add(destination)
            session.flush()

            cfg = DestinationTelegram(
                destination_id=destination.id,
                chat_id=chat_id,
                topic_id=topic_id,
            )
            session.add(cfg)
            session.commit()
            session.refresh(destination)
            return destination

    def get_destinations(
        self,
        active_only: bool = False,
        destination_type: Optional[str] = None,
    ) -> list[Destination]:
        with self._get_session() as session:
            query = session.query(Destination)
            if active_only:
                query = query.filter_by(is_active=True)
            if destination_type:
                query = query.filter_by(destination_type=destination_type)
            return query.all()

    def get_destination_rows(
        self,
        active_only: bool = False,
        destination_type: Optional[str] = None,
    ) -> list[dict]:
        with self._get_session() as session:
            query = (
                session.query(Destination, DestinationDiscord, DestinationTelegram)
                .outerjoin(
                    DestinationDiscord, DestinationDiscord.destination_id == Destination.id
                )
                .outerjoin(
                    DestinationTelegram, DestinationTelegram.destination_id == Destination.id
                )
            )
            if active_only:
                query = query.filter(Destination.is_active.is_(True))
            if destination_type:
                query = query.filter(Destination.destination_type == destination_type)

            out: list[dict] = []
            for destination, destination_discord, destination_telegram in query.all():
                out.append(
                    {
                        "destination_id": destination.id,
                        "destination_name": destination.name,
                        "destination_type": destination.destination_type,
                        "is_active": destination.is_active,
                        "discord_webhook_url": destination_discord.webhook_url
                        if destination_discord
                        else None,
                        "telegram_chat_id": destination_telegram.chat_id
                        if destination_telegram
                        else None,
                        "telegram_topic_id": destination_telegram.topic_id
                        if destination_telegram
                        else None,
                        "legacy_webhook_id": destination_discord.legacy_webhook_id
                        if destination_discord
                        else None,
                    }
                )
            return out

    def get_destination(self, destination_id: int) -> Optional[Destination]:
        with self._get_session() as session:
            return session.query(Destination).filter_by(id=destination_id).first()

    def update_destination(
        self,
        destination_id: int,
        name: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        with self._get_session() as session:
            destination = session.query(Destination).filter_by(id=destination_id).first()
            if not destination:
                return False
            if name is not None:
                destination.name = name
            if is_active is not None:
                destination.is_active = is_active
            session.commit()
            return True

    def update_discord_destination(
        self,
        destination_id: int,
        *,
        name: Optional[str] = None,
        webhook_url: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        with self._get_session() as session:
            destination = session.query(Destination).filter_by(id=destination_id).first()
            if not destination:
                return False
            if destination.destination_type != DestinationType.DISCORD_WEBHOOK.value:
                return False

            cfg = (
                session.query(DestinationDiscord)
                .filter_by(destination_id=destination_id)
                .first()
            )
            if cfg is None:
                return False

            if name is not None:
                destination.name = name
            if is_active is not None:
                destination.is_active = is_active
            if webhook_url is not None:
                cfg.webhook_url = webhook_url
            session.commit()
            return True

    def update_telegram_destination(
        self,
        destination_id: int,
        *,
        name: Optional[str] = None,
        chat_id: Optional[int] = None,
        topic_id: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        with self._get_session() as session:
            destination = session.query(Destination).filter_by(id=destination_id).first()
            if not destination:
                return False
            if destination.destination_type != DestinationType.TELEGRAM_CHAT.value:
                return False

            cfg = (
                session.query(DestinationTelegram)
                .filter_by(destination_id=destination_id)
                .first()
            )
            if cfg is None:
                return False

            if name is not None:
                destination.name = name
            if is_active is not None:
                destination.is_active = is_active
            if chat_id is not None:
                cfg.chat_id = chat_id
            cfg.topic_id = topic_id
            session.commit()
            return True

    def delete_destination(self, destination_id: int) -> bool:
        with self._get_session() as session:
            destination = session.query(Destination).filter_by(id=destination_id).first()
            if not destination:
                return False
            session.delete(destination)
            session.commit()
            return True

    def add_route_mapping(
        self,
        source_channel_db_id: int,
        destination_id: int,
        group_id: Optional[int] = None,
        is_active: bool = True,
        legacy_channel_mapping_id: Optional[int] = None,
    ) -> RouteMapping:
        with self._get_session() as session:
            query = session.query(RouteMapping).filter_by(
                source_channel_id=source_channel_db_id,
                destination_id=destination_id,
                group_id=group_id,
            )
            existing = query.first()
            if existing:
                existing.is_active = is_active
                if legacy_channel_mapping_id is not None:
                    existing.legacy_channel_mapping_id = legacy_channel_mapping_id
                session.commit()
                session.refresh(existing)
                return existing

            mapping = RouteMapping(
                source_channel_id=source_channel_db_id,
                destination_id=destination_id,
                group_id=group_id,
                is_active=is_active,
                legacy_channel_mapping_id=legacy_channel_mapping_id,
            )
            session.add(mapping)
            session.commit()
            session.refresh(mapping)
            return mapping

    def get_route_mappings(self, active_only: bool = False) -> list[RouteMapping]:
        self._sync_v2_compat()
        with self._get_session() as session:
            query = session.query(RouteMapping)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()

    def get_route_rows(self, active_only: bool = False) -> list[dict]:
        self._sync_v2_compat()
        with self._get_session() as session:
            query = (
                session.query(
                    RouteMapping,
                    TelegramChannel,
                    Destination,
                    DestinationDiscord,
                    DestinationTelegram,
                )
                .join(TelegramChannel, RouteMapping.source_channel_id == TelegramChannel.id)
                .join(Destination, RouteMapping.destination_id == Destination.id)
                .outerjoin(
                    DestinationDiscord, DestinationDiscord.destination_id == Destination.id
                )
                .outerjoin(
                    DestinationTelegram, DestinationTelegram.destination_id == Destination.id
                )
            )
            if active_only:
                query = query.filter(
                    RouteMapping.is_active.is_(True),
                    TelegramChannel.is_active.is_(True),
                    Destination.is_active.is_(True),
                )

            out: list[dict] = []
            for (
                route,
                channel,
                destination,
                destination_discord,
                destination_telegram,
            ) in query.all():
                out.append(
                    {
                        "route_id": route.id,
                        "route_is_active": route.is_active,
                        "group_id": route.group_id,
                        "legacy_channel_mapping_id": route.legacy_channel_mapping_id,
                        "source_channel_db_id": channel.id,
                        "source_channel_id": channel.channel_id,
                        "source_channel_name": channel.name,
                        "source_channel_username": channel.username,
                        "destination_id": destination.id,
                        "destination_name": destination.name,
                        "destination_type": destination.destination_type,
                        "discord_webhook_url": destination_discord.webhook_url
                        if destination_discord
                        else None,
                        "telegram_chat_id": destination_telegram.chat_id
                        if destination_telegram
                        else None,
                        "telegram_topic_id": destination_telegram.topic_id
                        if destination_telegram
                        else None,
                    }
                )
            return out

    def toggle_route_mapping(self, mapping_id: int, is_active: bool) -> bool:
        with self._get_session() as session:
            mapping = session.query(RouteMapping).filter_by(id=mapping_id).first()
            if not mapping:
                return False
            mapping.is_active = is_active
            session.commit()
            return True

    def update_route_mapping(
        self,
        mapping_id: int,
        *,
        destination_id: Optional[int] = None,
        group_id: Optional[int] = None,
    ) -> bool:
        with self._get_session() as session:
            mapping = session.query(RouteMapping).filter_by(id=mapping_id).first()
            if not mapping:
                return False
            if destination_id is not None:
                mapping.destination_id = destination_id
            mapping.group_id = group_id
            session.commit()
            return True

    def delete_route_mapping(self, mapping_id: int) -> bool:
        with self._get_session() as session:
            mapping = session.query(RouteMapping).filter_by(id=mapping_id).first()
            if not mapping:
                return False
            session.delete(mapping)
            session.commit()
            return True
