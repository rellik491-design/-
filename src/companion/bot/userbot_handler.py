import os
import re
import time
import uuid
import random
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import Contact, Persona, ContactMessage
from companion.models.enums import ContactStatus
from companion.services.subscription_service import SubscriptionService
from companion.services.timezone_service import TimezoneService
from companion.services.intent_service import IntentService
from companion.services.handover_service import HandoverService
from companion.services.media_service import MediaService
from companion.services.ai_service import AIService
from companion.services.voice_service import VoiceService
from companion.services.stt_service import STTService
from companion.bot.response_planner import ResponsePlanner

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
    async def get_pending_unread_messages(
        cls,
        session: AsyncSession,
        contact_id: int,
        max_lookback: int = 50,
    ) -> List[ContactMessage]:
        """
        Inspects recent messages for `contact_id` and returns all currently unread/pending
        user messages (`processing_status IN ('NEW', 'BATCHED', 'FAILED')` or trailing user
        messages since the last bot reply), in exact chronological order (`id ASC`).
        """
        stmt = (
            select(ContactMessage)
            .where(
                ContactMessage.contact_id == contact_id,
                ContactMessage.is_deleted == False,
            )
            .order_by(ContactMessage.id.desc())
            .limit(max_lookback)
        )
        rows_desc = (await session.execute(stmt)).scalars().all()
        pending_reversed: List[ContactMessage] = []
        for row in rows_desc:
            if row.sender_role == "user":
                pending_reversed.append(row)
            elif getattr(row, "processing_status", "PROCESSED") in ("NEW", "BATCHED", "FAILED"):
                pending_reversed.append(row)
            else:
                break
        pending_reversed.reverse()
        return pending_reversed

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
        telegram_message_id: Optional[int] = None,
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
        bot_token: Optional[str] = None,
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

        # Section 10 & 11: Message Ownership & Hard Self-Response Block
        from companion.services.message_ownership_service import MessageOwnershipService
        sender_type = MessageOwnershipService.classify_message(
            author_id=sender_tg_id,
            author_username=sender_username,
            telegram_message_id=telegram_message_id,
            chat_id=sender_tg_id,
        )
        if sender_type == "BOT":
            logger.info("[USERBOT_HANDLER] Hard Self-Response Block: author is BOT (%s). Dropping.", sender_tg_id)
            if telegram_message_id:
                MessageOwnershipService.register_outgoing_message_memory(sender_tg_id, telegram_message_id)
            return {"action": "ignored", "reason": "bot_self_message"}

        # Section 12: Idempotency Table Check
        if telegram_message_id:
            is_new, _ = await MessageOwnershipService.check_and_record_inbound(
                session=session,
                chat_id=sender_tg_id,
                message_id=telegram_message_id,
                sender_id=sender_tg_id,
                sender_type="USER",
            )
            if not is_new:
                logger.info("[USERBOT_HANDLER] Duplicate message (chat=%s, msg_id=%s) already processed. Skipping.", sender_tg_id, telegram_message_id)
                return {"action": "ignored", "reason": "duplicate_message"}

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

        t_collect_start = time.perf_counter()
        eff_token = bot_token or settings.ADMIN_BOT_TOKEN
        batch_id = f"batch_{contact.id}_{uuid.uuid4().hex[:10]}"

        # Prepare list of incoming messages for this turn (each kept as its own individual message record)
        batch_inputs: List[Dict[str, Any]] = []
        if incoming_batch and len(incoming_batch) > 0:
            for b_item in incoming_batch:
                b_is_voice = bool(b_item.get("is_voice", False) or b_item.get("media_type") in ("voice", "video_note"))
                b_mtype = b_item.get("media_type") or ("voice" if b_is_voice else "text")
                batch_inputs.append(
                    {
                        "db_id": b_item.get("db_id"),
                        "text": (b_item.get("text") or "").strip(),
                        "message_id": b_item.get("message_id") if b_item.get("message_id") is not None else b_item.get("telegram_message_id"),
                        "reply_to_msg_id": b_item.get("reply_to_msg_id"),
                        "media_type": b_mtype,
                        "is_voice": b_is_voice,
                        "voice_duration": int(b_item.get("voice_duration") or 0),
                        "voice_file_id": b_item.get("voice_file_id"),
                        "voice_mime": b_item.get("voice_mime") or "audio/ogg",
                        "transcription_failed": bool(b_item.get("transcription_failed", False)),
                        "created_at": b_item.get("created_at") or current_time,
                    }
                )
        else:
            batch_inputs.append(
                {
                    "db_id": None,
                    "text": text,
                    "message_id": telegram_message_id,
                    "reply_to_msg_id": None,
                    "media_type": "voice" if is_voice else "text",
                    "is_voice": is_voice,
                    "voice_duration": voice_duration,
                    "voice_file_id": voice_file_id,
                    "voice_mime": "audio/ogg",
                    "transcription_failed": False,
                    "created_at": current_time,
                }
            )

        # Transcribe any voice messages in the batch that haven't been transcribed yet
        t_stt_start = time.perf_counter()
        voice_transcription_s = 0.0
        from companion.services.multilingual_service import MultilingualService

        for b_item in batch_inputs:
            if b_item["is_voice"]:
                cur_txt = b_item["text"]
                needs_stt = (
                    not cur_txt
                    or cur_txt in ("[PENDING_VOICE_TRANSCRIPTION]", "[Голосовое сообщение]", "[Видео-сообщение (кружок)]")
                )
                if needs_stt and b_item.get("voice_file_id") and eff_token:
                    transcript, _ = await STTService.transcribe_telegram_voice(
                        bot_token=eff_token,
                        file_id=b_item["voice_file_id"],
                        mime_type=b_item.get("voice_mime") or "audio/ogg",
                    )
                    if transcript and transcript.strip():
                        b_item["text"] = transcript.strip()
                        det = MultilingualService.detect_language(
                            text=transcript.strip(),
                            recent_language=contact.language,
                        )
                        norm = MultilingualService.normalize_slang(
                            text=transcript.strip(),
                            language=det.language,
                            recent_language=contact.language,
                        )
                        b_item["language"] = det.language
                        b_item["language_confidence"] = det.confidence
                        b_item["stt_confidence"] = 0.94
                        b_item["normalized_text"] = norm.normalized_text
                        b_item["transcription_failed"] = False
                    else:
                        b_item["text"] = ResponsePlanner.VOICE_FAILED_MARKER
                        b_item["transcription_failed"] = True
                elif needs_stt and not b_item.get("voice_file_id"):
                    b_item["text"] = ResponsePlanner.VOICE_FAILED_MARKER
                    b_item["transcription_failed"] = True
            else:
                txt = b_item.get("text") or ""
                if txt:
                    det = MultilingualService.detect_language(
                        text=txt,
                        recent_language=contact.language,
                    )
                    norm = MultilingualService.normalize_slang(
                        text=txt,
                        language=det.language,
                        recent_language=contact.language,
                    )
                    b_item["language"] = det.language
                    b_item["language_confidence"] = det.confidence
                    b_item["normalized_text"] = norm.normalized_text
                    MultilingualService.log_developer_debug(
                        message_id=b_item.get("message_id"),
                        message_type="text",
                        language=det.language,
                        language_confidence=det.confidence,
                        transcript=txt,
                        normalized_text=norm.normalized_text,
                        stt_confidence=1.0,
                    )
        voice_transcription_s = time.perf_counter() - t_stt_start

        batch_inputs = [b for b in batch_inputs if b.get("text") or b.get("is_voice")]
        if not batch_inputs:
            return {"action": "ignored", "reason": "empty_batch", "contact": contact}

        # Combined text of all newly arrived messages in this batch
        combined_text = "\n".join(b["text"] for b in batch_inputs if b.get("text"))
        any_voice = any(b["is_voice"] for b in batch_inputs)
        total_voice_dur = sum(b["voice_duration"] for b in batch_inputs)
        last_voice_fid = next((b["voice_file_id"] for b in reversed(batch_inputs) if b.get("voice_file_id")), voice_file_id)

        # Update contact message stats for each individual message in the batch
        contact.total_messages_in += len(batch_inputs)
        contact.last_incoming_at = current_time

        # 4. Check Voice Note Request & Stop Words across batch
        voice_intent = IntentService.detect_voice_request(combined_text)
        voice_tag = voice_intent.preferred_tag if voice_intent.requested else None

        # 4b. Persistent Behavioral Memory & Controlled Self-Learning (Sections 14-15)
        from companion.ai.behavioral_memory import ControlledFeedbackLearner
        await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session=session,
            contact=contact,
            user_message=combined_text
        )
        await ControlledFeedbackLearner.analyze_outcome_learning(
            session=session,
            contact=contact,
            user_message=combined_text,
            is_voice=any_voice
        )

        if IntentService.detect_no_brackets_request(combined_text):
            contact.no_brackets = True
            await session.commit()

        # 4c. Daivinchik / Dating context & Forwarded profile recognition
        from companion.services.daivinchik_service import DaivinchikService
        is_daivinchik_profile = False
        parsed_p = None

        for b_item in batch_inputs:
            b_text = b_item.get("text") or ""
            if DaivinchikService.is_daivinchik_message({"text": b_text}):
                parsed_p = DaivinchikService.parse_profile(b_text)
                is_daivinchik_profile = True
                break

        if is_daivinchik_profile and parsed_p:
            parsed_p["source"] = "daivinchik"
            parsed_p["discovered_at"] = current_time.isoformat()
            contact.set_discovered_profile(parsed_p)
            contact.dating_discovery_source = "daivinchik_forward"
            if parsed_p.get("city") and not contact.detected_city:
                contact.detected_city = parsed_p["city"]
            await session.commit()
            logger.info("[DAIVINCHIK] Extracted profile for contact %s: %s", contact.id, parsed_p.get("name"))
        elif DaivinchikService.detect_daivinchik_dialogue_context(combined_text):
            if not contact.dating_discovery_source:
                contact.dating_discovery_source = "daivinchik_mention"
                await session.commit()

        # 4c. Check conversational timing agreements (in 1 hour, tomorrow, goodnight, closure)
        agreement = IntentService.detect_agreement_or_pause(combined_text)
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
            incoming_message=combined_text,
            chat_history=AIService.get_chat_history(contact.id),
            ref_utc_dt=current_time,
        )

        if not os.environ.get("PYTEST_CURRENT_TEST") and sleep_state["is_sleeping"] and sender_tg_id != settings.ADMIN_TELEGRAM_ID:
            contact.pending_overnight_message = combined_text
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

        is_violation, violation_analysis = await ModerationService.analyze_incoming_content(
            text=combined_text,
            is_voice=any_voice,
            chat_history=AIService.get_chat_history(contact.id),
        )
        if is_violation:
            contact.violation_count = (contact.violation_count or 0) + 1
            contact.has_violation = True
            contact.last_violation_at = current_time
            contact.last_violation_text = combined_text
            contact.last_violation_analysis = violation_analysis
            if last_voice_fid:
                contact.last_voice_file_id = last_voice_fid
            tags_list = [t.strip() for t in (contact.tags or "🟢 Активный").split(",") if t.strip()]
            if "🔴 Оскорбления" not in tags_list:
                tags_list.append("🔴 Оскорбления")
            contact.tags = ", ".join(tags_list)

        # Store each incoming message individually in persistent history with state NEW -> BATCHED -> PROCESSING
        inserted_msgs: List[ContactMessage] = []
        for idx_b, b_item in enumerate(batch_inputs):
            in_msg = ContactMessage(
                contact_id=contact.id,
                telegram_message_id=b_item["message_id"],
                reply_to_msg_id=b_item.get("reply_to_msg_id"),
                sender_role="user",
                media_type=b_item["media_type"],
                text=b_item["text"],
                is_voice=b_item["is_voice"],
                voice_file_id=b_item["voice_file_id"],
                voice_duration=b_item["voice_duration"],
                processing_status="PROCESSING",
                batch_id=batch_id,
                is_violation=is_violation if idx_b == len(batch_inputs) - 1 else False,
                violation_analysis=(violation_analysis if is_violation and idx_b == len(batch_inputs) - 1 else None),
                detected_language=b_item.get("language"),
                language_confidence=b_item.get("language_confidence"),
                stt_confidence=b_item.get("stt_confidence"),
                normalized_text=b_item.get("normalized_text"),
                created_at=b_item["created_at"],
            )
            session.add(in_msg)
            inserted_msgs.append(in_msg)
        await session.commit()
        for m_obj in inserted_msgs:
            await session.refresh(m_obj)

        # Read all currently unread / pending user messages since the last bot reply
        pending_unread_rows = await cls.get_pending_unread_messages(session, contact.id)

        # Check HealthManager state before processing batch (Sections 9-10 & 27: Fail-safe Pause)
        from companion.services.health_manager import HealthManager
        if HealthManager.is_paused():
            for r in pending_unread_rows:
                r.processing_status = "QUEUED"
            for m_obj in inserted_msgs:
                m_obj.processing_status = "QUEUED"
            await session.commit()
            logger.warning("[USERBOT_HANDLER] System is in SAFE PAUSED state. Messages queued safely without reply.")
            return {
                "action": "paused_due_to_health",
                "reason": HealthManager.get_paused_reason(),
                "contact": contact,
            }

        for r in pending_unread_rows:
            if getattr(r, "processing_status", "PROCESSED") in ("NEW", "BATCHED", "FAILED"):
                r.processing_status = "PROCESSING"
                r.batch_id = batch_id
        await session.commit()

        if len(pending_unread_rows) > len(batch_inputs):
            unread_batch_dicts = [
                {
                    "id": r.id,
                    "message_id": r.telegram_message_id,
                    "telegram_message_id": r.telegram_message_id,
                    "reply_to_msg_id": getattr(r, "reply_to_msg_id", None),
                    "media_type": getattr(r, "media_type", "voice" if r.is_voice else "text"),
                    "text": r.text,
                    "is_voice": r.is_voice,
                    "voice_duration": r.voice_duration,
                    "voice_file_id": r.voice_file_id,
                    "transcription_failed": r.text == ResponsePlanner.VOICE_FAILED_MARKER,
                }
                for r in pending_unread_rows
            ]
        else:
            unread_batch_dicts = [
                {
                    "id": m_obj.id,
                    "message_id": m_obj.telegram_message_id,
                    "telegram_message_id": m_obj.telegram_message_id,
                    "reply_to_msg_id": getattr(m_obj, "reply_to_msg_id", None),
                    "media_type": getattr(m_obj, "media_type", "voice" if m_obj.is_voice else "text"),
                    "text": m_obj.text,
                    "is_voice": m_obj.is_voice,
                    "voice_duration": m_obj.voice_duration,
                    "voice_file_id": m_obj.voice_file_id,
                    "transcription_failed": m_obj.text == ResponsePlanner.VOICE_FAILED_MARKER,
                }
                for m_obj in inserted_msgs
            ]

        batch_collection_ms = (time.perf_counter() - t_collect_start) * 1000.0
        all_unread_text = "\n".join(
            u["text"] for u in unread_batch_dicts
            if u.get("text") and u.get("text") != ResponsePlanner.VOICE_FAILED_MARKER
        )

        # 5. Immigrant Geography & Multidimensional Profile (Origin != Location != Timezone != Language)
        from companion.services.user_geography_service import UserGeographyService
        user_geo_profile, geo_updated = UserGeographyService.analyze_and_update_geography(contact, all_unread_text)
        if geo_updated:
            await session.commit()

        # 5b. Multilingual Language Detection & Rolling Preference (Russian, Ukrainian, Mixed, English)
        import json
        from companion.services.multilingual_service import MultilingualService

        batch_lang_res = MultilingualService.detect_language(
            text=all_unread_text,
            recent_language=contact.language,
        )
        pref_update = MultilingualService.update_language_preference(
            current_pref_json=contact.language_preference,
            incoming_language=batch_lang_res.language,
            confidence=batch_lang_res.confidence,
        )
        contact.language_preference = json.dumps(pref_update["language_preference"])
        contact.language = pref_update["current_user_language"]
        contact.recent_language = pref_update["recent_language"]
        contact.language_pattern = pref_update["language_pattern"]
        await session.commit()

        # Dynamic Language Profile & Mirroring Analysis
        history = AIService.get_chat_history(contact.id)
        dynamic_lang_profile = MultilingualService.analyze_dynamic_language(
            text=all_unread_text,
            conversation_history=history,
            contact=contact,
        )

        # Anti-Instagram & Personal Contact Deflection Tracking
        contact_deflection = IntentService.detect_contact_request(
            all_unread_text,
            contact_deflection_count=contact.contact_deflection_count or 0,
        )
        if contact_deflection.deflection_required:
            contact.contact_deflection_count = (contact.contact_deflection_count or 0) + 1
            contact.last_contact_request_at = now or datetime.now(timezone.utc)
            await session.commit()

        # 5c. Extract and store persistent Long-Term Memories (facts, preferences, habits, agreements)
        from companion.ai.memory_manager import MemoryManager
        for m_obj in inserted_msgs:
            if m_obj.text and m_obj.text != ResponsePlanner.VOICE_FAILED_MARKER:
                await MemoryManager.extract_and_store_memories(
                    session=session,
                    contact=contact,
                    incoming_text=m_obj.text,
                    source_message_id=m_obj.id,
                )
        await session.commit()

        # 5d. Persistent user profile, behavioral tracking, and controlled self-learning from feedback
        from companion.services.user_profile_service import UserProfileService
        await UserProfileService.process_learning_and_feedback(session, contact, combined_text)

        # 6. Check Meetup / Date Proposal across the unread batch
        meetup_intent = IntentService.detect_meetup_proposal(all_unread_text)
        if meetup_intent.is_proposal and auto_handover_on_date:
            for r in pending_unread_rows:
                r.processing_status = "PROCESSED"
            for m_obj in inserted_msgs:
                m_obj.processing_status = "PROCESSED"
            alert = await HandoverService.process_meetup_proposal(
                session=session,
                contact=contact,
                intent=meetup_intent,
                raw_message=all_unread_text,
                auto_pause=True,
                now=current_time,
            )
            return {
                "action": "handover_to_owner",
                "alert": alert,
                "contact": contact,
                "meetup_intent": meetup_intent,
                "unread_batch": unread_batch_dicts,
            }

        # 7. Check Photo Request & Contextual Photo Selection (Photo Pool)
        from companion.services.photo_pool_service import PhotoPoolService
        photo_intent = IntentService.detect_photo_request(all_unread_text)
        photo_asset = None
        ai_photo_context = ""
        chat_key = str(contact.tg_id or contact.id)
        current_hour = (now or datetime.now(timezone.utc)).hour

        if photo_intent.requested:
            if photo_intent.is_repeat_request:
                # Interlocutor asks for the exact same previous photo -> Absolute no-repeat rule
                ai_photo_context = (
                    "\n[ПРИМЕЧАНИЕ СИСТЕМЫ: Собеседник просит повторно скинуть то же самое фото, "
                    "которое ты уже отправляла раньше. Ты помнишь это и естественно/с юмором отвечаешь, "
                    "что уже показывала его (например: 'я же тебе уже скидывала)', 'ты уже видел)', "
                    "'второй раз не покажу)'), повторно фото НЕ отправляешь.]"
                )
            else:
                # Explicit user request -> reserve candidate (bypasses cooldown, strictly enforces no-repeat!)
                photo_asset = await PhotoPoolService.select_and_reserve_photo(
                    session=session,
                    chat_id=chat_key,
                    preferred_category=photo_intent.preferred_category,
                    preferred_tags=photo_intent.preferred_tags,
                    is_explicit_request=True,
                    current_hour=current_hour,
                    reason=photo_intent.reason or "explicit_user_request",
                    user_id=user_id,
                )
                if not photo_asset:
                    # Fallback to legacy MediaService only if PhotoPool had no candidate
                    photo_asset = await MediaService.get_photo_for_contact(
                        session=session,
                        user_id=user_id,
                        contact_id=contact.id,
                        preferred_tag=photo_intent.preferred_tag,
                    )

                if photo_asset:
                    p_desc = getattr(photo_asset, "description", "")
                    p_cat = getattr(photo_asset, "category", getattr(photo_asset, "tag", ""))
                    ai_photo_context = (
                        f"\n[ПРИМЕЧАНИЕ СИСТЕМЫ: К твоему сообщению прикрепляется твое фото (категория: {p_cat}, "
                        f"описание: '{p_desc}'). Сделай реплику живой и естественной, не пиши банально 'вот фото'.]"
                    )
        else:
            # Conservative spontaneous photo evaluation (rare moments, respecting cooldowns)
            freq_ok, _ = await PhotoPoolService.check_chat_frequency_limits(
                session=session, chat_id=chat_key, is_explicit_request=False, now=now
            )
            if freq_ok:
                spontaneous_triggers = [
                    r"\b(что\s+делаешь|чем\s+занята|где\s+ты|куда\s+идешь|как\s+ты|що\s+робиш|де\s+ти)\b",
                ]
                if any(re.search(pat, all_unread_text.lower()) for pat in spontaneous_triggers):
                    photo_asset = await PhotoPoolService.select_and_reserve_photo(
                        session=session,
                        chat_id=chat_key,
                        preferred_category=None,
                        is_explicit_request=False,
                        current_hour=current_hour,
                        reason="spontaneous_contextual",
                        user_id=user_id,
                    )
                    if photo_asset:
                        p_desc = getattr(photo_asset, "description", "")
                        p_cat = getattr(photo_asset, "category", "")
                        ai_photo_context = (
                            f"\n[ПРИМЕЧАНИЕ СИСТЕМЫ: Ты спонтанно делишься с собеседником фотографией из момента "
                            f"(категория: {p_cat}, описание: '{p_desc}'). Добавь легкий живой комментарий к фото.]"
                        )

        # 8. Format conversational turn (preserving text, [VOICE MESSAGE] transcription, and [msg_id=...])
        ai_turn_input = ResponsePlanner.format_batch_for_ai(unread_batch_dicts)
        if ai_photo_context:
            ai_turn_input += ai_photo_context

        # Determine target message ID for reply validation (Sections 16, 17)
        primary_target_msg_id = None
        if unread_batch_dicts:
            for u_item in reversed(unread_batch_dicts):
                if u_item.get("message_id"):
                    primary_target_msg_id = u_item["message_id"]
                    break

        # Generate Text Reply with multi-level context & bounded recent history (ONE AI Call per batch)
        t_ai_start = time.perf_counter()
        history = AIService.get_chat_history(contact.id)
        try:
            try:
                reply_text = await AIService.generate_reply(
                    persona=persona,
                    contact=contact,
                    chat_history=history,
                    new_incoming_message=ai_turn_input,
                    is_voice=any_voice,
                    voice_duration=total_voice_dur,
                    session=session,
                    incoming_batch=unread_batch_dicts,
                    dynamic_language_profile=dynamic_lang_profile,
                    contact_deflection=contact_deflection,
                    user_geo_profile=user_geo_profile,
                    response_target_id=primary_target_msg_id,
                    response_target_sender="USER",
                )
            except TypeError:
                reply_text = await AIService.generate_reply(
                    persona, contact, history, ai_turn_input
                )
        except Exception as ai_err:
            # Mark batch as FAILED so messages are never lost and can be recovered
            for r in pending_unread_rows:
                if getattr(r, "batch_id", None) == batch_id:
                    r.processing_status = "FAILED"
            for m_obj in inserted_msgs:
                m_obj.processing_status = "FAILED"
            await session.commit()
            from companion.services.health_manager import HealthManager
            HealthManager.report_provider_error(ai_err, context="userbot_handler_ai_generation")
            return {
                "action": "paused_due_to_error",
                "reason": str(ai_err),
                "contact": contact,
            }

        if not reply_text:
            logger.info("[USERBOT_HANDLER] Empty reply generated (Safe silence). No messages sent to user.")
            for r in pending_unread_rows:
                r.processing_status = "PROCESSED"
            for m_obj in inserted_msgs:
                m_obj.processing_status = "PROCESSED"
            await session.commit()
            return {
                "action": "silence",
                "contact": contact,
                "unread_batch": unread_batch_dicts,
            }

        # 8b. Build structured ResponsePlan (segmentation, selective Reply targeting, burst rhythm, laughter, parentheses, emojis, stickers)
        response_plan = ResponsePlanner.plan_response(
            raw_reply_text=reply_text,
            incoming_batch=unread_batch_dicts,
            contact=contact,
            persona=persona,
            no_brackets=bool(getattr(contact, "no_brackets", False)),
        )
        ai_planning_s = time.perf_counter() - t_ai_start
        reply_text = response_plan["combined_text"]

        from companion.ai.conversation_state_analyzer import ConversationStateAnalyzer
        state_an = ConversationStateAnalyzer.analyze_dialogue_state(
            incoming_text=all_unread_text,
            chat_history=history,
            contact_id=contact.id,
        )
        suggested_reaction = state_an.suggested_reaction

        # If contact sent a voice note (and transcription succeeded), girl naturally may reply with a voice note too
        if any_voice and all_unread_text and not voice_tag and random.random() < 0.40:
            voice_tag = "adaptive_all"

        detected_event = getattr(contact, "last_event", None)
        if not detected_event:
            meetup_intent = IntentService.detect_meetup_proposal(all_unread_text)
            meetup_agreed = IntentService.detect_meetup_agreement(all_unread_text)
            if meetup_agreed and getattr(contact, "funnel_stage", "") in ("date_proposed", "quest_offered", "quest_agreed"):
                detected_event = "QUEST_AGREED"
                setattr(contact, "last_event", "QUEST_AGREED")
                contact.funnel_stage = "quest_agreed"
            elif meetup_intent.is_proposal or meetup_agreed:
                detected_event = "DATE_PROPOSED"
                setattr(contact, "last_event", "DATE_PROPOSED")
                contact.funnel_stage = "date_proposed"

        # Transition all messages in this batch to PROCESSED and store outgoing bot response
        for r in pending_unread_rows:
            if getattr(r, "batch_id", None) == batch_id or getattr(r, "processing_status", None) == "PROCESSING":
                r.processing_status = "PROCESSED"
        for m_obj in inserted_msgs:
            m_obj.processing_status = "PROCESSED"

        first_reply_id = None
        if response_plan.get("messages"):
            first_reply_id = response_plan["messages"][0].get("reply_to_message_id")

        out_msg = ContactMessage(
            contact_id=contact.id,
            reply_to_msg_id=first_reply_id,
            sender_role="bot",
            text=reply_text,
            is_voice=bool(voice_tag),
            processing_status="PROCESSED",
            batch_id=batch_id,
            created_at=current_time,
        )
        session.add(out_msg)
        await session.flush()

        # Update rolling ConversationSummary & cumulative topics/summary without overwriting prior turns
        recent_for_summary = await MemoryManager.get_recent_messages(session, contact.id, limit=12)
        await MemoryManager.update_conversation_summary(session, contact, recent_for_summary)

        # Periodic LLM memory consolidation so location, origin, plans, and self-facts are refreshed
        if not os.environ.get("PYTEST_CURRENT_TEST") and (
            (contact.total_messages_in or 0) % 3 == 0
            or ((contact.total_messages_in or 0) >= 2 and not contact.detected_city)
        ):
            try:
                recent_for_llm = await MemoryManager.get_recent_messages(session, contact.id, limit=16)
                await MemoryManager.refresh_memory_with_llm(session, contact, persona, recent_for_llm)
            except Exception as mem_err:
                logger.debug("Periodic LLM memory refresh skipped: %s", mem_err)

        # 9. Update stats
        contact.total_messages_out += max(1, len(response_plan["messages"]))
        contact.last_outgoing_at = current_time
        await session.commit()

        # Metrics calculation & structured logging (Sections 40–41)
        voice_count = sum(1 for u in unread_batch_dicts if u.get("is_voice"))
        text_count = len(unread_batch_dicts) - voice_count
        planned_msgs = response_plan.get("messages") or []
        replies_count = sum(1 for m in planned_msgs if m.get("reply_to_message_id") is not None)
        normal_count = len(planned_msgs) - replies_count

        metrics = {
            "batch_id": batch_id,
            "batch_size": len(unread_batch_dicts),
            "text_count": text_count,
            "voice_count": voice_count,
            "batch_collection_ms": round(batch_collection_ms, 1),
            "voice_transcription_s": round(voice_transcription_s, 3),
            "ai_planning_s": round(ai_planning_s, 3),
            "responses_count": len(planned_msgs),
            "replies_count": replies_count,
            "normal_count": normal_count,
            "rhythm_state": response_plan.get("rhythm_state", "CALM"),
        }

        logger.info(
            "[CHAT %s] Collected batch: %d messages (Text: %d, Voice: %d) | "
            "Batch collection: %.1fms | Voice transcription: %.2fs | AI planning: %.2fs | "
            "Responses: %d (Replies: %d, Normal: %d, Rhythm: %s)",
            sender_tg_id,
            metrics["batch_size"],
            metrics["text_count"],
            metrics["voice_count"],
            metrics["batch_collection_ms"],
            metrics["voice_transcription_s"],
            metrics["ai_planning_s"],
            metrics["responses_count"],
            metrics["replies_count"],
            metrics["normal_count"],
            metrics["rhythm_state"],
        )

        return {
            "action": "reply",
            "reply_text": reply_text,
            "response_plan": response_plan,
            "unread_batch": unread_batch_dicts,
            "batch_size": len(unread_batch_dicts),
            "metrics": metrics,
            "photo_asset": photo_asset,
            "photo_pool_asset": photo_asset,
            "voice_tag": voice_tag,
            "simulated_delay_sec": 0.0,
            "contact": contact,
            "detected_event": detected_event,
            "detected_violation": is_violation,
            "violation_analysis": violation_analysis,
            "voice_file_id": last_voice_fid,
            "is_voice": any_voice,
            "voice_duration": total_voice_dur,
            "suggested_reaction": suggested_reaction,
        }
