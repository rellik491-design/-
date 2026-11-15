"""
UserProfileService: Persistent user profile, behavioral tracking, and controlled self-learning.
Enables the bot to remember user preferences, learn from explicit and implicit feedback,
and personalize conversational delivery without rewriting core system prompts or code.
Survives application restarts in SQLite.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.models.entities import Contact, ContactMemory

logger = logging.getLogger(__name__)


class UserProfileService:
    """
    Persistent User Profile & Controlled Self-Learning (Sections 27–41 & 56–57).
    Manages:
    - User communication preferences (preferred_name, communication_style, response_length, emoji_frequency).
    - Controlled learning from explicit feedback ('don't use so many emojis', 'I prefer short answers', 'call me Alex').
    - Memory confidence, updates, and contradiction corrections.
    """

    # Patterns for explicit emoji preferences (RU + EN + UK)
    EMOJI_LESS_PATTERNS = [
        re.compile(r"\b(?:не\s+ставь|меньше|хватит|убери|без|не\s+используй)\s+(?:смайл\w*|эмодзи|эмоджи|смайликов)\b", re.IGNORECASE),
        re.compile(r"\b(?:не\s+став|менше|годі|прибери|без|не\s+використовуй)\s+(?:смайл\w*|емодзі|смайликів)\b", re.IGNORECASE),
        re.compile(r"\b(?:too\s+many\s+emojis|less\s+emojis|no\s+emojis|stop\s+using\s+emojis|don't\s+use\s+so\s+many\s+emojis)\b", re.IGNORECASE),
    ]

    EMOJI_RESTORE_PATTERNS = [
        re.compile(r"\b(?:смайлики\s+(?:норм|ок|можно|люблю|нравятся)|верни\s+смайл\w*|больше\s+смайл\w*)\b", re.IGNORECASE),
        re.compile(r"\b(?:смайлики\s+(?:норм|ок|можна|люблю|подобаються)|поверни\s+смайл\w*|більше\s+смайл\w*)\b", re.IGNORECASE),
        re.compile(r"\b(?:emojis\s+are\s+fine|actually\s+emojis\s+are\s+fine|i\s+like\s+emojis)\b", re.IGNORECASE),
    ]

    # Patterns for response length preferences (RU + EN + UK)
    LENGTH_SHORT_PATTERNS = [
        re.compile(r"\b(?:пиши\s+короче|отвечай\s+кратко|слишком\s+длинно|коротко\s+отвечай|не\s+пиши\s+полотна|покороче)\b", re.IGNORECASE),
        re.compile(r"\b(?:пиши\s+коротше|відповідай\s+стисло|занадто\s+довго|коротко\s+відповідай|не\s+пиши\s+полотна|покоротше)\b", re.IGNORECASE),
        re.compile(r"\b(?:prefer\s+short\s+answers?|keep\s+it\s+short|too\s+long|shorter\s+answers?)\b", re.IGNORECASE),
    ]

    LENGTH_DETAILED_PATTERNS = [
        re.compile(r"\b(?:пиши\s+подробнее|развернуто\s+отвечай|хочу\s+подробный\s+ответ|побольше\s+деталей)\b", re.IGNORECASE),
        re.compile(r"\b(?:пиши\s+детальніше|розгорнуто\s+відповідай|хочу\s+детальнішу\s+відповідь|побільше\s+деталей)\b", re.IGNORECASE),
        re.compile(r"\b(?:more\s+details?|longer\s+answers?|detailed\s+answers?)\b", re.IGNORECASE),
    ]

    # Name preferences (RU + EN + UK)
    NAME_PATTERNS = [
        re.compile(r"\b(?:зови\s+меня|называй\s+меня|лучше\s+зови|просто\s+зови)\s+([А-ЯЁA-ZІЇЄҐ][а-яёa-zіїєґ]{1,20})\b", re.IGNORECASE),
        re.compile(r"\b(?:клич\s+мене|називай\s+мене|краще\s+клич|просто\s+клич)\s+([А-ЯЁA-ZІЇЄҐ][а-яёa-zіїєґ]{1,20})\b", re.IGNORECASE),
        re.compile(r"\b(?:call\s+me|just\s+call\s+me)\s+([A-Z][a-z]{1,20})\b", re.IGNORECASE),
    ]

    @classmethod
    async def process_learning_and_feedback(
        cls,
        session: AsyncSession,
        contact: Contact,
        incoming_text: str,
    ) -> Dict[str, Any]:
        """
        Analyzes incoming message for explicit feedback and behavioral preferences,
        updating the persistent profile in SQLite.
        """
        if not contact or not incoming_text:
            return {"updated": False}

        text = incoming_text.strip()
        low = text.lower()
        changes: List[str] = []

        # 1. Emoji frequency feedback
        for pat in cls.EMOJI_LESS_PATTERNS:
            if pat.search(low):
                contact.emoji_frequency = "none" if "без" in low or "no emojis" in low else "low"
                changes.append(f"emoji_frequency={contact.emoji_frequency}")
                break

        for pat in cls.EMOJI_RESTORE_PATTERNS:
            if pat.search(low):
                contact.emoji_frequency = "medium"
                changes.append("emoji_frequency=medium")
                break

        # 2. Response length feedback
        for pat in cls.LENGTH_SHORT_PATTERNS:
            if pat.search(low):
                contact.preferred_response_length = "short"
                changes.append("preferred_response_length=short")
                break

        for pat in cls.LENGTH_DETAILED_PATTERNS:
            if pat.search(low):
                contact.preferred_response_length = "detailed"
                changes.append("preferred_response_length=detailed")
                break

        # 3. Name preference
        for pat in cls.NAME_PATTERNS:
            m = pat.search(text)
            if m:
                pref_name = m.group(1).capitalize()
                if pref_name.lower() not in ("просто", "вообще", "сейчас", "ладно", "хорошо"):
                    contact.preferred_name = pref_name
                    changes.append(f"preferred_name={pref_name}")
                    # Also record in ContactMemory
                    stmt = select(ContactMemory).where(
                        ContactMemory.contact_id == contact.id,
                        ContactMemory.key == "preferred_name",
                    )
                    mem = (await session.execute(stmt)).scalars().first()
                    now = datetime.now(timezone.utc)
                    if mem:
                        mem.value = pref_name
                        mem.updated_at = now
                    else:
                        new_mem = ContactMemory(
                            contact_id=contact.id,
                            category="fact",
                            key="preferred_name",
                            value=pref_name,
                            confidence=0.98,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(new_mem)
                break

        # 4. Behavioral tracking: if interlocutor consistently sends very short messages
        words = text.split()
        if len(words) <= 2 and contact.total_messages_in >= 8:
            if getattr(contact, "preferred_response_length", None) == "medium":
                # User habitually interacts with minimal words -> adapt naturally towards concise answers
                contact.preferred_response_length = "short"
                changes.append("preferred_response_length=short (behavioral)")

        if changes:
            await session.commit()
            logger.info("[PROFILE %s] Updated persistent profile: %s", contact.tg_id, ", ".join(changes))
            return {"updated": True, "changes": changes}

        return {"updated": False}

    @classmethod
    def get_profile_dict(cls, contact: Optional[Contact]) -> Dict[str, Any]:
        """Returns structured dictionary representing persistent user/chat profile."""
        if not contact:
            return {
                "preferred_name": None,
                "communication_style": "casual",
                "preferred_response_length": "medium",
                "emoji_frequency": "medium",
                "likes_multi_message_responses": True,
                "timezone": "Europe/Moscow",
            }

        return {
            "preferred_name": getattr(contact, "preferred_name", None) or contact.first_name,
            "communication_style": getattr(contact, "communication_style", "casual") or "casual",
            "preferred_response_length": getattr(contact, "preferred_response_length", "medium") or "medium",
            "emoji_frequency": getattr(contact, "emoji_frequency", "medium") or "medium",
            "likes_multi_message_responses": getattr(contact, "likes_multi_message_responses", True),
            "timezone": getattr(contact, "detected_timezone", None) or "Europe/Moscow",
            "city": getattr(contact, "detected_city", None),
        }

    @classmethod
    def format_profile_prompt_block(cls, contact: Optional[Contact]) -> str:
        """
        Formats structured persistent user profile for prompt injection (Level 5 — Preferences & Profile).
        """
        p = cls.get_profile_dict(contact)
        lines = ["[ПЕРСОНАЛЬНЫЙ ПРОФИЛЬ СОБЕСЕДНИКА И ИЗУЧЕННЫЕ ПРЕДПОЧТЕНИЯ]:"]
        if p.get("preferred_name"):
            lines.append(f"• Предпочитаемое имя: {p['preferred_name']} (обращайся строго так, если уместно)")
        if p.get("preferred_response_length") == "short":
            lines.append("• Длина ответов: СТРОГО КОРОТКИЕ реплики (собеседник предпочитает лаконичные ответы)")
        elif p.get("preferred_response_length") == "detailed":
            lines.append("• Длина ответов: развернутые, содержательные ответы с деталями")

        if p.get("emoji_frequency") in ("none", "low"):
            lines.append("• Эмодзи: СТРОЖАЙШИЙ ЗАПРЕТ или минимум эмодзи (собеседник попросил не спамить смайликами)")
        elif p.get("emoji_frequency") == "high":
            lines.append("• Эмодзи: свободное теплое использование смайликов")

        if p.get("communication_style") == "direct":
            lines.append("• Стиль общения: прямой, без лишних витиеватостей")

        return "\n".join(lines)
