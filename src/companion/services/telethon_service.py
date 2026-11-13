import logging
from typing import List, Optional
from companion.config import settings

logger = logging.getLogger(__name__)

class TelethonService:
    """
    Optional MTProto companion service using Telethon.
    
    Telegram Bot API (readBusinessMessage) only marks the message history as seen (producing ✓✓),
    but CANNOT clear the 'media_unread' flag (the blue circle/dot on voice messages).
    
    Clearing the blue circle requires the MTProto RPC call `messages.readMessageContents(id=[...])`,
    which is an account-level client operation. If TELEGRAM_API_ID, TELEGRAM_API_HASH, and
    TELEGRAM_SESSION_STRING (or a session file) are provided, this service connects to Telegram MTProto
    and marks incoming voice/video notes as listened, instantly clearing the blue dot.
    """
    _client = None
    _is_initialized = False

    @classmethod
    async def get_client(cls):
        """Retrieves or lazily initializes the Telethon client instance if configured."""
        if cls._is_initialized:
            return cls._client

        api_id = getattr(settings, "TELEGRAM_API_ID", 0)
        api_hash = getattr(settings, "TELEGRAM_API_HASH", "")
        session_str = getattr(settings, "TELEGRAM_SESSION_STRING", None)

        if not api_id or not api_hash:
            logger.debug("TelethonService: TELEGRAM_API_ID or TELEGRAM_API_HASH not configured.")
            cls._is_initialized = True
            cls._client = None
            return None

        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            session = StringSession(session_str) if session_str else "companion_userbot"
            cls._client = TelegramClient(session, api_id, api_hash)
            await cls._client.connect()
            if await cls._client.is_user_authorized():
                me = await cls._client.get_me()
                logger.info("TelethonService: MTProto client authorized as %s (@%s)", me.first_name, me.username)
            else:
                logger.warning("TelethonService: MTProto client connected but not authorized.")
        except Exception as e:
            logger.warning("TelethonService: Failed to initialize MTProto client: %s", e)
            cls._client = None

        cls._is_initialized = True
        return cls._client

    @classmethod
    async def mark_voice_contents_listened(cls, chat_id: int, message_ids: List[int]) -> bool:
        """
        Marks specific voice notes as listened to via MTProto `ReadMessageContentsRequest`.
        This is the ONLY protocol-level action in Telegram that clears the blue circle (•).
        """
        if not message_ids:
            return False

        try:
            client = await cls.get_client()
            if not client or not await client.is_user_authorized():
                return False

            from telethon.tl.functions.messages import ReadMessageContentsRequest

            # Execute MTProto readMessageContents
            valid_ids = [int(mid) for mid in message_ids if mid]
            if valid_ids:
                await client(ReadMessageContentsRequest(id=valid_ids))
                logger.info("TelethonService: Successfully marked voice contents listened for msg IDs %s (blue dot cleared)", valid_ids)
                return True
        except Exception as e:
            logger.warning("TelethonService: Error in mark_voice_contents_listened: %s", e)
        return False
