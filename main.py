import logging
import sys
import argparse
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, set_key, unset_key

sys.path.insert(0, str(Path(__file__).parent))


EDITABLE_ENV_KEYS = (
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_SESSION_STRING",
    "DATABASE_PATH",
    "DATA_DIR",
    "LOG_LEVEL",
    "DISCORD_ALLOW_MASS_MENTIONS",
    "DISCORD_SUPPRESS_URL_EMBEDS",
    "DISCORD_STRIP_URLS",
    "DISCORD_INCLUDE_TELEGRAM_LINK",
)


def _env_path() -> Path:
    return Path.cwd() / ".env"


def _mask_secret(value: Optional[str], keep_start: int = 8, keep_end: int = 6) -> str:
    if not value:
        return "(unset)"
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}...{value[-keep_end:]}"


def _load_env_values() -> dict[str, str]:
    raw = dotenv_values(dotenv_path=_env_path())
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key and value is not None:
            out[str(key)] = str(value)
    return out


def _set_env_value(key: str, value: str) -> None:
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()
    set_key(str(env_path), key, value, quote_mode="auto")


def _unset_env_value(key: str) -> None:
    env_path = _env_path()
    if env_path.exists():
        unset_key(str(env_path), key)


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )
    # Avoid leaking secrets like Discord webhook tokens via HTTP request logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="teleforward")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("tui", help="Run interactive terminal UI (default)")
    sub.add_parser("run", help="Run headless forwarder (VPS)")

    doctor_parser = sub.add_parser("doctor", help="Validate configuration")
    doctor_parser.add_argument(
        "--test-webhooks",
        action="store_true",
        help="Make network requests to verify webhooks",
    )
    doctor_parser.add_argument(
        "--test-telegram-destinations",
        action="store_true",
        help="Connect to Telegram and validate configured Telegram destinations",
    )
    doctor_parser.add_argument(
        "--sync-session-to-env",
        action="store_true",
        help=(
            "If TELEGRAM_SESSION_STRING is only in database settings, "
            "write it to .env."
        ),
    )

    config_parser = sub.add_parser("config", help="Manage .env settings")
    config_sub = config_parser.add_subparsers(dest="config_cmd")

    config_sub.add_parser("show", help="Show editable .env keys")

    config_set = config_sub.add_parser("set", help="Set a key in .env")
    config_set.add_argument("key", choices=EDITABLE_ENV_KEYS)
    config_set.add_argument("value")

    config_unset = config_sub.add_parser("unset", help="Remove a key from .env")
    config_unset.add_argument("key", choices=EDITABLE_ENV_KEYS)

    migrate_parser = sub.add_parser(
        "migrate", help="v1/v2 migration and compatibility checks"
    )
    migrate_sub = migrate_parser.add_subparsers(dest="migrate_cmd")
    migrate_sub.add_parser(
        "verify-v2",
        help="Verify legacy v1 rows are mirrored into v2 destinations/routes",
    )

    return parser


def _run_config_command(args: argparse.Namespace) -> None:
    if not args.config_cmd:
        print("Usage: teleforward config [show|set|unset] ...")
        return

    if args.config_cmd == "show":
        values = _load_env_values()
        print(f"ENV_PATH={_env_path()}")
        for key in EDITABLE_ENV_KEYS:
            value = values.get(key)
            if key in {"TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING"}:
                shown = _mask_secret(value)
            else:
                shown = value or "(unset)"
            print(f"{key}={shown}")
        return

    if args.config_cmd == "set":
        _set_env_value(args.key, args.value)
        print(f"Updated {args.key} in {_env_path()}")
        return

    if args.config_cmd == "unset":
        _unset_env_value(args.key)
        print(f"Removed {args.key} from {_env_path()}")
        return

    print("Unknown config subcommand.")


def _run_migrate_command(args: argparse.Namespace, db) -> None:
    if not args.migrate_cmd:
        print("Usage: teleforward migrate verify-v2")
        return

    if args.migrate_cmd == "verify-v2":
        report = db.get_legacy_migration_report()
        print("LEGACY_POLICY=compat_history_frozen")
        print(
            "LEGACY_RETIREMENT_NOTE=v1 tables stay as compatibility history in v2.0; "
            "no destructive drop command is provided."
        )
        print(f"LEGACY_WEBHOOKS_TOTAL={report['legacy_webhooks_total']}")
        print(f"MIRRORED_DESTINATIONS_TOTAL={report['mirrored_destinations_total']}")
        print(f"LEGACY_MAPPINGS_TOTAL={report['legacy_mappings_total']}")
        print(f"MIRRORED_ROUTES_TOTAL={report['mirrored_routes_total']}")
        print(
            "LEGACY_TRANSFORM_RULES_LINKED_TOTAL="
            f"{report['legacy_transform_rules_linked_total']}"
        )

        failures: list[str] = []
        if report["missing_webhook_ids"]:
            failures.append(f"missing webhook mirrors: {report['missing_webhook_ids']}")
        if report["missing_mapping_ids"]:
            failures.append(f"missing route mirrors: {report['missing_mapping_ids']}")
        if report["orphaned_webhook_mirror_ids"]:
            failures.append(
                "orphaned destination_discord legacy refs: "
                f"{report['orphaned_webhook_mirror_ids']}"
            )
        if report["orphaned_mapping_mirror_ids"]:
            failures.append(
                "orphaned route_mappings legacy refs: "
                f"{report['orphaned_mapping_mirror_ids']}"
            )
        if report["unmatched_transform_mapping_ids"]:
            failures.append(
                "transform rules reference missing legacy mappings: "
                f"{report['unmatched_transform_mapping_ids']}"
            )

        if failures:
            print("VERIFY_V2=FAILED")
            for item in failures:
                print(f"  - {item}")
            sys.exit(1)

        print("VERIFY_V2=OK")
        return

    print("Unknown migrate subcommand.")


def main():
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:])
    cmd = args.cmd or "tui"

    if cmd == "config":
        _run_config_command(args)
        return
    if cmd == "migrate":
        from database.db import Database

        raw_env = _load_env_values()
        database_path = raw_env.get("DATABASE_PATH")
        db = Database(database_path=database_path)
        _run_migrate_command(args, db)
        return

    from config import get_config
    from database.db import Database
    from tui.app import run_tui, run_headless
    import asyncio

    try:
        config = get_config()
    except ValueError as e:
        print(f"Configuration error: {e}")
        print("\nPlease set the following environment variables:")
        print("  TELEGRAM_API_ID=your_api_id")
        print("  TELEGRAM_API_HASH=your_api_hash")
        print("\nYou can manage .env from CLI:")
        print("  teleforward config show")
        print("  teleforward config set TELEGRAM_API_ID 12345")
        print("  teleforward config set TELEGRAM_API_HASH your_hash")
        sys.exit(1)

    config.ensure_directories()
    setup_logging(config.log_level)

    db = Database(database_path=config.database_path)

    if cmd == "tui":
        asyncio.run(run_tui(config=config, db=db))
        return
    if cmd == "run":
        asyncio.run(run_headless(config=config, db=db))
        return
    if cmd == "doctor":
        from core.discord_sender import DiscordWebhookSender
        from core.telegram_client import TelegramClientWrapper
        from database.models import DestinationType

        errors: list[str] = []
        warnings: list[str] = []

        session_string = (
            config.telegram_session_string
            if config.telegram_session_string is not None
            else db.get_setting("telegram_session_string")
        )
        session_from_db = db.get_setting("telegram_session_string")
        session_source = (
            "env"
            if config.telegram_session_string is not None
            else ("db" if session_from_db else "none")
        )

        print(f"DATABASE_PATH={config.database_path}")
        print(f"DATA_DIR={config.resolve_data_dir()}")
        print(f"ENV_PATH={_env_path()}")
        print(f"TELEGRAM_SESSION_SOURCE={session_source}")

        if getattr(args, "sync_session_to_env", False):
            if config.telegram_session_string is None and session_from_db:
                _set_env_value("TELEGRAM_SESSION_STRING", session_from_db)
                print("Synced TELEGRAM_SESSION_STRING from database to .env")
            elif config.telegram_session_string is not None:
                print("Skipped sync: TELEGRAM_SESSION_STRING already set in .env")
            else:
                print("Skipped sync: no TELEGRAM_SESSION_STRING found in db or .env")

        if not session_string:
            warnings.append(
                "No Telegram session found. Run `python main.py tui` -> 'Login' to save one, "
                "or set TELEGRAM_SESSION_STRING."
            )

        channels = db.get_telegram_channels(active_only=True)
        destination_rows = db.get_destination_rows(active_only=True)
        route_rows = db.get_route_rows(active_only=True)

        print(f"ACTIVE_SOURCES={len(channels)}")
        print(f"ACTIVE_CHANNELS={len(channels)}")
        print(f"ACTIVE_DESTINATIONS={len(destination_rows)}")
        print(f"ACTIVE_ROUTES={len(route_rows)}")

        if not channels:
            warnings.append("No active Telegram channels saved.")
        if not destination_rows:
            warnings.append("No active destinations saved.")
        if not route_rows:
            warnings.append("No active route mappings saved.")

        sender = DiscordWebhookSender()
        for route in route_rows:
            destination_type = route.get("destination_type")
            destination_name = route.get("destination_name") or str(
                route.get("destination_id")
            )
            if destination_type == DestinationType.DISCORD_WEBHOOK.value:
                webhook_url = route.get("discord_webhook_url")
                if not webhook_url or not sender.is_discord_webhook_url(webhook_url):
                    errors.append(
                        f"Route destination '{destination_name}' has invalid Discord webhook URL."
                    )
            elif destination_type == DestinationType.TELEGRAM_CHAT.value:
                if route.get("telegram_chat_id") is None:
                    errors.append(
                        f"Route destination '{destination_name}' missing telegram_chat_id."
                    )

        if getattr(args, "test_webhooks", False):
            discord_webhooks = sorted(
                {
                    str(row["discord_webhook_url"])
                    for row in destination_rows
                    if row.get("destination_type") == DestinationType.DISCORD_WEBHOOK.value
                    and row.get("discord_webhook_url")
                }
            )
            if not discord_webhooks:
                warnings.append("No Discord destinations found to test.")

            async def _test_all():
                for webhook_url in discord_webhooks:
                    ok, why = await sender.test_webhook(webhook_url)
                    if not ok:
                        errors.append(f"Discord destination webhook test failed: {why}")
                await sender.close()

            asyncio.run(_test_all())

        if getattr(args, "test_telegram_destinations", False):
            telegram_targets = sorted(
                {
                    int(r["telegram_chat_id"])
                    for r in route_rows
                    if r.get("destination_type") == DestinationType.TELEGRAM_CHAT.value
                    and r.get("telegram_chat_id") is not None
                }
            )
            if not telegram_targets:
                warnings.append("No Telegram destinations found to test.")
            elif not session_string:
                errors.append(
                    "Cannot test Telegram destinations: no TELEGRAM_SESSION_STRING available."
                )
            else:
                async def _test_telegram_destinations():
                    telegram = TelegramClientWrapper(
                        api_id=config.telegram_api_id,
                        api_hash=config.telegram_api_hash,
                        session_string=session_string,
                        data_dir=config.resolve_data_dir(),
                    )
                    try:
                        await telegram.start(phone=None)
                        for chat_id in telegram_targets:
                            info = await telegram.get_channel_info(chat_id)
                            if not info:
                                errors.append(
                                    f"Telegram destination chat_id={chat_id} not reachable."
                                )
                    except Exception as e:
                        errors.append(f"Telegram destination test failed: {e}")
                    finally:
                        try:
                            await telegram.stop()
                        except Exception:
                            pass

                asyncio.run(_test_telegram_destinations())

        if warnings:
            print("Warnings:")
            for w in warnings:
                print(f"  - {w}")
        if errors:
            print("Errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)

        print("OK: configuration looks good.")
        return

    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
