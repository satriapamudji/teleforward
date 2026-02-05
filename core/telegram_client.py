import os
from pathlib import Path
from typing import Optional, Callable, Any
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, User, Message


class TelegramClientWrapper:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_string: Optional[str] = None,
        data_dir: Optional[Path] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.data_dir = (
            Path(data_dir)
            if data_dir is not None
            else (Path(__file__).parent.parent / "data")
        )
        self.client: Optional[TelegramClient] = None
        self._message_handler: Optional[Callable] = None
        self._is_running = False

    def _ensure_private_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass

    def _get_session(self):
        if self.session_string is not None:
            return StringSession(self.session_string or "")

        self._ensure_private_dir(self.data_dir)
        return str(self.data_dir / "teleforward")

    async def start(
        self,
        phone: Optional[str] = None,
        code_callback: Optional[Callable] = None,
        password_callback: Optional[Callable] = None,
    ):
        if self.client is None:
            session = self._get_session()
            self.client = TelegramClient(session, self.api_id, self.api_hash)

        await self.client.connect()

        if not await self.client.is_user_authorized():
            if phone is None:
                raise ValueError("Phone number required for first-time login")

            await self.client.send_code_request(phone)

            if code_callback:
                code = await code_callback()
            else:
                code = input("Enter the code: ")

            try:
                await self.client.sign_in(phone, code)
            except Exception:
                if password_callback:
                    password = await password_callback()
                else:
                    password = input("Enter 2FA password: ")
                await self.client.sign_in(password=password)

        return self

    async def stop(self):
        self._is_running = False
        if self.client:
            disconnect_coro = self.client.disconnect()
            if disconnect_coro is not None:
                await disconnect_coro

    def get_session_string(self) -> str:
        if self.client and isinstance(self.client.session, StringSession):
            return self.client.session.save()
        return ""

    def export_session_string(self) -> str:
        """Return a TELEGRAM_SESSION_STRING even if the client is using a file session.

        Telethon's StringSession is just the same auth key + DC info serialized into
        a string. This method builds a StringSession from the active session data.
        """
        if not self.client:
            return ""

        # If we're already using StringSession, just save it.
        if isinstance(self.client.session, StringSession):
            return self.client.session.save()

        sess = getattr(self.client, "session", None)
        if sess is None:
            return ""

        try:
            dc_id = getattr(sess, "dc_id", None)
            server_address = getattr(sess, "server_address", None)
            port = getattr(sess, "port", None)
            auth_key = getattr(sess, "auth_key", None)

            if auth_key is None:
                return ""

            s = StringSession()
            if dc_id is not None and server_address is not None and port is not None:
                s.set_dc(dc_id, server_address, port)
            s.auth_key = auth_key
            return s.save()
        except Exception:
            return ""

    async def get_me(self) -> Optional[User]:
        if self.client:
            result = await self.client.get_me()
            if isinstance(result, User):
                return result
        return None

    async def get_dialogs(self, limit: int = 100) -> list[dict]:
        if not self.client:
            return []

        dialogs: list[dict] = []
        async for d in self.iter_dialogs(limit=limit):
            dialogs.append(d)
        return dialogs

    async def iter_dialogs(self, limit: int = 0):
        """Yield dialogs (channels/groups) from Telegram, paginated by Telethon.

        `limit=0` means no explicit limit (Telethon may still cap internally).
        """
        if not self.client:
            return

        count = 0
        async for d in self.client.iter_dialogs():
            entity = getattr(d, "entity", None)
            if isinstance(entity, Channel):
                yield {
                    "id": -100 * 10**10 - entity.id if entity.id > 0 else entity.id,
                    "telegram_id": entity.id,
                    "name": entity.title,
                    "username": getattr(entity, "username", None),
                    "type": "channel" if entity.broadcast else "supergroup",
                }
                count += 1
            elif isinstance(entity, Chat):
                yield {
                    "id": -entity.id,
                    "telegram_id": entity.id,
                    "name": entity.title,
                    "username": None,
                    "type": "group",
                }
                count += 1

            if limit and count >= limit:
                return

    async def get_channel_info(self, channel_id: int) -> Optional[dict]:
        if not self.client:
            return None

        try:
            entity = await self.client.get_entity(channel_id)
            if isinstance(entity, (Channel, Chat)):
                return {
                    "id": channel_id,
                    "name": getattr(entity, "title", "Unknown"),
                    "username": getattr(entity, "username", None),
                    "type": "channel"
                    if isinstance(entity, Channel) and entity.broadcast
                    else "group",
                }
        except Exception:
            pass
        return None

    def set_message_handler(self, handler: Callable[[Message], Any]):
        self._message_handler = handler

    async def run_until_disconnected(self, channel_ids: list[int]):
        if not self.client or not self._message_handler:
            return

        from telethon import events

        @self.client.on(events.NewMessage(chats=channel_ids))
        async def handler(event):
            if self._message_handler:
                await self._message_handler(event.message)

        self._is_running = True
        disconnect_coro = self.client.run_until_disconnected()
        if disconnect_coro is not None:
            await disconnect_coro

    async def iter_messages(
        self,
        channel_id: int,
        limit: int = 100,
        offset_id: int = 0,
        reverse: bool = False,
    ):
        if not self.client:
            return

        async for message in self.client.iter_messages(
            channel_id, limit=limit, offset_id=offset_id, reverse=reverse
        ):
            yield message

    async def download_media(
        self, message: Message, path: Optional[str] = None
    ) -> Optional[str]:
        if not self.client or not message.media:
            return None

        download_dir = self.data_dir / "downloads"
        self._ensure_private_dir(download_dir)

        result = await self.client.download_media(message, file=str(download_dir) + "/")
        if isinstance(result, (str, bytes)):
            return str(result) if isinstance(result, bytes) else result
        return None


telegram_client: Optional[TelegramClientWrapper] = None


def get_telegram_client() -> Optional[TelegramClientWrapper]:
    return telegram_client


def set_telegram_client(client: TelegramClientWrapper):
    global telegram_client
    telegram_client = client
