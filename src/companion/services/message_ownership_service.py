"""
MessageOwnershipService: Author Identity Classification, Hard Self-Response Block,
Idempotent Deduplication, and Native Reply Target Validation (Sections 10–17).

Ensures the bot NEVER responds to its own messages and processes every update uniquely.
"""

import collections
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import ProcessedMessage

logger = logging.getLogger(__name__)


@dataclass
class MessageOwnershipRecord:
    chat_id: int
    message_id: int
    author_id: Optional[int]
    sender_type: str  # "USER", "BOT", "OTHER"
    is_bot: bool
    is_self: bool
    timestamp: datetime

    def __eq__(self, other):
        if isinstance(other, str):
            return self.sender_type == other.upper()
        if isinstance(other, MessageOwnershipRecord):
            return (self.chat_id, self.message_id, self.author_id, self.sender_type) == (other.chat_id, other.message_id, other.author_id, other.sender_type)
        return False


class MessageOwnershipService:
    """
    Central Message Ownership & Self-Response Prevention Service.
    Maintains:
    - Known bot Telegram ID and bot username.
    - Fast in-memory cache of recently registered outgoing messages (ring buffer).
    - Persistent database storage in `processed_messages`.
    """

    _bot_id: Optional[int] = None
    _bot_username: Optional[str] = None
    _outgoing_keys: "collections.OrderedDict[Tuple[int, int], float]" = collections.OrderedDict()
    MAX_OUTGOING_CACHE_SIZE = 5000

    @classmethod
    def set_bot_identity(cls, bot_id: Optional[int] = None, bot_username: Optional[str] = None) -> None:
        if bot_id:
            cls._bot_id = int(bot_id)
        if bot_username:
            cls._bot_username = bot_username.lstrip("@").lower()

    @classmethod
    def get_bot_id(cls) -> Optional[int]:
        if cls._bot_id:
            return cls._bot_id
        # Fallback to configured bot token ID if available (Telegram bot tokens start with '<bot_id>:')
        token = getattr(settings, "ADMIN_BOT_TOKEN", None)
        if token and ":" in token:
            prefix = token.split(":", 1)[0]
            if prefix.isdigit():
                cls._bot_id = int(prefix)
                return cls._bot_id
        return None

    @classmethod
    def get_bot_username(cls) -> Optional[str]:
        if cls._bot_username:
            return cls._bot_username
        uname = getattr(settings, "BOT_USERNAME", None)
        if uname:
            cls._bot_username = uname.lstrip("@").lower()
            return cls._bot_username
        return None

    @classmethod
    def register_outgoing_message_memory(cls, chat_id: int, message_id: int) -> None:
        """
        Immediately registers outgoing message in in-memory ring buffer (Section 13).
        Called synchronously right after Telegram API returns successful sendMessage response.
        """
        key = (int(chat_id), int(message_id))
        cls._outgoing_keys[key] = time.time()
        if len(cls._outgoing_keys) > cls.MAX_OUTGOING_CACHE_SIZE:
            cls._outgoing_keys.popitem(last=False)

    @classmethod
    def is_known_outgoing(cls, chat_id: int, message_id: int) -> bool:
        """Checks if this (chat_id, message_id) was sent by us."""
        key = (int(chat_id), int(message_id))
        return key in cls._outgoing_keys

    @classmethod
    def is_bot_outgoing(cls, message_id: int, chat_id: Optional[int] = None) -> bool:
        """Checks if message_id was sent by the bot (either in specific chat or across any chat)."""
        if chat_id is not None:
            return (int(chat_id), int(message_id)) in cls._outgoing_keys
        return any(m_id == int(message_id) for _, m_id in cls._outgoing_keys.keys())

    @classmethod
    async def register_outgoing_message_db(
        cls,
        session: AsyncSession,
        chat_id: int,
        message_id: int,
        bot_id: Optional[int] = None,
    ) -> None:
        """
        Persists outgoing bot message into `processed_messages` table (Section 13).
        """
        cls.register_outgoing_message_memory(chat_id, message_id)
        eff_bot_id = bot_id or cls.get_bot_id()
        now = datetime.now(timezone.utc)

        try:
            # Check existence first
            stmt = select(ProcessedMessage).where(
                ProcessedMessage.chat_id == int(chat_id),
                ProcessedMessage.message_id == int(message_id),
            )
            existing = (await session.execute(stmt)).scalars().first()
            if not existing:
                proc = ProcessedMessage(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    sender_id=eff_bot_id,
                    sender_type="BOT",
                    outgoing=True,
                    status="OUTGOING",
                    received_at=now,
                    processed_at=now,
                )
                session.add(proc)
                await session.commit()
        except Exception as e:
            logger.debug("[MESSAGE_OWNERSHIP] register_outgoing_message_db skipped: %s", e)

    @classmethod
    def classify_message(
        cls,
        message_data_or_author_id: Any = None,
        business_owner_id: Optional[int] = None,
        bot_id: Optional[int] = None,
        author_id: Optional[int] = None,
        author_username: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
        chat_id: Optional[int] = None,
    ) -> MessageOwnershipRecord:
        """
        Classifies incoming message by author identity BEFORE entering reasoning pipeline (Section 10).
        Assigns sender_type = USER | BOT | OTHER.
        """
        eff_bot_id = bot_id or cls.get_bot_id()
        bot_uname = cls.get_bot_username()

        # Handle direct integer author_id
        if isinstance(message_data_or_author_id, int):
            eff_author_id = message_data_or_author_id
            eff_chat_id = chat_id or eff_author_id
            eff_msg_id = telegram_message_id or 0
            is_same = bool(eff_bot_id and eff_author_id == eff_bot_id)
            sender_type = "BOT" if is_same else "USER"
            return MessageOwnershipRecord(
                chat_id=eff_chat_id,
                message_id=eff_msg_id,
                author_id=eff_author_id,
                sender_type=sender_type,
                is_bot=(sender_type == "BOT"),
                is_self=(sender_type == "BOT"),
                timestamp=datetime.now(timezone.utc),
            )

        message_data = message_data_or_author_id if isinstance(message_data_or_author_id, dict) else {}
        c_id = chat_id or int(message_data.get("chat", {}).get("id") or 0)
        m_id = telegram_message_id or int(message_data.get("message_id") or 0)
        from_data = message_data.get("from") or {}
        a_id = author_id or from_data.get("id") or message_data.get("sender_id")
        if a_id is not None:
            a_id = int(a_id)
        uname = author_username or from_data.get("username")

        is_bot_flag = bool(from_data.get("is_bot", False))
        is_same_bot_id = bool(eff_bot_id and a_id == eff_bot_id)
        is_same_bot_uname = bool(bot_uname and uname and uname.lstrip("@").lower() == bot_uname)
        is_in_outgoing_cache = cls.is_known_outgoing(c_id, m_id)

        if is_same_bot_id or is_same_bot_uname or is_in_outgoing_cache or is_bot_flag:
            sender_type = "BOT"
            is_self = True
        elif business_owner_id and a_id == int(business_owner_id):
            sender_type = "OTHER"
            is_self = False
        else:
            sender_type = "USER"
            is_self = False

        date_val = message_data.get("date")
        if date_val and isinstance(date_val, (int, float)):
            ts = datetime.fromtimestamp(date_val, tz=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        return MessageOwnershipRecord(
            chat_id=c_id,
            message_id=m_id,
            author_id=a_id,
            sender_type=sender_type,
            is_bot=(sender_type == "BOT"),
            is_self=is_self,
            timestamp=ts,
        )

    @classmethod
    async def check_and_record_inbound(
        cls,
        session: AsyncSession,
        record: Optional[MessageOwnershipRecord] = None,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
        sender_id: Optional[int] = None,
        sender_type: str = "USER",
        **kwargs: Any,
    ) -> Tuple[bool, str]:
        """
        Idempotency & Self-Response check (Sections 11 & 12).
        Returns: (should_process: bool, reason: str)
        If should_process is False, message MUST NOT enter user-response queue.
        """
        if record is None:
            c_id = int(chat_id or 0)
            m_id = int(message_id or 0)
            s_id = int(sender_id or 0)
            is_self = (sender_type == "BOT")
            record = MessageOwnershipRecord(
                chat_id=c_id,
                message_id=m_id,
                author_id=s_id,
                sender_type=sender_type,
                is_bot=(sender_type == "BOT"),
                is_self=is_self,
                timestamp=datetime.now(timezone.utc),
            )
        # Hard Self-Response Block (Section 11)
        if record.is_self or record.sender_type == "BOT":
            logger.info(
                "[SELF_RESPONSE_BLOCK] Blocked self-message from bot in chat %s (msg_id=%s, author=%s)",
                record.chat_id,
                record.message_id,
                record.author_id,
            )
            # Record in DB as IGNORED_SELF if not present
            try:
                stmt = select(ProcessedMessage).where(
                    ProcessedMessage.chat_id == record.chat_id,
                    ProcessedMessage.message_id == record.message_id,
                )
                existing = (await session.execute(stmt)).scalars().first()
                if not existing:
                    session.add(
                        ProcessedMessage(
                            chat_id=record.chat_id,
                            message_id=record.message_id,
                            sender_id=record.author_id,
                            sender_type="BOT",
                            outgoing=True,
                            status="IGNORED_SELF",
                            received_at=record.timestamp,
                            processed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
            except Exception:
                pass
            return False, "self_bot_message_ignored"

        # Check idempotency table (Section 12)
        try:
            stmt = select(ProcessedMessage).where(
                ProcessedMessage.chat_id == record.chat_id,
                ProcessedMessage.message_id == record.message_id,
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing:
                logger.info(
                    "[IDEMPOTENCY] Duplicate message %s in chat %s skipped (status=%s)",
                    record.message_id,
                    record.chat_id,
                    existing.status,
                )
                return False, f"duplicate_message_{existing.status}"

            # Insert processing record
            new_proc = ProcessedMessage(
                chat_id=record.chat_id,
                message_id=record.message_id,
                sender_id=record.author_id,
                sender_type=record.sender_type,
                outgoing=False,
                status="PROCESSING",
                received_at=record.timestamp,
            )
            session.add(new_proc)
            await session.commit()
            return True, "new_user_message"
        except Exception as e:
            logger.warning("[MESSAGE_OWNERSHIP] DB idempotency check error: %s", e)
            return True, "fallback_allow"

    @classmethod
    def filter_user_messages_for_batch(
        cls,
        batch_items: List[Dict[str, Any]],
        bot_id: Optional[int] = None,
        business_owner_id: Optional[int] = None,
        bot_tg_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Batching safety filter (Section 14):
        Filters incoming batches so ONLY real USER messages enter the AI processing pipeline.
        Discards any bot messages or owner messages from being treated as pending user prompts.
        """
        filtered = []
        eff_bot_id = bot_id or bot_tg_id or cls.get_bot_id()

        for item in batch_items:
            m_data = item.get("message_data") or item
            c_rec = cls.classify_message(m_data, business_owner_id=business_owner_id, bot_id=eff_bot_id)
            if c_rec.sender_type == "USER" and not c_rec.is_self:
                filtered.append(item)
            else:
                logger.info(
                    "[BATCH_SAFETY] Filtered out non-user message (sender_type=%s, msg_id=%s)",
                    c_rec.sender_type,
                    c_rec.message_id,
                )
        return filtered

    @classmethod
    def validate_response_target(
        cls,
        target_chat_id: int,
        target_message_id: Optional[int],
        current_chat_id: int,
        recognized_user_message_ids: Optional[Set[int]] = None,
    ) -> Tuple[bool, Optional[int], str]:
        """
        Validates target message ID before using real Telegram native reply (Section 16 & 17).
        Prevents answering the wrong chat, wrong message, or bot's own message.
        """
        if target_message_id is None:
            return False, None, "no_target_id"

        if target_chat_id != current_chat_id:
            logger.error("[REPLY_TARGET] Target chat %s != current chat %s", target_chat_id, current_chat_id)
            return False, None, "chat_mismatch"

        # Check if the target is our own bot message
        if cls.is_known_outgoing(current_chat_id, target_message_id):
            logger.error("[REPLY_TARGET] Target %s is bot's own message! Rejected.", target_message_id)
            return False, None, "cannot_reply_to_bot_message"

        if recognized_user_message_ids is not None and target_message_id not in recognized_user_message_ids:
            logger.warning("[REPLY_TARGET] Target %s not in recognized user message IDs", target_message_id)
            return False, None, "target_not_recognized"

        return True, target_message_id, "valid_user_target"
