import logging
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )


def main():
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
        sys.exit(1)

    config.ensure_directories()
    setup_logging(config.log_level)

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

    args = parser.parse_args(sys.argv[1:])
    cmd = args.cmd or "tui"

    db = Database(database_path=config.database_path)

    if cmd == "tui":
        asyncio.run(run_tui(config=config, db=db))
        return
    if cmd == "run":
        asyncio.run(run_headless(config=config, db=db))
        return
    if cmd == "doctor":
        from core.discord_sender import DiscordWebhookSender

        errors: list[str] = []
        warnings: list[str] = []

        session_string = (
            config.telegram_session_string
            if config.telegram_session_string is not None
            else db.get_setting("telegram_session_string")
        )

        print(f"DATABASE_PATH={config.database_path}")
        print(f"DATA_DIR={config.resolve_data_dir()}")

        if not session_string:
            warnings.append(
                "No Telegram session found. Run `python main.py tui` -> 'Login' to save one, "
                "or set TELEGRAM_SESSION_STRING."
            )

        channels = db.get_telegram_channels(active_only=True)
        webhooks = db.get_discord_webhooks(active_only=True)
        mappings = db.get_channel_mappings(active_only=True)

        if not channels:
            warnings.append("No active Telegram channels saved.")
        if not webhooks:
            warnings.append("No active Discord webhooks saved.")
        if not mappings:
            warnings.append("No active channel mappings saved.")

        sender = DiscordWebhookSender()
        for wh in webhooks:
            if not sender.is_discord_webhook_url(wh.url):
                errors.append(f"Webhook '{wh.name}' has an invalid URL.")

        if getattr(args, "test_webhooks", False):
            async def _test_all():
                for wh in webhooks:
                    ok, why = await sender.test_webhook(wh.url)
                    if not ok:
                        errors.append(f"Webhook '{wh.name}' test failed: {why}")
                await sender.close()

            asyncio.run(_test_all())

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
