import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Config:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_string: Optional[str] = None
    database_path: str = "data/teleforward.db"
    data_dir: Optional[str] = None
    log_level: str = "INFO"
    discord_allow_mass_mentions: bool = False
    discord_suppress_url_embeds: bool = True
    discord_strip_urls: bool = False
    discord_include_telegram_link: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        api_id_str = os.getenv("TELEGRAM_API_ID", "")
        if not api_id_str:
            raise ValueError("TELEGRAM_API_ID environment variable required")

        api_hash = os.getenv("TELEGRAM_API_HASH", "")
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH environment variable required")

        session_string = os.getenv("TELEGRAM_SESSION_STRING")
        if session_string == "":
            session_string = None

        return cls(
            telegram_api_id=int(api_id_str),
            telegram_api_hash=api_hash,
            telegram_session_string=session_string,
            database_path=os.getenv("DATABASE_PATH", "data/teleforward.db"),
            data_dir=os.getenv("DATA_DIR") or None,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            discord_allow_mass_mentions=os.getenv(
                "DISCORD_ALLOW_MASS_MENTIONS", "false"
            ).lower()
            == "true",
            discord_suppress_url_embeds=os.getenv(
                "DISCORD_SUPPRESS_URL_EMBEDS", "true"
            ).lower()
            == "true",
            discord_strip_urls=os.getenv("DISCORD_STRIP_URLS", "false").lower()
            == "true",
            discord_include_telegram_link=os.getenv(
                "DISCORD_INCLUDE_TELEGRAM_LINK", "true"
            ).lower()
            == "true",
        )

    def resolve_data_dir(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        return Path(self.database_path).parent

    def ensure_directories(self):
        data_dir = self.resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        downloads_dir = data_dir / "downloads"
        downloads_dir.mkdir(exist_ok=True)


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def set_config(config: Config):
    global _config
    _config = config
