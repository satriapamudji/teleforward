from datetime import datetime
from enum import Enum
from typing import Optional
import os
from sqlalchemy import (
    create_engine,
    ForeignKey,
    Text,
    Boolean,
    Integer,
    String,
    DateTime,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session
from pathlib import Path


class Base(DeclarativeBase):
    pass


class TransformType(str, Enum):
    KEYWORD_WHITELIST = "keyword_whitelist"
    KEYWORD_BLACKLIST = "keyword_blacklist"
    TEXT_REPLACE = "text_replace"
    ADD_PREFIX = "add_prefix"
    ADD_SUFFIX = "add_suffix"
    STRIP_LINKS = "strip_links"
    STRIP_MENTIONS = "strip_mentions"


class DestinationType(str, Enum):
    DISCORD_WEBHOOK = "discord_webhook"
    TELEGRAM_CHAT = "telegram_chat"


class TelegramChannel(Base):
    __tablename__ = "telegram_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mappings: Mapped[list["ChannelMapping"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    route_mappings: Mapped[list["RouteMapping"]] = relationship(
        back_populates="source_channel", cascade="all, delete-orphan"
    )


class DiscordWebhook(Base):
    __tablename__ = "discord_webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mappings: Mapped[list["ChannelMapping"]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )


class ForwardingGroup(Base):
    __tablename__ = "forwarding_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mappings: Mapped[list["ChannelMapping"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    transforms: Mapped[list["TransformRule"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class ChannelMapping(Base):
    __tablename__ = "channel_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_channels.id", ondelete="CASCADE")
    )
    webhook_id: Mapped[int] = mapped_column(
        ForeignKey("discord_webhooks.id", ondelete="CASCADE")
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("forwarding_groups.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped["TelegramChannel"] = relationship(back_populates="mappings")
    webhook: Mapped["DiscordWebhook"] = relationship(back_populates="mappings")
    group: Mapped[Optional["ForwardingGroup"]] = relationship(back_populates="mappings")


class Destination(Base):
    __tablename__ = "destinations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    destination_type: Mapped[str] = mapped_column(String(50), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    discord_config: Mapped[Optional["DestinationDiscord"]] = relationship(
        back_populates="destination", cascade="all, delete-orphan", uselist=False
    )
    telegram_config: Mapped[Optional["DestinationTelegram"]] = relationship(
        back_populates="destination", cascade="all, delete-orphan", uselist=False
    )
    route_mappings: Mapped[list["RouteMapping"]] = relationship(
        back_populates="destination", cascade="all, delete-orphan"
    )


class DestinationDiscord(Base):
    __tablename__ = "destination_discord"

    destination_id: Mapped[int] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), primary_key=True
    )
    webhook_url: Mapped[str] = mapped_column(Text)
    legacy_webhook_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("discord_webhooks.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )

    destination: Mapped["Destination"] = relationship(back_populates="discord_config")


class DestinationTelegram(Base):
    __tablename__ = "destination_telegram"

    destination_id: Mapped[int] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    topic_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    destination: Mapped["Destination"] = relationship(back_populates="telegram_config")


class RouteMapping(Base):
    __tablename__ = "route_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_channels.id", ondelete="CASCADE")
    )
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("forwarding_groups.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    legacy_channel_mapping_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channel_mappings.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )

    source_channel: Mapped["TelegramChannel"] = relationship(
        back_populates="route_mappings"
    )
    destination: Mapped["Destination"] = relationship(back_populates="route_mappings")
    group: Mapped[Optional["ForwardingGroup"]] = relationship()


class TransformRule(Base):
    __tablename__ = "transform_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("forwarding_groups.id", ondelete="CASCADE"), nullable=True
    )
    mapping_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channel_mappings.id", ondelete="CASCADE"), nullable=True
    )
    transform_type: Mapped[str] = mapped_column(String(50))
    pattern: Mapped[str] = mapped_column(Text)
    replacement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    group: Mapped[Optional["ForwardingGroup"]] = relationship(
        back_populates="transforms"
    )


class ForwardLog(Base):
    __tablename__ = "forward_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_id: Mapped[int] = mapped_column(
        ForeignKey("channel_mappings.id", ondelete="CASCADE")
    )
    telegram_message_id: Mapped[int] = mapped_column(Integer, index=True)
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transformed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forwarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ForwardLogV2(Base):
    __tablename__ = "forward_logs_v2"

    id: Mapped[int] = mapped_column(primary_key=True)
    route_mapping_id: Mapped[int] = mapped_column(
        ForeignKey("route_mappings.id", ondelete="CASCADE"), index=True
    )
    telegram_message_id: Mapped[int] = mapped_column(Integer, index=True)
    destination_type: Mapped[str] = mapped_column(String(50), index=True)
    destination_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transformed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forwarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


def get_db_path(database_path: Optional[str] = None) -> Path:
    if database_path:
        db_path = Path(database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return db_path

    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "teleforward.db"


def get_engine(database_path: Optional[str] = None):
    db_path = get_db_path(database_path=database_path).resolve()
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        except Exception:
            pass

    return engine


def init_db(database_path: Optional[str] = None):
    engine = get_engine(database_path=database_path)
    Base.metadata.create_all(engine)
    _sync_v2_from_v1(engine)

    if os.name == "posix":
        try:
            db_path = get_db_path(database_path=database_path)
            if db_path.exists():
                os.chmod(db_path, 0o600)
        except OSError:
            pass
    return engine


def _sync_v2_from_v1(engine) -> None:
    """Idempotent v1 -> v2 sync for Discord destinations and route mappings."""

    with engine.begin() as conn:
        required_tables = (
            "discord_webhooks",
            "channel_mappings",
            "destinations",
            "destination_discord",
            "route_mappings",
        )
        for table_name in required_tables:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name=:name LIMIT 1"
                ),
                {"name": table_name},
            ).scalar()
            if not exists:
                return

        webhook_rows = conn.execute(
            text(
                "SELECT id, name, url, is_active, created_at "
                "FROM discord_webhooks"
            )
        ).mappings()

        # v2 is now the source of truth.
        # This sync is intentionally "insert-only" for legacy backfill:
        # - create missing v2 records from v1 rows
        # - do not overwrite existing v2 rows
        # - do not delete existing v2 rows
        for row in webhook_rows:
            destination_id = conn.execute(
                text(
                    "SELECT destination_id FROM destination_discord "
                    "WHERE legacy_webhook_id = :legacy_webhook_id"
                ),
                {"legacy_webhook_id": row["id"]},
            ).scalar()

            if destination_id is None:
                insert_result = conn.execute(
                    text(
                        "INSERT INTO destinations "
                        "(name, destination_type, is_active, created_at) "
                        "VALUES (:name, :destination_type, :is_active, :created_at)"
                    ),
                    {
                        "name": row["name"],
                        "destination_type": DestinationType.DISCORD_WEBHOOK.value,
                        "is_active": row["is_active"],
                        "created_at": row["created_at"],
                    },
                )
                destination_id = insert_result.lastrowid
                if destination_id is None:
                    destination_id = conn.execute(
                        text(
                            "SELECT id FROM destinations "
                            "WHERE destination_type = :destination_type "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {
                            "destination_type": DestinationType.DISCORD_WEBHOOK.value,
                        },
                    ).scalar()
                if destination_id is None:
                    continue

                conn.execute(
                    text(
                        "INSERT INTO destination_discord "
                        "(destination_id, webhook_url, legacy_webhook_id) "
                        "VALUES (:destination_id, :webhook_url, :legacy_webhook_id)"
                    ),
                    {
                        "destination_id": destination_id,
                        "webhook_url": row["url"],
                        "legacy_webhook_id": row["id"],
                    },
                )

        mapping_rows = conn.execute(
            text(
                "SELECT id, channel_id, webhook_id, group_id, is_active, created_at "
                "FROM channel_mappings"
            )
        ).mappings()

        for row in mapping_rows:
            destination_id = conn.execute(
                text(
                    "SELECT destination_id FROM destination_discord "
                    "WHERE legacy_webhook_id = :legacy_webhook_id"
                ),
                {"legacy_webhook_id": row["webhook_id"]},
            ).scalar()
            if destination_id is None:
                continue

            existing_route_id = conn.execute(
                text(
                    "SELECT id FROM route_mappings "
                    "WHERE legacy_channel_mapping_id = :legacy_mapping_id"
                ),
                {"legacy_mapping_id": row["id"]},
            ).scalar()

            params = {
                "source_channel_id": row["channel_id"],
                "destination_id": destination_id,
                "group_id": row["group_id"],
                "is_active": row["is_active"],
                "created_at": row["created_at"],
                "legacy_channel_mapping_id": row["id"],
            }
            if existing_route_id is None:
                conn.execute(
                    text(
                        "INSERT INTO route_mappings "
                        "("
                        "source_channel_id, destination_id, group_id, "
                        "is_active, created_at, legacy_channel_mapping_id"
                        ") VALUES ("
                        ":source_channel_id, :destination_id, :group_id, "
                        ":is_active, :created_at, :legacy_channel_mapping_id"
                        ")"
                    ),
                    params,
                )


def get_session(database_path: Optional[str] = None) -> Session:
    engine = get_engine(database_path=database_path)
    return Session(engine)
