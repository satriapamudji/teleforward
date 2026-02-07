import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass
from getpass import getpass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Callable, Optional
from dotenv import dotenv_values, set_key, unset_key

from rich.console import Console, Group
from rich.columns import Columns
from rich import box
from rich.align import Align
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from config import Config
from core.discord_sender import DiscordWebhookSender, discord_sender, DiscordMessage
from core.forwarder import Forwarder
from core.telegram_sender import TelegramOutgoingMessage, telegram_destination_sender
from core.telegram_client import TelegramClientWrapper, set_telegram_client
from database.db import Database
from database.models import DestinationType


logger = logging.getLogger(__name__)

THEME = Theme(
    {
        "title": "bold white",
        "accent": "cyan",
        "border": "bright_black",
        "muted": "dim",
        "ok": "green",
        "error": "bold red",
        "warn": "yellow",
        "info": "dim cyan",
        "key": "bold cyan",
        "value": "white",
        "status.active": "green",
        "status.disabled": "dim red",
        "heading": "bold",
    }
)

console = Console(theme=THEME)


def _w() -> int:
    """Current terminal width (re-read on every call)."""
    return console.size.width


def _feedback(
    msg: str,
    *,
    title: str | None = None,
) -> Panel:
    """Compact one-liner panel that doesn't stretch to full terminal width."""
    return Panel.fit(
        msg,
        title=title,
        border_style="bright_black",
        padding=(0, 1),
    )


async def _read_home_choice_live(render_home: Callable[[str], Panel]) -> str:
    """Live-updating menu input for the home screen."""
    if not sys.stdin.isatty():
        return Prompt.ask("Select (0-17, q=exit)", default="").strip()

    if os.name == "nt":
        import msvcrt

        typed = ""
        with Live(
            Align.center(render_home(typed)),
            console=console,
            refresh_per_second=20,
            auto_refresh=False,
        ) as live:
            while True:
                live.update(Align.center(render_home(typed)), refresh=True)

                while msvcrt.kbhit():
                    ch = msvcrt.getwch()

                    # Extended keys (arrows/function keys): ignore payload char.
                    if ch in {"\x00", "\xe0"}:
                        if msvcrt.kbhit():
                            _ = msvcrt.getwch()
                        continue

                    if ch == "\x03":
                        raise KeyboardInterrupt
                    if ch in {"\r", "\n"}:
                        return typed.strip()
                    if ch == "\x1b":
                        return "q"
                    if ch in {"\b", "\x7f"}:
                        typed = typed[:-1]
                        continue
                    if ch.isprintable():
                        typed += ch

                await asyncio.sleep(0.05)

    import select
    import termios
    import tty

    typed = ""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    with Live(
        Align.center(render_home(typed)),
        console=console,
        refresh_per_second=20,
        auto_refresh=False,
    ) as live:
        try:
            tty.setcbreak(fd)
            while True:
                live.update(Align.center(render_home(typed)), refresh=True)

                ready, _, _ = select.select([fd], [], [], 0)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    if ch in {"\r", "\n"}:
                        return typed.strip()
                    if ch == "\x1b":
                        # Bare ESC quits; arrow/function escape sequences are ignored.
                        next_ready, _, _ = select.select([fd], [], [], 0.01)
                        if not next_ready:
                            return "q"
                        seq_head = sys.stdin.read(1)
                        if seq_head == "[":
                            while True:
                                seq_ready, _, _ = select.select([fd], [], [], 0)
                                if not seq_ready:
                                    break
                                seq_ch = sys.stdin.read(1)
                                if seq_ch.isalpha() or seq_ch == "~":
                                    break
                            continue
                        continue
                    if ch in {"\b", "\x7f"}:
                        typed = typed[:-1]
                        continue
                    if ch.isprintable():
                        typed += ch

                await asyncio.sleep(0.05)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


class CancelAction(Exception):
    pass


def _app_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        return package_version("teleforward")
    except PackageNotFoundError:
        return "unknown"


def _pause() -> None:
    try:
        input("\nPress Enter to continue...")
    except KeyboardInterrupt:
        raise


def _prompt(prompt: str, default: Optional[str] = None) -> str:
    if default is None:
        value = Prompt.ask(prompt)
    else:
        value = Prompt.ask(prompt, default=default, show_default=(default != ""))
    value = (value or "").strip()
    if value.lower() in {"q", "quit", "cancel"}:
        raise CancelAction()
    return value


def _prompt_int(prompt: str) -> int:
    while True:
        try:
            raw = IntPrompt.ask(prompt)
        except KeyboardInterrupt:
            raise
        except Exception:
            console.print(_feedback("[warn]⚠[/warn] Please enter a valid integer."))
            continue
        try:
            return int(raw)
        except ValueError:
            console.print(_feedback("[warn]⚠[/warn] Please enter a valid integer."))


def _choose_index(prompt: str, max_index: int) -> int:
    while True:
        try:
            raw = Prompt.ask(prompt).strip()
        except KeyboardInterrupt:
            raise
        if raw.lower() in {"q", "quit", "cancel"}:
            raise CancelAction()
        try:
            idx = int(raw)
        except ValueError:
            console.print(_feedback("[warn]⚠[/warn] Please enter a number."))
            continue
        if 1 <= idx <= max_index:
            return idx
        console.print(
            _feedback(
                f"[warn]⚠[/warn] Please enter a number between 1 and {max_index}."
            )
        )


def _choose_many_indexes(prompt: str, max_index: int) -> list[int]:
    while True:
        try:
            raw = Prompt.ask(prompt).strip()
        except KeyboardInterrupt:
            raise
        if raw.lower() in {"a", "all", "*"}:
            return list(range(1, max_index + 1))
        if raw.lower() in {"q", "quit", "cancel"}:
            raise CancelAction()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        try:
            idxs = sorted({int(p) for p in parts})
        except ValueError:
            console.print(
                _feedback(
                    "[warn]⚠[/warn] Please enter a comma-separated list like: 1,2,5 (or 'all')."
                )
            )
            continue
        if not idxs:
            console.print(_feedback("[warn]⚠[/warn] Select at least one item."))
            continue
        if any(i < 1 or i > max_index for i in idxs):
            console.print(
                _feedback(f"[warn]⚠[/warn] Indexes must be between 1 and {max_index}.")
            )
            continue
        return idxs


@dataclass
class TuiContext:
    config: Config
    db: Database
    telegram: TelegramClientWrapper
    env_path: Path


async def _ensure_telegram_connected(ctx: TuiContext, interactive: bool) -> bool:
    try:
        await ctx.telegram.start(
            phone=None,
            code_callback=None,
            password_callback=None,
        )
        # If the session was created as a file session, proactively export it and
        # store it in the DB so headless runs can use it.
        session = ctx.telegram.export_session_string()
        if session:
            ctx.db.set_setting("telegram_session_string", session)
        return True
    except ValueError:
        if not interactive:
            return False

    console.print(_feedback("[info]●[/info] Telegram login (enter 'q' to cancel)."))
    phone = _prompt("Telegram phone (e.g. +15551234567)")

    async def code_callback() -> str:
        return _prompt("Telegram login code")

    async def password_callback() -> str:
        return getpass("Telegram 2FA password (if enabled): ")

    await ctx.telegram.start(
        phone=phone,
        code_callback=code_callback,
        password_callback=password_callback,
    )

    session = ctx.telegram.export_session_string()
    if session:
        ctx.db.set_setting("telegram_session_string", session)
        console.print(
            _feedback("[ok]✔[/ok] Saved Telegram session string to the database.")
        )
    return True


def _print_channels(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels()
    if not channels:
        console.print(
            _feedback("[warn]⚠[/warn] No source channels saved.", title="Sources")
        )
        return
    table = Table(
        title="Source channels",
        show_lines=False,
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Username", style="dim", ratio=1, overflow="fold")
    table.add_column("Channel ID", style="value", overflow="ellipsis", max_width=18)
    table.add_column("Status", no_wrap=True)
    for i, ch in enumerate(channels, start=1):
        status = (
            Text("✔ active", style="ok")
            if ch.is_active
            else Text("○ disabled", style="status.disabled")
        )
        table.add_row(
            str(i),
            ch.name,
            f"@{ch.username}" if ch.username else "-",
            str(ch.channel_id),
            status,
        )
    console.print(table)


def _print_webhooks(ctx: TuiContext) -> None:
    webhooks = ctx.db.get_discord_webhooks()
    if not webhooks:
        console.print(
            _feedback("[warn]⚠[/warn] No Discord webhooks saved.", title="Discord")
        )
        return
    table = Table(
        title="Discord webhooks",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Status", no_wrap=True)
    for i, wh in enumerate(webhooks, start=1):
        status = (
            Text("✔ active", style="ok")
            if wh.is_active
            else Text("○ disabled", style="status.disabled")
        )
        table.add_row(str(i), wh.name, status)
    console.print(table)


async def _login_status(ctx: TuiContext) -> None:
    try:
        if not await _ensure_telegram_connected(ctx, interactive=False):
            console.print(
                _feedback(
                    "[warn]⚠[/warn] Not connected (no valid session).",
                    title="Telegram",
                )
            )
            return
        me = await ctx.telegram.get_me()
        if not me:
            console.print(
                _feedback(
                    "[warn]⚠[/warn] Connected, but not authorized.",
                    title="Telegram",
                )
            )
            return
        console.print(
            _feedback(
                f"[ok]✔[/ok] Connected as [bold]{me.first_name}[/bold] (id={me.id})",
                title="Telegram",
            )
        )
    except Exception as e:
        console.print(_feedback(f"[error]✘[/error] {e}", title="Telegram status error"))


async def _export_telegram_session(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=True)
    if not ok:
        console.print(_feedback("[warn]⚠[/warn] Telegram login required."))
        return

    session = ctx.telegram.export_session_string()
    if not session:
        console.print(
            _feedback(
                "[error]✘[/error] Could not read a Telegram session string from the client.",
                title="Telegram session",
            )
        )
        return

    preview = session[:24] + "..." + session[-12:]
    console.print(
        Panel.fit(
            "This session string is equivalent to your Telegram login.\n"
            "Treat it like a password and store it as a secret.\n\n"
            f"[dim]Preview[/dim]=[white]{preview}[/white]\n"
            f"[dim]Length[/dim]=[white]{len(session)}[/white]",
            title="Export Telegram session string",
            border_style="bright_black",
        )
    )

    try:
        reveal = _prompt(
            "Reveal full session string? (y/n, default n)", default="n"
        ).lower()
    except CancelAction:
        return
    if reveal not in {"y", "yes"}:
        console.print(_feedback("[info]●[/info] Not revealed."))
        return

    console.print(
        _feedback(
            f"TELEGRAM_SESSION_STRING={session}",
            title="Copy into /etc/teleforward/teleforward.env",
        )
    )


async def _import_channels(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=True)
    if not ok:
        console.print(_feedback("[warn]⚠[/warn] Telegram login required."))
        return

    max_fetch = 10_000
    batch_size = 400

    dialog_iter = ctx.telegram.iter_dialogs(limit=max_fetch)
    dialogs: list[dict] = []
    dialogs_by_id: dict[int, dict] = {}
    exhausted = False

    async def fetch_more(target_total: int) -> None:
        nonlocal exhausted
        if exhausted:
            return
        while len(dialogs) < target_total:
            try:
                d = await dialog_iter.__anext__()  # type: ignore[attr-defined]
            except StopAsyncIteration:
                exhausted = True
                break
            dialogs.append(d)
            dialogs_by_id[d["id"]] = d

    await fetch_more(batch_size)
    if not dialogs:
        console.print(_feedback("[warn]⚠[/warn] No dialogs found."))
        return
    selected_ids: set[int] = set()
    query = ""
    page = 0
    page_size = 20

    def apply_filter() -> list[dict]:
        if not query:
            return dialogs
        out = []
        q = query.lower()
        for d in dialogs:
            hay = " ".join(
                [
                    str(d.get("name", "")),
                    str(d.get("username", "")),
                    str(d.get("id", "")),
                    str(d.get("type", "")),
                ]
            ).lower()
            if q in hay:
                out.append(d)
        return out

    while True:
        console.clear()
        loaded = len(dialogs)
        console.print(
            Panel.fit(
                f"[bold]Import from Telegram[/bold]\n\n"
                f"[dim]Loaded[/dim]=[white]{loaded}[/white]  "
                f"[dim]Filtered[/dim]=[white]{len(apply_filter())}[/white]  "
                f"[dim]Selected[/dim]=[white]{len(selected_ids)}[/white]  "
                f"[dim]More[/dim]=[white]{'yes' if not exhausted else 'no'}[/white]\n"
                f"[dim]Search[/dim]=[white]{query or '(all)'}[/white]",
                border_style="bright_black",
            )
        )
        filtered = apply_filter()
        if not filtered:
            console.print(_feedback("[warn]⚠[/warn] No matches. Change search."))
            try:
                query = _prompt("Search query (blank=all)", default="")
            except CancelAction:
                return
            page = 0
            continue

        total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        end = min(start + page_size, len(filtered))
        page_items = filtered[start:end]

        table = Table(
            title="Telegram dialogs",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            expand=True,
        )
        table.add_column("#", style="key", no_wrap=True, width=4)
        table.add_column("Sel", style="ok", no_wrap=True, width=3)
        table.add_column("Name", style="heading", ratio=1, overflow="fold")
        table.add_column("Username", style="dim", ratio=1, overflow="fold")
        table.add_column("ID", style="value", overflow="ellipsis", max_width=18)
        table.add_column("Type", style="dim", no_wrap=True)
        for i, d in enumerate(page_items, start=1):
            did = d["id"]
            sel = "x" if did in selected_ids else ""
            table.add_row(
                str(i),
                sel,
                str(d.get("name", "")),
                f"@{d['username']}" if d.get("username") else "-",
                str(did),
                str(d.get("type", "")),
            )
        console.print(table)
        console.print(
            Panel.fit(
                "Commands:\n"
                "  - numbers (e.g. 1,3,5): toggle selection on this page\n"
                "  - n / p: next / previous page\n"
                "  - s: set search query\n"
                "  - c: clear selection\n"
                "  - i: import selected\n"
                "  - q: cancel\n",
                title=f"Page {page + 1}/{total_pages}  (showing {start + 1}-{end})",
                border_style="bright_black",
            )
        )

        try:
            cmd = _prompt("Import command", default="").strip()
        except CancelAction:
            return

        if cmd == "":
            continue
        low = cmd.lower()
        if low in {"n", "next"}:
            if page + 1 >= total_pages and not exhausted:
                await fetch_more(len(dialogs) + batch_size)
            page += 1
            continue
        if low in {"p", "prev", "previous"}:
            page -= 1
            continue
        if low in {"s", "search"}:
            try:
                query = _prompt("Search query (blank=all)", default="")
            except CancelAction:
                return
            page = 0
            continue
        if low in {"c", "clear"}:
            selected_ids.clear()
            continue
        if low in {"i", "import"}:
            if not selected_ids:
                console.print(_feedback("[warn]⚠[/warn] Nothing selected."))
                continue
            break

        # toggle selection on current page by index
        parts = [p.strip() for p in cmd.split(",") if p.strip()]
        try:
            idxs = sorted({int(p) for p in parts})
        except ValueError:
            console.print(_feedback("[warn]⚠[/warn] Unknown command."))
            continue
        if not idxs or any(i < 1 or i > len(page_items) for i in idxs):
            console.print(_feedback("[warn]⚠[/warn] Index out of range for this page."))
            continue
        for i in idxs:
            did = page_items[i - 1]["id"]
            if did in selected_ids:
                selected_ids.remove(did)
            else:
                selected_ids.add(did)

    imported = 0
    selected = [dialogs_by_id[i] for i in selected_ids if i in dialogs_by_id]
    for d in selected:
        existing = ctx.db.get_telegram_channel(d["id"])
        if existing is None:
            imported += 1
        ctx.db.add_telegram_channel(d["id"], d["name"], d.get("username"))

    console.print(
        _feedback(
            f"[ok]✔[/ok] Imported/updated [bold]{len(selected)}[/bold] items ([bold]{imported}[/bold] new).",
            title="Import complete",
        )
    )


def _add_channel_manual(ctx: TuiContext) -> None:
    try:
        channel_id = _prompt_int("Source Telegram channel id (e.g. -1001234567890)")
        name = _prompt("Name")
        username = _prompt("Username (optional, without @)", default="")
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    username = username or None
    if not name:
        console.print(_feedback("[warn]⚠[/warn] Name is required."))
        return
    ctx.db.add_telegram_channel(channel_id, name, username)
    console.print(_feedback("[ok]✔[/ok] Saved.", title="Telegram channel"))


async def _add_webhook(ctx: TuiContext) -> None:
    try:
        name = _prompt("Discord webhook name (label)")
        url = _prompt("Discord webhook URL")
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    if not name or not url:
        console.print(_feedback("[warn]⚠[/warn] Name and URL are required."))
        return

    ok, why = await discord_sender.test_webhook(url)
    if not ok:
        console.print(_feedback(f"[error]✘[/error] {why}", title="Webhook test failed"))
        return

    ctx.db.add_discord_webhook(name=name, url=url)
    console.print(_feedback("[ok]✔[/ok] Saved.", title="Webhook"))


async def _send_test_message(ctx: TuiContext) -> None:
    destinations = ctx.db.get_destination_rows(active_only=True)
    if not destinations:
        console.print(
            _feedback("[warn]⚠[/warn] No active destinations. Add one first.")
        )
        return

    table = Table(
        title="Destinations",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Type", style="dim", no_wrap=True)
    table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
    for i, row in enumerate(destinations, start=1):
        table.add_row(
            str(i),
            str(row.get("destination_name") or f"dest-{row.get('destination_id')}"),
            _destination_type_human(str(row.get("destination_type") or "")),
            _destination_target_label(row),
        )
    console.print(table)
    try:
        dest_idx = _choose_index(
            "Select destination (1..N, q=cancel)", len(destinations)
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    destination = destinations[dest_idx - 1]

    default = "teleforward test message"
    try:
        content = _prompt(f"Message content (blank for '{default}')", default="")
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    content = content or default

    destination_type = destination.get("destination_type")
    ok = False
    why: Optional[str] = None

    if destination_type == DestinationType.DISCORD_WEBHOOK.value:
        webhook_url = destination.get("discord_webhook_url")
        if not webhook_url:
            console.print(
                _feedback("[error]✘[/error] Destination is missing webhook URL.")
            )
            return
        ok, why = await discord_sender.send(
            str(webhook_url),
            message=DiscordMessage(
                content="",
                embeds=[
                    {
                        "title": "teleforward test",
                        "description": content,
                        "color": 0x5865F2,
                        "footer": {"text": "teleforward"},
                    }
                ],
            ),
        )
    elif destination_type == DestinationType.TELEGRAM_CHAT.value:
        connected = await _ensure_telegram_connected(ctx, interactive=True)
        if not connected:
            console.print(_feedback("[warn]⚠[/warn] Telegram login required."))
            return
        chat_id = destination.get("telegram_chat_id")
        if chat_id is None:
            console.print(
                _feedback("[error]✘[/error] Destination is missing telegram_chat_id.")
            )
            return
        ok, why = await telegram_destination_sender.send(
            telegram=ctx.telegram,
            chat_id=int(chat_id),
            message=TelegramOutgoingMessage(
                text=content,
                topic_id=destination.get("telegram_topic_id"),
            ),
        )
    else:
        console.print(_feedback("[error]✘[/error] Unsupported destination type."))
        return

    if ok:
        console.print(_feedback("[ok]✔[/ok] Test message sent OK."))
    else:
        console.print(_feedback(f"[error]✘[/error] {why}", title="Test message failed"))


def _manage_channels(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels(active_only=False)
    if not channels:
        console.print(_feedback("[warn]⚠[/warn] No source channels saved."))
        return

    table = Table(
        title="Manage source channels",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Username", style="dim", ratio=1, overflow="fold")
    table.add_column("Channel ID", style="value", overflow="ellipsis", max_width=18)
    table.add_column("Status", no_wrap=True)
    for i, ch in enumerate(channels, start=1):
        status = (
            Text("✔ active", style="ok")
            if ch.is_active
            else Text("○ disabled", style="status.disabled")
        )
        table.add_row(
            str(i),
            ch.name,
            f"@{ch.username}" if ch.username else "-",
            str(ch.channel_id),
            status,
        )
    console.print(table)

    try:
        idx = _choose_index("Select channel (1..N, q=back)", len(channels))
    except CancelAction:
        return
    ch = channels[idx - 1]

    actions = Table(title=f"Channel: {ch.name}", box=box.SIMPLE, show_header=False)
    actions.add_column("Key", style="key", no_wrap=True, width=4)
    actions.add_column("Action", style="white")
    actions.add_row("1", "Toggle active/disabled")
    actions.add_row("2", "Rename / update username")
    actions.add_row("3", "Delete channel (and its mappings/logs)")
    actions.add_row("0", "Back")
    console.print(actions)

    try:
        choice = _prompt("Action (0-3)", default="0")
    except CancelAction:
        return

    if choice == "1":
        ctx.db.toggle_telegram_channel(ch.id, not ch.is_active)
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return
    if choice == "2":
        try:
            new_name = _prompt("New name (blank=keep)", default="")
            new_username = _prompt("New username (blank=keep, '-'=clear)", default="")
        except CancelAction:
            return

        name_to_set = ch.name if new_name == "" else new_name
        if new_username == "":
            username_to_set = ch.username
        elif new_username == "-":
            username_to_set = None
        else:
            username_to_set = new_username.lstrip("@")

        ctx.db.update_telegram_channel(
            ch.id, name=name_to_set, username=username_to_set
        )
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return
    if choice == "3":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return
        ctx.db.delete_telegram_channel(ch.id)
        console.print(_feedback("[ok]✔[/ok] Deleted."))
        return


async def _manage_webhooks(ctx: TuiContext) -> None:
    webhooks = ctx.db.get_discord_webhooks(active_only=False)
    if not webhooks:
        console.print(_feedback("[warn]⚠[/warn] No Discord webhooks saved."))
        return

    table = Table(
        title="Manage Discord webhooks",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Status", no_wrap=True)
    for i, wh in enumerate(webhooks, start=1):
        status = (
            Text("✔ active", style="ok")
            if wh.is_active
            else Text("○ disabled", style="status.disabled")
        )
        table.add_row(str(i), wh.name, status)
    console.print(table)

    try:
        idx = _choose_index("Select webhook (1..N, q=back)", len(webhooks))
    except CancelAction:
        return
    wh = webhooks[idx - 1]

    actions = Table(title=f"Webhook: {wh.name}", box=box.SIMPLE, show_header=False)
    actions.add_column("Key", style="key", no_wrap=True, width=4)
    actions.add_column("Action", style="white")
    actions.add_row("1", "Toggle active/disabled")
    actions.add_row("2", "Rename")
    actions.add_row("3", "Update URL (and test)")
    actions.add_row("4", "Delete webhook (and its mappings/logs)")
    actions.add_row("0", "Back")
    console.print(actions)

    try:
        choice = _prompt("Action (0-4)", default="0")
    except CancelAction:
        return

    if choice == "1":
        ctx.db.toggle_discord_webhook(wh.id, not wh.is_active)
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return
    if choice == "2":
        try:
            new_name = _prompt("New name", default=wh.name)
        except CancelAction:
            return
        ctx.db.update_discord_webhook(wh.id, name=new_name)
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return
    if choice == "3":
        try:
            new_url = _prompt("New webhook URL")
        except CancelAction:
            return
        ok, why = await discord_sender.test_webhook(new_url)
        if not ok:
            console.print(
                _feedback(f"[error]✘[/error] {why}", title="Webhook test failed")
            )
            return
        ctx.db.update_discord_webhook(wh.id, url=new_url)
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return
    if choice == "4":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return
        ctx.db.delete_discord_webhook(wh.id)
        console.print(_feedback("[ok]✔[/ok] Deleted."))
        return


def _manage_mappings(ctx: TuiContext) -> None:
    channels = {c.id: c for c in ctx.db.get_telegram_channels(active_only=False)}
    webhooks = ctx.db.get_discord_webhooks(active_only=False)
    if not webhooks:
        console.print(_feedback("[warn]⚠[/warn] No Discord webhooks saved."))
        return

    def parse_rows(raw: str, max_rows: int) -> Optional[list[int]]:
        try:
            idxs = sorted({int(p.strip()) for p in raw.split(",") if p.strip()})
        except ValueError:
            return None
        if not idxs:
            return None
        if any(i < 1 or i > max_rows for i in idxs):
            return None
        return idxs

    def select_rows_dialog(title: str, grouped: list) -> list[int]:
        selected: set[int] = set()
        while True:
            console.clear()
            table = Table(
                title=title,
                box=box.SIMPLE_HEAD,
                show_edge=False,
                expand=True,
            )
            table.add_column("Row", style="key", no_wrap=True, width=4)
            table.add_column("Sel", style="ok", no_wrap=True, width=3)
            table.add_column(
                "Source channel", style="heading", ratio=1, overflow="fold"
            )
            table.add_column(
                "Channel ID", style="value", overflow="ellipsis", max_width=18
            )
            table.add_column("Status", no_wrap=True)
            for i, m in enumerate(grouped, start=1):
                ch = channels.get(m.channel_id)
                status = (
                    Text("✔ active", style="ok")
                    if m.is_active
                    else Text("○ disabled", style="status.disabled")
                )
                table.add_row(
                    str(i),
                    "✓" if i in selected else "",
                    ch.name if ch else f"(channel db_id={m.channel_id})",
                    str(ch.channel_id) if ch else "-",
                    status,
                )
            console.print(table)
            console.print(
                Panel.fit(
                    "Selection:\n"
                    "  - numbers (e.g. 1,3,5): toggle selection\n"
                    "  - Enter               : continue\n"
                    "  - c                   : clear selection\n"
                    "  - q                   : back\n",
                    title=f"Selected: {len(selected)}",
                    border_style="bright_black",
                )
            )
            try:
                cmd = _prompt("Select rows", default="").strip()
            except CancelAction:
                return []
            low = cmd.lower()
            if cmd == "":
                if not selected:
                    console.print(_feedback("[warn]⚠[/warn] Select at least one row."))
                    continue
                return sorted(selected)
            if low in {"c", "clear"}:
                selected.clear()
                continue
            parts = [p.strip() for p in cmd.split(",") if p.strip()]
            try:
                idxs = sorted({int(p) for p in parts})
            except ValueError:
                console.print(_feedback("[warn]⚠[/warn] Use row numbers like: 1,2,5"))
                continue
            if not idxs or any(i < 1 or i > len(grouped) for i in idxs):
                console.print(_feedback("[warn]⚠[/warn] Row index out of range."))
                continue
            for i in idxs:
                if i in selected:
                    selected.remove(i)
                else:
                    selected.add(i)

    while True:
        console.clear()
        mappings = ctx.db.get_channel_mappings(active_only=False)

        counts: dict[int, int] = {}
        for m in mappings:
            counts[m.webhook_id] = counts.get(m.webhook_id, 0) + 1

        wh_table = Table(
            title="Manage mappings (webhook → channels)",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            expand=True,
        )
        wh_table.add_column("#", style="key", no_wrap=True, width=4)
        wh_table.add_column("Webhook", style="heading", ratio=1, overflow="fold")
        wh_table.add_column("Mapped channels", style="value", no_wrap=True)
        wh_table.add_column("Status", no_wrap=True)
        for i, wh in enumerate(webhooks, start=1):
            status = (
                Text("✔ active", style="ok")
                if wh.is_active
                else Text("○ disabled", style="status.disabled")
            )
            wh_table.add_row(
                str(i),
                wh.name,
                str(counts.get(wh.id, 0)),
                status,
            )
        console.print(wh_table)

        try:
            wh_idx = _choose_index("Pick webhook (1..N, q=back)", len(webhooks))
        except CancelAction:
            return
        selected_webhook = webhooks[wh_idx - 1]

        # Webhook detail view
        while True:
            console.clear()
            mappings = ctx.db.get_channel_mappings(active_only=False)
            grouped = [m for m in mappings if m.webhook_id == selected_webhook.id]

            table = Table(
                title=f"Channels → {selected_webhook.name}",
                box=box.SIMPLE_HEAD,
                show_edge=False,
                expand=True,
            )
            table.add_column("#", style="key", no_wrap=True, width=4)
            table.add_column(
                "Source channel", style="heading", ratio=1, overflow="fold"
            )
            table.add_column(
                "Channel ID", style="value", overflow="ellipsis", max_width=18
            )
            table.add_column("Status", no_wrap=True)
            for i, m in enumerate(grouped, start=1):
                ch = channels.get(m.channel_id)
                status = (
                    Text("✔ active", style="ok")
                    if m.is_active
                    else Text("○ disabled", style="status.disabled")
                )
                table.add_row(
                    str(i),
                    ch.name if ch else f"(channel db_id={m.channel_id})",
                    str(ch.channel_id) if ch else "-",
                    status,
                )
            console.print(table)
            if not grouped:
                console.print(
                    _feedback("[warn]⚠[/warn] No channels mapped yet. Use 'a' to add.")
                )

            console.print(
                Panel.fit(
                    "Commands:\n\n"
                    "  [key]a[/key]  add channel(s)\n"
                    "  [key]t[/key]  toggle mapping(s)\n"
                    "  [key]e[/key]  enable mapping(s)\n"
                    "  [key]x[/key]  disable mapping(s)\n"
                    "  [key]d[/key]  delete mapping(s)\n"
                    "  [key]m[/key]  move mapping(s) to another webhook\n"
                    "  [key]q[/key]  back\n",
                    title="Manage",
                    border_style="bright_black",
                )
            )

            try:
                cmd = _prompt("Command", default="").strip()
            except CancelAction:
                break

            if not cmd:
                continue
            low = cmd.lower()
            if low in {"b", "back"}:
                break

            parts = low.split(maxsplit=1)
            action = parts[0]
            raw = parts[1] if len(parts) > 1 else ""

            # aliases
            if action == "t":
                action = "toggle"
            if action == "d":
                action = "delete"
            if action == "e":
                action = "enable"
            if action == "x":
                action = "disable"
            if action == "r":
                action = "move"
            if action == "m":
                action = "move"

            if action in {"a", "add"}:
                mapped_channel_ids = {m.channel_id for m in grouped}
                available = [
                    c
                    for c in ctx.db.get_telegram_channels(active_only=True)
                    if c.id not in mapped_channel_ids
                ]
                if not available:
                    console.print(
                        _feedback(
                            "[warn]⚠[/warn] All active channels are already mapped to this webhook (or no active channels exist)."
                        )
                    )
                    continue

                add_table = Table(
                    title=f"Add channels → {selected_webhook.name}",
                    box=box.SIMPLE_HEAD,
                    show_edge=False,
                )
                add_table.add_column("#", style="key", no_wrap=True)
                add_table.add_column("Name", style="heading")
                add_table.add_column("Username", style="dim")
                add_table.add_column("Channel ID", style="value")
                for i, ch in enumerate(available, start=1):
                    add_table.add_row(
                        str(i),
                        ch.name,
                        f"@{ch.username}" if ch.username else "-",
                        str(ch.channel_id),
                    )
                console.print(add_table)

                try:
                    idxs = _choose_many_indexes(
                        "Select channels to add (e.g. 1,2,5 | q=cancel)",
                        max_index=len(available),
                    )
                except CancelAction:
                    continue

                to_add = [available[i - 1] for i in idxs]
                try:
                    confirm = _prompt(
                        f"Create {len(to_add)} mapping(s) to '{selected_webhook.name}'? (y/n, default y)",
                        default="y",
                    ).lower()
                except CancelAction:
                    continue
                if confirm not in {"y", "yes"}:
                    console.print(_feedback("[warn]⚠[/warn] Canceled."))
                    continue

                existing_pairs = {(m.channel_id, m.webhook_id) for m in mappings}
                created = 0
                for ch in to_add:
                    key = (ch.id, selected_webhook.id)
                    if key in existing_pairs:
                        continue
                    ctx.db.add_channel_mapping(
                        channel_db_id=ch.id,
                        webhook_db_id=selected_webhook.id,
                    )
                    created += 1
                console.print(_feedback(f"[ok]✔[/ok] Added {created} mapping(s)."))
                continue

            if action in {"toggle", "enable", "disable", "delete", "move"}:
                if not grouped:
                    console.print(
                        _feedback("[warn]⚠[/warn] No mappings to modify yet.")
                    )
                    continue

                idxs: Optional[list[int]]
                if raw.strip():
                    idxs = parse_rows(raw, len(grouped))
                    if idxs is None:
                        console.print(
                            _feedback("[warn]⚠[/warn] Use row numbers like: 1,2,5")
                        )
                        continue
                else:
                    idxs = select_rows_dialog(
                        title=f"Select mappings to {action} -> {selected_webhook.name}",
                        grouped=grouped,
                    )
                    if not idxs:
                        console.print(_feedback("[warn]⚠[/warn] Canceled."))
                        continue
                targets = [grouped[i - 1] for i in idxs]

                if action == "toggle":
                    for m in targets:
                        ctx.db.toggle_channel_mapping(m.id, not m.is_active)
                    console.print(_feedback("[ok]✔[/ok] Toggled."))
                    continue
                if action == "enable":
                    for m in targets:
                        ctx.db.toggle_channel_mapping(m.id, True)
                    console.print(_feedback("[ok]✔[/ok] Enabled."))
                    continue
                if action == "disable":
                    for m in targets:
                        ctx.db.toggle_channel_mapping(m.id, False)
                    console.print(_feedback("[ok]✔[/ok] Disabled."))
                    continue
                if action == "delete":
                    try:
                        confirm = _prompt(
                            f"Type 'delete' to confirm deleting {len(targets)} mapping(s)",
                            default="",
                        )
                    except CancelAction:
                        continue
                    if confirm != "delete":
                        console.print(_feedback("[warn]⚠[/warn] Canceled."))
                        continue
                    for m in targets:
                        ctx.db.delete_channel_mapping(m.id)
                    console.print(_feedback("[ok]✔[/ok] Deleted."))
                    continue

                # move
                active_webhooks = [
                    w for w in webhooks if w.is_active and w.id != selected_webhook.id
                ]
                if not active_webhooks:
                    console.print(
                        _feedback("[warn]⚠[/warn] No other active webhooks to move to.")
                    )
                    continue
                pick_table = Table(
                    title="Move to which webhook?", box=box.SIMPLE_HEAD, show_edge=False
                )
                pick_table.add_column("#", style="key", no_wrap=True)
                pick_table.add_column("Name", style="heading")
                for i, w in enumerate(active_webhooks, start=1):
                    pick_table.add_row(str(i), w.name)
                console.print(pick_table)
                try:
                    new_idx = _choose_index(
                        "Webhook (1..N, q=cancel)", len(active_webhooks)
                    )
                except CancelAction:
                    continue
                new_wh = active_webhooks[new_idx - 1]
                for m in targets:
                    ctx.db.update_channel_mapping_webhook(m.id, new_wh.id)
                console.print(
                    _feedback(
                        f"[ok]✔[/ok] Moved {len(targets)} mapping(s) to '{new_wh.name}'."
                    )
                )
                continue

            console.print(_feedback("[warn]⚠[/warn] Unknown command."))


def _create_mappings(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels(active_only=True)
    webhooks = ctx.db.get_discord_webhooks(active_only=True)

    if not channels:
        console.print(
            _feedback(
                "[warn]⚠[/warn] No active Telegram channels. Add/import some first."
            )
        )
        return
    if not webhooks:
        console.print(
            _feedback("[warn]⚠[/warn] No active Discord webhooks. Add one first.")
        )
        return

    channel_table = Table(
        title="Source Telegram channels", box=box.SIMPLE_HEAD, show_edge=False
    )
    channel_table.add_column("#", style="key", no_wrap=True)
    channel_table.add_column("Name", style="heading")
    channel_table.add_column("Username", style="dim")
    channel_table.add_column("Channel ID", style="value")
    for i, ch in enumerate(channels, start=1):
        channel_table.add_row(
            str(i),
            ch.name,
            f"@{ch.username}" if ch.username else "-",
            str(ch.channel_id),
        )
    console.print(channel_table)

    try:
        channel_idxs = _choose_many_indexes(
            "Select source channels (e.g. 1,2 | 'all' | q=cancel)",
            max_index=len(channels),
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    selected_channels = [channels[i - 1] for i in channel_idxs]
    selected_table = Table(
        title="Selected source channels", box=box.SIMPLE_HEAD, show_edge=False
    )
    selected_table.add_column("#", style="key", no_wrap=True)
    selected_table.add_column("Name", style="heading")
    selected_table.add_column("Channel ID", style="value")
    for i, ch in enumerate(selected_channels, start=1):
        selected_table.add_row(str(i), ch.name, str(ch.channel_id))
    console.print(selected_table)

    webhook_table = Table(
        title="Destination Discord webhook", box=box.SIMPLE_HEAD, show_edge=False
    )
    webhook_table.add_column("#", style="key", no_wrap=True)
    webhook_table.add_column("Name", style="heading")
    for i, wh in enumerate(webhooks, start=1):
        webhook_table.add_row(str(i), wh.name)
    console.print(webhook_table)

    try:
        webhook_idx = _choose_index(
            "Select destination webhook (1..N, q=cancel)",
            max_index=len(webhooks),
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    webhook = webhooks[webhook_idx - 1]

    try:
        confirm = (
            _prompt(
                f"Create {len(selected_channels)} mapping(s) to '{webhook.name}'? (y/n, default y)",
                default="y",
            ).lower()
            or "y"
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    if confirm not in {"y", "yes"}:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    existing = {
        (m.channel_id, m.webhook_id)
        for m in ctx.db.get_channel_mappings(active_only=False)
    }
    created = 0
    skipped = 0
    for ch in selected_channels:
        key = (ch.id, webhook.id)
        if key in existing:
            skipped += 1
            continue
        ctx.db.add_channel_mapping(channel_db_id=ch.id, webhook_db_id=webhook.id)
        created += 1

    console.print(
        _feedback(
            f"[ok]✔[/ok] Created [bold]{created}[/bold] mapping(s). "
            f"Skipped [bold]{skipped}[/bold] existing.",
            title="Mappings",
        )
    )


def _destination_type_human(destination_type: str) -> str:
    if destination_type == DestinationType.DISCORD_WEBHOOK.value:
        return "discord"
    if destination_type == DestinationType.TELEGRAM_CHAT.value:
        return "telegram"
    return destination_type


def _destination_target_label(row: dict) -> str:
    destination_type = row.get("destination_type")
    if destination_type == DestinationType.DISCORD_WEBHOOK.value:
        url = str(row.get("discord_webhook_url") or "")
        if not url:
            return "(missing webhook url)"
        redacted = DiscordWebhookSender.redact_webhook_url(url)
        if len(redacted) <= 32:
            return redacted
        return f"{redacted[:24]}..."

    if destination_type == DestinationType.TELEGRAM_CHAT.value:
        chat_id = row.get("telegram_chat_id")
        topic_id = row.get("telegram_topic_id")
        if topic_id is None:
            return f"chat_id={chat_id}"
        return f"chat_id={chat_id} topic_id={topic_id}"

    return "-"


def _print_destinations_v2(ctx: TuiContext) -> None:
    rows = ctx.db.get_destination_rows(active_only=False)
    if not rows:
        console.print(
            _feedback("[warn]⚠[/warn] No destinations saved.", title="Destinations")
        )
        return

    table = Table(
        title="Destinations",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Type", style="info", no_wrap=True)
    table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
    table.add_column("Status", no_wrap=True)
    for i, row in enumerate(rows, start=1):
        status = (
            Text("✔ active", style="ok")
            if row.get("is_active")
            else Text("○ disabled", style="status.disabled")
        )
        table.add_row(
            str(i),
            str(row.get("destination_name") or f"dest-{row.get('destination_id')}"),
            _destination_type_human(str(row.get("destination_type") or "")),
            _destination_target_label(row),
            status,
        )
    console.print(table)


async def _add_destination_v2(ctx: TuiContext) -> None:
    mode_table = Table(title="Add destination", box=box.SIMPLE_HEAD)
    mode_table.add_column("#", style="key", no_wrap=True)
    mode_table.add_column("Type", style="heading")
    mode_table.add_row("1", "Discord webhook destination")
    mode_table.add_row("2", "Telegram chat destination")
    console.print(mode_table)

    try:
        kind = _choose_index("Destination type (1..2, q=cancel)", 2)
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    if kind == 1:
        try:
            name = _prompt("Destination name")
            url = _prompt("Discord webhook URL")
        except CancelAction:
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return

        ok, why = await discord_sender.test_webhook(url)
        if not ok:
            console.print(
                _feedback(f"[error]✘[/error] {why}", title="Webhook test failed")
            )
            return
        ctx.db.add_discord_destination(name=name, webhook_url=url)
        console.print(_feedback("[ok]✔[/ok] Discord destination saved."))
        return

    try:
        name = _prompt("Destination name")
        chat_id = _prompt_int("Telegram destination chat_id (e.g. -1001234567890)")
        topic_raw = _prompt("Telegram topic_id (optional, blank for none)", default="")
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    topic_id: Optional[int] = None
    if topic_raw.strip():
        try:
            topic_id = int(topic_raw.strip())
        except ValueError:
            console.print(_feedback("[warn]⚠[/warn] topic_id must be an integer."))
            return

    ctx.db.add_telegram_destination(name=name, chat_id=chat_id, topic_id=topic_id)
    console.print(_feedback("[ok]✔[/ok] Telegram destination saved."))


async def _manage_destinations_v2(ctx: TuiContext) -> None:
    rows = ctx.db.get_destination_rows(active_only=False)
    if not rows:
        console.print(_feedback("[warn]⚠[/warn] No destinations saved."))
        return

    table = Table(
        title="Manage destinations",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    table.add_column("#", style="key", no_wrap=True, width=4)
    table.add_column("Name", style="heading", ratio=1, overflow="fold")
    table.add_column("Type", style="info", no_wrap=True)
    table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
    table.add_column("Status", no_wrap=True)
    for i, row in enumerate(rows, start=1):
        status = (
            Text("✔ active", style="ok")
            if row.get("is_active")
            else Text("○ disabled", style="status.disabled")
        )
        table.add_row(
            str(i),
            str(row.get("destination_name") or f"dest-{row.get('destination_id')}"),
            _destination_type_human(str(row.get("destination_type") or "")),
            _destination_target_label(row),
            status,
        )
    console.print(table)

    try:
        idx = _choose_index("Select destination (1..N, q=back)", len(rows))
    except CancelAction:
        return
    row = rows[idx - 1]
    destination_id = int(row["destination_id"])

    actions = Table(
        title=f"Destination: {row.get('destination_name')}", box=box.SIMPLE_HEAD
    )
    actions.add_column("Key", style="key", no_wrap=True)
    actions.add_column("Action", style="heading")
    actions.add_row("1", "Toggle active/disabled")
    actions.add_row("2", "Rename destination")
    actions.add_row("3", "Edit target settings")
    actions.add_row("4", "Delete destination (and linked routes/logs)")
    actions.add_row("0", "Back")
    console.print(actions)

    try:
        choice = _prompt("Action (0-4)", default="0")
    except CancelAction:
        return

    if choice == "1":
        ctx.db.update_destination(
            destination_id, is_active=not bool(row.get("is_active"))
        )
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "2":
        try:
            new_name = _prompt(
                "New name", default=str(row.get("destination_name") or "")
            )
        except CancelAction:
            return
        ctx.db.update_destination(destination_id, name=new_name)
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "3":
        destination_type = str(row.get("destination_type") or "")
        if destination_type == DestinationType.DISCORD_WEBHOOK.value:
            try:
                new_url = _prompt(
                    "New Discord webhook URL",
                    default=str(row.get("discord_webhook_url") or ""),
                )
            except CancelAction:
                return
            ok, why = await discord_sender.test_webhook(new_url)
            if not ok:
                console.print(
                    _feedback(f"[error]✘[/error] {why}", title="Webhook test failed")
                )
                return
            ctx.db.update_discord_destination(
                destination_id,
                webhook_url=new_url,
            )
            console.print(_feedback("[ok]✔[/ok] Updated."))
            return

        if destination_type == DestinationType.TELEGRAM_CHAT.value:
            try:
                chat_default = str(row.get("telegram_chat_id") or "")
                chat_raw = _prompt(
                    "Telegram destination chat_id",
                    default=chat_default,
                )
                new_chat_id = int(chat_raw)
            except CancelAction:
                return
            except Exception:
                console.print(_feedback("[warn]⚠[/warn] Invalid chat_id."))
                return

            topic_default = (
                ""
                if row.get("telegram_topic_id") is None
                else str(row.get("telegram_topic_id"))
            )
            try:
                topic_raw = _prompt(
                    "Telegram topic_id (blank to clear)",
                    default=topic_default,
                )
            except CancelAction:
                return
            new_topic_id: Optional[int] = None
            if topic_raw.strip():
                try:
                    new_topic_id = int(topic_raw.strip())
                except ValueError:
                    console.print(
                        _feedback("[warn]⚠[/warn] topic_id must be an integer.")
                    )
                    return

            ctx.db.update_telegram_destination(
                destination_id,
                chat_id=new_chat_id,
                topic_id=new_topic_id,
            )
            console.print(_feedback("[ok]✔[/ok] Updated."))
            return

        console.print(_feedback("[warn]⚠[/warn] Unknown destination type."))
        return

    if choice == "4":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return
        ctx.db.delete_destination(destination_id)
        console.print(_feedback("[ok]✔[/ok] Deleted."))
        return


def _create_routes_v2(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels(active_only=True)
    destinations = ctx.db.get_destination_rows(active_only=True)

    if not channels:
        console.print(_feedback("[warn]⚠[/warn] No active Telegram source channels."))
        return
    if not destinations:
        console.print(
            _feedback("[warn]⚠[/warn] No active destinations. Add one first.")
        )
        return

    channel_table = Table(
        title="Source Telegram channels",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    channel_table.add_column("#", style="key", no_wrap=True, width=4)
    channel_table.add_column("Name", style="heading", ratio=1, overflow="fold")
    channel_table.add_column(
        "Channel ID", style="value", overflow="ellipsis", max_width=18
    )
    for i, ch in enumerate(channels, start=1):
        channel_table.add_row(str(i), ch.name, str(ch.channel_id))
    console.print(channel_table)

    try:
        source_idxs = _choose_many_indexes(
            "Select source channels (e.g. 1,2 | all | q=cancel)",
            max_index=len(channels),
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    selected_sources = [channels[i - 1] for i in source_idxs]

    dest_table = Table(
        title="Destination",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    dest_table.add_column("#", style="key", no_wrap=True, width=4)
    dest_table.add_column("Name", style="heading", ratio=1, overflow="fold")
    dest_table.add_column("Type", style="info", no_wrap=True)
    dest_table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
    for i, row in enumerate(destinations, start=1):
        dest_table.add_row(
            str(i),
            str(row.get("destination_name") or f"dest-{row.get('destination_id')}"),
            _destination_type_human(str(row.get("destination_type") or "")),
            _destination_target_label(row),
        )
    console.print(dest_table)

    try:
        dest_idx = _choose_index(
            "Select destination (1..N, q=cancel)", len(destinations)
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    destination = destinations[dest_idx - 1]
    destination_id = int(destination["destination_id"])

    groups = ctx.db.get_forwarding_groups(active_only=True)
    group_id: Optional[int] = None
    if groups:
        group_table = Table(
            title="Optional forwarding group",
            box=box.SIMPLE_HEAD,
            expand=True,
            show_edge=False,
        )
        group_table.add_column("Key", style="key", no_wrap=True, width=4)
        group_table.add_column("Group", style="heading", ratio=1, overflow="fold")
        group_table.add_row("0", "(no group)")
        for i, g in enumerate(groups, start=1):
            group_table.add_row(str(i), g.name)
        console.print(group_table)

        while True:
            try:
                raw = _prompt("Group key (0..N, default 0)", default="0")
            except CancelAction:
                console.print(_feedback("[warn]⚠[/warn] Canceled."))
                return
            try:
                choice = int(raw)
            except ValueError:
                console.print(_feedback("[warn]⚠[/warn] Please enter a number."))
                continue
            if choice == 0:
                group_id = None
                break
            if 1 <= choice <= len(groups):
                group_id = groups[choice - 1].id
                break
            console.print(_feedback("[warn]⚠[/warn] Out of range."))

    try:
        confirm = (
            _prompt(
                f"Create routes from {len(selected_sources)} source(s) to '{destination.get('destination_name')}'? (y/n, default y)",
                default="y",
            ).lower()
            or "y"
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    if confirm not in {"y", "yes"}:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    existing_keys = {
        (int(r["source_channel_db_id"]), int(r["destination_id"]), r.get("group_id"))
        for r in ctx.db.get_route_rows(active_only=False)
    }

    created = 0
    reactivated = 0
    for source in selected_sources:
        key = (source.id, destination_id, group_id)
        if key in existing_keys:
            # Upsert behavior: ensure it is active.
            rows = [
                r
                for r in ctx.db.get_route_rows(active_only=False)
                if int(r["source_channel_db_id"]) == source.id
                and int(r["destination_id"]) == destination_id
                and r.get("group_id") == group_id
            ]
            if rows and not rows[0].get("route_is_active"):
                ctx.db.toggle_route_mapping(int(rows[0]["route_id"]), True)
                reactivated += 1
            continue
        ctx.db.add_route_mapping(
            source_channel_db_id=source.id,
            destination_id=destination_id,
            group_id=group_id,
            is_active=True,
        )
        created += 1

    console.print(
        _feedback(
            f"[ok]✔[/ok] Created [bold]{created}[/bold] route(s). "
            f"Reactivated [bold]{reactivated}[/bold].",
            title="Routes",
        )
    )


def _manage_routes_v2(ctx: TuiContext) -> None:
    route_rows = ctx.db.get_route_rows(active_only=False)
    if not route_rows:
        console.print(_feedback("[warn]⚠[/warn] No routes saved."))
        return

    channels = {c.id: c for c in ctx.db.get_telegram_channels(active_only=False)}
    destinations = ctx.db.get_destination_rows(active_only=True)
    groups = {g.id: g for g in ctx.db.get_forwarding_groups(active_only=False)}

    route_table = Table(
        title="Manage routes",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_edge=False,
    )
    route_table.add_column("#", style="key", no_wrap=True, width=4)
    route_table.add_column("Source", style="heading", ratio=1, overflow="fold")
    route_table.add_column("Destination", style="heading", ratio=1, overflow="fold")
    route_table.add_column("Type", style="info", no_wrap=True)
    route_table.add_column("Group", style="value", ratio=1, overflow="fold")
    route_table.add_column("Status", no_wrap=True)
    for i, r in enumerate(route_rows, start=1):
        src = channels.get(int(r["source_channel_db_id"]))
        group = groups.get(r.get("group_id")) if r.get("group_id") else None
        status = (
            Text("✔ active", style="ok")
            if r.get("route_is_active")
            else Text("○ disabled", style="status.disabled")
        )
        route_table.add_row(
            str(i),
            src.name if src else f"(source db_id={r['source_channel_db_id']})",
            str(r.get("destination_name") or f"dest-{r.get('destination_id')}"),
            _destination_type_human(str(r.get("destination_type") or "")),
            group.name if group else "-",
            status,
        )
    console.print(route_table)

    try:
        idx = _choose_index("Select route (1..N, q=back)", len(route_rows))
    except CancelAction:
        return
    row = route_rows[idx - 1]
    route_id = int(row["route_id"])

    actions = Table(title=f"Route #{route_id}", box=box.SIMPLE_HEAD)
    actions.add_column("Key", style="key", no_wrap=True)
    actions.add_column("Action", style="heading")
    actions.add_row("1", "Toggle active/disabled")
    actions.add_row("2", "Change destination")
    actions.add_row("3", "Change forwarding group")
    actions.add_row("4", "Delete route")
    actions.add_row("0", "Back")
    console.print(actions)

    try:
        choice = _prompt("Action (0-4)", default="0")
    except CancelAction:
        return

    if choice == "1":
        ctx.db.toggle_route_mapping(route_id, not bool(row.get("route_is_active")))
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "2":
        destination_rows = ctx.db.get_destination_rows(active_only=True)
        if not destination_rows:
            console.print(_feedback("[warn]⚠[/warn] No active destinations available."))
            return
        table = Table(
            title="Move route to destination",
            box=box.SIMPLE_HEAD,
            expand=True,
            show_edge=False,
        )
        table.add_column("#", style="key", no_wrap=True, width=4)
        table.add_column("Name", style="heading", ratio=1, overflow="fold")
        table.add_column("Type", style="info", no_wrap=True)
        table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
        for i, d in enumerate(destination_rows, start=1):
            table.add_row(
                str(i),
                str(d.get("destination_name")),
                _destination_type_human(str(d.get("destination_type") or "")),
                _destination_target_label(d),
            )
        console.print(table)
        try:
            dest_idx = _choose_index(
                "Destination (1..N, q=cancel)", len(destination_rows)
            )
        except CancelAction:
            return
        selected_destination = destination_rows[dest_idx - 1]
        selected_destination_id = int(selected_destination["destination_id"])

        duplicate = [
            r
            for r in route_rows
            if int(r["source_channel_db_id"]) == int(row["source_channel_db_id"])
            and int(r["destination_id"]) == selected_destination_id
            and r.get("group_id") == row.get("group_id")
            and int(r["route_id"]) != route_id
        ]
        if duplicate:
            console.print(
                _feedback(
                    "[warn]⚠[/warn] A route with the same source/destination/group already exists."
                )
            )
            return

        ctx.db.update_route_mapping(
            route_id,
            destination_id=selected_destination_id,
            group_id=row.get("group_id"),
        )
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "3":
        active_groups = ctx.db.get_forwarding_groups(active_only=True)
        pick_table = Table(
            title="Select forwarding group",
            box=box.SIMPLE_HEAD,
            expand=True,
            show_edge=False,
        )
        pick_table.add_column("Key", style="key", no_wrap=True, width=4)
        pick_table.add_column("Group", style="heading", ratio=1, overflow="fold")
        pick_table.add_row("0", "(no group)")
        for i, g in enumerate(active_groups, start=1):
            pick_table.add_row(str(i), g.name)
        console.print(pick_table)

        while True:
            try:
                raw = _prompt("Group key (0..N, q=cancel)", default="0")
            except CancelAction:
                return
            try:
                group_idx = int(raw)
            except ValueError:
                console.print(_feedback("[warn]⚠[/warn] Please enter a number."))
                continue

            if group_idx == 0:
                new_group_id = None
                break
            if 1 <= group_idx <= len(active_groups):
                new_group_id = active_groups[group_idx - 1].id
                break
            console.print(_feedback("[warn]⚠[/warn] Out of range."))

        duplicate = [
            r
            for r in route_rows
            if int(r["source_channel_db_id"]) == int(row["source_channel_db_id"])
            and int(r["destination_id"]) == int(row["destination_id"])
            and r.get("group_id") == new_group_id
            and int(r["route_id"]) != route_id
        ]
        if duplicate:
            console.print(
                _feedback(
                    "[warn]⚠[/warn] A route with the same source/destination/group already exists."
                )
            )
            return

        ctx.db.update_route_mapping(
            route_id,
            destination_id=int(row["destination_id"]),
            group_id=new_group_id,
        )
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "4":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return
        ctx.db.delete_route_mapping(route_id)
        console.print(_feedback("[ok]✔[/ok] Deleted."))
        return
    if not destinations:
        console.print(
            _feedback("[warn]⚠[/warn] No active destinations. Add one first.")
        )
        return

    channel_table = Table(
        title="Source Telegram channels",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    channel_table.add_column("#", style="key", no_wrap=True, width=4)
    channel_table.add_column("Name", style="heading", ratio=1, overflow="fold")
    channel_table.add_column(
        "Channel ID", style="value", overflow="ellipsis", max_width=18
    )
    for i, ch in enumerate(channels, start=1):
        channel_table.add_row(str(i), ch.name, str(ch.channel_id))
    console.print(channel_table)

    try:
        source_idxs = _choose_many_indexes(
            "Select source channels (e.g. 1,2 | all | q=cancel)",
            max_index=len(channels),
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    selected_sources = [channels[i - 1] for i in source_idxs]

    dest_table = Table(
        title="Destination",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    dest_table.add_column("#", style="key", no_wrap=True, width=4)
    dest_table.add_column("Name", style="heading", ratio=1, overflow="fold")
    dest_table.add_column("Type", style="dim", no_wrap=True)
    dest_table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
    for i, row in enumerate(destinations, start=1):
        dest_table.add_row(
            str(i),
            str(row.get("destination_name") or f"dest-{row.get('destination_id')}"),
            _destination_type_human(str(row.get("destination_type") or "")),
            _destination_target_label(row),
        )
    console.print(dest_table)

    try:
        dest_idx = _choose_index(
            "Select destination (1..N, q=cancel)", len(destinations)
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    destination = destinations[dest_idx - 1]
    destination_id = int(destination["destination_id"])

    groups = ctx.db.get_forwarding_groups(active_only=True)
    group_id: Optional[int] = None
    if groups:
        group_table = Table(
            title="Optional forwarding group",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            expand=True,
        )
        group_table.add_column("Key", style="key", no_wrap=True, width=4)
        group_table.add_column("Group", style="heading", ratio=1, overflow="fold")
        group_table.add_row("0", "(no group)")
        for i, g in enumerate(groups, start=1):
            group_table.add_row(str(i), g.name)
        console.print(group_table)

        while True:
            try:
                raw = _prompt("Group key (0..N, default 0)", default="0")
            except CancelAction:
                console.print(_feedback("[warn]⚠[/warn] Canceled."))
                return
            try:
                choice = int(raw)
            except ValueError:
                console.print(_feedback("[warn]⚠[/warn] Please enter a number."))
                continue
            if choice == 0:
                group_id = None
                break
            if 1 <= choice <= len(groups):
                group_id = groups[choice - 1].id
                break
            console.print(_feedback("[warn]⚠[/warn] Out of range."))

    try:
        confirm = (
            _prompt(
                f"Create routes from {len(selected_sources)} source(s) to '{destination.get('destination_name')}'? (y/n, default y)",
                default="y",
            ).lower()
            or "y"
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    if confirm not in {"y", "yes"}:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return

    existing_keys = {
        (int(r["source_channel_db_id"]), int(r["destination_id"]), r.get("group_id"))
        for r in ctx.db.get_route_rows(active_only=False)
    }

    created = 0
    reactivated = 0
    for source in selected_sources:
        key = (source.id, destination_id, group_id)
        if key in existing_keys:
            # Upsert behavior: ensure it is active.
            rows = [
                r
                for r in ctx.db.get_route_rows(active_only=False)
                if int(r["source_channel_db_id"]) == source.id
                and int(r["destination_id"]) == destination_id
                and r.get("group_id") == group_id
            ]
            if rows and not rows[0].get("route_is_active"):
                ctx.db.toggle_route_mapping(int(rows[0]["route_id"]), True)
                reactivated += 1
            continue
        ctx.db.add_route_mapping(
            source_channel_db_id=source.id,
            destination_id=destination_id,
            group_id=group_id,
            is_active=True,
        )
        created += 1

    console.print(
        _feedback(
            f"[ok]✔[/ok] Created [bold]{created}[/bold] route(s). "
            f"Reactivated [bold]{reactivated}[/bold].",
            title="Routes",
        )
    )


def _manage_routes_v2(ctx: TuiContext) -> None:
    route_rows = ctx.db.get_route_rows(active_only=False)
    if not route_rows:
        console.print(_feedback("[warn]⚠[/warn] No routes saved."))
        return

    channels = {c.id: c for c in ctx.db.get_telegram_channels(active_only=False)}
    groups = {g.id: g for g in ctx.db.get_forwarding_groups(active_only=False)}

    route_table = Table(
        title="Manage routes",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    route_table.add_column("#", style="key", no_wrap=True, width=4)
    route_table.add_column("Source", style="heading", ratio=1, overflow="fold")
    route_table.add_column("Destination", style="heading", ratio=1, overflow="fold")
    route_table.add_column("Type", style="dim", no_wrap=True)
    route_table.add_column("Group", style="value", ratio=1, overflow="fold")
    route_table.add_column("Status", no_wrap=True)
    for i, r in enumerate(route_rows, start=1):
        src = channels.get(int(r["source_channel_db_id"]))
        group = groups.get(r.get("group_id")) if r.get("group_id") else None
        status = (
            Text("✔ active", style="ok")
            if r.get("route_is_active")
            else Text("○ disabled", style="status.disabled")
        )
        route_table.add_row(
            str(i),
            src.name if src else f"(source db_id={r['source_channel_db_id']})",
            str(r.get("destination_name") or f"dest-{r.get('destination_id')}"),
            _destination_type_human(str(r.get("destination_type") or "")),
            group.name if group else "-",
            status,
        )
    console.print(route_table)

    try:
        idx = _choose_index("Select route (1..N, q=back)", len(route_rows))
    except CancelAction:
        return
    row = route_rows[idx - 1]
    route_id = int(row["route_id"])

    actions = Table(title=f"Route #{route_id}", box=box.SIMPLE, show_header=False)
    actions.add_column("Key", style="key", no_wrap=True, width=4)
    actions.add_column("Action", style="white")
    actions.add_row("1", "Toggle active/disabled")
    actions.add_row("2", "Change destination")
    actions.add_row("3", "Change forwarding group")
    actions.add_row("4", "Delete route")
    actions.add_row("0", "Back")
    console.print(actions)

    try:
        choice = _prompt("Action (0-4)", default="0")
    except CancelAction:
        return

    if choice == "1":
        ctx.db.toggle_route_mapping(route_id, not bool(row.get("route_is_active")))
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "2":
        destination_rows = ctx.db.get_destination_rows(active_only=True)
        if not destination_rows:
            console.print(_feedback("[warn]⚠[/warn] No active destinations available."))
            return
        table = Table(
            title="Move route to destination",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            expand=True,
        )
        table.add_column("#", style="key", no_wrap=True, width=4)
        table.add_column("Name", style="heading", ratio=1, overflow="fold")
        table.add_column("Type", style="dim", no_wrap=True)
        table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
        for i, d in enumerate(destination_rows, start=1):
            table.add_row(
                str(i),
                str(d.get("destination_name")),
                _destination_type_human(str(d.get("destination_type") or "")),
                _destination_target_label(d),
            )
        console.print(table)
        try:
            dest_idx = _choose_index(
                "Destination (1..N, q=cancel)", len(destination_rows)
            )
        except CancelAction:
            return
        selected_destination = destination_rows[dest_idx - 1]
        selected_destination_id = int(selected_destination["destination_id"])

        duplicate = [
            r
            for r in route_rows
            if int(r["source_channel_db_id"]) == int(row["source_channel_db_id"])
            and int(r["destination_id"]) == selected_destination_id
            and r.get("group_id") == row.get("group_id")
            and int(r["route_id"]) != route_id
        ]
        if duplicate:
            console.print(
                _feedback(
                    "[warn]⚠[/warn] A route with the same source/destination/group already exists."
                )
            )
            return

        ctx.db.update_route_mapping(
            route_id,
            destination_id=selected_destination_id,
            group_id=row.get("group_id"),
        )
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "3":
        active_groups = ctx.db.get_forwarding_groups(active_only=True)
        pick_table = Table(
            title="Select forwarding group",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            expand=True,
        )
        pick_table.add_column("Key", style="key", no_wrap=True, width=4)
        pick_table.add_column("Group", style="heading", ratio=1, overflow="fold")
        pick_table.add_row("0", "(no group)")
        for i, g in enumerate(active_groups, start=1):
            pick_table.add_row(str(i), g.name)
        console.print(pick_table)

        while True:
            try:
                raw = _prompt("Group key (0..N, q=cancel)", default="0")
            except CancelAction:
                return
            try:
                group_idx = int(raw)
            except ValueError:
                console.print(_feedback("[warn]⚠[/warn] Please enter a number."))
                continue

            if group_idx == 0:
                new_group_id = None
                break
            if 1 <= group_idx <= len(active_groups):
                new_group_id = active_groups[group_idx - 1].id
                break
            console.print(_feedback("[warn]⚠[/warn] Out of range."))

        duplicate = [
            r
            for r in route_rows
            if int(r["source_channel_db_id"]) == int(row["source_channel_db_id"])
            and int(r["destination_id"]) == int(row["destination_id"])
            and r.get("group_id") == new_group_id
            and int(r["route_id"]) != route_id
        ]
        if duplicate:
            console.print(
                _feedback(
                    "[warn]⚠[/warn] A route with the same source/destination/group already exists."
                )
            )
            return

        ctx.db.update_route_mapping(
            route_id,
            destination_id=int(row["destination_id"]),
            group_id=new_group_id,
        )
        console.print(_feedback("[ok]✔[/ok] Updated."))
        return

    if choice == "4":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return
        ctx.db.delete_route_mapping(route_id)
        console.print(_feedback("[ok]✔[/ok] Deleted."))
        return


async def _run_forwarder(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=False)
    if not ok:
        console.print(
            _feedback("[warn]⚠[/warn] No valid Telegram session. Run 'Login' first.")
        )
        return

    forwarder = Forwarder(
        db=ctx.db,
        telegram=ctx.telegram,
        allow_mass_mentions=ctx.config.discord_allow_mass_mentions,
        suppress_url_embeds=ctx.config.discord_suppress_url_embeds,
        strip_urls=ctx.config.discord_strip_urls,
        include_telegram_link=ctx.config.discord_include_telegram_link,
    )

    def on_forward(event: dict):
        status = "OK" if event.get("success") else "ERR"
        destination = event.get("destination_name") or event.get("webhook_name", "?")
        channel_id = event.get("channel_id")
        message_id = event.get("message_id")
        error = event.get("error")
        extra = f" ({error})" if error else ""
        style = "green" if status == "OK" else "red"
        console.print(
            f"[{style}]{status}[/{style}] tg={channel_id} msg={message_id} -> {destination}{extra}"
        )

    forwarder.set_on_forward_callback(on_forward)

    async def wait_for_quit() -> None:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                return
            if line.strip().lower() in {"q", "quit", "exit"}:
                return

    is_tty = False
    try:
        is_tty = sys.stdin.isatty()
    except Exception:
        is_tty = False

    hint = (
        "Forwarder running. Type 'q' + Enter (or Ctrl+C) to stop."
        if is_tty
        else "Forwarder running. Press Ctrl+C to stop."
    )
    console.print(_feedback(hint))

    try:
        run_task = asyncio.create_task(forwarder.start(), name="forwarder")
        quit_task = (
            asyncio.create_task(wait_for_quit(), name="wait-for-q") if is_tty else None
        )

        pending: set[asyncio.Task] = {run_task}
        if quit_task is not None:
            pending.add(quit_task)

        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

        if quit_task is not None and quit_task in done and not run_task.done():
            console.print(_feedback("[info]●[/info] Stopping..."))
            await forwarder.stop()
            try:
                await asyncio.wait_for(run_task, timeout=10.0)
            except asyncio.TimeoutError:
                run_task.cancel()
        elif not run_task.done():
            await forwarder.stop()
    except KeyboardInterrupt:
        console.print(_feedback("[info]●[/info] Stopping..."))
    finally:
        try:
            await forwarder.stop()
        except Exception:
            pass


async def _test_forward_last_message(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=True)
    if not ok:
        console.print(_feedback("[warn]⚠[/warn] Telegram login required."))
        return

    channels = ctx.db.get_telegram_channels(active_only=True)
    if not channels:
        console.print(_feedback("[warn]⚠[/warn] No active Telegram channels saved."))
        return

    source_table = Table(
        title="Select source Telegram channel",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    source_table.add_column("#", style="key", no_wrap=True, width=4)
    source_table.add_column("Name", style="heading", ratio=1, overflow="fold")
    source_table.add_column("Username", style="dim", ratio=1, overflow="fold")
    source_table.add_column(
        "Channel ID", style="value", overflow="ellipsis", max_width=18
    )
    for i, ch in enumerate(channels, start=1):
        source_table.add_row(
            str(i),
            ch.name,
            f"@{ch.username}" if ch.username else "-",
            str(ch.channel_id),
        )
    console.print(source_table)

    try:
        src_idx = _choose_index("Source channel (1..N, q=cancel)", len(channels))
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    src = channels[src_idx - 1]

    recent = []
    async for msg in ctx.telegram.iter_messages(src.channel_id, limit=5):
        recent.append(msg)
    if not recent:
        console.print(_feedback("[warn]⚠[/warn] No messages found in that channel."))
        return

    msg_table = Table(
        title="Recent messages (most recent first)",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    msg_table.add_column("#", style="key", no_wrap=True, width=4)
    msg_table.add_column("ID", style="value", overflow="ellipsis", max_width=18)
    msg_table.add_column("Time", style="dim")
    msg_table.add_column("Media", style="dim", no_wrap=True)
    msg_table.add_column("Preview", style="heading", ratio=1, overflow="fold")
    for i, m in enumerate(recent, start=1):
        text = (getattr(m, "text", None) or getattr(m, "message", "") or "").strip()
        has_media = "yes" if getattr(m, "media", None) else "no"
        if text:
            preview = (text[:80] + "...") if len(text) > 80 else text
        else:
            preview = "(media only)" if has_media == "yes" else "(no text)"
        ts = getattr(m, "date", None)
        ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
        msg_table.add_row(str(i), str(getattr(m, "id", "?")), ts_s, has_media, preview)
    console.print(msg_table)

    try:
        pick = Prompt.ask("Pick message (1..5, default 1)", default="1").strip()
        if pick.lower() in {"q", "quit", "cancel"}:
            raise CancelAction()
        msg_idx = int(pick)
        if msg_idx < 1 or msg_idx > len(recent):
            console.print(_feedback("[warn]⚠[/warn] Out of range."))
            return
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    except Exception:
        console.print(_feedback("[warn]⚠[/warn] Invalid selection."))
        return

    message = recent[msg_idx - 1]

    sender_name: Optional[str] = None
    try:
        sender = getattr(message, "sender", None)
        if not sender and hasattr(message, "get_sender"):
            sender = await message.get_sender()  # type: ignore[attr-defined]
        if sender:
            sender_name = (
                getattr(sender, "first_name", None)
                or getattr(sender, "title", None)
                or getattr(sender, "username", None)
            )
    except Exception:
        sender_name = None

    forwarder = Forwarder(
        db=ctx.db,
        telegram=ctx.telegram,
        allow_mass_mentions=ctx.config.discord_allow_mass_mentions,
        suppress_url_embeds=ctx.config.discord_suppress_url_embeds,
        strip_urls=ctx.config.discord_strip_urls,
        include_telegram_link=ctx.config.discord_include_telegram_link,
    )

    forwarder.reload_mappings()
    route_infos = forwarder._channel_webhook_map.get(src.channel_id, [])  # type: ignore[attr-defined]
    if not route_infos:
        console.print(
            _feedback("[warn]⚠[/warn] No active routes found for this source channel.")
        )
        return

    route_table = Table(
        title=f"Routes from {src.name}",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    route_table.add_column("#", style="key", no_wrap=True, width=4)
    route_table.add_column("Destination", style="heading", ratio=1, overflow="fold")
    route_table.add_column("Type", style="dim", no_wrap=True)
    route_table.add_column("Target", style="value", overflow="ellipsis", max_width=18)
    for i, route in enumerate(route_infos, start=1):
        destination_type = str(route.get("destination_type") or "")
        if destination_type == DestinationType.DISCORD_WEBHOOK.value:
            target = DiscordWebhookSender.redact_webhook_url(
                str(route.get("webhook_url") or "-")
            )
            if len(target) > 36:
                target = target[:30] + "..."
        elif destination_type == DestinationType.TELEGRAM_CHAT.value:
            chat_id = route.get("telegram_chat_id")
            topic_id = route.get("telegram_topic_id")
            target = (
                f"chat_id={chat_id} topic_id={topic_id}"
                if topic_id is not None
                else f"chat_id={chat_id}"
            )
        else:
            target = "-"

        route_table.add_row(
            str(i),
            str(route.get("destination_name") or route.get("webhook_name") or "?"),
            _destination_type_human(destination_type),
            target,
        )
    console.print(route_table)
    try:
        route_idx = _choose_index(
            "Destination route (1..N, q=cancel)", len(route_infos)
        )
    except CancelAction:
        console.print(_feedback("[warn]⚠[/warn] Canceled."))
        return
    selected_route = route_infos[route_idx - 1]

    text = getattr(message, "text", None) or getattr(message, "message", "") or ""
    if getattr(message, "media", None) and not str(text).strip():
        console.print(
            _feedback(
                "[warn]⚠[/warn] This is a media-only message (no caption). teleforward skips media-only posts.",
                title="Skipped",
            )
        )
        return
    transformer = forwarder._channel_transformer_map.get(src.channel_id)  # type: ignore[attr-defined]
    if transformer:
        result = transformer.transform(text)
        if not result.should_forward:
            console.print(
                Panel.fit(
                    f"[warn]⚠[/warn] Message would be blocked by rules:\n\n{result.blocked_by}",
                    title="Blocked",
                    border_style="bright_black",
                )
            )
            try:
                override = _prompt("Send anyway? (y/n, default n)", default="n").lower()
            except CancelAction:
                return
            if override not in {"y", "yes"}:
                console.print(_feedback("[warn]⚠[/warn] Canceled."))
                return
        transformed_text = result.transformed_text
    else:
        transformed_text = text

    transformed = forwarder._neutralize_mass_mentions(transformed_text)  # type: ignore

    media_path: Optional[str] = None
    if getattr(message, "media", None):
        try:
            media_path = await ctx.telegram.download_media(message)
        except Exception as e:
            console.print(
                _feedback(f"[warn]⚠[/warn] {e}", title="Media download failed")
            )
            media_path = None

    channel_username = (src.username or "").lstrip("@") if src.username else None
    telegram_link = (
        f"https://t.me/{channel_username}/{message.id}"
        if channel_username and getattr(message, "id", None)
        else None
    )
    attachment_name = None
    if media_path:
        try:
            attachment_name = Path(media_path).name
        except Exception:
            attachment_name = None

    embed = forwarder._build_embed(  # type: ignore
        channel_name=src.name,
        sender_name=sender_name or src.name,
        text=transformed,
        timestamp=getattr(message, "date", None),
        telegram_link=telegram_link,
        attachment_name=attachment_name,
    )
    destination_type = selected_route.get("destination_type")
    if destination_type == DestinationType.DISCORD_WEBHOOK.value:
        webhook_url = selected_route.get("webhook_url")
        if not webhook_url:
            console.print(
                _feedback("[error]✘[/error] Selected route has no webhook URL.")
            )
            if media_path:
                try:
                    Path(media_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return
        ok2, why = await discord_sender.send(
            str(webhook_url),
            message=DiscordMessage(
                content="",
                embeds=[embed],
                allowed_mentions=forwarder._discord_allowed_mentions(),  # type: ignore
                file_path=media_path,
                file_name=attachment_name,
            ),
        )
    elif destination_type == DestinationType.TELEGRAM_CHAT.value:
        destination_chat_id = selected_route.get("telegram_chat_id")
        if destination_chat_id is None:
            console.print(
                _feedback("[error]✘[/error] Selected route has no telegram_chat_id.")
            )
            if media_path:
                try:
                    Path(media_path).unlink(missing_ok=True)
                except Exception:
                    pass
            return
        outgoing_text = forwarder._build_telegram_text(  # type: ignore[attr-defined]
            channel_name=src.name,
            sender_name=sender_name or src.name,
            text=transformed,
            telegram_link=telegram_link,
        )
        ok2, why = await forwarder.telegram_sender.send(  # type: ignore[attr-defined]
            telegram=ctx.telegram,
            chat_id=int(destination_chat_id),
            message=TelegramOutgoingMessage(
                text=outgoing_text,
                file_path=media_path,
                topic_id=selected_route.get("telegram_topic_id"),
            ),
        )
    else:
        console.print(_feedback("[error]✘[/error] Unsupported route type."))
        if media_path:
            try:
                Path(media_path).unlink(missing_ok=True)
            except Exception:
                pass
        return

    if media_path:
        try:
            Path(media_path).unlink(missing_ok=True)
        except Exception:
            pass

    if ok2:
        console.print(_feedback("[ok]✔[/ok] Sent last-message test OK."))
    else:
        console.print(_feedback(f"[error]✘[/error] {why}", title="Send failed"))


def _print_logs(ctx: TuiContext) -> None:
    logs_v2 = ctx.db.get_forward_logs_v2(limit=20)

    if not logs_v2:
        console.print(_feedback("[warn]⚠[/warn] No forward logs yet."))
        return

    table_v2 = Table(
        title="Recent forward logs",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
    )
    table_v2.add_column("Time", style="dim")
    table_v2.add_column("Status", style="heading", no_wrap=True)
    table_v2.add_column("Route", style="value", overflow="ellipsis", max_width=18)
    table_v2.add_column("Destination", style="dim", ratio=1, overflow="fold")
    table_v2.add_column("Msg", style="value", overflow="ellipsis", max_width=18)
    table_v2.add_column("Error", style="error", ratio=1, overflow="fold")
    for row in logs_v2:
        ts = row.forwarded_at.strftime("%Y-%m-%d %H:%M:%S")
        status = row.status.upper()
        status_style = "ok" if status == "SUCCESS" else "error"
        destination = f"{row.destination_type}:{row.destination_name or '?'}"
        table_v2.add_row(
            ts,
            Text(status, style=status_style),
            str(row.route_mapping_id),
            destination,
            str(row.telegram_message_id),
            row.error_message or "",
        )
    console.print(table_v2)


def _mask_secret(value: Optional[str], keep_start: int = 8, keep_end: int = 6) -> str:
    if not value:
        return "(unset)"
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}...{value[-keep_end:]}"


def _load_env_values(env_path: Path) -> dict[str, str]:
    raw = dotenv_values(dotenv_path=env_path)
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key and value is not None:
            out[str(key)] = str(value)
    return out


def _set_env_value(env_path: Path, key: str, value: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()
    set_key(str(env_path), key, value, quote_mode="auto")


def _unset_env_value(env_path: Path, key: str) -> None:
    if env_path.exists():
        unset_key(str(env_path), key)


def _session_source_label(ctx: TuiContext) -> str:
    if ctx.config.telegram_session_string is not None:
        return "env"
    if ctx.db.get_setting("telegram_session_string"):
        return "db"
    return "none"


def _dashboard_header(
    *,
    config: Config,
    app_version: str,
    sources_count: int,
    destinations_count: int,
    routes_count: int,
    session_source: str,
) -> Panel:
    title = Text()
    title.append("teleforward", style="bold white")
    title.append(f" {app_version}", style="dim")

    stat_items = [
        Text.from_markup(f"[key]Sources:[/key] {sources_count}"),
        Text.from_markup(f"[key]Destinations:[/key] {destinations_count}"),
        Text.from_markup(f"[key]Routes:[/key] {routes_count}"),
        Text.from_markup(f"[key]Session:[/key] {session_source.upper()}"),
    ]
    stats_row = Columns(stat_items, padding=(0, 2), expand=False)

    w = _w()
    db_path = f"DATABASE_PATH={config.database_path}"
    data_dir = f"DATA_DIR={config.resolve_data_dir()}"
    session_lbl = f"SESSION={session_source}"
    if w >= 100:
        subtitle = Text(f"{db_path}   {data_dir}   {session_lbl}", style="dim")
    else:
        subtitle = Text(f"{db_path}\n{data_dir}\n{session_lbl}", style="dim")

    body = Group(stats_row, subtitle)

    return Panel.fit(
        body,
        title=title,
        title_align="left",
        border_style="bright_black",
        box=box.ROUNDED,
    )


async def _manage_runtime_settings(ctx: TuiContext) -> None:
    while True:
        env_values = _load_env_values(ctx.env_path)
        env_session = env_values.get("TELEGRAM_SESSION_STRING")
        db_session = ctx.db.get_setting("telegram_session_string")
        effective_session = (
            ctx.config.telegram_session_string
            if ctx.config.telegram_session_string is not None
            else db_session
        )

        details = Table(
            title="Environment and Runtime Settings",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            expand=True,
        )
        details.add_column("Key", style="key")
        details.add_column("Value", style="value", ratio=1, overflow="fold")
        details.add_row("ENV file", str(ctx.env_path.resolve()))
        details.add_row(
            "TELEGRAM_API_ID (.env)", env_values.get("TELEGRAM_API_ID", "(unset)")
        )
        details.add_row(
            "TELEGRAM_API_HASH (.env)",
            _mask_secret(env_values.get("TELEGRAM_API_HASH")),
        )
        details.add_row(
            "TELEGRAM_SESSION_STRING (.env)",
            _mask_secret(env_session),
        )
        details.add_row(
            "TELEGRAM_SESSION_STRING (db)",
            _mask_secret(db_session),
        )
        details.add_row(
            "Effective session source",
            "env"
            if ctx.config.telegram_session_string is not None
            else ("db" if db_session else "none"),
        )
        details.add_row("Effective session (this run)", _mask_secret(effective_session))
        details.add_row(
            "DATABASE_PATH (.env)", env_values.get("DATABASE_PATH", "(unset)")
        )
        details.add_row("DATA_DIR (.env)", env_values.get("DATA_DIR", "(unset)"))
        details.add_row("LOG_LEVEL (.env)", env_values.get("LOG_LEVEL", "(unset)"))
        console.print(details)

        actions = Table(title="Settings Actions", box=box.SIMPLE, show_header=False)
        actions.add_column("Key", style="key", no_wrap=True, width=4)
        actions.add_column("Action", style="white")
        actions.add_row("1", "Set TELEGRAM_API_ID in .env")
        actions.add_row("2", "Set TELEGRAM_API_HASH in .env")
        actions.add_row("3", "Set TELEGRAM_SESSION_STRING manually in .env")
        actions.add_row("4", "Export logged-in Telegram session to db + .env")
        actions.add_row("5", "Set DATABASE_PATH in .env")
        actions.add_row("6", "Set DATA_DIR in .env")
        actions.add_row("7", "Set LOG_LEVEL in .env")
        actions.add_row("8", "Clear TELEGRAM_SESSION_STRING from .env")
        actions.add_row("9", "Clear DATABASE_PATH and DATA_DIR from .env")
        actions.add_row("0", "Back")
        console.print(actions)
        console.print(
            _feedback(
                "[info]●[/info] Changes to .env apply on next process start. "
                "Database path changes require restart."
            )
        )

        try:
            choice = _prompt("Settings action (0-9)", default="0")
        except CancelAction:
            return

        try:
            if choice == "0":
                return
            if choice == "1":
                value = _prompt("TELEGRAM_API_ID")
                int(value)
                _set_env_value(ctx.env_path, "TELEGRAM_API_ID", value)
                console.print(_feedback("[ok]✔[/ok] Updated TELEGRAM_API_ID in .env"))
            elif choice == "2":
                value = _prompt("TELEGRAM_API_HASH")
                if not value:
                    console.print(_feedback("[warn]⚠[/warn] Value cannot be empty."))
                    continue
                _set_env_value(ctx.env_path, "TELEGRAM_API_HASH", value)
                console.print(_feedback("[ok]✔[/ok] Updated TELEGRAM_API_HASH in .env"))
            elif choice == "3":
                value = _prompt("TELEGRAM_SESSION_STRING")
                if not value:
                    console.print(_feedback("[warn]⚠[/warn] Value cannot be empty."))
                    continue
                _set_env_value(ctx.env_path, "TELEGRAM_SESSION_STRING", value)
                console.print(
                    _feedback("[ok]✔[/ok] Updated TELEGRAM_SESSION_STRING in .env")
                )
            elif choice == "4":
                ok = await _ensure_telegram_connected(ctx, interactive=True)
                if not ok:
                    console.print(_feedback("[warn]⚠[/warn] Telegram login required."))
                    continue
                session = ctx.telegram.export_session_string()
                if not session:
                    console.print(
                        _feedback(
                            "[error]✘[/error] Failed to export session from current client."
                        )
                    )
                    continue
                ctx.db.set_setting("telegram_session_string", session)
                _set_env_value(ctx.env_path, "TELEGRAM_SESSION_STRING", session)
                console.print(
                    _feedback("[ok]✔[/ok] Saved TELEGRAM_SESSION_STRING to db and .env")
                )
            elif choice == "5":
                value = _prompt("DATABASE_PATH (e.g. data/teleforward.db)")
                _set_env_value(ctx.env_path, "DATABASE_PATH", value)
                console.print(_feedback("[ok]✔[/ok] Updated DATABASE_PATH in .env"))
            elif choice == "6":
                value = _prompt("DATA_DIR (e.g. data or /var/lib/teleforward)")
                _set_env_value(ctx.env_path, "DATA_DIR", value)
                console.print(_feedback("[ok]✔[/ok] Updated DATA_DIR in .env"))
            elif choice == "7":
                value = _prompt("LOG_LEVEL", default="INFO").upper()
                if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
                    console.print(_feedback("[warn]⚠[/warn] Invalid log level."))
                    continue
                _set_env_value(ctx.env_path, "LOG_LEVEL", value)
                console.print(_feedback("[ok]✔[/ok] Updated LOG_LEVEL in .env"))
            elif choice == "8":
                _unset_env_value(ctx.env_path, "TELEGRAM_SESSION_STRING")
                console.print(
                    _feedback("[ok]✔[/ok] Cleared TELEGRAM_SESSION_STRING from .env")
                )
            elif choice == "9":
                _unset_env_value(ctx.env_path, "DATABASE_PATH")
                _unset_env_value(ctx.env_path, "DATA_DIR")
                console.print(
                    _feedback("[ok]✔[/ok] Cleared DATABASE_PATH and DATA_DIR from .env")
                )
            else:
                console.print(_feedback("[warn]⚠[/warn] Unknown option."))
        except ValueError:
            console.print(_feedback("[warn]⚠[/warn] Invalid value."))
        except Exception as e:
            console.print(_feedback(f"[error]✘[/error] {e}", title="Settings error"))


async def run_tui(config: Config, db: Database) -> None:
    app_version = _app_version()
    session_string: Optional[str] = (
        config.telegram_session_string
        if config.telegram_session_string is not None
        else db.get_setting("telegram_session_string")
    )

    telegram = TelegramClientWrapper(
        api_id=config.telegram_api_id,
        api_hash=config.telegram_api_hash,
        session_string=session_string,
        data_dir=config.resolve_data_dir(),
    )
    set_telegram_client(telegram)

    ctx = TuiContext(
        config=config,
        db=db,
        telegram=telegram,
        env_path=Path.cwd() / ".env",
    )

    def is_first_run() -> bool:
        has_v2 = (
            db.get_destinations(active_only=True)
            and db.get_route_rows(active_only=True)
            and db.get_telegram_channels(active_only=True)
        )
        return not has_v2

    async def setup_wizard() -> None:
        console.print(_feedback("[info]●[/info] Setup wizard"))
        console.print("[bold]Step 1[/bold]: Login to Telegram")
        await _ensure_telegram_connected(ctx, interactive=True)
        console.print("[bold]Step 2[/bold]: Import or add source channels")
        try:
            mode = (
                _prompt(
                    "Import source channels from Telegram? (y/n, default y)",
                    default="y",
                ).lower()
                or "y"
            )
        except CancelAction:
            console.print(_feedback("[warn]⚠[/warn] Canceled."))
            return
        if mode in {"y", "yes"}:
            await _import_channels(ctx)
        else:
            _add_channel_manual(ctx)
        console.print("[bold]Step 3[/bold]: Add a destination (Discord or Telegram)")
        await _add_destination_v2(ctx)
        console.print("[bold]Step 4[/bold]: Create routes")
        _create_routes_v2(ctx)
        console.print(
            _feedback(
                "[ok]✔[/ok] Setup complete. Use 'Run forwarder' to start forwarding."
            )
        )

    if is_first_run():
        console.print(
            _feedback(
                "[info]●[/info] No forwarding setup found. Starting setup wizard..."
            )
        )
        await setup_wizard()

    try:
        while True:
            console.clear()
            sources_count = len(db.get_telegram_channels(active_only=True))
            destinations_count = len(db.get_destinations(active_only=True))
            routes_count = len(db.get_route_rows(active_only=True))
            session_source = _session_source_label(ctx)

            def _menu_grid() -> Table:
                g = Table.grid(padding=(0, 1))
                g.add_column(style="key", no_wrap=True, justify="right", width=4)
                g.add_column(style="white")
                return g

            left = _menu_grid()
            left.add_row(Text("Sources", style="dim"), "")
            left.add_row("1", "Telegram account status")
            left.add_row("2", "Login / refresh session")
            left.add_row("3", "Export session string")
            left.add_row("4", "Import source channels")
            left.add_row("5", "Add source channel")
            left.add_row("6", "Manage source channels")
            left.add_row("", "")
            left.add_row(Text("Run", style="dim"), "")
            left.add_row("12", "Run forwarder")
            left.add_row("13", "Show recent logs")

            right = _menu_grid()
            right.add_row(Text("Destinations", style="dim"), "")
            right.add_row("7", "List destinations")
            right.add_row("8", "Add destination")
            right.add_row("9", "Manage destinations")
            right.add_row("10", "Create routes")
            right.add_row("11", "Manage routes")
            right.add_row("", "")
            right.add_row(Text("Tools", style="dim"), "")
            right.add_row("14", "Run setup wizard")
            right.add_row("15", "Send test message")
            right.add_row("16", "Test forward latest")
            right.add_row("17", "Settings")
            right.add_row("0", "Exit")

            def _render_home(typed: str = "") -> Panel:
                header = _dashboard_header(
                    config=config,
                    app_version=app_version,
                    sources_count=sources_count,
                    destinations_count=destinations_count,
                    routes_count=routes_count,
                    session_source=session_source,
                )

                terminal_width = _w()
                if terminal_width >= 110:
                    menu_content = Columns(
                        [left, right],
                        expand=False,
                        equal=True,
                        padding=(0, 3),
                    )
                else:
                    menu_content = Group(left, Text(""), right)

                input_hint = Text.from_markup(
                    f"[key]Select[/key] [dim](0-17, q=exit)[/dim]: [value]{typed}[/value]"
                )
                home_width = min(120, max(24, terminal_width - 4))
                return Panel(
                    Group(header, Text(""), menu_content, Text(""), input_hint),
                    title="Home",
                    border_style="bright_black",
                    box=box.ROUNDED,
                    padding=(1, 2),
                    width=home_width,
                )

            try:
                choice = await _read_home_choice_live(_render_home)
            except KeyboardInterrupt:
                raise
            if choice.lower() in {"q", "quit"}:
                choice = "0"

            try:
                if choice == "0":
                    await ctx.telegram.stop()
                    return
                if choice == "1":
                    await _login_status(ctx)
                elif choice == "2":
                    await _ensure_telegram_connected(ctx, interactive=True)
                elif choice == "3":
                    await _export_telegram_session(ctx)
                elif choice == "4":
                    await _import_channels(ctx)
                elif choice == "5":
                    _add_channel_manual(ctx)
                elif choice == "6":
                    _manage_channels(ctx)
                elif choice == "7":
                    _print_destinations_v2(ctx)
                elif choice == "8":
                    await _add_destination_v2(ctx)
                elif choice == "9":
                    await _manage_destinations_v2(ctx)
                elif choice == "10":
                    _create_routes_v2(ctx)
                elif choice == "11":
                    _manage_routes_v2(ctx)
                elif choice == "12":
                    await _run_forwarder(ctx)
                elif choice == "13":
                    _print_logs(ctx)
                elif choice == "14":
                    await setup_wizard()
                elif choice == "15":
                    await _send_test_message(ctx)
                elif choice == "16":
                    await _test_forward_last_message(ctx)
                elif choice == "17":
                    await _manage_runtime_settings(ctx)
                else:
                    console.print(_feedback("[warn]⚠[/warn] Unknown option."))
            except CancelAction:
                console.print(_feedback("[warn]⚠[/warn] Canceled."))
            _pause()
    except KeyboardInterrupt:
        console.print(_feedback("[info]●[/info] Exiting…"))
    finally:
        try:
            await ctx.telegram.stop()
        except Exception:
            pass


async def run_headless(config: Config, db: Database) -> None:
    session_string: Optional[str] = (
        config.telegram_session_string
        if config.telegram_session_string is not None
        else db.get_setting("telegram_session_string")
    )
    if not session_string:
        raise RuntimeError(
            "No TELEGRAM_SESSION_STRING set and no saved session in the database."
        )

    telegram = TelegramClientWrapper(
        api_id=config.telegram_api_id,
        api_hash=config.telegram_api_hash,
        session_string=session_string,
        data_dir=config.resolve_data_dir(),
    )
    set_telegram_client(telegram)

    await telegram.start(phone=None)
    forwarder = Forwarder(
        db=db,
        telegram=telegram,
        allow_mass_mentions=config.discord_allow_mass_mentions,
        suppress_url_embeds=config.discord_suppress_url_embeds,
        strip_urls=config.discord_strip_urls,
        include_telegram_link=config.discord_include_telegram_link,
    )

    def on_forward(event: dict):
        status = "OK" if event.get("success") else "ERR"
        destination = event.get("destination_name") or event.get("webhook_name", "?")
        channel_id = event.get("channel_id")
        message_id = event.get("message_id")
        error = event.get("error")
        extra = f" ({error})" if error else ""
        logger.info(
            "%s tg=%s msg=%s -> %s%s",
            status,
            channel_id,
            message_id,
            destination,
            extra,
        )

    forwarder.set_on_forward_callback(on_forward)

    try:
        await forwarder.start()
    except KeyboardInterrupt:
        logger.info("Stopping (Ctrl+C)...")
    finally:
        await forwarder.stop()
        await telegram.stop()
