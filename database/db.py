from typing import Optional
from sqlalchemy.orm import Session, sessionmaker
from .models import (
    init_db,
    TelegramChannel,
    DiscordWebhook,
    ForwardingGroup,
    ChannelMapping,
    TransformRule,
    ForwardLog,
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
            return webhook

    def get_discord_webhooks(self, active_only: bool = False) -> list[DiscordWebhook]:
        with self._get_session() as session:
            query = session.query(DiscordWebhook)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()

    def delete_discord_webhook(self, db_id: int) -> bool:
        with self._get_session() as session:
            webhook = session.query(DiscordWebhook).filter_by(id=db_id).first()
            if webhook:
                session.delete(webhook)
                session.commit()
                return True
            return False

    def toggle_discord_webhook(self, db_id: int, is_active: bool) -> bool:
        with self._get_session() as session:
            webhook = session.query(DiscordWebhook).filter_by(id=db_id).first()
            if webhook:
                webhook.is_active = is_active
                session.commit()
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
            mapping = session.query(ChannelMapping).filter_by(id=db_id).first()
            if mapping:
                session.delete(mapping)
                session.commit()
                return True
            return False

    def toggle_channel_mapping(self, db_id: int, is_active: bool) -> bool:
        with self._get_session() as session:
            mapping = session.query(ChannelMapping).filter_by(id=db_id).first()
            if mapping:
                mapping.is_active = is_active
                session.commit()
                return True
            return False

    def update_channel_mapping_webhook(self, db_id: int, webhook_db_id: int) -> bool:
        with self._get_session() as session:
            mapping = session.query(ChannelMapping).filter_by(id=db_id).first()
            if not mapping:
                return False
            mapping.webhook_id = webhook_db_id
            session.commit()
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

    def get_forward_logs(self, limit: int = 100, offset: int = 0) -> list[ForwardLog]:
        with self._get_session() as session:
            return (
                session.query(ForwardLog)
                .order_by(ForwardLog.forwarded_at.desc())
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
