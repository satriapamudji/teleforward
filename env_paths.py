import os
from pathlib import Path
from typing import Optional


SYSTEM_ENV_FILE = Path("/etc/teleforward/teleforward.env")


def resolve_env_file_path(cwd: Optional[Path] = None) -> Path:
    override = (os.getenv("TELEFORWARD_ENV_FILE") or "").strip()
    if override:
        return Path(override).expanduser()

    if os.name == "posix" and SYSTEM_ENV_FILE.exists():
        return SYSTEM_ENV_FILE

    base = cwd or Path.cwd()
    return base / ".env"

