import re
import random
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import Contact, Persona
from companion.models.enums import ContactStatus
from companion.services.subscription_service import SubscriptionService
from companion.services.timezone_service import TimezoneService
from companion.services.intent_service import IntentService
from companion.services.handover_service import HandoverService
from companion.services.media_service import MediaService
from companion.services.ai_service import AIService
from companion.services.voice_service import VoiceService

logger = logging.getLogger(__name__)


class UserbotHandler:
    @classmethod
    async def get_or_create_contact(
        cls,
        session: AsyncSession,
        user_id: int,
        tg_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        is_incoming: bool = True,
    ) -> Contact:
        """Finds existing contact or creates a new one for incoming communication."""
        stmt = select(Contact).where(Contact.user_id == user_id, Contact.tg_id == tg_id)
        res = await session.execute(stmt)
        contact = res.scalars().first()

        if not contact:
            contact = Contact(
                user_id=user_id,
                tg_id=tg_id,
                username=username,
                first_name=first_name,
                is_initiated_by_user=is_incoming,  # True only if the user wrote to us first
                status=ContactStatus.ACTIVE,
                bot_paused=False,
            )
            session.add(contact)
            await session.commit()
            await session.refresh(contact)
        else:
            # Update info if changed
            if username and contact.username != username:
                contact.username = username
            if first_name and contact.first_name != first_name:
                contact.first_name = first_name

        return contact

    @classmethod
    async def process_incoming_message(
        cls,
        session: AsyncSession,
        user_id: int,
        sender_tg_id: int,
        sender_username: Optional[str],
        sender_first_name: Optional[str],
        text: str,
        now: Optional[datetime] = None,
        auto_handover_on_date: bool = True,
        account_id: Optional[int] = None,
        is_voice: bool = False,
        voice_duration: int = 0,
        voice_file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Main pipeline for processing incoming messages:
        1. Checks active subscription.
        2. Retrieves contact and applies anti-spam rules.
        3. Detects city and timezone.
        4. Detects meetup proposal and triggers handover if found.
        5. Generates appropriate response (text, photo, voice).
        """
        current_time = now or datetime.now(timezone.utc)

        # 1. Verify subscription
        sub = await SubscriptionService.get_active_subscription(session, user_id, current_time)
        if not sub:
            return {"action": "ignored", "reason": "subscription_expired"}

        # 2. Get Persona (prioritizing account-specific persona if account_id is provided)
        persona = None
        if account_id:
            from companion.models.entities import AccountSession
            from companion.services.account_service import AccountService
            stmt_acc = select(AccountSession).where(AccountSession.id == account_id)
            acc = (await session.execute(stmt_acc)).scalars().first()
            if acc:
                persona = await AccountService.ensure_account_persona(session, acc)

        if not persona:
            stmt_persona = select(Persona).where(Persona.user_id == user_id, Persona.is_active == True)
            res_persona = await session.execute(stmt_persona)
            persona = res_persona.scalars().first()

        if not persona:
            persona = Persona(user_id=user_id)
            session.add(persona)
            await session.commit()
            await session.refresh(persona)

        # 3. Get or create contact (strict: mark is_initiated_by_user=True)
        contact = await cls.get_or_create_contact(
            session, user_id, sender_tg_id, sender_username, sender_first_name, is_incoming=True
        )

        # Ensure admin testing is never locked out by stale pause
        if sender_tg_id == settings.ADMIN_TELEGRAM_ID and contact.bot_paused:
            contact.bot_paused = False
            contact.ai_active = True
            contact.paused_reason = None
            await session.commit()

        # Check if chat is paused by owner or previous meetup alert, or AI is turned off
        if contact.bot_paused or not getattr(contact, "ai_active", True):
            # Auto-resume owner_manual_takeover if owner hasn't sent a message in > 15 minutes
            if contact.paused_reason == "owner_manual_takeover":
                last_out = contact.last_outgoing_at
                is_stale_owner_pause = False
                if last_out:
                    curr_tz = current_time if current_time.tzinfo is not None else current_time.replace(tzinfo=timezone.utc)
                    out_tz = last_out if last_out.tzinfo is not None else last_out.replace(tzinfo=timezone.utc)
                    diff_sec = abs((curr_tz - out_tz).total_seconds())
                    if diff_sec > 900:  # > 15 minutes
                        is_stale_owner_pause = True
                else:
                    is_stale_owner_pause = True

                if is_stale_owner_pause:
                    contact.bot_paused = False
                    contact.ai_active = True
                    contact.paused_reason = None
                    await session.commit()
                    logger.info("Auto-resumed bot in chat %s after owner manual takeover idle timeout (>15m)", sender_tg_id)
                else:
                    return {
                        "action": "ignored",
                        "reason": "chat_paused (owner_manual_takeover_recent)",
                        "contact": contact,
                    }
            else:
                return {
                    "action": "ignored",
                    "reason": f"chat_paused ({contact.paused_reason or 'ai_disabled'})",
                    "contact": contact,
                }

        # Update contact message stats
        contact.total_messages_in += 1
        contact.last_incoming_at = current_time

        # 4. Check Voice Note Request & Stop Words
        voice_intent = IntentService.detect_voice_request(text)
        voice_tag = voice_intent.preferred_tag if voice_intent.requested else None

        # 4b. Check preference for communication without brackets
        if IntentService.detect_no_brackets_request(text):
            contact.no_brackets = True
            await session.commit()

        # 4c. Check conversational timing agreements (in 1 hour, tomorrow, goodnight, closure)
        agreement = IntentService.detect_agreement_or_pause(text)
        if agreement == "in_one_hour":
            jitter_mins = random.randint(68, 85)
            contact.scheduled_reengage_at = current_time + timedelta(minutes=jitter_mins)
            contact.reengage_context = "in_one_hour"
            await session.commit()
        elif agreement == "tomorrow":
            contact.scheduled_reengage_at = TimezoneService.calculate_next_morning_time(
                persona.timezone_name, wake_hour=13, ref_utc_dt=current_time
            )
            contact.reengage_context = "tomorrow_talk"
            await session.commit()
        elif agreement == "goodnight":
            contact.scheduled_reengage_at = TimezoneService.calculate_next_morning_time(
                persona.timezone_name, wake_hour=9, ref_utc_dt=current_time
            )
            contact.reengage_context = "morning_greeting"
            await session.commit()

        # 4d. Dynamic sleep analysis for this specific dialogue
        sleep_state = TimezoneService.analyze_dialogue_sleep_state(
            persona_tz=persona.timezone_name,
            base_sleep_start=persona.sleep_start,
            base_sleep_end=persona.sleep_end,
            contact_last_outgoing_at=contact.last_outgoing_at,
            contact_last_incoming_at=contact.last_incoming_at,
            incoming_message=text,
            chat_history=AIService.get_chat_history(contact.id),
            ref_utc_dt=current_time,
        )

        if sleep_state["is_sleeping"] and sender_tg_id != settings.ADMIN_TELEGRAM_ID:
            contact.pending_overnight_message = text
            contact.scheduled_reengage_at = TimezoneService.calculate_next_morning_time(
                persona.timezone_name, wake_hour=9, ref_utc_dt=current_time
            )
            contact.reengage_context = "morning_reply_to_overnight"
            await session.commit()
            return {
                "action": "sleeping_overnight",
                "reason": "girl_is_asleep_overnight",
                "contact": contact,
                "sleep_state": sleep_state,
            }

        # Context-aware moderation: NEVER blocks automatically! Analyzes context,
        # tags lead, saves violation details and notifies owner for manual decision.
        from companion.services.moderation_service import ModerationService
        from companion.models.entities import ContactMessage

        is_violation, violation_analysis = await ModerationService.analyze_incoming_content(
            text=text,
            is_voice=is_voice,
            chat_history=AIService.get_chat_history(contact.id),
        )
        if is_violation:
            contact.violation_count = (contact.violation_count or 0) + 1
            contact.has_violation = True
            contact.last_violation_at = current_time
            contact.last_violation_text = text
            contact.last_violation_analysis = violation_analysis
            if voice_file_id:
                contact.last_voice_file_id = voice_file_id
            tags_list = [t.strip() for t in (contact.tags or "🟢 Активный").split(",") if t.strip()]
            if "🔴 Оскорбления" not in tags_list:
                tags_list.append("🔴 Оскорбления")
            contact.tags = ", ".join(tags_list)

        # Store incoming message in persistent history
        in_msg = ContactMessage(
            contact_id=contact.id,
            sender_role="user",
            text=text,
            is_voice=is_voice,
            voice_file_id=voice_file_id,
            voice_duration=voice_duration,
            is_violation=is_violation,
            violation_analysis=violation_analysis if is_violation else None,
            created_at=current_time,
        )
        session.add(in_msg)
        await session.commit()

        # 5. Detect City / Timezone clues
        city, tz = TimezoneService.detect_city_and_timezone(text)
        if city and tz:
            contact.detected_city = city
            contact.detected_timezone = tz
            await session.commit()

        # 5b. Detect Language (strictly Russian default, ignore Ukrainian, English only if 100% Latin)
        has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", text))
        has_latin = bool(re.search(r"[a-zA-Z]", text))
        if has_cyrillic:
            contact.language = "ru"
        elif has_latin and len(text.strip().split()) >= 1:
            contact.language = "en"
        await session.commit()

        # 6. Check Meetup / Date Proposal
        meetup_intent = IntentService.detect_meetup_proposal(text)
        if meetup_intent.is_proposal and auto_handover_on_date:
            alert = await HandoverService.process_meetup_proposal(
                session=session,
                contact=contact,
                intent=meetup_intent,
                raw_message=text,
                auto_pause=True,
                now=current_time,
            )
            return {
                "action": "handover_to_owner",
                "alert": alert,
                "contact": contact,
                "meetup_intent": meetup_intent,
            }

        # 7. Check Photo Request
        photo_intent = IntentService.detect_photo_request(text)
        photo_asset = None
        if photo_intent.requested:
            photo_asset = await MediaService.get_photo_for_contact(
                session=session,
                user_id=user_id,
                contact_id=contact.id,
                preferred_tag=photo_intent.preferred_tag,
            )

        # 8. Generate Text Reply with multi-turn history
        history = AIService.get_chat_history(contact.id)
        try:
            reply_text = await AIService.generate_reply(
                persona=persona,
                contact=contact,
                chat_history=history,
                new_incoming_message=text,
                is_voice=is_voice,
                voice_duration=voice_duration,
            )
        except TypeError:
            # Backward-compatible fallback for 4-arg legacy mocks
            reply_text = await AIService.generate_reply(
                persona, contact, history, text
            )

        # If contact sent a voice note, girl naturally may reply with a voice note too
        if is_voice and not voice_tag and random.random() < 0.40:
            voice_tag = "adaptive_all"

        detected_event = getattr(contact, "last_event", None)

        # Store outgoing bot response in persistent history
        out_msg = ContactMessage(
            contact_id=contact.id,
            sender_role="bot",
            text=reply_text,
            is_voice=bool(voice_tag),
            created_at=current_time,
        )
        session.add(out_msg)

        # Auto-update brief dialogue summary and topics
        summary, topics = ModerationService.generate_topics_and_summary(
            [{"sender_role": "user", "text": text}],
            default_summary=contact.dialogue_summary
        )
        contact.dialogue_summary = summary
        contact.dialogue_topics = topics

        # 9. Update stats
        contact.total_messages_out += 1
        contact.last_outgoing_at = current_time
        await session.commit()

        # Decide human-like delay (regular 10-25s vs normal 20-45s)
        is_regular = False
        if contact and contact.total_messages_in >= 2 and contact.last_outgoing_at:
            last_out = contact.last_outgoing_at
            if last_out.tzinfo is None and current_time.tzinfo is not None:
                last_out = last_out.replace(tzinfo=timezone.utc)
            elif last_out.tzinfo is not None and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
            if abs((current_time - last_out).total_seconds()) <= 900:
                is_regular = True

        if sender_tg_id == settings.ADMIN_TELEGRAM_ID:
            simulated_delay = random.uniform(1.5, 3.5)
        elif is_regular:
            simulated_delay = random.uniform(10.0, 25.0)
        else:
            simulated_delay = random.uniform(20.0, 45.0)

        return {
            "action": "reply",
            "reply_text": reply_text,
            "photo_asset": photo_asset,
            "voice_tag": voice_tag,
            "simulated_delay_sec": simulated_delay,
            "contact": contact,
            "detected_event": detected_event,
            "detected_violation": is_violation,
            "violation_analysis": violation_analysis,
            "voice_file_id": voice_file_id,
            "is_voice": is_voice,
            "voice_duration": voice_duration,
        }
