from datetime import datetime, timezone
from typing import Dict, Any, List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models.entities import Contact, DailyStat
from companion.models.enums import ContactStatus


class StatsService:
    @classmethod
    async def get_overview(cls, session: AsyncSession, user_id: int) -> Dict[str, Any]:
        """
        Calculates aggregate metrics for the user:
        - Total contacts
        - Active contacts
        - Successful leads (meetup invitations)
        - Paused chats
        - Total incoming & outgoing messages
        """
        # Count contacts by status
        stmt_contacts = select(Contact).where(Contact.user_id == user_id)
        res_contacts = await session.execute(stmt_contacts)
        all_contacts = res_contacts.scalars().all()

        total_contacts = len(all_contacts)
        active_count = sum(1 for c in all_contacts if c.status == ContactStatus.ACTIVE and not c.bot_paused)
        leads_count = sum(
            1 for c in all_contacts
            if c.status == ContactStatus.LEAD_MEETING
            or getattr(c, "funnel_stage", "") in ("quest_agreed", "date_proposed", "quest_offered")
            or getattr(c, "alert_quest_sent", False)
            or getattr(c, "alert_date_sent", False)
        )
        paused_count = sum(1 for c in all_contacts if c.bot_paused)

        total_in = sum(c.total_messages_in for c in all_contacts)
        total_out = sum(c.total_messages_out for c in all_contacts)

        # Calculate conversion rate: leads / total contacts
        conversion_rate = round((leads_count / total_contacts * 100), 1) if total_contacts > 0 else 0.0

        return {
            "total_contacts": total_contacts,
            "active_dialogues": active_count,
            "successful_leads": leads_count,
            "paused_dialogues": paused_count,
            "total_messages_in": total_in,
            "total_messages_out": total_out,
            "conversion_rate_percent": conversion_rate,
        }

    @classmethod
    async def get_leads(cls, session: AsyncSession, user_id: int) -> List[Dict[str, Any]]:
        """
        Fetches all hot leads (contacts that proposed a meetup/date or agreed to quest)
        so the owner can follow up directly. Categorizes them into 'pending_reminder' and 'accepted'.
        """
        from sqlalchemy import or_, desc
        from companion.bot.admin_notifier import AdminNotifier

        stmt = (
            select(Contact)
            .where(
                Contact.user_id == user_id,
                or_(
                    Contact.status == ContactStatus.LEAD_MEETING,
                    Contact.funnel_stage.in_(["quest_agreed", "date_proposed", "quest_offered"]),
                    Contact.alert_quest_sent == True,
                    Contact.alert_date_sent == True,
                    Contact.paused_reason.like("%quest%"),
                    Contact.paused_reason.like("%lead%"),
                    Contact.paused_reason.like("%owner_manual_takeover%"),
                ),
            )
            .order_by(
                desc(Contact.lead_detected_at.isnot(None)),
                desc(Contact.lead_detected_at),
                desc(Contact.last_incoming_at),
                desc(Contact.id),
            )
        )
        res = await session.execute(stmt)
        leads = res.scalars().all()

        results = []
        for c in leads:
            tg_link = f"https://t.me/{c.username}" if c.username else f"tg://user?id={c.tg_id}"
            is_ai = getattr(c, "ai_active", not c.bot_paused)
            is_accepted = bool(not is_ai or c.bot_paused or (c.paused_reason and ("accepted" in c.paused_reason or "owner" in c.paused_reason)))
            is_alarm = AdminNotifier.is_alarm_active(c.id)
            is_muted = getattr(c, "quest_alarm_muted", False)

            summary = c.lead_summary
            if not summary:
                if getattr(c, "funnel_stage", "") == "quest_agreed" or getattr(c, "alert_quest_sent", False):
                    summary = "Согласился на квест 🎯"
                elif getattr(c, "funnel_stage", "") == "date_proposed" or getattr(c, "alert_date_sent", False):
                    summary = "Предложение встречи ☕"
                else:
                    summary = "Предложение встречи / квеста"

            time_val = c.lead_detected_at or c.last_incoming_at
            detected_str = time_val.strftime("%d.%m %H:%M") if time_val else None

            results.append({
                "id": c.id,
                "contact_id": c.id,
                "tg_id": c.tg_id,
                "name": c.first_name or c.username or f"User_{c.tg_id}",
                "username": c.username,
                "tg_link": tg_link,
                "city": c.detected_city,
                "detected_at": detected_str,
                "proposal_summary": summary,
                "bot_paused": c.bot_paused,
                "ai_active": is_ai,
                "is_accepted": is_accepted,
                "is_alarm_active": is_alarm,
                "quest_alarm_muted": is_muted,
                "category": "accepted" if is_accepted else "pending_reminder",
            })
        return results

    @classmethod
    async def get_all_contacts(cls, session: AsyncSession, user_id: int) -> List[Dict[str, Any]]:
        """Lists all contacts with their Telegram IDs, statuses, and city info."""
        stmt = select(Contact).where(Contact.user_id == user_id).order_by(Contact.last_incoming_at.desc())
        res = await session.execute(stmt)
        contacts = res.scalars().all()

        return [
            {
                "id": c.id,
                "tg_id": c.tg_id,
                "name": c.first_name or c.username or f"ID_{c.tg_id}",
                "username": c.username,
                "status": c.status.value,
                "city": c.detected_city,
                "bot_paused": c.bot_paused,
                "messages_in": c.total_messages_in,
                "messages_out": c.total_messages_out,
            }
            for c in contacts
        ]

    @classmethod
    async def get_insult_leads(cls, session: AsyncSession, user_id: int) -> List[Contact]:
        """Fetches all contacts flagged for insults or rude messages."""
        from sqlalchemy import or_
        stmt = (
            select(Contact)
            .where(
                Contact.user_id == user_id,
                or_(Contact.has_violation == True, Contact.violation_count > 0)
            )
            .order_by(Contact.last_violation_at.desc(), Contact.id.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_chat_leads(cls, session: AsyncSession, user_id: int) -> List[Contact]:
        """Fetches all contacts with active or previous conversation history."""
        stmt = (
            select(Contact)
            .where(Contact.user_id == user_id)
            .order_by(Contact.last_incoming_at.desc(), Contact.id.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_detailed_leads_stats(
        cls, session: AsyncSession, user_id: int, period: str = "today"
    ) -> Dict[str, Any]:
        """
        Calculates comprehensive lead and conversation metrics for requested period:
        'today', 'yesterday', '7d', '30d'.
        """
        from datetime import timedelta, date, time
        now_utc = datetime.now(timezone.utc)

        if period == "yesterday":
            yesterday_date = now_utc.date() - timedelta(days=1)
            start_dt = datetime.combine(yesterday_date, time.min, tzinfo=timezone.utc)
            end_dt = datetime.combine(yesterday_date, time.max, tzinfo=timezone.utc)
            period_label = "За вчера"
        elif period == "7d":
            start_dt = now_utc - timedelta(days=7)
            end_dt = now_utc
            period_label = "За 7 дней"
        elif period == "30d":
            start_dt = now_utc - timedelta(days=30)
            end_dt = now_utc
            period_label = "За 30 дней"
        else:  # today
            today_date = now_utc.date()
            start_dt = datetime.combine(today_date, time.min, tzinfo=timezone.utc)
            end_dt = now_utc
            period_label = "За сегодня"

        # Fetch contacts for user
        contacts = await cls.get_chat_leads(session, user_id)

        # Query messages in interval
        from companion.models.entities import ContactMessage
        contact_ids = [c.id for c in contacts]
        msgs_in_period = []
        if contact_ids:
            stmt_msgs = (
                select(ContactMessage)
                .where(
                    ContactMessage.contact_id.in_(contact_ids),
                    ContactMessage.created_at >= start_dt.replace(tzinfo=None),
                    ContactMessage.created_at <= end_dt.replace(tzinfo=None),
                )
            )
            res_msgs = await session.execute(stmt_msgs)
            msgs_in_period = list(res_msgs.scalars().all())

        # Leads active in period
        active_contact_ids = {m.contact_id for m in msgs_in_period}
        period_contacts = [c for c in contacts if c.id in active_contact_ids or (c.created_at and c.created_at.replace(tzinfo=timezone.utc) >= start_dt)]
        if not period_contacts and period == "today":
            # If no new messages today yet, show all recent contacts
            period_contacts = contacts[:10]

        total_leads = len(period_contacts)
        new_clients = sum(
            1 for c in period_contacts
            if c.created_at and c.created_at.replace(tzinfo=timezone.utc) >= start_dt
        )
        active_dialogues = sum(
            1 for c in period_contacts
            if c.status == ContactStatus.ACTIVE and c.ai_active and not c.bot_paused
        )
        completed_dialogues = sum(
            1 for c in period_contacts
            if c.status == ContactStatus.STALE
            or (c.last_incoming_at and (now_utc - c.last_incoming_at.replace(tzinfo=timezone.utc)).total_seconds() > 86400)
        )
        blocked_clients = sum(
            1 for c in period_contacts
            if c.status == ContactStatus.BLOCKED or not c.ai_active or c.bot_paused
        )
        insult_leads = sum(
            1 for c in period_contacts
            if c.has_violation or (c.violation_count and c.violation_count > 0)
        )

        in_user_msgs = [m for m in msgs_in_period if m.sender_role == "user"]
        in_text_count = sum(1 for m in in_user_msgs if not m.is_voice)
        in_voice_count = sum(1 for m in in_user_msgs if m.is_voice)
        voice_transcribed_count = in_voice_count  # All incoming voices are transcribed via STT
        avg_messages = round(len(msgs_in_period) / total_leads, 1) if total_leads > 0 else 0.0

        lead_details = []
        for c in period_contacts:
            c_msgs = [m for m in msgs_in_period if m.contact_id == c.id]
            c_in = sum(1 for m in c_msgs if m.sender_role == "user")
            c_voice = sum(1 for m in c_msgs if m.sender_role == "user" and m.is_voice)

            status_str = "Активен"
            if c.status == ContactStatus.BLOCKED or not c.ai_active:
                status_str = "Заблокирован"
            elif c.bot_paused:
                status_str = "На паузе"

            owner_decision = "Продолжить" if c.ai_active else "Заблокирован"

            lead_details.append({
                "id": c.id,
                "name": c.first_name or "Без имени",
                "username": c.username or "",
                "tg_id": c.tg_id,
                "first_contact": c.created_at.strftime("%d.%m.%Y %H:%M") if c.created_at else "-",
                "last_message_time": c.last_incoming_at.strftime("%d.%m.%Y %H:%M") if c.last_incoming_at else "-",
                "total_msgs": len(c_msgs) or (c.total_messages_in + c.total_messages_out),
                "voice_msgs": c_voice,
                "tags": c.tags or "🟢 Активный",
                "status": status_str,
                "has_insult": "Да" if (c.has_violation or c.violation_count > 0) else "Нет",
                "summary": c.dialogue_summary or c.lead_summary or "Обычное общение",
                "topics": c.dialogue_topics or "знакомство",
                "last_ai_action": "Отправлен ответ" if c.ai_active else "ИИ остановлен",
                "owner_decision": owner_decision,
            })

        return {
            "period": period,
            "period_label": period_label,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "total_leads": total_leads,
            "new_clients": new_clients,
            "active_dialogues": active_dialogues,
            "completed_dialogues": completed_dialogues,
            "blocked_clients": blocked_clients,
            "insult_leads": insult_leads,
            "incoming_text_count": in_text_count,
            "incoming_voice_count": in_voice_count,
            "voice_transcribed_count": voice_transcribed_count,
            "avg_messages_per_client": avg_messages,
            "leads": lead_details,
        }

    @classmethod
    async def generate_leads_csv(cls, session: AsyncSession, user_id: int, period: str = "today") -> bytes:
        """
        Generates CSV export with UTF-8 BOM encoding for seamless Microsoft Excel opening.
        """
        import csv
        import io

        stats = await cls.get_detailed_leads_stats(session, user_id, period)
        output = io.StringIO()
        # UTF-8 BOM
        output.write("\ufeff")

        writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        # Header row
        writer.writerow([
            "ID",
            "Имя",
            "Username",
            "Telegram ID",
            "Дата первого контакта",
            "Время последнего сообщения",
            "Сообщений за период",
            "Голосовых сообщений",
            "Теги клиента",
            "Статус общения",
            "Наличие оскорблений",
            "Краткое содержание диалога",
            "Основные темы",
            "Последнее действие нейросети",
            "Решение владельца",
        ])

        for l in stats["leads"]:
            writer.writerow([
                l["id"],
                l["name"],
                f"@{l['username']}" if l["username"] else "нет",
                l["tg_id"],
                l["first_contact"],
                l["last_message_time"],
                l["total_msgs"],
                l["voice_msgs"],
                l["tags"],
                l["status"],
                l["has_insult"],
                l["summary"],
                l["topics"],
                l["last_ai_action"],
                l["owner_decision"],
            ])

        return output.getvalue().encode("utf-8")

