import logging
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.columns import Columns
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table
from rich.text import Text

from config import Config
from core.discord_sender import discord_sender, DiscordMessage
from core.forwarder import Forwarder
from core.telegram_client import TelegramClientWrapper, set_telegram_client
from database.db import Database


logger = logging.getLogger(__name__)
console = Console()


class CancelAction(Exception):
    pass


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
            console.print(Panel("Please enter a valid integer.", border_style="yellow"))
            continue
        try:
            return int(raw)
        except ValueError:
            console.print(Panel("Please enter a valid integer.", border_style="yellow"))


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
            console.print(Panel("Please enter a number.", border_style="yellow"))
            continue
        if 1 <= idx <= max_index:
            return idx
        console.print(
            Panel(f"Please enter a number between 1 and {max_index}.", border_style="yellow")
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
                Panel(
                    "Please enter a comma-separated list like: 1,2,5 (or 'all').",
                    border_style="yellow",
                )
            )
            continue
        if not idxs:
            console.print(Panel("Select at least one item.", border_style="yellow"))
            continue
        if any(i < 1 or i > max_index for i in idxs):
            console.print(
                Panel(f"Indexes must be between 1 and {max_index}.", border_style="yellow")
            )
            continue
        return idxs


@dataclass
class TuiContext:
    config: Config
    db: Database
    telegram: TelegramClientWrapper


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

    console.print(Panel("Telegram login (enter 'q' to cancel).", border_style="cyan"))
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
        console.print(Panel("Saved Telegram session string to the database.", border_style="green"))
    return True


def _print_channels(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels()
    if not channels:
        console.print(Panel("No Telegram channels saved.", title="Telegram", border_style="yellow"))
        return
    table = Table(title="Telegram channels", show_lines=False)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Username", style="magenta")
    table.add_column("Channel ID", style="white")
    table.add_column("Status", style="green")
    for i, ch in enumerate(channels, start=1):
        status = "active" if ch.is_active else "disabled"
        status_style = "green" if ch.is_active else "red"
        table.add_row(
            str(i),
            ch.name,
            f"@{ch.username}" if ch.username else "-",
            str(ch.channel_id),
            Text(status, style=status_style),
        )
    console.print(table)


def _print_webhooks(ctx: TuiContext) -> None:
    webhooks = ctx.db.get_discord_webhooks()
    if not webhooks:
        console.print(Panel("No Discord webhooks saved.", title="Discord", border_style="yellow"))
        return
    table = Table(title="Discord webhooks")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Status", style="green")
    for i, wh in enumerate(webhooks, start=1):
        status = "active" if wh.is_active else "disabled"
        status_style = "green" if wh.is_active else "red"
        table.add_row(str(i), wh.name, Text(status, style=status_style))
    console.print(table)


async def _login_status(ctx: TuiContext) -> None:
    try:
        if not await _ensure_telegram_connected(ctx, interactive=False):
            console.print(Panel("Not connected (no valid session).", title="Telegram", border_style="yellow"))
            return
        me = await ctx.telegram.get_me()
        if not me:
            console.print(Panel("Connected, but not authorized.", title="Telegram", border_style="yellow"))
            return
        console.print(Panel(f"Connected as [bold]{me.first_name}[/bold] (id={me.id})", title="Telegram", border_style="green"))
    except Exception as e:
        console.print(Panel(f"{e}", title="Telegram status error", border_style="red"))


async def _export_telegram_session(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=True)
    if not ok:
        console.print(Panel("Telegram login required.", border_style="yellow"))
        return

    session = ctx.telegram.export_session_string()
    if not session:
        console.print(
            Panel(
                "Could not read a Telegram session string from the client.",
                title="Telegram session",
                border_style="red",
            )
        )
        return

    preview = session[:24] + "..." + session[-12:]
    console.print(
        Panel(
            "This session string is equivalent to your Telegram login.\n"
            "Treat it like a password and store it as a secret.\n\n"
            f"[dim]Preview[/dim]=[white]{preview}[/white]\n"
            f"[dim]Length[/dim]=[white]{len(session)}[/white]",
            title="Export Telegram session string",
            border_style="yellow",
        )
    )

    try:
        reveal = _prompt("Reveal full session string? (y/n, default n)", default="n").lower()
    except CancelAction:
        return
    if reveal not in {"y", "yes"}:
        console.print(Panel("Not revealed.", border_style="cyan"))
        return

    console.print(
        Panel(
            f"TELEGRAM_SESSION_STRING={session}",
            title="Copy into /etc/teleforward/teleforward.env",
            border_style="green",
        )
    )
    try:
        input("\nPress Enter to clear this from the screen...")
    except KeyboardInterrupt:
        raise
    console.clear()


async def _import_channels(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=True)
    if not ok:
        console.print(Panel("Telegram login required.", border_style="yellow"))
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
        console.print(Panel("No dialogs found.", border_style="yellow"))
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
            Panel(
                f"[bold]Import from Telegram[/bold]\n\n"
                f"[dim]Loaded[/dim]=[white]{loaded}[/white]  "
                f"[dim]Filtered[/dim]=[white]{len(apply_filter())}[/white]  "
                f"[dim]Selected[/dim]=[white]{len(selected_ids)}[/white]  "
                f"[dim]More[/dim]=[white]{'yes' if not exhausted else 'no'}[/white]\n"
                f"[dim]Search[/dim]=[white]{query or '(all)'}[/white]",
                border_style="cyan",
            )
        )
        filtered = apply_filter()
        if not filtered:
            console.print(Panel("No matches. Change search.", border_style="yellow"))
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

        table = Table(title="Telegram dialogs")
        table.add_column("#", style="cyan", no_wrap=True)
        table.add_column("Sel", style="green", no_wrap=True)
        table.add_column("Name", style="bold")
        table.add_column("Username", style="magenta")
        table.add_column("ID", style="white")
        table.add_column("Type", style="blue")
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
            Panel(
                "Commands:\n"
                "  - numbers (e.g. 1,3,5): toggle selection on this page\n"
                "  - n / p: next / previous page\n"
                "  - s: set search query\n"
                "  - c: clear selection\n"
                "  - i: import selected\n"
                "  - q: cancel\n",
                title=f"Page {page+1}/{total_pages}  (showing {start+1}-{end})",
                border_style="cyan",
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
                console.print(Panel("Nothing selected.", border_style="yellow"))
                continue
            break

        # toggle selection on current page by index
        parts = [p.strip() for p in cmd.split(",") if p.strip()]
        try:
            idxs = sorted({int(p) for p in parts})
        except ValueError:
            console.print(Panel("Unknown command.", border_style="yellow"))
            continue
        if not idxs or any(i < 1 or i > len(page_items) for i in idxs):
            console.print(Panel("Index out of range for this page.", border_style="yellow"))
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
        Panel(
            f"Imported/updated [bold]{len(selected)}[/bold] items ([bold]{imported}[/bold] new).",
            title="Import complete",
            border_style="green",
        )
    )


def _add_channel_manual(ctx: TuiContext) -> None:
    try:
        channel_id = _prompt_int("Telegram channel id (e.g. -1001234567890)")
        name = _prompt("Name")
        username = _prompt("Username (optional, without @)", default="")
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    username = username or None
    if not name:
        console.print(Panel("Name is required.", border_style="yellow"))
        return
    ctx.db.add_telegram_channel(channel_id, name, username)
    console.print(Panel("Saved.", title="Telegram channel", border_style="green"))


async def _add_webhook(ctx: TuiContext) -> None:
    try:
        name = _prompt("Discord webhook name (label)")
        url = _prompt("Discord webhook URL")
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    if not name or not url:
        console.print(Panel("Name and URL are required.", border_style="yellow"))
        return

    ok, why = await discord_sender.test_webhook(url)
    if not ok:
        console.print(Panel(f"{why}", title="Webhook test failed", border_style="red"))
        return

    ctx.db.add_discord_webhook(name=name, url=url)
    console.print(Panel("Saved.", title="Webhook", border_style="green"))


async def _send_test_message(ctx: TuiContext) -> None:
    webhooks = ctx.db.get_discord_webhooks(active_only=True)
    if not webhooks:
        console.print(Panel("No active webhooks. Add one first.", border_style="yellow"))
        return

    table = Table(title="Destination webhook")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    for i, wh in enumerate(webhooks, start=1):
        table.add_row(str(i), wh.name)
    console.print(table)
    try:
        webhook_idx = _choose_index("Select destination webhook (1..N, q=cancel)", len(webhooks))
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    webhook = webhooks[webhook_idx - 1]

    default = "TeleForward test message"
    try:
        content = _prompt(f"Message content (blank for '{default}')", default="")
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    content = content or default

    ok, why = await discord_sender.send(
        webhook.url,
        message=DiscordMessage(
            content="",
            embeds=[
                {
                    "title": "TeleForward test",
                    "description": content,
                    "color": 0x5865F2,
                    "footer": {"text": "TeleForward"},
                }
            ],
        ),
    )
    if ok:
        console.print(Panel("Test message sent OK.", border_style="green"))
    else:
        console.print(Panel(f"{why}", title="Test message failed", border_style="red"))


def _manage_channels(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels(active_only=False)
    if not channels:
        console.print(Panel("No Telegram channels saved.", border_style="yellow"))
        return

    table = Table(title="Manage Telegram channels")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Username", style="magenta")
    table.add_column("Channel ID", style="white")
    table.add_column("Status", style="green", no_wrap=True)
    for i, ch in enumerate(channels, start=1):
        status = "active" if ch.is_active else "disabled"
        status_style = "green" if ch.is_active else "red"
        table.add_row(
            str(i),
            ch.name,
            f"@{ch.username}" if ch.username else "-",
            str(ch.channel_id),
            Text(status, style=status_style),
        )
    console.print(table)

    try:
        idx = _choose_index("Select channel (1..N, q=back)", len(channels))
    except CancelAction:
        return
    ch = channels[idx - 1]

    actions = Table(title=f"Channel: {ch.name}")
    actions.add_column("Key", style="cyan", no_wrap=True)
    actions.add_column("Action", style="bold")
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
        console.print(Panel("Updated.", border_style="green"))
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

        ctx.db.update_telegram_channel(ch.id, name=name_to_set, username=username_to_set)
        console.print(Panel("Updated.", border_style="green"))
        return
    if choice == "3":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(Panel("Canceled.", border_style="yellow"))
            return
        ctx.db.delete_telegram_channel(ch.id)
        console.print(Panel("Deleted.", border_style="green"))
        return


async def _manage_webhooks(ctx: TuiContext) -> None:
    webhooks = ctx.db.get_discord_webhooks(active_only=False)
    if not webhooks:
        console.print(Panel("No Discord webhooks saved.", border_style="yellow"))
        return

    table = Table(title="Manage Discord webhooks")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Status", style="green", no_wrap=True)
    for i, wh in enumerate(webhooks, start=1):
        status = "active" if wh.is_active else "disabled"
        status_style = "green" if wh.is_active else "red"
        table.add_row(str(i), wh.name, Text(status, style=status_style))
    console.print(table)

    try:
        idx = _choose_index("Select webhook (1..N, q=back)", len(webhooks))
    except CancelAction:
        return
    wh = webhooks[idx - 1]

    actions = Table(title=f"Webhook: {wh.name}")
    actions.add_column("Key", style="cyan", no_wrap=True)
    actions.add_column("Action", style="bold")
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
        console.print(Panel("Updated.", border_style="green"))
        return
    if choice == "2":
        try:
            new_name = _prompt("New name", default=wh.name)
        except CancelAction:
            return
        ctx.db.update_discord_webhook(wh.id, name=new_name)
        console.print(Panel("Updated.", border_style="green"))
        return
    if choice == "3":
        try:
            new_url = _prompt("New webhook URL")
        except CancelAction:
            return
        ok, why = await discord_sender.test_webhook(new_url)
        if not ok:
            console.print(Panel(f"{why}", title="Webhook test failed", border_style="red"))
            return
        ctx.db.update_discord_webhook(wh.id, url=new_url)
        console.print(Panel("Updated.", border_style="green"))
        return
    if choice == "4":
        try:
            confirm = _prompt("Type 'delete' to confirm", default="")
        except CancelAction:
            return
        if confirm != "delete":
            console.print(Panel("Canceled.", border_style="yellow"))
            return
        ctx.db.delete_discord_webhook(wh.id)
        console.print(Panel("Deleted.", border_style="green"))
        return


def _manage_mappings(ctx: TuiContext) -> None:
    channels = {c.id: c for c in ctx.db.get_telegram_channels(active_only=False)}
    webhooks = ctx.db.get_discord_webhooks(active_only=False)
    if not webhooks:
        console.print(Panel("No Discord webhooks saved.", border_style="yellow"))
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
            table = Table(title=title)
            table.add_column("Row", style="cyan", no_wrap=True)
            table.add_column("Sel", style="green", no_wrap=True)
            table.add_column("Source channel", style="bold")
            table.add_column("Channel ID", style="white", no_wrap=True)
            table.add_column("Status", style="green", no_wrap=True)
            for i, m in enumerate(grouped, start=1):
                ch = channels.get(m.channel_id)
                status = "active" if m.is_active else "disabled"
                status_style = "green" if m.is_active else "red"
                table.add_row(
                    str(i),
                    "✓" if i in selected else "",
                    ch.name if ch else f"(channel db_id={m.channel_id})",
                    str(ch.channel_id) if ch else "-",
                    Text(status, style=status_style),
                )
            console.print(table)
            console.print(
                Panel(
                    "Selection:\n"
                    "  - numbers (e.g. 1,3,5): toggle selection\n"
                    "  - Enter               : continue\n"
                    "  - c                   : clear selection\n"
                    "  - q                   : back\n",
                    title=f"Selected: {len(selected)}",
                    border_style="cyan",
                )
            )
            try:
                cmd = _prompt("Select rows", default="").strip()
            except CancelAction:
                return []
            low = cmd.lower()
            if cmd == "":
                if not selected:
                    console.print(Panel("Select at least one row.", border_style="yellow"))
                    continue
                return sorted(selected)
            if low in {"c", "clear"}:
                selected.clear()
                continue
            parts = [p.strip() for p in cmd.split(",") if p.strip()]
            try:
                idxs = sorted({int(p) for p in parts})
            except ValueError:
                console.print(Panel("Use row numbers like: 1,2,5", border_style="yellow"))
                continue
            if not idxs or any(i < 1 or i > len(grouped) for i in idxs):
                console.print(Panel("Row index out of range.", border_style="yellow"))
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

        wh_table = Table(title="Manage mappings (webhook -> channels)")
        wh_table.add_column("Row", style="cyan", no_wrap=True)
        wh_table.add_column("Webhook", style="bold")
        wh_table.add_column("Mapped channels", style="white", no_wrap=True)
        wh_table.add_column("Status", style="green", no_wrap=True)
        for i, wh in enumerate(webhooks, start=1):
            status = "active" if wh.is_active else "disabled"
            status_style = "green" if wh.is_active else "red"
            wh_table.add_row(
                str(i),
                wh.name,
                str(counts.get(wh.id, 0)),
                Text(status, style=status_style),
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

            table = Table(title=f"Channels -> {selected_webhook.name}")
            table.add_column("Row", style="cyan", no_wrap=True)
            table.add_column("Source channel", style="bold")
            table.add_column("Channel ID", style="white", no_wrap=True)
            table.add_column("Status", style="green", no_wrap=True)
            for i, m in enumerate(grouped, start=1):
                ch = channels.get(m.channel_id)
                status = "active" if m.is_active else "disabled"
                status_style = "green" if m.is_active else "red"
                table.add_row(
                    str(i),
                    ch.name if ch else f"(channel db_id={m.channel_id})",
                    str(ch.channel_id) if ch else "-",
                    Text(status, style=status_style),
                )
            console.print(table)
            if not grouped:
                console.print(
                    Panel(
                        "No channels mapped yet. Use 'a' to add.",
                        border_style="yellow",
                    )
                )

            console.print(
                Panel(
                    "Commands:\n\n"
                    "  - a : add channel(s)\n"
                    "  - t : toggle mapping(s)\n"
                    "  - e : enable mapping(s)\n"
                    "  - x : disable mapping(s)\n"
                    "  - d : delete mapping(s)\n"
                    "  - m : move mapping(s) to another webhook\n"
                    "  - q : back\n",
                    title="Manage",
                    border_style="cyan",
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
                        Panel(
                            "All active channels are already mapped to this webhook (or no active channels exist).",
                            border_style="yellow",
                        )
                    )
                    continue

                add_table = Table(title=f"Add channels -> {selected_webhook.name}")
                add_table.add_column("Row", style="cyan", no_wrap=True)
                add_table.add_column("Name", style="bold")
                add_table.add_column("Username", style="magenta")
                add_table.add_column("Channel ID", style="white")
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
                    console.print(Panel("Canceled.", border_style="yellow"))
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
                console.print(Panel(f"Added {created} mapping(s).", border_style="green"))
                continue

            if action in {"toggle", "enable", "disable", "delete", "move"}:
                if not grouped:
                    console.print(Panel("No mappings to modify yet.", border_style="yellow"))
                    continue

                idxs: Optional[list[int]]
                if raw.strip():
                    idxs = parse_rows(raw, len(grouped))
                    if idxs is None:
                        console.print(Panel("Use row numbers like: 1,2,5", border_style="yellow"))
                        continue
                else:
                    idxs = select_rows_dialog(
                        title=f"Select mappings to {action} -> {selected_webhook.name}",
                        grouped=grouped,
                    )
                    if not idxs:
                        console.print(Panel("Canceled.", border_style="yellow"))
                        continue
                targets = [grouped[i - 1] for i in idxs]

                if action == "toggle":
                    for m in targets:
                        ctx.db.toggle_channel_mapping(m.id, not m.is_active)
                    console.print(Panel("Toggled.", border_style="green"))
                    continue
                if action == "enable":
                    for m in targets:
                        ctx.db.toggle_channel_mapping(m.id, True)
                    console.print(Panel("Enabled.", border_style="green"))
                    continue
                if action == "disable":
                    for m in targets:
                        ctx.db.toggle_channel_mapping(m.id, False)
                    console.print(Panel("Disabled.", border_style="green"))
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
                        console.print(Panel("Canceled.", border_style="yellow"))
                        continue
                    for m in targets:
                        ctx.db.delete_channel_mapping(m.id)
                    console.print(Panel("Deleted.", border_style="green"))
                    continue

                # move
                active_webhooks = [
                    w for w in webhooks if w.is_active and w.id != selected_webhook.id
                ]
                if not active_webhooks:
                    console.print(Panel("No other active webhooks to move to.", border_style="yellow"))
                    continue
                pick_table = Table(title="Move to which webhook?")
                pick_table.add_column("Row", style="cyan", no_wrap=True)
                pick_table.add_column("Name", style="bold")
                for i, w in enumerate(active_webhooks, start=1):
                    pick_table.add_row(str(i), w.name)
                console.print(pick_table)
                try:
                    new_idx = _choose_index("Webhook (1..N, q=cancel)", len(active_webhooks))
                except CancelAction:
                    continue
                new_wh = active_webhooks[new_idx - 1]
                for m in targets:
                    ctx.db.update_channel_mapping_webhook(m.id, new_wh.id)
                console.print(
                    Panel(
                        f"Moved {len(targets)} mapping(s) to '{new_wh.name}'.",
                        border_style="green",
                    )
                )
                continue

            console.print(Panel("Unknown command.", border_style="yellow"))


def _create_mappings(ctx: TuiContext) -> None:
    channels = ctx.db.get_telegram_channels(active_only=True)
    webhooks = ctx.db.get_discord_webhooks(active_only=True)

    if not channels:
        console.print(
            Panel(
                "No active Telegram channels. Add/import some first.",
                border_style="yellow",
            )
        )
        return
    if not webhooks:
        console.print(
            Panel(
                "No active Discord webhooks. Add one first.",
                border_style="yellow",
            )
        )
        return

    channel_table = Table(title="Source Telegram channels")
    channel_table.add_column("#", style="cyan", no_wrap=True)
    channel_table.add_column("Name", style="bold")
    channel_table.add_column("Username", style="magenta")
    channel_table.add_column("Channel ID", style="white")
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
        console.print(Panel("Canceled.", border_style="yellow"))
        return

    selected_channels = [channels[i - 1] for i in channel_idxs]
    selected_table = Table(title="Selected source channels")
    selected_table.add_column("#", style="cyan", no_wrap=True)
    selected_table.add_column("Name", style="bold")
    selected_table.add_column("Channel ID", style="white")
    for i, ch in enumerate(selected_channels, start=1):
        selected_table.add_row(str(i), ch.name, str(ch.channel_id))
    console.print(selected_table)

    webhook_table = Table(title="Destination Discord webhook")
    webhook_table.add_column("#", style="cyan", no_wrap=True)
    webhook_table.add_column("Name", style="bold")
    for i, wh in enumerate(webhooks, start=1):
        webhook_table.add_row(str(i), wh.name)
    console.print(webhook_table)

    try:
        webhook_idx = _choose_index(
            "Select destination webhook (1..N, q=cancel)",
            max_index=len(webhooks),
        )
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return

    webhook = webhooks[webhook_idx - 1]

    try:
        confirm = _prompt(
            f"Create {len(selected_channels)} mapping(s) to '{webhook.name}'? (y/n, default y)",
            default="y",
        ).lower() or "y"
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    if confirm not in {"y", "yes"}:
        console.print(Panel("Canceled.", border_style="yellow"))
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
        Panel(
            f"Created [bold]{created}[/bold] mapping(s). "
            f"Skipped [bold]{skipped}[/bold] existing.",
            title="Mappings",
            border_style="green",
        )
    )


async def _run_forwarder(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=False)
    if not ok:
        console.print(
            Panel("No valid Telegram session. Run 'Login' first.", border_style="yellow")
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
        webhook = event.get("webhook_name", "?")
        channel_id = event.get("channel_id")
        message_id = event.get("message_id")
        error = event.get("error")
        extra = f" ({error})" if error else ""
        style = "green" if status == "OK" else "red"
        console.print(f"[{style}]{status}[/{style}] tg={channel_id} msg={message_id} -> {webhook}{extra}")

    forwarder.set_on_forward_callback(on_forward)

    console.print(Panel("Forwarder running. Press Ctrl+C to stop.", border_style="cyan"))
    try:
        await forwarder.start()
    except KeyboardInterrupt:
        console.print(Panel("Stopping...", border_style="cyan"))
    finally:
        await forwarder.stop()
        await ctx.telegram.stop()


async def _test_forward_last_message(ctx: TuiContext) -> None:
    ok = await _ensure_telegram_connected(ctx, interactive=True)
    if not ok:
        console.print(Panel("Telegram login required.", border_style="yellow"))
        return

    channels = ctx.db.get_telegram_channels(active_only=True)
    webhooks = ctx.db.get_discord_webhooks(active_only=True)
    if not channels:
        console.print(Panel("No active Telegram channels saved.", border_style="yellow"))
        return
    if not webhooks:
        console.print(Panel("No active Discord webhooks saved.", border_style="yellow"))
        return

    source_table = Table(title="Select source Telegram channel")
    source_table.add_column("#", style="cyan", no_wrap=True)
    source_table.add_column("Name", style="bold")
    source_table.add_column("Username", style="magenta")
    source_table.add_column("Channel ID", style="white")
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
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    src = channels[src_idx - 1]

    recent = []
    async for msg in ctx.telegram.iter_messages(src.channel_id, limit=5):
        recent.append(msg)
    if not recent:
        console.print(Panel("No messages found in that channel.", border_style="yellow"))
        return

    msg_table = Table(title="Recent messages (most recent first)")
    msg_table.add_column("#", style="cyan", no_wrap=True)
    msg_table.add_column("ID", style="white", no_wrap=True)
    msg_table.add_column("Time", style="blue")
    msg_table.add_column("Media", style="magenta", no_wrap=True)
    msg_table.add_column("Preview", style="bold")
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
            console.print(Panel("Out of range.", border_style="yellow"))
            return
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    except Exception:
        console.print(Panel("Invalid selection.", border_style="yellow"))
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

    dest_table = Table(title="Select destination webhook")
    dest_table.add_column("#", style="cyan", no_wrap=True)
    dest_table.add_column("Name", style="bold")
    for i, wh in enumerate(webhooks, start=1):
        dest_table.add_row(str(i), wh.name)
    console.print(dest_table)
    try:
        wh_idx = _choose_index("Destination webhook (1..N, q=cancel)", len(webhooks))
    except CancelAction:
        console.print(Panel("Canceled.", border_style="yellow"))
        return
    webhook = webhooks[wh_idx - 1]

    forwarder = Forwarder(
        db=ctx.db,
        telegram=ctx.telegram,
        allow_mass_mentions=ctx.config.discord_allow_mass_mentions,
        suppress_url_embeds=ctx.config.discord_suppress_url_embeds,
        strip_urls=ctx.config.discord_strip_urls,
        include_telegram_link=ctx.config.discord_include_telegram_link,
    )

    forwarder.reload_mappings()
    text = (getattr(message, "text", None) or getattr(message, "message", "") or "")
    if getattr(message, "media", None) and not str(text).strip():
        console.print(
            Panel(
                "This is a media-only message (no caption). TeleForward is configured to skip media-only posts.",
                title="Skipped",
                border_style="yellow",
            )
        )
        return
    transformer = forwarder._channel_transformer_map.get(src.channel_id)  # type: ignore[attr-defined]
    if transformer:
        result = transformer.transform(text)
        if not result.should_forward:
            console.print(
                Panel(
                    f"Message would be blocked by rules:\n\n{result.blocked_by}",
                    title="Blocked",
                    border_style="yellow",
                )
            )
            try:
                override = _prompt("Send anyway? (y/n, default n)", default="n").lower()
            except CancelAction:
                return
            if override not in {"y", "yes"}:
                console.print(Panel("Canceled.", border_style="yellow"))
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
            console.print(Panel(f"{e}", title="Media download failed", border_style="yellow"))
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

    ok2, why = await discord_sender.send(
        webhook.url,
        message=DiscordMessage(
            content="",
            embeds=[embed],
            allowed_mentions=forwarder._discord_allowed_mentions(),  # type: ignore
            file_path=media_path,
            file_name=attachment_name,
        ),
    )

    if media_path:
        try:
            Path(media_path).unlink(missing_ok=True)
        except Exception:
            pass

    if ok2:
        console.print(Panel("Sent last-message test OK.", border_style="green"))
    else:
        console.print(Panel(f"{why}", title="Send failed", border_style="red"))


def _print_logs(ctx: TuiContext) -> None:
    logs = ctx.db.get_forward_logs(limit=20)
    if not logs:
        console.print(Panel("No forward logs yet.", border_style="yellow"))
        return
    table = Table(title="Recent forward logs")
    table.add_column("Time", style="blue")
    table.add_column("Status", style="bold")
    table.add_column("Mapping", style="white", no_wrap=True)
    table.add_column("Msg", style="white", no_wrap=True)
    table.add_column("Error", style="red")
    for row in logs:
        ts = row.forwarded_at.strftime("%Y-%m-%d %H:%M:%S")
        status = row.status.upper()
        status_style = "green" if status == "SUCCESS" else "red"
        table.add_row(
            ts,
            Text(status, style=status_style),
            str(row.mapping_id),
            str(row.telegram_message_id),
            row.error_message or "",
        )
    console.print(table)


async def run_tui(config: Config, db: Database) -> None:
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

    ctx = TuiContext(config=config, db=db, telegram=telegram)

    header_panel = Panel(
        f"[bold]TeleForward[/bold] - Telegram -> Discord\n\n"
        f"[dim]DATABASE_PATH[/dim]=[white]{config.database_path}[/white]\n"
        f"[dim]DATA_DIR[/dim]=[white]{config.resolve_data_dir()}[/white]\n"
        f"[dim]Mass mentions[/dim]=[white]{'enabled' if config.discord_allow_mass_mentions else 'blocked'}[/white]\n"
        f"[dim]Tips[/dim]=[white]type 'q' to cancel most prompts[/white]",
        border_style="cyan",
    )

    def is_first_run() -> bool:
        return not (
            db.get_discord_webhooks(active_only=True)
            and db.get_telegram_channels(active_only=True)
            and db.get_channel_mappings(active_only=True)
        )

    async def setup_wizard() -> None:
        console.print(Panel("Setup wizard", border_style="cyan"))
        console.print("[bold]Step 1[/bold]: Login to Telegram")
        await _ensure_telegram_connected(ctx, interactive=True)
        console.print("[bold]Step 2[/bold]: Import or add channels")
        try:
            mode = _prompt("Import from Telegram? (y/n, default y)", default="y").lower() or "y"
        except CancelAction:
            console.print(Panel("Canceled.", border_style="yellow"))
            return
        if mode in {"y", "yes"}:
            await _import_channels(ctx)
        else:
            _add_channel_manual(ctx)
        console.print("[bold]Step 3[/bold]: Add a Discord webhook")
        await _add_webhook(ctx)
        console.print("[bold]Step 4[/bold]: Create mappings")
        _create_mappings(ctx)
        console.print(Panel("Setup complete. Use 'Run forwarder' to start forwarding.", border_style="green"))

    if is_first_run():
        console.print(Panel("No forwarding setup found. Starting setup wizard...", border_style="cyan"))
        await setup_wizard()

    try:
        while True:
            console.clear()
            console.print(header_panel)
            channels_count = len(db.get_telegram_channels(active_only=True))
            webhooks_count = len(db.get_discord_webhooks(active_only=True))
            mappings_count = len(db.get_channel_mappings(active_only=True))

            telegram_menu = Table(show_header=False, box=None, pad_edge=False)
            telegram_menu.add_column("Key", style="cyan", no_wrap=True)
            telegram_menu.add_column("Action", style="bold")
            telegram_menu.add_row("1", "Telegram status")
            telegram_menu.add_row("2", "Login / refresh session")
            telegram_menu.add_row("3", "Export session string (copy to env)")
            telegram_menu.add_row("4", "Import channels (search)")
            telegram_menu.add_row("5", "Add channel manually")
            telegram_menu.add_row("6", "Manage channels (rename/toggle/delete)")

            discord_menu = Table(show_header=False, box=None, pad_edge=False)
            discord_menu.add_column("Key", style="cyan", no_wrap=True)
            discord_menu.add_column("Action", style="bold")
            discord_menu.add_row("7", "List webhooks")
            discord_menu.add_row("8", "Add webhook")
            discord_menu.add_row("9", "Manage webhooks (rename/toggle/delete)")

            mapping_menu = Table(show_header=False, box=None, pad_edge=False)
            mapping_menu.add_column("Key", style="cyan", no_wrap=True)
            mapping_menu.add_column("Action", style="bold")
            mapping_menu.add_row("10", "Create mappings (multi-select)")
            mapping_menu.add_row("11", "Manage mappings (edit/toggle/delete)")

            run_menu = Table(show_header=False, box=None, pad_edge=False)
            run_menu.add_column("Key", style="cyan", no_wrap=True)
            run_menu.add_column("Action", style="bold")
            run_menu.add_row("12", "Run forwarder")
            run_menu.add_row("13", "Show recent logs")

            tools_menu = Table(show_header=False, box=None, pad_edge=False)
            tools_menu.add_column("Key", style="cyan", no_wrap=True)
            tools_menu.add_column("Action", style="bold")
            tools_menu.add_row("14", "Setup wizard")
            tools_menu.add_row("15", "Send test message to webhook")
            tools_menu.add_row("16", "Test forward last Telegram message")
            tools_menu.add_row("0", "Exit")

            console.print(
                Panel(
                    Columns(
                        [
                            Panel(telegram_menu, title="Telegram", border_style="cyan"),
                            Panel(discord_menu, title="Discord", border_style="cyan"),
                            Panel(mapping_menu, title="Mappings", border_style="cyan"),
                            Panel(run_menu, title="Run", border_style="cyan"),
                            Panel(tools_menu, title="Tools", border_style="cyan"),
                        ],
                        equal=True,
                        expand=True,
                    ),
                    title=f"Status: channels={channels_count} webhooks={webhooks_count} mappings={mappings_count}",
                    border_style="cyan",
                )
            )

            try:
                choice = Prompt.ask("Select (0-16, q=exit)", default="").strip()
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
                    _print_webhooks(ctx)
                elif choice == "8":
                    await _add_webhook(ctx)
                elif choice == "9":
                    await _manage_webhooks(ctx)
                elif choice == "10":
                    _create_mappings(ctx)
                elif choice == "11":
                    _manage_mappings(ctx)
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
                else:
                    console.print(Panel("Unknown option.", border_style="yellow"))
            except CancelAction:
                console.print(Panel("Canceled.", border_style="yellow"))
            _pause()
    except KeyboardInterrupt:
        console.print(Panel("Exiting...", border_style="cyan"))
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
        webhook = event.get("webhook_name", "?")
        channel_id = event.get("channel_id")
        message_id = event.get("message_id")
        error = event.get("error")
        extra = f" ({error})" if error else ""
        logger.info(
            "%s tg=%s msg=%s -> %s%s",
            status,
            channel_id,
            message_id,
            webhook,
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
