import asyncio
import os
import json
import time
import random
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import AccountSession, User, Contact
from companion.models.enums import AccountStatus
from companion.services.invite_service import InviteService
from companion.services.subscription_service import SubscriptionService
from companion.services.handover_service import HandoverService
from companion.services.voice_service import VoiceService
from companion.services.stt_service import STTService
from companion.services.intent_service import IntentService
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier
from companion.bot.response_planner import AsyncMessageBatcher, ResponsePlanner

logger = logging.getLogger(__name__)


class BusinessChatBot:
    """
    Handles Telegram Business (Chat Automation) updates:
    - business_connection: when a user adds the bot to their Telegram account
    - business_message: messages in personal chats managed by the bot (15–20s reading cycle & batching)
    - edited_business_message: edits to previously sent messages (updates DB without duplicate reply)
    - deleted_business_messages: marks deleted messages in DB
    """

    _chat_locks: Dict[str, asyncio.Lock] = {}
    _batcher = AsyncMessageBatcher(
        default_debounce_seconds=settings.MESSAGE_READ_INTERVAL_MIN,
        max_batch_wait_seconds=settings.MESSAGE_READ_INTERVAL_MAX,
    )

    @classmethod
    def _get_chat_lock(cls, connection_id: str, chat_id: int) -> asyncio.Lock:
        key = f"{connection_id}:{chat_id}"
        lock = cls._chat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._chat_locks[key] = lock
            if len(cls._chat_locks) > 1000:
                # Prune oldest unlocked entries
                for k in list(cls._chat_locks.keys())[:200]:
                    if not cls._chat_locks[k].locked():
                        cls._chat_locks.pop(k, None)
        return lock

    @classmethod
    async def process_business_connection(
        cls, session: AsyncSession, connection_data: Dict[str, Any], bot_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Handles 'business_connection' webhook update from Telegram Bot API.
        Automatically links Telegram account to the User and checks subscription validity.
        """
        connection_id = connection_data.get("id")
        user_info = connection_data.get("user", {})
        is_enabled = connection_data.get("is_enabled", True)
        tg_user_id = user_info.get("id")

        if not tg_user_id:
            return {"status": "error", "reason": "missing_user_id"}

        # 1. Resolve or create User
        stmt_u = select(User).where(User.telegram_id == tg_user_id)
        res_u = await session.execute(stmt_u)
        user = res_u.scalars().first()
        if not user:
            user = User(
                telegram_id=tg_user_id,
                username=user_info.get("username"),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # 2. Check active subscription
        sub = await SubscriptionService.get_active_subscription(session, user.id)
        has_sub = sub is not None and sub.is_valid()
        if not has_sub:
            admin_stmt = select(User).where(User.telegram_id == settings.ADMIN_TELEGRAM_ID)
            admin_user = (await session.execute(admin_stmt)).scalars().first()
            if admin_user:
                admin_sub = await SubscriptionService.get_active_subscription(session, admin_user.id)
                if admin_sub and admin_sub.is_valid():
                    sub = await SubscriptionService.activate_plan(
                        session=session,
                        user_id=user.id,
                        plan=PlanDuration.DAYS_31,
                        custom_days=365,
                        extend=False,
                    )
                    has_sub = True

        # 3. Find or create AccountSession
        stmt = select(AccountSession).where(
            (AccountSession.business_connection_id == connection_id)
            | ((AccountSession.user_id == user.id) & (AccountSession.tg_user_id == tg_user_id))
        )
        res = await session.execute(stmt)
        account = res.scalars().first()

        active_flag = is_enabled and has_sub
        status_flag = AccountStatus.CONNECTED if active_flag else AccountStatus.DISCONNECTED

        if account:
            account.business_connection_id = connection_id
            account.is_active = active_flag
            account.status = status_flag
            account.tg_user_id = tg_user_id
            account.first_name = user_info.get("first_name", "")
            account.username = user_info.get("username", "")
        else:
            account = AccountSession(
                user_id=user.id,
                business_connection_id=connection_id,
                tg_user_id=tg_user_id,
                first_name=user_info.get("first_name", ""),
                username=user_info.get("username", ""),
                auth_type="business_automation",
                is_active=active_flag,
                status=status_flag,
            )
            session.add(account)
        await session.commit()
        await session.refresh(account)
        from companion.services.account_service import AccountService
        await AccountService.ensure_account_persona(session, account)

        # 4. Notify user about connection status
        can_reply = connection_data.get("can_reply", True)
        token = bot_token or settings.ADMIN_BOT_TOKEN
        if token and tg_user_id:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    if is_enabled:
                        if has_sub:
                            if not can_reply:
                                text = (
                                    f"⚠️ <b>Внимание! Бот не может отвечать в ваших чатах!</b>\n\n"
                                    f"В настройках Telegram Business отключен параметр <b>«Отвечать на сообщения»</b>.\n\n"
                                    f"<b>Как включить за 10 секунд:</b>\n"
                                    f"1. Откройте в Telegram: <b>Настройки ➔ Telegram Business ➔ Чат-боты</b>\n"
                                    f"2. Выберите нашего бота\n"
                                    f"3. Включите тумблер <b>«Отвечать на сообщения»</b>\n"
                                    f"4. В пункте <b>«Чаты»</b> обязательно выберите <b>«Все личные чаты»</b> (а не только «Новые»)"
                                )
                            else:
                                text = (
                                    f"🎉 <b>Автоматизация чатов успешно подключена!</b>\n\n"
                                    f"Бот теперь привязан к вашему аккаунту и готов отвечать на сообщения "
                                    f"по тарифу <b>{sub.plan.value}</b> (осталось {sub.days_remaining()} дн.).\n\n"
                                    f"📌 <b>ОБЯЗАТЕЛЬНО ПРОВЕРЬТЕ НАСТРОЙКИ В TELEGRAM:</b>\n"
                                    f"1. <b>Настройки Telegram ➔ Telegram Business ➔ Чат-боты ➔ выберите бота</b>\n"
                                    f"2. В пункте <b>«Чаты»</b> выберите <b>«Все личные чаты»</b> (если выбрать «Новые», Telegram не передаст боту существующие диалоги!)\n"
                                    f"3. Убедитесь, что тумблер <b>«Отвечать на сообщения»</b> включен.\n\n"
                                    f"ℹ️ <i>Telegram передает сообщения боту в реальном времени. Если у вас есть старые непрочитанные диалоги за прошлые дни, напишите клиенту первое сообщение сами — и бот продолжит общение, либо дождитесь нового входящего сообщения!</i>"
                                )
                        else:
                            text = (
                                f"⚠️ <b>Бот добавлен в Telegram Business, но у вас нет активной подписки!</b>\n\n"
                                f"Автоматические ответы в ваших чатах приостановлены. "
                                f"Активируйте тариф или подарочный ключ через /start."
                            )
                    else:
                        text = "ℹ️ Автоматизация чатов отключена в настройках вашего Telegram."
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": tg_user_id, "text": text, "parse_mode": "HTML"},
                    )
            except Exception as e:
                logger.error("Error sending business connection notification: %s", e)

        return {"status": "connected" if active_flag else "no_subscription", "account_id": account.id, "is_enabled": is_enabled, "can_reply": can_reply}

    @classmethod
    async def process_business_message(
        cls,
        session: AsyncSession,
        message_data: Dict[str, Any],
        bot_token: Optional[str] = None,
        now: Optional[datetime] = None,
        auto_handover_on_date: bool = False,
        simulate_delays: bool = True,
        debounce_seconds: Optional[float] = None,
        read_interval_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Processes 'business_message' update from Telegram.
        1. Checks idempotency and captures raw message metadata (text, voice, media, reply_to) in arrival order.
        2. Waits for the 15–20s reading cycle (or configurable `read_interval_seconds` / `debounce_seconds`).
        3. Collects ALL pending messages from the reading cycle in one batch.
        4. Transcribes any voice messages in the batch, runs ONE AI analysis + ResponsePlanner,
           marks messages read (✓✓), and immediately sends the planned responses.
        """
        connection_id = message_data.get("business_connection_id")
        chat_id = message_data.get("chat", {}).get("id")
        sender = message_data.get("from", {})
        sender_id = sender.get("id")
        text = (message_data.get("text") or message_data.get("caption") or "").strip()

        # Detect voice / video_note / audio metadata immediately without blocking arrival order
        voice_data = message_data.get("voice") or message_data.get("audio") or message_data.get("video_note")
        is_voice = bool(voice_data)
        voice_duration = int(voice_data.get("duration", 0)) if voice_data else 0
        file_id = voice_data.get("file_id") if voice_data else None
        is_video = "video_note" in message_data
        default_mime = "video/mp4" if is_video else "audio/ogg"
        voice_mime = (voice_data.get("mime_type") if voice_data else None) or default_mime

        if is_voice and not text:
            text = "[PENDING_VOICE_TRANSCRIPTION]"

        media_type = "text"
        if is_video:
            media_type = "video_note"
        elif is_voice:
            media_type = "voice"
        elif "photo" in message_data:
            media_type = "photo"
        elif "sticker" in message_data:
            media_type = "sticker"
        elif "animation" in message_data:
            media_type = "animation"

        token = bot_token or settings.ADMIN_BOT_TOKEN

        # Multimodal Vision: process stickers, GIFs (animations), photos and reply contexts
        if not is_voice and (not text or "photo" in message_data or "sticker" in message_data or "animation" in message_data or "reply_to_message" in message_data):
            from companion.services.vision_service import VisionService
            try:
                media_ctx = await VisionService.extract_media_context(
                    bot_token=token,
                    message_data=message_data,
                )
                augmented = media_ctx.get("text_augmented")
                if augmented:
                    text = augmented
                    logger.info("Processed visual/media context in chat %s: %s", chat_id, text[:120])
            except Exception as e:
                logger.warning("Error extracting media context in chat %s: %s", chat_id, e)

            # Robust fallback so incoming media without caption is never dropped
            if not text:
                if "photo" in message_data:
                    text = "[Собеседник прислал фото]"
                elif "sticker" in message_data:
                    text = "[Собеседник прислал стикер]"
                elif "animation" in message_data:
                    text = "[Собеседник прислал GIF-анимацию/мем]"

        if not connection_id or not chat_id or (not text and not is_voice):
            logger.info("Business message ignored due to missing fields: conn=%s, chat=%s, has_text=%s", connection_id, chat_id, bool(text))
            return {"action": "ignored", "reason": "missing_fields"}

        message_id = message_data.get("message_id")
        reply_to_msg = message_data.get("reply_to_message") or {}
        reply_to_msg_id = reply_to_msg.get("message_id") if isinstance(reply_to_msg, dict) else None

        # Section 10 & 11: Message Ownership & Hard Self-Response Block
        from companion.services.message_ownership_service import MessageOwnershipService
        sender_type = MessageOwnershipService.classify_message(
            author_id=sender_id,
            author_username=sender.get("username"),
            telegram_message_id=message_id,
            chat_id=chat_id,
        )
        if sender_type == "BOT":
            logger.info("[BUSINESS_BOT] Hard Self-Response Block: sender %s is BOT. Ignoring inbound message.", sender_id)
            if message_id:
                MessageOwnershipService.register_outgoing_message_memory(chat_id, message_id)
            return {"action": "ignored", "reason": "bot_self_message"}
        lock = cls._get_chat_lock(str(connection_id), int(chat_id))

        # Idempotency check under short lock before reading cycle
        if message_id is not None:
            async with lock:
                from companion.models.entities import ProcessedUpdate
                update_key = f"biz_msg:{connection_id}:{chat_id}:{message_id}"
                existing_upd = (
                    await session.execute(
                        select(ProcessedUpdate).where(ProcessedUpdate.update_key == update_key)
                    )
                ).scalars().first()
                if existing_upd:
                    logger.info("Duplicate business_message skipped (idempotent key=%s)", update_key)
                    return {"action": "ignored", "reason": "duplicate_message"}
                session.add(
                    ProcessedUpdate(
                        update_key=update_key,
                        update_type="business_message",
                        chat_id=int(chat_id),
                    )
                )
                await session.commit()

        is_mock = (not simulate_delays) or (token in ("mock_token", "test_token"))
        explicit_interval = read_interval_seconds if read_interval_seconds is not None else debounce_seconds
        if explicit_interval is not None:
            eff_interval = max(0.0, float(explicit_interval))
        elif is_mock:
            eff_interval = 0.0
        else:
            eff_interval = random.uniform(
                float(getattr(settings, "MESSAGE_READ_INTERVAL_MIN", 15.0)),
                float(getattr(settings, "MESSAGE_READ_INTERVAL_MAX", 20.0)),
            )

        batch_item = {
            "text": text,
            "message_id": message_id,
            "telegram_message_id": message_id,
            "reply_to_msg_id": reply_to_msg_id,
            "media_type": media_type,
            "is_voice": is_voice,
            "voice_duration": voice_duration,
            "voice_file_id": file_id,
            "voice_mime": voice_mime,
            "sender": sender,
            "sender_id": sender_id,
            "message_data": message_data,
            "created_at": now or datetime.now(timezone.utc),
        }

        async def _execute_batch(collected_items: List[Dict[str, Any]]) -> Dict[str, Any]:
            last_item = collected_items[-1]
            async with lock:
                return await cls._process_business_message_locked(
                    session=session,
                    message_data=last_item["message_data"],
                    connection_id=connection_id,
                    chat_id=chat_id,
                    sender=last_item["sender"],
                    sender_id=last_item["sender_id"],
                    text=last_item["text"],
                    is_voice=any(it["is_voice"] for it in collected_items),
                    voice_duration=sum(it["voice_duration"] for it in collected_items),
                    file_id=last_item["voice_file_id"],
                    message_id=last_item["message_id"],
                    bot_token=bot_token,
                    now=now,
                    auto_handover_on_date=auto_handover_on_date,
                    simulate_delays=simulate_delays,
                    incoming_batch=collected_items,
                )

        if eff_interval > 0:
            scope_key = f"biz:{connection_id}:{chat_id}"
            return await cls._batcher.enqueue_and_wait(
                scope_key=scope_key,
                item=batch_item,
                processor=_execute_batch,
                debounce_seconds=eff_interval,
            )
        else:
            return await _execute_batch([batch_item])

    @classmethod
    async def _process_business_message_locked(
        cls,
        session: AsyncSession,
        message_data: Dict[str, Any],
        connection_id: str,
        chat_id: int,
        sender: Dict[str, Any],
        sender_id: Optional[int],
        text: str,
        is_voice: bool,
        voice_duration: int,
        file_id: Optional[str],
        message_id: Optional[int],
        bot_token: Optional[str] = None,
        now: Optional[datetime] = None,
        auto_handover_on_date: bool = False,
        simulate_delays: bool = True,
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        token = bot_token or settings.ADMIN_BOT_TOKEN
        logger.info(
            "Incoming business message in chat %s from sender %s (conn: %s): text='%s', is_voice=%s",
            chat_id, sender_id, connection_id, text[:60], is_voice
        )

        # 1. Verify health status and active subscription
        from companion.services.health_manager import HealthManager
        if HealthManager.is_paused():
            logger.warning("[BUSINESS_BOT] System in SAFE PAUSED state (%s). Suppressing message processing for chat %s", HealthManager.get_paused_reason(), chat_id)
            return {"action": "paused_due_to_health", "reason": HealthManager.get_paused_reason()}

        is_valid = await InviteService.is_business_connection_valid(session, connection_id, now=now)
        if not is_valid:
            logger.info("Business connection %s invalid or subscription expired", connection_id)
            return {"action": "ignored", "reason": "subscription_expired_or_invalid"}

        # Retrieve account
        stmt = select(AccountSession).where(
            AccountSession.business_connection_id == connection_id
        )
        res = await session.execute(stmt)
        account = res.scalars().first()
        if not account:
            logger.warning("AccountSession not found for business_connection_id: %s", connection_id)
            return {"action": "ignored", "reason": "account_not_found"}

        # 2. Check if the girl herself wrote this message (Human takeover)
        if sender_id == account.tg_user_id:
            contact = await UserbotHandler.get_or_create_contact(
                session=session,
                user_id=account.user_id,
                tg_id=chat_id,
                is_incoming=False,
            )
            await HandoverService.handle_manual_owner_message(
                session=session, contact=contact, text=text
            )
            logger.info("Owner manual message in business chat %s: '%s'. Auto-reply paused.", chat_id, text[:60])
            # Send notification to owner bot with unpause button
            owner_user_stmt = select(User).where(User.id == account.user_id)
            owner_user = (await session.execute(owner_user_stmt)).scalars().first()
            owner_tg = (owner_user.telegram_id if owner_user and owner_user.telegram_id else None) or account.tg_user_id
            if owner_tg and token and token not in ("mock_token", "test_token"):
                name = contact.first_name or contact.username or f"Чат {chat_id}"
                try:
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={
                                "chat_id": owner_tg,
                                "text": (
                                    f"👤 <b>Вы ответили вручную в чате с {name}:</b>\n"
                                    f"<i>«{text[:200]}»</i>\n\n"
                                    f"⏸ Автоответ бота в этом чате временно приостановлен, чтобы не перебивать вас.\n"
                                    f"<i>(Через 15 минут без сообщений автоответ возобновится автоматически)</i>"
                                ),
                                "parse_mode": "HTML",
                                "reply_markup": {
                                    "inline_keyboard": [
                                        [{"text": "▶️ Возобновить автоответ бота", "callback_data": f"unpause_contact_{contact.id}"}]
                                    ]
                                }
                            }
                        )
                except Exception as notify_err:
                    logger.debug("Failed to send takeover notification: %s", notify_err)
            return {"action": "human_takeover_paused", "chat_id": chat_id}

        # 3. Message from male interlocutor -> Run standard processing pipeline with quest funnel
        # Immediately mark all messages in this batch as read in Telegram (two checkmarks ✓✓)
        is_mock = (not simulate_delays) or (token in ("mock_token", "test_token"))
        if token and not is_mock:
            if incoming_batch:
                for b_item in incoming_batch:
                    b_msg_id = b_item.get("telegram_message_id") or b_item.get("message_id")
                    if b_msg_id:
                        await cls.read_business_message(token, connection_id, chat_id, b_msg_id)
            elif message_id:
                await cls.read_business_message(token, connection_id, chat_id, message_id)

        # Natural listening simulation if the batch contains incoming voice notes
        batch_voice_durations = [
            int(b.get("voice_duration") or 0)
            for b in (incoming_batch or [])
            if b.get("is_voice") or b.get("media_type") in ("voice", "video_note")
        ]
        if is_voice and voice_duration > 0:
            batch_voice_durations.append(voice_duration)

        if batch_voice_durations and not is_mock:
            max_voice_len = max(batch_voice_durations)
            # Snappy, natural listening simulation (0.8s - 2.0s) so interlocutor feels instant responsiveness
            listen_pause = min(max(float(max_voice_len) * 0.35, 0.8), 2.0)
            logger.info(
                "[CHAT %s] Simulating listening to voice note (%ds) for %.1fs before AI response",
                chat_id, max_voice_len, listen_pause
            )
            await asyncio.sleep(listen_pause)

            # Attempt to clear the blue circle (•) via MTProto ReadMessageContentsRequest if Telethon is configured
            try:
                from companion.services.telethon_service import TelethonService
                voice_msg_ids = []
                if incoming_batch:
                    for b_item in incoming_batch:
                        if b_item.get("is_voice") or b_item.get("media_type") in ("voice", "video_note"):
                            v_id = b_item.get("telegram_message_id") or b_item.get("message_id")
                            if v_id:
                                voice_msg_ids.append(v_id)
                elif message_id and is_voice:
                    voice_msg_ids.append(message_id)

                if voice_msg_ids:
                    await TelethonService.mark_voice_contents_listened(chat_id, voice_msg_ids)
            except Exception as e_tel:
                logger.debug("TelethonService mark contents skipped: %s", e_tel)

        try:
            res = await UserbotHandler.process_incoming_message(
                session=session,
                user_id=account.user_id,
                sender_tg_id=sender_id,
                sender_username=sender.get("username"),
                sender_first_name=sender.get("first_name"),
                text=text,
                now=now,
                auto_handover_on_date=auto_handover_on_date,
                account_id=account.id,
                is_voice=is_voice,
                voice_duration=voice_duration,
                voice_file_id=file_id,
                telegram_message_id=message_id,
                incoming_batch=incoming_batch,
                bot_token=token,
            )
        except Exception as proc_err:
            logger.exception("[BUSINESS_BOT] Error in process_incoming_message: %s", proc_err)
            from companion.services.health_manager import HealthManager
            HealthManager.report_provider_error(proc_err, context="business_bot_process_incoming")
            return {"action": "paused_due_to_error", "reason": str(proc_err)}

        # Subscription owner lookup
        owner_user_stmt = select(User).where(User.id == account.user_id)
        owner_user = (await session.execute(owner_user_stmt)).scalars().first()
        owner_tg_id = (owner_user.telegram_id if owner_user and owner_user.telegram_id else None) or account.tg_user_id or settings.ADMIN_TELEGRAM_ID
        contact = res.get("contact")
        contact_name = (contact.first_name if contact else None) or sender.get("first_name") or "Собеседник"
        contact_username = (contact.username if contact else None) or sender.get("username")
        contact_id = contact.id if contact else chat_id

        action = res.get("action")
        if action in ("ignored", "sleeping_overnight", "silence", "paused_due_to_health", "paused_due_to_error") or not res.get("reply_text"):
            return res

        unread_batch_res = res.get("unread_batch") or []
        combined_transcribed = " | ".join(
            u["text"] for u in unread_batch_res
            if u.get("text") and u.get("text") != ResponsePlanner.VOICE_FAILED_MARKER
        ) or text
        alert_user_message = f"🎙 [ГС {voice_duration} сек]: «{combined_transcribed}»" if is_voice else combined_transcribed

        detected_event = res.get("detected_event")
        meetup_intent = IntentService.detect_meetup_proposal(alert_user_message)
        meetup_agreed = IntentService.detect_meetup_agreement(alert_user_message)

        if detected_event == "QUEST_AGREED" or (meetup_agreed and getattr(contact, "funnel_stage", "") in ("date_proposed", "quest_offered", "quest_agreed")):
            if contact and not getattr(contact, "alert_quest_sent", False):
                contact.alert_quest_sent = True
                contact.funnel_stage = "quest_agreed"
                await session.commit()
                await AdminNotifier.send_quest_agreed_alarm(
                    owner_tg_id=owner_tg_id,
                    contact_name=contact_name,
                    contact_username=contact_username,
                    contact_tg_id=sender_id,
                    user_message=alert_user_message,
                    contact_id=contact_id,
                )
        elif detected_event == "DATE_PROPOSED" or meetup_intent.is_proposal or meetup_agreed:
            if contact and not getattr(contact, "alert_date_sent", False):
                contact.alert_date_sent = True
                contact.funnel_stage = "date_proposed"
                await session.commit()
                await AdminNotifier.send_date_proposed_alert(
                    owner_tg_id=owner_tg_id,
                    contact_name=contact_name,
                    contact_username=contact_username,
                    contact_tg_id=sender_id,
                    user_message=alert_user_message,
                    contact_id=contact_id,
                )

        # Context-aware moderation alert: notify owner with actions, bot continues dialogue
        if res.get("detected_violation"):
            await AdminNotifier.send_insult_alert(
                owner_tg_id=owner_tg_id,
                contact_name=contact_name,
                contact_username=contact_username,
                contact_tg_id=sender_id,
                user_message=alert_user_message,
                analysis_reason=res.get("violation_analysis") or "Выявлены грубые выражения или оскорбления.",
                contact_id=contact_id,
                has_voice=is_voice,
                voice_duration=voice_duration,
                voice_file_id=file_id if is_voice else None,
            )

        action = res.get("action")
        if action in ("reply", "blocked"):
            token = bot_token or settings.ADMIN_BOT_TOKEN
            reply_text = res.get("reply_text")
            voice_tag = res.get("voice_tag")
            response_plan = res.get("response_plan") or {}
            planned_messages = response_plan.get("messages") or [
                {"text": b.strip(), "reply_to_message_id": None}
                for b in (reply_text or "").split("||")
                if b.strip()
            ]
            planned_sticker = response_plan.get("sticker")
            delay_range = response_plan.get("inter_message_delay") or (
                float(getattr(settings, "INTER_MESSAGE_DELAY_MIN", 0.5)),
                float(getattr(settings, "INTER_MESSAGE_DELAY_MAX", 1.2)),
            )
            is_mock = (not simulate_delays) or (token in ("mock_token", "test_token"))
            if token and reply_text:
                t_send_start = time.perf_counter()

                # 1. Immediately mark all collected messages in the batch as read -> Two checkmarks (✓✓)
                message_id = message_data.get("message_id")
                if incoming_batch:
                    for b_item in incoming_batch:
                        b_msg_id = b_item.get("telegram_message_id") or b_item.get("message_id")
                        if b_msg_id:
                            await cls.read_business_message(token, connection_id, chat_id, b_msg_id)
                else:
                    await cls.read_business_message(token, connection_id, chat_id, message_id)

                # 2. Fast, responsive delivery of the planned batch response (no 15–45s unread stall after AI!)
                # Photo Pool Delivery (Reserve-Before-Send lifecycle tracking)
                photo_obj = res.get("photo_pool_asset") or res.get("photo_asset")
                if photo_obj:
                    p_path = getattr(photo_obj, "storage_path", None) or getattr(photo_obj, "file_path", None) or getattr(photo_obj, "telegram_file_id", None)
                    p_id = getattr(photo_obj, "photo_id", None) or str(getattr(photo_obj, "id", ""))
                    if p_path:
                        await cls.send_business_chat_action(token, connection_id, chat_id, "upload_photo")
                        if not is_mock:
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                        sent_photo_msg_id = await cls.send_business_photo(
                            bot_token=token,
                            business_connection_id=connection_id,
                            chat_id=chat_id,
                            photo_path_or_id=p_path,
                        )
                        from companion.services.photo_pool_service import PhotoPoolService
                        if sent_photo_msg_id:
                            await PhotoPoolService.mark_photo_sent(
                                session=session,
                                chat_id=str(chat_id),
                                photo_id=p_id,
                                message_id=sent_photo_msg_id,
                            )
                        else:
                            await PhotoPoolService.mark_photo_failed(
                                session=session,
                                chat_id=str(chat_id),
                                photo_id=p_id,
                                release_reservation=False,
                            )

                if voice_tag:
                    await cls.send_business_chat_action(token, connection_id, chat_id, "record_voice")
                    await VoiceService.send_telegram_voice_note(
                        bot_token=token,
                        chat_id=chat_id,
                        voice_tag=voice_tag,
                        business_connection_id=connection_id,
                    )
                elif VoiceService.should_send_voice(user_message=combined_transcribed, bot_reply=reply_text, is_incoming_voice=is_voice):
                    clean_voice = reply_text.replace("||", " ")
                    voice_text = VoiceService.shorten_for_voice(clean_voice, max_chars=180)
                    sent = await VoiceService.send_telegram_voice_note(
                        bot_token=token,
                        chat_id=chat_id,
                        text=voice_text,
                        business_connection_id=connection_id,
                    )
                    if not sent:
                        from companion.services.output_sanitizer import OutputSanitizer
                        for idx, msg_item in enumerate(planned_messages):
                            bubble = OutputSanitizer.sanitize_outgoing_text(msg_item.get("text", ""))
                            if not bubble:
                                continue
                            reply_to_id = msg_item.get("reply_to_message_id")
                            if idx > 0 and not is_mock:
                                await asyncio.sleep(random.uniform(delay_range[0], delay_range[1]))
                            await cls.send_business_chat_action(token, connection_id, chat_id, "typing")
                            if not is_mock:
                                from companion.services.typing_simulator import TypingSimulator
                                typing_duration = TypingSimulator.calculate_typing_duration(bubble)
                                await asyncio.sleep(typing_duration)
                            await cls.send_business_message(
                                token, connection_id, chat_id, bubble, reply_to_message_id=reply_to_id
                            )
                else:
                    from companion.services.output_sanitizer import OutputSanitizer
                    for idx, msg_item in enumerate(planned_messages):
                        bubble = OutputSanitizer.sanitize_outgoing_text(msg_item.get("text", ""))
                        if not bubble:
                            continue
                        reply_to_id = msg_item.get("reply_to_message_id")
                        if idx > 0 and not is_mock:
                            await asyncio.sleep(random.uniform(delay_range[0], delay_range[1]))
                        await cls.send_business_chat_action(token, connection_id, chat_id, "typing")
                        if not is_mock:
                            from companion.services.typing_simulator import TypingSimulator
                            typing_duration = TypingSimulator.calculate_typing_duration(bubble)
                            await asyncio.sleep(typing_duration)
                        await cls.send_business_message(
                            token, connection_id, chat_id, bubble, reply_to_message_id=reply_to_id
                        )
                    if planned_sticker and planned_sticker.get("file_id"):
                        if not is_mock:
                            await asyncio.sleep(random.uniform(0.4, 0.8))
                        await cls.send_business_sticker(
                            token, connection_id, chat_id, planned_sticker["file_id"]
                        )

                # Native Telegram Reactions (Section 34: Bot API 7.0+ setMessageReaction)
                from companion.services.reaction_service import TelegramReactionService
                sug_reaction = res.get("suggested_reaction")
                if sug_reaction and not is_mock:
                    turn_cnt = getattr(res.get("contact"), "total_messages_out", 0) or 1
                    if TelegramReactionService.can_react(int(chat_id), turn_cnt):
                        target_msg_id = None
                        if incoming_batch:
                            for b in reversed(incoming_batch):
                                target_msg_id = b.get("telegram_message_id") or b.get("message_id")
                                if target_msg_id:
                                    break
                        elif message_id:
                            target_msg_id = message_id
                        if target_msg_id:
                            await TelegramReactionService.set_message_reaction(
                                bot_token=token,
                                chat_id=int(chat_id),
                                message_id=int(target_msg_id),
                                reaction_emoji=sug_reaction,
                            )
                            TelegramReactionService.record_reaction_sent(int(chat_id), turn_cnt)

                telegram_send_s = time.perf_counter() - t_send_start
                if isinstance(res.get("metrics"), dict):
                    res["metrics"]["telegram_send_s"] = round(telegram_send_s, 3)
                    logger.info(
                        "[CHAT %s] Telegram sending: %.2fs | Responses: %d | Replies: %d | Normal messages: %d",
                        chat_id,
                        telegram_send_s,
                        res["metrics"].get("responses_count", len(planned_messages)),
                        res["metrics"].get("replies_count", 0),
                        res["metrics"].get("normal_count", len(planned_messages)),
                    )

        elif action == "handover_to_owner":
            alert_data = res.get("alert")
            if alert_data:
                alert_text = AdminNotifier.format_meetup_alert(alert_data)
                await AdminNotifier.send_telegram_alert(alert_text, owner_tg_id)

        return res

    @staticmethod
    async def send_business_message(
        bot_token: str,
        business_connection_id: str,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
    ) -> bool:
        """Sends message on behalf of the user's account using business_connection_id and native Telegram Reply."""
        from companion.services.output_sanitizer import OutputSanitizer
        clean_text = OutputSanitizer.sanitize_outgoing_text(text)
        is_valid_out, invalid_reason = OutputSanitizer.validate_final_outgoing(clean_text)
        if not is_valid_out:
            logger.warning(
                "[OUTPUT_SANITIZER] Blocked invalid outgoing business message in chat %s (reason=%s): %r",
                chat_id,
                invalid_reason,
                str(text)[:120],
            )
            return False

        if not bot_token or bot_token in ("mock_token", "test_token"):
            return True
        from companion.bot.reply_validator import ReplyValidator
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "business_connection_id": business_connection_id,
            "chat_id": chat_id,
            "text": clean_text,
        }
        if reply_to_message_id is not None:
            reply_params = ReplyValidator.build_reply_parameters(reply_to_message_id, is_business_connection=True)
            if reply_params:
                payload["reply_parameters"] = reply_params

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.post(url, json=payload)
                    if res.status_code == 200:
                        try:
                            resp_json = res.json()
                            sent_id = resp_json.get("result", {}).get("message_id")
                            if sent_id:
                                from companion.services.message_ownership_service import MessageOwnershipService
                                MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                        except Exception:
                            pass
                        return True
                    # If Telegram returns 400 Bad Request due to reply target, gracefully fallback to sending without reply
                    if res.status_code == 400 and "reply_parameters" in payload:
                        logger.warning("Telegram rejected reply_parameters for chat %s (%s). Retrying without reply target.", chat_id, res.text)
                        payload.pop("reply_parameters", None)
                        payload.pop("reply_to_message_id", None)
                        payload.pop("allow_sending_without_reply", None)
                        retry_res = await client.post(url, json=payload)
                        if retry_res.status_code == 200:
                            try:
                                resp_json = retry_res.json()
                                sent_id = resp_json.get("result", {}).get("message_id")
                                if sent_id:
                                    from companion.services.message_ownership_service import MessageOwnershipService
                                    MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                            except Exception:
                                pass
                            return True
                        return False

                    logger.error("Failed to send business message in chat %s: HTTP %s - %s", chat_id, res.status_code, res.text)
                    return False
            except Exception as e:
                logger.error("Exception in send_business_message in chat %s (attempt %d): %r", chat_id, attempt + 1, e)
                if attempt == 0:
                    await asyncio.sleep(0.5)
                else:
                    return False
        return False

    @staticmethod
    async def send_business_sticker(
        bot_token: str,
        business_connection_id: str,
        chat_id: int,
        sticker_file_id: str,
        reply_to_message_id: Optional[int] = None,
    ) -> bool:
        """Sends a curated sticker on behalf of the user's account using business_connection_id."""
        if not bot_token or bot_token in ("mock_token", "test_token") or not sticker_file_id:
            return True
        from companion.bot.reply_validator import ReplyValidator
        url = f"https://api.telegram.org/bot{bot_token}/sendSticker"
        payload: Dict[str, Any] = {
            "business_connection_id": business_connection_id,
            "chat_id": chat_id,
            "sticker": sticker_file_id,
        }
        if reply_to_message_id is not None:
            reply_params = ReplyValidator.build_reply_parameters(reply_to_message_id, is_business_connection=True)
            if reply_params:
                payload["reply_parameters"] = reply_params
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    try:
                        resp_json = res.json()
                        sent_id = resp_json.get("result", {}).get("message_id")
                        if sent_id:
                            from companion.services.message_ownership_service import MessageOwnershipService
                            MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                    except Exception:
                        pass
                    return True
                if res.status_code == 400 and "reply_parameters" in payload:
                    payload.pop("reply_parameters", None)
                    payload.pop("reply_to_message_id", None)
                    payload.pop("allow_sending_without_reply", None)
                    retry_res = await client.post(url, json=payload)
                    if retry_res.status_code == 200:
                        try:
                            resp_json = retry_res.json()
                            sent_id = resp_json.get("result", {}).get("message_id")
                            if sent_id:
                                from companion.services.message_ownership_service import MessageOwnershipService
                                MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                        except Exception:
                            pass
                        return True
                    return False
                return False
        except Exception:
            return False

    @classmethod
    async def send_business_photo(
        cls,
        bot_token: str,
        business_connection_id: str,
        chat_id: int,
        photo_path_or_id: str,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Sends a photo on behalf of the user's business connection.
        Supports both telegram_file_id and local file upload (multipart).
        Returns Telegram message_id on success, or None on failure.
        """
        if not bot_token or bot_token in ("mock_token", "test_token"):
            return 999999

        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        data: Dict[str, Any] = {
            "business_connection_id": business_connection_id,
            "chat_id": str(chat_id),
        }
        if caption:
            from companion.services.output_sanitizer import OutputSanitizer
            clean_caption = OutputSanitizer.sanitize_outgoing_text(caption)
            if clean_caption:
                data["caption"] = clean_caption

        from companion.bot.reply_validator import ReplyValidator
        if reply_to_message_id is not None:
            reply_params = ReplyValidator.build_reply_parameters(reply_to_message_id, is_business_connection=True)
            if reply_params:
                data["reply_parameters"] = json.dumps(reply_params)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if os.path.exists(photo_path_or_id):
                    with open(photo_path_or_id, "rb") as f:
                        fname = os.path.basename(photo_path_or_id)
                        files = {"photo": (fname, f, "image/jpeg")}
                        res = await client.post(url, data=data, files=files)
                else:
                    data["photo"] = photo_path_or_id
                    res = await client.post(url, data=data)

                if res.status_code == 200:
                    resp_json = res.json()
                    sent_id = resp_json.get("result", {}).get("message_id") or 1
                    try:
                        from companion.services.message_ownership_service import MessageOwnershipService
                        MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                    except Exception:
                        pass
                    return sent_id

                # If failed with reply parameters, retry without reply
                if res.status_code == 400 and "reply_parameters" in data:
                    data.pop("reply_parameters", None)
                    if os.path.exists(photo_path_or_id):
                        with open(photo_path_or_id, "rb") as f:
                            fname = os.path.basename(photo_path_or_id)
                            files = {"photo": (fname, f, "image/jpeg")}
                            res_retry = await client.post(url, data=data, files=files)
                    else:
                        res_retry = await client.post(url, data=data)
                    if res_retry.status_code == 200:
                        resp_json = res_retry.json()
                        sent_id = resp_json.get("result", {}).get("message_id") or 1
                        try:
                            from companion.services.message_ownership_service import MessageOwnershipService
                            MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                        except Exception:
                            pass
                        return sent_id

                logger.error("Failed to send business photo in chat %s: HTTP %s - %s", chat_id, res.status_code, res.text)
                return None
        except Exception as e:
            logger.error("Exception in send_business_photo in chat %s: %r", chat_id, e)
            return None

    @staticmethod
    async def send_business_chat_action(
        bot_token: str, business_connection_id: str, chat_id: int, action: str = "typing"
    ) -> bool:
        """Emulates 'typing...' status in the chat on behalf of the account."""
        if not bot_token or bot_token in ("mock_token", "test_token"):
            return True
        url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
        payload = {
            "business_connection_id": business_connection_id,
            "chat_id": chat_id,
            "action": action,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception:
            return False

    @staticmethod
    async def read_business_message(
        bot_token: str,
        business_connection_id: str,
        chat_id: int,
        message_id: Optional[int],
    ) -> bool:
        """
        Marks an incoming message as read on behalf of the connected business account
        using Telegram Bot API readBusinessMessage, converting 1 checkmark to 2 checkmarks (✓✓).
        """
        if not bot_token or bot_token in ("mock_token", "test_token") or not message_id:
            return True
        url = f"https://api.telegram.org/bot{bot_token}/readBusinessMessage"
        payload = {
            "business_connection_id": str(business_connection_id),
            "chat_id": chat_id,
            "message_id": message_id,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    logger.info("readBusinessMessage OK for chat %s msg %s", chat_id, message_id)
                    return True
                else:
                    logger.warning("readBusinessMessage returned %s: %s", res.status_code, res.text)
                    return False
        except Exception as e:
            logger.warning("readBusinessMessage failed: %s", e)
            return False

    @classmethod
    def calculate_dynamic_delays(
        cls,
        contact: Optional[Contact],
        now: Optional[datetime] = None,
        reply_text: str = "",
        incoming_text: str = "",
    ) -> Dict[str, Any]:
        """
        Calculates ultra-realistic human response timings based on conversation regularity:
        - Regular active chat (within last 15 min, >=2 incoming msgs):
          * 75% chance: fast mobile reply (10-25s total)
          * 25% chance: short pause (30-60s total)
        - Non-regular chat (new contact, or dormant dialogue > 15 min):
          * natural initial reply (15-35s unread delay)
        - Urgent / ping messages ('ответь', 'ты тут', 'эй', '?'):
          * instant attention (3-8s unread delay)
        """
        curr = now or datetime.now(timezone.utc)
        is_regular = False

        if contact and contact.total_messages_in >= 2 and contact.last_outgoing_at:
            last_out = contact.last_outgoing_at
            if last_out.tzinfo is None and curr.tzinfo is not None:
                last_out = last_out.replace(tzinfo=timezone.utc)
            elif last_out.tzinfo is not None and curr.tzinfo is None:
                curr = curr.replace(tzinfo=timezone.utc)

            time_since_last_out = abs((curr - last_out).total_seconds())
            if time_since_last_out <= 900:  # <= 15 minutes
                is_regular = True

        first_bubble = (reply_text.split("||")[0] if reply_text else "").strip()
        typing_sec = max(3.0, min(2.0 + len(first_bubble) * 0.08 + random.uniform(1.0, 2.0), 9.0))

        is_admin = contact and contact.tg_id == settings.ADMIN_TELEGRAM_ID
        is_urgent_ping = any(w in incoming_text.lower().strip() for w in ["ответь", "ты тут", "ау", "эй", "где ты", "отвечай"])

        if is_admin:
            unread = random.uniform(1.5, 3.5)
            thinking = random.uniform(1.0, 2.0)
        elif is_urgent_ping:
            # Sensed urgency/impatient ping -> quick read and reply
            unread = random.uniform(3.0, 8.0)
            thinking = random.uniform(1.5, 3.0)
        elif is_regular:
            # Active ping-pong conversation
            if random.random() < 0.75:
                # Fast response with phone in hands (12-25 sec total)
                unread = random.uniform(5.0, 14.0)
                thinking = random.uniform(1.5, 3.0)
            else:
                # Short pause for 30-50 seconds (briefly distracted)
                unread = random.uniform(25.0, 45.0)
                thinking = random.uniform(2.0, 4.0)
        else:
            # Non-regular / initial reply (15 - 30 seconds total)
            unread = random.uniform(12.0, 25.0)
            thinking = random.uniform(2.0, 4.0)

        return {
            "is_regular": is_regular,
            "unread_delay": unread,
            "thinking_delay": thinking,
            "typing_delay": typing_sec,
            "total_delay": unread + thinking + typing_sec,
        }

    @classmethod
    async def process_edited_business_message(
        cls,
        session: AsyncSession,
        message_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Handles 'edited_business_message' updates cleanly:
        Updates the stored ContactMessage text and marks `is_edited=True`,
        extracting any new long-term facts without generating a duplicate AI reply
        or double-counting incoming message statistics.
        """
        from companion.models.entities import Contact, ContactMessage
        from companion.ai.memory_manager import MemoryManager

        connection_id = message_data.get("business_connection_id")
        chat_id = message_data.get("chat", {}).get("id")
        message_id = message_data.get("message_id")
        new_text = (message_data.get("text") or message_data.get("caption") or "").strip()

        if not connection_id or not chat_id or not new_text:
            return {"action": "ignored", "reason": "missing_edit_fields"}

        stmt_acc = select(AccountSession).where(AccountSession.business_connection_id == connection_id)
        account = (await session.execute(stmt_acc)).scalars().first()
        if not account:
            return {"action": "ignored", "reason": "account_not_found"}

        stmt_c = select(Contact).where(Contact.user_id == account.user_id, Contact.tg_id == chat_id)
        contact = (await session.execute(stmt_c)).scalars().first()
        if not contact:
            return {"action": "ignored", "reason": "contact_not_found"}

        msg_row = None
        if message_id is not None:
            stmt_m = select(ContactMessage).where(
                ContactMessage.contact_id == contact.id,
                ContactMessage.telegram_message_id == message_id,
            )
            msg_row = (await session.execute(stmt_m)).scalars().first()

        if not msg_row:
            # Fallback to latest user message in this chat
            stmt_last = (
                select(ContactMessage)
                .where(
                    ContactMessage.contact_id == contact.id,
                    ContactMessage.sender_role == "user",
                    ContactMessage.is_deleted == False,
                )
                .order_by(ContactMessage.id.desc())
                .limit(1)
            )
            msg_row = (await session.execute(stmt_last)).scalars().first()

        if msg_row:
            msg_row.text = new_text
            msg_row.is_edited = True
            await MemoryManager.extract_and_store_memories(
                session=session,
                contact=contact,
                incoming_text=new_text,
                source_message_id=msg_row.id,
            )
            await session.commit()
            return {"action": "message_edited", "contact_id": contact.id, "message_id": msg_row.id}

        return {"action": "ignored", "reason": "original_message_not_found"}

    @classmethod
    async def process_deleted_business_messages(
        cls,
        session: AsyncSession,
        delete_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Handles 'deleted_business_messages' updates from Telegram Business API:
        Marks corresponding ContactMessage rows as `is_deleted=True` so they are excluded
        from AI context windows.
        """
        from companion.models.entities import Contact, ContactMessage

        connection_id = delete_data.get("business_connection_id")
        chat_id = delete_data.get("chat", {}).get("id")
        message_ids = delete_data.get("message_ids") or []

        if not connection_id or not chat_id or not message_ids:
            return {"action": "ignored", "reason": "missing_delete_fields"}

        stmt_acc = select(AccountSession).where(AccountSession.business_connection_id == connection_id)
        account = (await session.execute(stmt_acc)).scalars().first()
        if not account:
            return {"action": "ignored", "reason": "account_not_found"}

        stmt_c = select(Contact).where(Contact.user_id == account.user_id, Contact.tg_id == chat_id)
        contact = (await session.execute(stmt_c)).scalars().first()
        if not contact:
            return {"action": "ignored", "reason": "contact_not_found"}

        stmt_msgs = select(ContactMessage).where(
            ContactMessage.contact_id == contact.id,
            ContactMessage.telegram_message_id.in_(message_ids),
        )
        rows = (await session.execute(stmt_msgs)).scalars().all()
        deleted_count = 0
        for r in rows:
            r.is_deleted = True
            deleted_count += 1

        if deleted_count > 0:
            await session.commit()

        return {"action": "messages_deleted", "deleted_count": deleted_count}

