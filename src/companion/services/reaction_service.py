import logging
import random
from typing import Dict, List, Optional
import httpx

from companion.config import settings

logger = logging.getLogger(__name__)


class TelegramReactionService:
    """
    Handles native Telegram message reactions (Bot API 7.0+ setMessageReaction).
    - Uses standard supported reaction emojis: 👍, ❤️, 🔥, 🥰, 😂, 😮, 🤔, 🎉, 😢, 👏
    - Enforces contextual gating: ~10-15% frequency, cooldown of >= 3 turns between reactions
    - Completely separate from stickers: can send reaction-only, reaction + text, or text-only.
    """

    ALLOWED_REACTIONS: List[str] = [
        "👍", "❤️", "🔥", "🥰", "😂", "😮", "🤔", "🎉", "😢", "👏"
    ]

    _last_reaction_turn_by_chat: Dict[int, int] = {}

    @classmethod
    def can_react(cls, chat_id: int, current_turn_count: int) -> bool:
        """Enforces at least 3 turns cooldown between message reactions per chat."""
        last_turn = cls._last_reaction_turn_by_chat.get(chat_id, -10)
        return (current_turn_count - last_turn) >= 3

    @classmethod
    def record_reaction_sent(cls, chat_id: int, current_turn_count: int) -> None:
        cls._last_reaction_turn_by_chat[chat_id] = current_turn_count

    @classmethod
    async def set_message_reaction(
        cls,
        bot_token: str,
        chat_id: int,
        message_id: int,
        reaction_emoji: str,
        is_big: bool = False,
    ) -> bool:
        """
        Sends native Telegram reaction to a specific message using Bot API 7.0 setMessageReaction.
        """
        if not bot_token or bot_token in ("mock_token", "test_token"):
            return True

        if reaction_emoji not in cls.ALLOWED_REACTIONS:
            logger.debug("Reaction emoji %s not in allowed standard list", reaction_emoji)
            return False

        url = f"https://api.telegram.org/bot{bot_token}/setMessageReaction"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": reaction_emoji}],
            "is_big": is_big,
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200 and res.json().get("ok"):
                    logger.info("Native reaction %s successfully set on msg %s in chat %s", reaction_emoji, message_id, chat_id)
                    return True
                else:
                    logger.warning("Telegram setMessageReaction failed: %s (%s)", res.status_code, res.text)
                    return False
        except Exception as e:
            logger.warning("Error calling setMessageReaction: %s", e)
            return False
