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

    if os.name == "posix":
        try:
            db_path = get_db_path(database_path=database_path)
            if db_path.exists():
                os.chmod(db_path, 0o600)
        except OSError:
            pass
    return engine


def get_session(database_path: Optional[str] = None) -> Session:
    engine = get_engine(database_path=database_path)
    return Session(engine)
