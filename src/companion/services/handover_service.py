from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models.entities import Contact, DailyStat
from companion.models.enums import ContactStatus
from companion.services.intent_service import MeetupIntent


class HandoverService:
    @classmethod
    async def process_meetup_proposal(
        cls,
        session: AsyncSession,
        contact: Contact,
        intent: MeetupIntent,
        raw_message: str,
        auto_pause: bool = True,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Processes a detected date/meetup invitation:
        1. Updates contact status to LEAD_MEETING.
        2. Pauses bot if auto_pause is True.
        3. Updates daily metrics.
        4. Generates an alert payload for the owner.
        """
        current_time = now or datetime.now(timezone.utc)
        contact.status = ContactStatus.LEAD_MEETING
        contact.lead_detected_at = current_time
        contact.lead_summary = f"[{intent.category}] {raw_message.strip()}"

        if auto_pause:
            contact.bot_paused = True
            contact.ai_active = False
            contact.paused_reason = f"meetup_proposal ({intent.trigger})"

        # Update or create daily stat for today
        today_date = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(DailyStat).where(
            DailyStat.user_id == contact.user_id,
            DailyStat.stat_date == today_date,
        )
        res = await session.execute(stmt)
        stat = res.scalars().first()
        if not stat:
            stat = DailyStat(
                user_id=contact.user_id,
                stat_date=today_date,
                leads_count=1,
            )
            session.add(stat)
        else:
            stat.leads_count += 1

        await session.commit()
        await session.refresh(contact)

        # Build notification payload for admin
        name = contact.first_name or contact.username or f"User_{contact.tg_id}"
        tg_link = (
            f"https://t.me/{contact.username}"
            if contact.username
            else f"tg://user?id={contact.tg_id}"
        )

        return {
            "type": "lead_meetup_alert",
            "contact_id": contact.id,
            "tg_id": contact.tg_id,
            "contact_name": name,
            "tg_link": tg_link,
            "trigger": intent.trigger,
            "message": raw_message,
            "bot_paused": contact.bot_paused,
            "paused_reason": contact.paused_reason,
        }

    @classmethod
    async def set_contact_pause_state(
        cls,
        session: AsyncSession,
        contact_id: int,
        paused: bool,
        reason: Optional[str] = None,
    ) -> Optional[Contact]:
        """Manually pauses or resumes bot operation in a specific chat."""
        stmt = select(Contact).where(Contact.id == contact_id)
        res = await session.execute(stmt)
        contact = res.scalars().first()
        if not contact:
            return None

        contact.bot_paused = paused
        contact.ai_active = not paused
        contact.paused_reason = reason if paused else None
        if not paused and contact.status == ContactStatus.PAUSED_MANUAL:
            contact.status = ContactStatus.ACTIVE
        elif paused:
            contact.status = ContactStatus.PAUSED_MANUAL

        await session.commit()
        await session.refresh(contact)
        return contact

    @classmethod
    async def handle_manual_owner_message(
        cls, session: AsyncSession, contact: Contact, text: str
    ) -> None:
        """
        When the real human sends a message from their phone/desktop app in this chat,
        auto-pause the bot to prevent awkward double responses.
        """
        contact.bot_paused = True
        contact.ai_active = False
        contact.paused_reason = "owner_manual_takeover"
        contact.last_outgoing_at = datetime.now(timezone.utc)
        contact.total_messages_out += 1
        await session.commit()
