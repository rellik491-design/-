import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models.entities import Contact, Persona
from companion.models.enums import ContactStatus
from companion.services.timezone_service import TimezoneService

logger = logging.getLogger(__name__)


class ReengagementService:
    @classmethod
    async def find_due_scheduled_reengagements(
        cls,
        session: AsyncSession,
        now: Optional[datetime] = None,
    ) -> List[Contact]:
        """
        Finds contacts whose contextual follow-up time has arrived:
        - In 1 hour (e.g. 68-85 min later)
        - Tomorrow conversation resumption
        - Good morning after night sleep
        - Overnight message reply upon waking up
        """
        current_time = now or datetime.now(timezone.utc)
        stmt = select(Contact).where(
            and_(
                Contact.status == ContactStatus.ACTIVE,
                Contact.bot_paused == False,
                Contact.ai_active == True,
                Contact.scheduled_reengage_at.is_not(None),
                Contact.scheduled_reengage_at <= current_time,
            )
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def find_reengagement_candidates(
        cls,
        session: AsyncSession,
        user_id: int,
        persona: Persona,
        min_hours: int = 24,
        max_hours: int = 72,
        now: Optional[datetime] = None,
    ) -> List[Contact]:
        """
        Finds contacts eligible for a friendly re-engagement ping:
        1. Contact status is ACTIVE (not paused, not blocked, not already a meetup lead).
        2. Bot is not paused for this contact.
        3. Strict anti-spam: is_initiated_by_user is True.
        4. Inactivity window is between min_hours and max_hours.
        5. It is NOT NIGHT for either persona or contact!
        """
        current_time = now or datetime.now(timezone.utc)
        min_cutoff = current_time - timedelta(hours=min_hours)
        max_cutoff = current_time - timedelta(hours=max_hours)

        # Check persona sleep time first
        if TimezoneService.is_sleeping_hours(
            persona.timezone_name,
            persona.sleep_start,
            persona.sleep_end,
            ref_utc_dt=current_time,
        ):
            return []

        # Query candidates
        stmt = select(Contact).where(
            and_(
                Contact.user_id == user_id,
                Contact.status == ContactStatus.ACTIVE,
                Contact.bot_paused == False,
                Contact.is_initiated_by_user == True,
                Contact.last_incoming_at.is_not(None),
                Contact.last_incoming_at <= min_cutoff,
                Contact.last_incoming_at >= max_cutoff,
            )
        )
        res = await session.execute(stmt)
        potential_contacts = res.scalars().all()

        eligible_contacts = []
        for contact in potential_contacts:
            # Check if contact is sleeping (if timezone known)
            if not TimezoneService.is_safe_to_ping(
                persona_tz=persona.timezone_name,
                persona_sleep_start=persona.sleep_start,
                persona_sleep_end=persona.sleep_end,
                contact_tz=contact.detected_timezone,
                ref_utc_dt=current_time,
            ):
                continue

            # Don't double ping if we already sent an outgoing ping recently
            if contact.last_outgoing_at and contact.last_outgoing_at > contact.last_incoming_at:
                if contact.last_outgoing_at > min_cutoff:
                    continue

            eligible_contacts.append(contact)

        return eligible_contacts

    @staticmethod
    def build_reengagement_prompt(persona: Persona, contact: Contact) -> str:
        """Constructs instruction for LLM to write a casual follow-up."""
        name = contact.first_name or "собеседник"
        ctx = getattr(contact, "reengage_context", None)
        overnight_msg = getattr(contact, "pending_overnight_message", None)

        if ctx == "morning_reply_to_overnight" and overnight_msg:
            return (
                f"Инструкция: Ты только что проснулась утром (около 9-10 утра по твоему времени). "
                f"Парень {name} написал тебе ночью, пока ты спала: «{overnight_msg}». "
                f"Напиши легкое и живое сообщение: скажи, что уже спала ночью, добродушно ответь на его слова/вопрос, "
                f"пожелай доброго утра или спроси как его утро. Коротко (1-2 предложения), в твоем естественном стиле."
            )
        elif ctx == "morning_greeting":
            return (
                f"Инструкция: Наступило утро (около 9-10 утра по твоему времени). "
                f"Вчера вечером вы с {name} пожелали друг другу спокойной ночи. "
                f"Напиши легкое утреннее сообщение (1 предложение): пожелай доброго утра, спроси как спалось "
                f"или продолжи вчерашнюю тему разговора. Без навязчивости, в своем привычном девичьем стиле."
            )
        elif ctx == "in_one_hour":
            return (
                f"Инструкция: Вы с {name} договаривались списаться примерно через час. Прошел час с небольшим. "
                f"Напиши короткое непринужденное сообщение: 'Привет, ну что, освободился?)' или 'Я тут, как твои дела?'."
            )
        elif ctx == "tomorrow_talk":
            return (
                f"Инструкция: Вы с {name} вчера договаривались пообщаться завтра. Наступил следующий день. "
                f"Напиши приветливое сообщение: 'Привет! Как проходит твой день? Как раз вспомнила про наш разговор...'."
            )

        return (
            f"Инструкция: Ты не общалась с {name} уже больше суток. "
            f"Напиши короткое, живое и легкое сообщение (1 предложение), "
            f"например 'Привет, как твои дела?', 'Куда пропал?' или спроси о чем-то простом. "
            f"Не навязывайся, без формализма, в своем привычном стиле."
        )
