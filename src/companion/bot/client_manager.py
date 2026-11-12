import asyncio
import logging
from typing import Dict, Optional
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from companion.config import settings
from companion.models import async_session_maker
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier
from companion.services.handover_service import HandoverService

logger = logging.getLogger(__name__)


class ClientManager:
    """
    Manages the concurrent lifecycle of multiple userbot sessions.
    Each connected Telegram account runs independently in the background.
    """
    _clients: Dict[int, TelegramClient] = {}

    @classmethod
    async def start_account(
        cls,
        account_id: int,
        session_string: str,
        user_id: int,
        owner_tg_id: Optional[int] = None,
    ) -> bool:
        """
        Initializes and launches a Telegram client for the specified account session.
        Binds message event listeners with anti-spam, time-awareness, and meetup handover.
        """
        if account_id in cls._clients:
            logger.info("Account %d is already running", account_id)
            return True

        api_id = settings.TELEGRAM_API_ID or 12345
        api_hash = settings.TELEGRAM_API_HASH or "0123456789abcdef0123456789abcdef"

        try:
            client = TelegramClient(StringSession(session_string), api_id, api_hash)

            # Handler for INCOMING private messages
            @client.on(events.NewMessage(incoming=True))
            async def handle_incoming(event):
                if not event.is_private:
                    return  # We only process private direct chats, not public groups

                sender = await event.get_sender()
                if not sender or getattr(sender, "bot", False):
                    return  # Ignore other bots or service channels

                text = event.text or ""
                if not text.strip():
                    return

                async with async_session_maker() as session:
                    res = await UserbotHandler.process_incoming_message(
                        session=session,
                        user_id=user_id,
                        sender_tg_id=sender.id,
                        sender_username=getattr(sender, "username", None),
                        sender_first_name=getattr(sender, "first_name", None),
                        text=text,
                    )

                    action = res.get("action")
                    if action == "reply":
                        # 1. Unread pause (чтение с паузой)
                        await asyncio.sleep(2.5)

                        # 2. Две галочки (✓✓)
                        try:
                            await client.send_read_acknowledge(event.chat_id, event.message)
                        except Exception:
                            pass

                        # 3. Пауза на осмысление после прочтения
                        await asyncio.sleep(1.5)

                        reply_text = res.get("reply_text")
                        if reply_text:
                            bubbles = [b.strip() for b in reply_text.split("||") if b.strip()]
                            for idx, bubble in enumerate(bubbles):
                                if idx > 0:
                                    await asyncio.sleep(2.0)
                                typing_duration = max(3.0, min(2.0 + len(bubble) * 0.08, 10.0))
                                async with client.action(event.chat_id, "typing"):
                                    await asyncio.sleep(typing_duration)
                                await event.reply(bubble)

                        # If photo was requested and retrieved
                        photo_asset = res.get("photo_asset")
                        if photo_asset:
                            await event.reply(file=photo_asset.file_path)

                    elif action == "handover_to_owner":
                        # Send alert to owner in the Admin Bot
                        alert_data = res.get("alert")
                        if alert_data:
                            alert_msg = AdminNotifier.format_meetup_alert(alert_data)
                            target_admin_id = owner_tg_id or settings.ADMIN_TELEGRAM_ID
                            await AdminNotifier.send_telegram_alert(alert_msg, target_admin_id)

            # Handler for OUTGOING messages (human takeover detection)
            @client.on(events.NewMessage(outgoing=True))
            async def handle_outgoing(event):
                if not event.is_private:
                    return

                # If the owner manually typed a message in this chat, pause the bot
                async with async_session_maker() as session:
                    contact = await UserbotHandler.get_or_create_contact(
                        session=session,
                        user_id=user_id,
                        tg_id=event.chat_id,
                        is_incoming=False,
                    )
                    await HandoverService.handle_manual_owner_message(
                        session=session, contact=contact, text=event.text or ""
                    )

            await client.connect()
            cls._clients[account_id] = client
            logger.info("Successfully started userbot for account %d", account_id)
            return True
        except Exception as e:
            logger.error("Failed to start account %d: %s", account_id, e)
            return False

    @classmethod
    async def stop_account(cls, account_id: int) -> None:
        """Stops and disconnects an account."""
        client = cls._clients.pop(account_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
            logger.info("Stopped userbot for account %d", account_id)

    @classmethod
    async def is_account_running(cls, account_id: int) -> bool:
        """Checks if account client is currently active."""
        return account_id in cls._clients
