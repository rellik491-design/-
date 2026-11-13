from collections import OrderedDict
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import Contact, ContactMessage, ContactMemory, ConversationSummary
from companion.services.moderation_service import ModerationService
from companion.services.timezone_service import TimezoneService

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Multi-tier Memory Architecture (Phase 10):
    1. Short-Term Memory: Recent conversation turns (DB-backed + bounded LRU RAM cache).
    2. Conversation Memory: Rolling summary, active topics, unresolved questions, and commitments (`ConversationSummary`).
    3. Long-Term Memory: Persistent structured facts, preferences, habits, agreements, and goals (`ContactMemory`).
       Strictly avoids memory spam by only extracting high-confidence durable facts.
    """

    MAX_CACHED_CONTACTS = 500
    MAX_SHORT_TERM_TURNS = 24  # 12 pairs in cache

    _short_term_cache: "OrderedDict[int, List[Dict[str, str]]]" = OrderedDict()

    # High-precision patterns for extracting genuine long-term facts without memory spam
    FACT_PATTERNS: List[Tuple[str, str, re.Pattern]] = [
        (
            "fact",
            "occupation",
            re.compile(
                r"(?:я\s+работаю|работаю\s+в|я\s+по\s+профессии|занимаюсь\s+бизнесом|я\s+программист|я\s+врач|я\s+юрист|я\s+предприниматель|я\s+инженер|я\s+дизайнер|я\s+строитель|я\s+менеджер|в\s+сервисе\s+[^\.,!\?]{2,35}|работаю\s+[^\.,!\?]{3,40})",
                re.IGNORECASE,
            ),
        ),
        (
            "fact",
            "car_or_transport",
            re.compile(
                r"(?:езжу\s+на|у\s+меня\s+(?:машина|тачка|авто|мотоцикл|бмв|мерседес|ауди|тойота|порше)|крутил\s+тачки|люблю\s+автомобили)",
                re.IGNORECASE,
            ),
        ),
        (
            "habit",
            "sport_or_hobby",
            re.compile(
                r"(?:хожу\s+в\s+зал|занимаюсь\s+спортом|тренируюсь|играю\s+в\s+[^\.,!\?]{3,30}|увлекаюсь\s+[^\.,!\?]{3,35}|обожаю\s+[^\.,!\?]{3,35}|люблю\s+(?:горы|путешествия|квесты|кино|музыку|готовка|готовить|футбол|бокс|теннис|бег))",
                re.IGNORECASE,
            ),
        ),
        (
            "agreement",
            "meetup_plan",
            re.compile(
                r"(?:давай\s+в\s+(?:субботу|воскресенье|пятницу|выходные)|спишемся\s+(?:завтра|вечером|позже|через\s+час)|погнали\s+на\s+квест|я\s+за\s+квест|встретимся\s+[^\.,!\?]{3,35})",
                re.IGNORECASE,
            ),
        ),
        (
            "preference",
            "food_or_leisure",
            re.compile(
                r"(?:не\s+люблю\s+[^\.,!\?]{3,35}|предпочитаю\s+[^\.,!\?]{3,35}|мой\s+любимый\s+[^\.,!\?]{3,35})",
                re.IGNORECASE,
            ),
        ),
        (
            "relationship",
            "relationship_context",
            re.compile(
                r"(?:недавно\s+расстался|ищу\s+серьезные\s+отношения|живу\s+один|разведен|холост|не\s+женат)",
                re.IGNORECASE,
            ),
        ),
    ]

    @classmethod
    def get_cached_short_term(cls, contact_id: Optional[int]) -> List[Dict[str, str]]:
        if not contact_id:
            return []
        if contact_id in cls._short_term_cache:
            cls._short_term_cache.move_to_end(contact_id)
            return list(cls._short_term_cache[contact_id])
        return []

    @classmethod
    def add_short_term_turn(
        cls,
        contact_id: Optional[int],
        user_msg: str,
        bot_reply: str,
    ) -> None:
        if not contact_id:
            return
        history = cls._short_term_cache.get(contact_id, [])
        if user_msg:
            history.append({"role": "user", "text": user_msg})
        if bot_reply:
            history.append({"role": "model", "text": bot_reply})
        if len(history) > cls.MAX_SHORT_TERM_TURNS:
            history = history[-cls.MAX_SHORT_TERM_TURNS:]
        cls._short_term_cache[contact_id] = history
        cls._short_term_cache.move_to_end(contact_id)
        while len(cls._short_term_cache) > cls.MAX_CACHED_CONTACTS:
            cls._short_term_cache.popitem(last=False)

    @classmethod
    async def get_recent_messages(
        cls,
        session: Optional[AsyncSession],
        contact_id: Optional[int],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Loads the recent conversation window (Short-Term Memory) from SQLite if a session is provided,
        falling back to the bounded LRU cache if called without a DB session.
        Never loads the entire conversation history!
        """
        window_size = limit or getattr(settings, "AI_RECENT_WINDOW_SIZE", 10)
        if session is not None and contact_id:
            stmt = (
                select(ContactMessage)
                .where(
                    ContactMessage.contact_id == contact_id,
                    ContactMessage.is_deleted == False,
                )
                .order_by(desc(ContactMessage.id))
                .limit(window_size)
            )
            rows = (await session.execute(stmt)).scalars().all()
            if rows:
                ordered = list(reversed(rows))
                result = [
                    {
                        "id": r.id,
                        "role": "user" if r.sender_role == "user" else "model",
                        "sender_role": r.sender_role,
                        "text": r.text,
                        "is_voice": r.is_voice,
                        "created_at": r.created_at,
                    }
                    for r in ordered
                ]
                # Sync cache with DB window
                cls._short_term_cache[contact_id] = [
                    {"role": item["role"], "text": item["text"]} for item in result
                ]
                cls._short_term_cache.move_to_end(contact_id)
                return result

        cached = cls.get_cached_short_term(contact_id)
        return cached[-window_size:]

    @classmethod
    async def extract_and_store_memories(
        cls,
        session: AsyncSession,
        contact: Contact,
        incoming_text: str,
        source_message_id: Optional[int] = None,
    ) -> List[ContactMemory]:
        """
        Extracts genuine long-term facts, preferences, habits, and agreements from incoming text
        and upserts them into `contact_memories`. Ignores random chatter to prevent memory spam.
        """
        if not incoming_text or not contact or not contact.id:
            return []

        cleaned = incoming_text.strip()
        if len(cleaned) < 4:
            return []

        extracted_items: List[Tuple[str, str, str, float]] = []

        # 1. City & origin detection
        city, _ = TimezoneService.detect_city_and_timezone(cleaned)
        if city:
            extracted_items.append(("fact", "city", city, 0.95))
        if contact.origin_city:
            extracted_items.append(("fact", "origin_city", contact.origin_city, 0.95))
        if contact.client_free_time:
            extracted_items.append(("fact", "free_time_schedule", contact.client_free_time, 0.90))
        if contact.no_brackets:
            extracted_items.append(("preference", "formatting_brackets", "Не использовать скобочки ')' в сообщениях", 1.0))

        # 2. Explicit self-introduction of name
        name_match = re.search(r"(?:меня\s+зовут|я\s+кстати)\s+([А-ЯЁA-Z][а-яёa-z]{2,18})", cleaned)
        if name_match:
            spoken_name = name_match.group(1).capitalize()
            if spoken_name.lower() not in ("просто", "вообще", "сейчас", "только", "тоже", "очень"):
                extracted_items.append(("fact", "preferred_name", spoken_name, 0.95))

        # 3. Pattern-based extraction for occupation, car, hobbies, agreements, preferences
        for category, key, pattern in cls.FACT_PATTERNS:
            m = pattern.search(cleaned)
            if m:
                val = m.group(0).strip()
                if 4 <= len(val) <= 120:
                    extracted_items.append((category, key, val, 0.85))

        if not extracted_items:
            return []

        # Upsert into ContactMemory
        stmt_existing = select(ContactMemory).where(ContactMemory.contact_id == contact.id)
        existing_memories = {
            (mem.category, mem.key): mem
            for mem in (await session.execute(stmt_existing)).scalars().all()
        }

        touched: List[ContactMemory] = []
        now = datetime.now(timezone.utc)
        for category, key, val, conf in extracted_items:
            existing = existing_memories.get((category, key))
            if existing:
                if existing.value != val:
                    existing.value = val
                    existing.confidence = max(existing.confidence, conf)
                    existing.source_message_id = source_message_id or existing.source_message_id
                    existing.updated_at = now
                touched.append(existing)
            else:
                new_mem = ContactMemory(
                    contact_id=contact.id,
                    category=category,
                    key=key,
                    value=val,
                    confidence=conf,
                    source_message_id=source_message_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_mem)
                existing_memories[(category, key)] = new_mem
                touched.append(new_mem)

        return touched

    @classmethod
    async def get_long_term_memories(
        cls,
        session: Optional[AsyncSession],
        contact_id: Optional[int],
    ) -> List[ContactMemory]:
        if session is None or not contact_id:
            return []
        stmt = (
            select(ContactMemory)
            .where(ContactMemory.contact_id == contact_id)
            .order_by(ContactMemory.category, ContactMemory.id)
        )
        return list((await session.execute(stmt)).scalars().all())

    @classmethod
    async def get_memories(
        cls,
        session: Optional[AsyncSession],
        contact_id: Optional[int],
    ) -> List[ContactMemory]:
        return await cls.get_long_term_memories(session, contact_id)

    @classmethod
    async def get_or_create_summary(
        cls,
        session: AsyncSession,
        contact_id: int,
    ) -> ConversationSummary:
        stmt_sum = select(ConversationSummary).where(ConversationSummary.contact_id == contact_id)
        summary_rec = (await session.execute(stmt_sum)).scalars().first()
        if not summary_rec:
            summary_rec = ConversationSummary(
                contact_id=contact_id,
                summary_text="",
                active_topics="",
                unresolved_questions="",
                commitments="",
                messages_covered_count=0,
            )
            session.add(summary_rec)
            await session.commit()
            await session.refresh(summary_rec)
        return summary_rec

    @classmethod
    async def update_conversation_summary(
        cls,
        session: AsyncSession,
        contact: Contact,
        recent_messages: List[Dict[str, Any]],
    ) -> Optional[ConversationSummary]:
        """
        Maintains a rolling ConversationSummary in `conversation_summaries` and updates
        `contact.dialogue_summary` and `contact.dialogue_topics` using cumulative conversation turns
        rather than overwriting with a single message.
        """
        if not contact or not contact.id:
            return None

        # Normalize messages for ModerationService.generate_topics_and_summary
        formatted_msgs = [
            {
                "sender_role": "user" if m.get("role") == "user" or m.get("sender_role") == "user" else "bot",
                "text": m.get("text", ""),
            }
            for m in recent_messages
            if m.get("text")
        ]
        if not formatted_msgs:
            return None

        topics_str, summary_str = ModerationService.generate_topics_and_summary(formatted_msgs)

        # Merge topics with existing topics so earlier topics are not lost
        existing_topics = [
            t.strip() for t in (contact.dialogue_topics or "").split(",") if t.strip() and t.strip() != "общее знакомство"
        ]
        new_topics = [t.strip() for t in topics_str.split(",") if t.strip()]
        merged_topics = list(dict.fromkeys(existing_topics + new_topics))[:6]
        final_topics_str = ", ".join(merged_topics) if merged_topics else topics_str

        # Detect unresolved questions from the user's last messages
        unresolved: List[str] = []
        for m in reversed(formatted_msgs):
            if m["sender_role"] == "user" and "?" in m["text"]:
                q_text = m["text"].strip()
                if len(q_text) <= 160:
                    unresolved.append(q_text)
                break

        # Detect commitments / agreements
        commitments: List[str] = []
        if contact.funnel_stage == "quest_agreed":
            commitments.append("Договорились пойти вместе на квест")
        elif contact.funnel_stage == "date_proposed":
            commitments.append("Обсуждается идея встречи / свидания")
        if contact.reengage_context:
            commitments.append(f"Пауза/договоренность: {contact.reengage_context}")

        stmt_sum = select(ConversationSummary).where(ConversationSummary.contact_id == contact.id)
        summary_rec = (await session.execute(stmt_sum)).scalars().first()
        now = datetime.now(timezone.utc)
        last_msg_id = recent_messages[-1].get("id") if recent_messages else None

        # Combine previous summary if conversation is long
        combined_summary = summary_str
        if summary_rec and summary_rec.summary_text and summary_rec.summary_text not in summary_str:
            prev_clean = summary_rec.summary_text.split(" | ")[0]
            if prev_clean and prev_clean != summary_str:
                combined_summary = f"{prev_clean} | Сейчас: {summary_str}"[:450]

        contact.dialogue_topics = final_topics_str
        contact.dialogue_summary = combined_summary

        if not summary_rec:
            summary_rec = ConversationSummary(
                contact_id=contact.id,
                summary_text=combined_summary,
                active_topics=final_topics_str,
                unresolved_questions="; ".join(unresolved),
                commitments="; ".join(commitments),
                messages_covered_count=(contact.total_messages_in or 0) + (contact.total_messages_out or 0),
                last_message_id=last_msg_id,
                updated_at=now,
            )
            session.add(summary_rec)
        else:
            summary_rec.summary_text = combined_summary
            summary_rec.active_topics = final_topics_str
            summary_rec.unresolved_questions = "; ".join(unresolved)
            if commitments:
                summary_rec.commitments = "; ".join(commitments)
            summary_rec.messages_covered_count = (contact.total_messages_in or 0) + (contact.total_messages_out or 0)
            summary_rec.last_message_id = last_msg_id or summary_rec.last_message_id
            summary_rec.updated_at = now

        return summary_rec
