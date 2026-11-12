import asyncio
import random
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import AccountSession, User
from companion.models.enums import AccountStatus
from companion.services.invite_service import InviteService
from companion.services.subscription_service import SubscriptionService
from companion.services.handover_service import HandoverService
from companion.services.voice_service import VoiceService
from companion.services.stt_service import STTService
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier

logger = logging.getLogger(__name__)


class BusinessChatBot:
    """
    Handles Telegram Business (Chat Automation) updates:
    - business_connection: when a user adds the bot to their Telegram account
    - business_message: messages in personal chats managed by the bot
    """

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
    ) -> Dict[str, Any]:
        """
        Processes 'business_message' update from Telegram.
        1. Checks if connection & subscription are valid (not expired).
        2. Detects if message was sent by the girl herself (human takeover).
        3. If message is from a male contact, runs time-awareness, anti-spam,
           meetup detection, and sends automated response via Bot API.
        """
        connection_id = message_data.get("business_connection_id")
        chat_id = message_data.get("chat", {}).get("id")
        sender = message_data.get("from", {})
        sender_id = sender.get("id")
        text = message_data.get("text", "").strip()

        # Handle incoming voice notes, audio or video notes from contact (Speech-to-Text)
        voice_data = message_data.get("voice") or message_data.get("audio") or message_data.get("video_note")
        is_voice = False
        voice_duration = 0
        file_id = voice_data.get("file_id") if voice_data else None

        token = bot_token or settings.ADMIN_BOT_TOKEN
        if not text and voice_data:
            voice_duration = voice_data.get("duration", 0)
            is_video = "video_note" in message_data
            default_mime = "video/mp4" if is_video else "audio/ogg"
            mime = voice_data.get("mime_type") or default_mime
            if file_id and token:
                logger.info(
                    "Incoming %s in business chat %s from %s (%d sec). Transcribing...",
                    "video note" if is_video else "voice note",
                    chat_id, sender_id, voice_duration
                )
                transcript, _ = await STTService.transcribe_telegram_voice(
                    bot_token=token,
                    file_id=file_id,
                    mime_type=mime,
                )
                if transcript:
                    text = transcript
                    is_voice = True
                    logger.info("Transcribed %s from %s: '%s'", "video note" if is_video else "voice note", sender_id, text)
                else:
                    logger.warning("STT transcription empty for media %s from %s", file_id, sender_id)
                    text = "[Видео-сообщение (кружок)]" if is_video else "[Голосовое сообщение]"
                    is_voice = True

        # Multimodal Vision: process stickers, GIFs (animations), photos and reply contexts
        if not text or "photo" in message_data or "sticker" in message_data or "animation" in message_data or "reply_to_message" in message_data:
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

        if not connection_id or not chat_id or not text:
            logger.info("Business message ignored due to missing fields: conn=%s, chat=%s, has_text=%s", connection_id, chat_id, bool(text))
            return {"action": "ignored", "reason": "missing_fields"}

        logger.info(
            "Incoming business message in chat %s from sender %s (conn: %s): text='%s', is_voice=%s",
            chat_id, sender_id, connection_id, text[:60], is_voice
        )

        # 1. Verify business connection and active subscription
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
        )

        # Subscription owner lookup
        owner_user_stmt = select(User).where(User.id == account.user_id)
        owner_user = (await session.execute(owner_user_stmt)).scalars().first()
        owner_tg_id = (owner_user.telegram_id if owner_user and owner_user.telegram_id else None) or account.tg_user_id or settings.ADMIN_TELEGRAM_ID
        contact = res.get("contact")
        contact_name = (contact.first_name if contact else None) or sender.get("first_name") or "Собеседник"
        contact_username = (contact.username if contact else None) or sender.get("username")
        contact_id = contact.id if contact else chat_id

        action = res.get("action")
        if action in ("ignored", "sleeping_overnight"):
            return res

        alert_user_message = f"🎙 [ГС {voice_duration} сек]: «{text}»" if is_voice else text

        detected_event = res.get("detected_event")
        if detected_event == "DATE_PROPOSED":
            if contact and not getattr(contact, "alert_date_sent", False):
                contact.alert_date_sent = True
                await session.commit()
                await AdminNotifier.send_date_proposed_alert(
                    owner_tg_id=owner_tg_id,
                    contact_name=contact_name,
                    contact_username=contact_username,
                    contact_tg_id=sender_id,
                    user_message=alert_user_message,
                    contact_id=contact_id,
                )
        elif detected_event == "QUEST_AGREED":
            if contact and not getattr(contact, "alert_quest_sent", False):
                contact.alert_quest_sent = True
                await session.commit()
                await AdminNotifier.send_quest_agreed_alarm(
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
            is_mock = (not simulate_delays) or (token in ("mock_token", "test_token"))
            if token and reply_text:
                delays = cls.calculate_dynamic_delays(contact, now=now, reply_text=reply_text, incoming_text=text)

                # 1. Unread pause (пауза до прочтения - имитация того, что девушка не сидит 24/7 в чате)
                # 15-35 сек или 1-2 мин при активном общении; 4-8 мин при нерегулярном
                if not is_mock:
                    logger.info(
                        "Business chat %s: dynamic delay (regular=%s): unread=%.1fs, thinking=%.1fs, typing=%.1fs",
                        chat_id, delays["is_regular"], delays["unread_delay"], delays["thinking_delay"], delays["typing_delay"]
                    )
                    await asyncio.sleep(delays["unread_delay"])

                # 2. Отметка о прочтении -> Две галочки (✓✓)
                message_id = message_data.get("message_id")
                await cls.read_business_message(token, connection_id, chat_id, message_id)

                # 3. Пауза на осмысление после прочтения
                if not is_mock:
                    await asyncio.sleep(delays["thinking_delay"])

                # 4. Отправка ответа (ГС или текст с органичным набором)
                if voice_tag:
                    if not is_mock:
                        await cls.send_business_chat_action(token, connection_id, chat_id, "record_voice")
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                    await VoiceService.send_telegram_voice_note(
                        bot_token=token,
                        chat_id=chat_id,
                        voice_tag=voice_tag,
                        business_connection_id=connection_id,
                    )
                elif VoiceService.should_send_voice(user_message=text, bot_reply=reply_text, is_incoming_voice=is_voice):
                    clean_voice = reply_text.replace("||", " ")
                    voice_text = VoiceService.shorten_for_voice(clean_voice, max_chars=180)
                    duration = min(max(len(voice_text) / 12.0, 3.5), 8.5)
                    if not is_mock:
                        await cls.send_business_chat_action(token, connection_id, chat_id, "record_voice")
                        await asyncio.sleep(duration)
                    sent = await VoiceService.send_telegram_voice_note(
                        bot_token=token,
                        chat_id=chat_id,
                        text=voice_text,
                        business_connection_id=connection_id,
                    )
                    if not sent:
                        bubbles = [b.strip() for b in reply_text.split("||") if b.strip()]
                        for idx, bubble in enumerate(bubbles):
                            if idx > 0 and not is_mock:
                                await asyncio.sleep(random.uniform(1.5, 3.0))
                            typing_duration = max(3.0, min(2.0 + len(bubble) * 0.08 + random.uniform(1.0, 2.0), 12.0))
                            if is_mock:
                                await cls.send_business_chat_action(token, connection_id, chat_id, "typing")
                            else:
                                elapsed = 0.0
                                while elapsed < typing_duration:
                                    await cls.send_business_chat_action(token, connection_id, chat_id, "typing")
                                    step = min(4.0, typing_duration - elapsed)
                                    await asyncio.sleep(step)
                                    elapsed += step
                            await cls.send_business_message(token, connection_id, chat_id, bubble)
                else:
                    bubbles = [b.strip() for b in reply_text.split("||") if b.strip()]
                    for idx, bubble in enumerate(bubbles):
                        if idx > 0 and not is_mock:
                            await asyncio.sleep(random.uniform(1.5, 3.0))
                        typing_duration = max(3.0, min(2.0 + len(bubble) * 0.08 + random.uniform(1.0, 2.0), 12.0))
                        if is_mock:
                            await cls.send_business_chat_action(token, connection_id, chat_id, "typing")
                        else:
                            elapsed = 0.0
                            while elapsed < typing_duration:
                                await cls.send_business_chat_action(token, connection_id, chat_id, "typing")
                                step = min(4.0, typing_duration - elapsed)
                                await asyncio.sleep(step)
                                elapsed += step
                        await cls.send_business_message(token, connection_id, chat_id, bubble)

        elif action == "handover_to_owner":
            alert_data = res.get("alert")
            if alert_data:
                alert_text = AdminNotifier.format_meetup_alert(alert_data)
                await AdminNotifier.send_telegram_alert(alert_text, owner_tg_id)

        return res

    @staticmethod
    async def send_business_message(
        bot_token: str, business_connection_id: str, chat_id: int, text: str
    ) -> bool:
        """Sends message on behalf of the user's account using business_connection_id."""
        if not bot_token or bot_token in ("mock_token", "test_token"):
            return True
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "business_connection_id": business_connection_id,
            "chat_id": chat_id,
            "text": text,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    return True
                logger.error("Failed to send business message in chat %s: HTTP %s - %s", chat_id, res.status_code, res.text)
                return False
        except Exception as e:
            logger.error("Exception in send_business_message in chat %s: %s", chat_id, e)
            return False

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

