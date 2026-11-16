import asyncio
from typing import Dict, Any, Optional
import httpx
from companion.config import settings


class AdminNotifier:
    @staticmethod
    def format_meetup_alert(alert_data: Dict[str, Any]) -> str:
        """
        Formats a high-priority alert for the owner when a man suggests meeting up.
        Uses clean HTML formatting to avoid markdown parsing issues with special characters.
        """
        name = alert_data.get("contact_name", "Собеседник")
        tg_id = alert_data.get("tg_id")
        tg_link = alert_data.get("tg_link", "")
        message = alert_data.get("message", "").strip()
        trigger = alert_data.get("trigger", "встреча")

        user_link = f'<a href="{tg_link}">{name}</a>' if tg_link else f"<b>{name}</b>"

        text = (
            f"🔥 <b>ГОРЯЧИЙ ЛИД: ПРЕДЛОЖЕНИЕ ВСТРЕЧИ!</b>\n\n"
            f"👤 <b>Собеседник:</b> {user_link} (ID: <code>{tg_id}</code>)\n"
            f"💬 <b>Что написал:</b> <i>«{message}»</i>\n"
            f"🎯 <b>Триггер:</b> <code>{trigger}</code>\n\n"
            f"⏸ <b>Статус бота:</b> Поставлен на ПАУЗУ для этого чата.\n"
            f"👉 Перейдите в Telegram и продолжите общение самостоятельно!"
        )
        return text

    @staticmethod
    def format_stats_report(stats: Dict[str, Any]) -> str:
        """Formats an overview analytics report in HTML."""
        return (
            f"📊 <b>СТАТИСТИКА РАБОТЫ AI-КОМПАНЬОНА</b>\n\n"
            f"👥 Всего контактов в базе: <b>{stats.get('total_contacts', 0)}</b>\n"
            f"💬 Активных диалогов: <b>{stats.get('active_dialogues', 0)}</b>\n"
            f"🔥 Успешных лидов (позвали на встречу): <b>{stats.get('successful_leads', 0)}</b>\n"
            f"⏸ Чатов на ручной паузе: <b>{stats.get('paused_dialogues', 0)}</b>\n"
            f"📈 Конверсия во встречу: <b>{stats.get('conversion_rate_percent', 0.0)}%</b>\n\n"
            f"✉️ Входящих сообщений: {stats.get('total_messages_in', 0)}\n"
            f"🚀 Отправлено ответов: {stats.get('total_messages_out', 0)}"
        )

    _active_alarms: Dict[int, Any] = {}

    @classmethod
    async def send_telegram_alert(
        cls,
        text: str,
        admin_tg_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        disable_notification: bool = False,
    ) -> bool:
        """Sends alert message to the owner via Telegram Bot API with sound/markup control."""
        bot_token = settings.ADMIN_BOT_TOKEN
        chat_id = admin_tg_id or settings.ADMIN_TELEGRAM_ID

        if not bot_token or not chat_id:
            return False

        if bot_token in ("mock_token", "test_token"):
            return True

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": disable_notification,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception:
            return False

    @classmethod
    async def send_date_proposed_alert(
        cls,
        owner_tg_id: int,
        contact_name: str,
        contact_username: Optional[str],
        contact_tg_id: int,
        user_message: str,
        contact_id: Optional[int] = None,
    ) -> bool:
        """Sends notification when man proposes a date/meetup and bot starts funneling to quest."""
        link_str = f'<a href="tg://user?id={contact_tg_id}">{contact_name}</a>'
        username_str = f" (@{contact_username})" if contact_username else ""

        text = (
            f"🔔 <b>Лид предложил встретиться!</b>\n\n"
            f"👤 <b>Профиль:</b> {link_str}{username_str}\n"
            f"💬 <b>Его сообщение:</b> <i>«{user_message}»</i>\n\n"
            f"🎯 <i>Бот начал плавно подводить его к квесту.</i>"
        )
        kb = None
        if contact_id:
            kb = {
                "inline_keyboard": [
                    [{"text": "⏸ Принять лида (Остановить ИИ)", "callback_data": f"lead_accept_{contact_id}"}]
                ]
            }
        return await cls.send_telegram_alert(text=text, admin_tg_id=owner_tg_id, reply_markup=kb, disable_notification=False)

    @classmethod
    def is_alarm_active(cls, contact_id: int) -> bool:
        """Checks if there is an active alarm task for this contact."""
        return contact_id in cls._active_alarms

    @classmethod
    async def send_quest_agreed_alarm(
        cls,
        owner_tg_id: int,
        contact_name: str,
        contact_username: Optional[str],
        contact_tg_id: int,
        user_message: str,
        contact_id: int,
    ) -> bool:
        """
        Urgent alarm mode when man agrees to the quest:
        - Sends first audible alert message with 'Принял лида' button
        - Sends up to 3 rapid reminder pings (every 3s)
        - Keeps recurring background reminder every 60s until owner presses 'Принял лида'
        """
        link_str = f'<a href="tg://user?id={contact_tg_id}">{contact_name}</a>'
        username_str = f" (@{contact_username})" if contact_username else ""

        alarm_text = (
            f"🚨🔥 <b>ЛИД СОГЛАСИЛСЯ НА КВЕСТ!</b> 🔥🚨\n\n"
            f"👤 <b>Профиль:</b> {link_str}{username_str}\n"
            f"💬 <b>Его ответ:</b> <i>«{user_message}»</i>\n\n"
            f"⚡️ <b>Срочно перехватывай диалог!</b>"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "✅ Принял лида (Остановить ИИ в этом чате)", "callback_data": f"lead_accept_{contact_id}"}]
            ]
        }

        # 1. Stop any existing alarm task for this contact first
        cls.stop_quest_alarm(contact_id)

        # 2. Send first urgent alert
        await cls.send_telegram_alert(
            text=alarm_text,
            admin_tg_id=owner_tg_id,
            reply_markup=kb,
            disable_notification=False,
        )

        # 3. Background alarm runner
        async def _alarm_loop():
            try:
                # 3 additional rapid alerts every 3 seconds
                for i in range(3):
                    await asyncio.sleep(3)
                    if not cls.is_alarm_active(contact_id):
                        return
                    await cls.send_telegram_alert(
                        text=f"🚨 <b>ВНИМАНИЕ! Мужчина ждет ответ по квесту!</b>\nПрофиль: {link_str}{username_str}",
                        admin_tg_id=owner_tg_id,
                        reply_markup=kb,
                        disable_notification=False,
                    )

                # Recurring 60s reminder until stopped
                while cls.is_alarm_active(contact_id):
                    await asyncio.sleep(60)
                    if not cls.is_alarm_active(contact_id):
                        return
                    await cls.send_telegram_alert(
                        text=f"⏰ <b>НАПОМИНАНИЕ: Лид согласился на квест!</b>\nСрочно напишите ему в Telegram: {link_str}{username_str}",
                        admin_tg_id=owner_tg_id,
                        reply_markup=kb,
                        disable_notification=False,
                    )
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(_alarm_loop())
        cls._active_alarms[contact_id] = task
        return True

    @classmethod
    def stop_quest_alarm(cls, contact_id: int):
        """Stops the recurring alarm when the owner accepts the lead."""
        task = cls._active_alarms.pop(contact_id, None)
        if task and not task.done():
            task.cancel()

    @classmethod
    async def send_insult_alert(
        cls,
        owner_tg_id: int,
        contact_name: str,
        contact_username: Optional[str],
        contact_tg_id: int,
        user_message: str,
        analysis_reason: str,
        contact_id: int,
        has_voice: bool = False,
        voice_duration: int = 0,
        voice_file_id: Optional[str] = None,
    ) -> bool:
        """
        Sends context-aware alert to subscription owner when a potential insult/toxicity is identified.
        The bot DOES NOT block the user automatically.
        Owner is presented with instant action buttons:
        - [✅ Продолжить диалог]
        - [⛔️ Заблокировать]
        - [🗑 Удалить диалог]
        - [🎙 Послушать войс] (if voice note was sent)
        """
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%H:%M")
        link_str = f'<a href="tg://user?id={contact_tg_id}">{contact_name}</a>'
        username_str = f" (@{contact_username})" if contact_username else ""

        text = (
            f"⚠️ <b>Обнаружено потенциальное оскорбление!</b>\n\n"
            f"👤 <b>Клиент:</b> {link_str}{username_str}\n"
            f"🆔 <b>ID:</b> <code>{contact_tg_id}</code>\n"
            f"📅 <b>Время:</b> {now_str}\n\n"
            f"💬 <b>Сообщение:</b>\n"
            f"«<i>{user_message}</i>»\n\n"
            f"🧠 <b>Анализ:</b>\n"
            f"{analysis_reason}\n\n"
            f"<i>Бот продолжает диалог. Выберите действие:</i>"
        )

        buttons = [
            [
                {"text": "✅ Продолжить диалог", "callback_data": f"lead_action_continue_{contact_id}"},
                {"text": "⛔️ Заблокировать", "callback_data": f"lead_action_block_{contact_id}"},
            ],
            [
                {"text": "🗑 Удалить диалог", "callback_data": f"lead_action_delete_{contact_id}"},
            ]
        ]
        if has_voice and voice_file_id:
            buttons.append([{"text": f"🎙 Послушать войс ({voice_duration}с)", "callback_data": f"lead_play_voice_{contact_id}"}])

        kb = {"inline_keyboard": buttons}
        return await cls.send_telegram_alert(
            text=text,
            admin_tg_id=owner_tg_id,
            reply_markup=kb,
            disable_notification=False,
        )

    @staticmethod
    def format_meeting_handoff_alert(
        handoff_payload: Dict[str, Any],
        contact_name: str = "Собеседник",
        contact_username: Optional[str] = None,
        conversation_context: str = "",
    ) -> str:
        """Formats the structured MEETING HANDOFF notification for the owner."""
        user_id = handoff_payload.get("user_id") or handoff_payload.get("chat_id") or ""
        username_str = f" (@{contact_username})" if contact_username else ""
        user_ident = f"{contact_name}{username_str} [ID: {user_id}]".strip()

        curr_city = handoff_payload.get("current_city") or handoff_payload.get("city") or "не указан"
        meet_city = handoff_payload.get("meeting_city") or handoff_payload.get("city") or curr_city
        act_type = handoff_payload.get("activity_type") or "cinema/quest"
        venue = handoff_payload.get("venue") or "не выбрано"
        item_name = handoff_payload.get("activity") or act_type
        m_date = handoff_payload.get("date") or "не уточнена"
        m_time = handoff_payload.get("time") or "не уточнено"
        p_conn = handoff_payload.get("persona_connection") or "нет"
        p_trip = handoff_payload.get("persona_trip") or "нет"
        reason = handoff_payload.get("reason") or "booking/link required"

        lines = [
            "<b>MEETING HANDOFF</b>",
            "",
            f"User: {user_ident}",
            f"Current city: {curr_city}",
            f"Meeting city: {meet_city}",
            "",
            f"Activity: {act_type}",
            f"Venue: {venue}",
            f"Film/Quest: {item_name}",
            "",
            f"Date: {m_date}",
            f"Time: {m_time}",
            "",
            f"Persona connection:\n{p_conn}",
            "",
            f"Persona trip:\n{p_trip}",
            "",
            f"Reason for handoff:\n{reason}",
        ]
        if conversation_context:
            lines.extend(["", f"Conversation context:\n<i>{conversation_context}</i>"])
        return "\n".join(lines)

    @classmethod
    async def send_meeting_handoff_alert(
        cls,
        owner_tg_id: int,
        handoff_payload: Dict[str, Any],
        contact_name: str = "Собеседник",
        contact_username: Optional[str] = None,
        contact_id: Optional[int] = None,
        conversation_context: str = "",
    ) -> bool:
        """Sends the MEETING HANDOFF notification to the owner."""
        text = cls.format_meeting_handoff_alert(
            handoff_payload=handoff_payload,
            contact_name=contact_name,
            contact_username=contact_username,
            conversation_context=conversation_context,
        )
        kb = None
        if contact_id:
            kb = {
                "inline_keyboard": [
                    [{"text": "✅ Принять лида (Управление встречей)", "callback_data": f"lead_accept_{contact_id}"}]
                ]
            }
        return await cls.send_telegram_alert(
            text=text,
            admin_tg_id=owner_tg_id,
            reply_markup=kb,
            disable_notification=False,
        )

