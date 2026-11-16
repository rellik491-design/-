import pytest
import random
from unittest.mock import AsyncMock
from companion.models import init_db, async_session_maker, User, Contact, ContactStatus
from companion.bot.telegram_bot_engine import TelegramBotEngine
from companion.bot.admin_notifier import AdminNotifier
from companion.services.stats_service import StatsService
from sqlalchemy import select

@pytest.mark.asyncio
async def test_lead_alarm_has_mute_and_accept_buttons():
    AdminNotifier.send_telegram_alert = AsyncMock(return_value=True)
    AdminNotifier._active_alarms.clear()

    # Trigger alarm
    await AdminNotifier.send_quest_agreed_alarm(
        owner_tg_id=123456,
        contact_name="Тестовый Лид",
        contact_username="test_lead",
        contact_tg_id=987654,
        user_message="Давай сходим на квест!",
        contact_id=9999,
    )

    assert AdminNotifier.is_alarm_active(9999) is True
    # Verify buttons passed to send_telegram_alert
    assert AdminNotifier.send_telegram_alert.called
    _, kwargs = AdminNotifier.send_telegram_alert.call_args
    kb = kwargs["reply_markup"]["inline_keyboard"]
    callbacks = [btn["callback_data"] for row in kb for btn in row]
    assert "lead_accept_9999" in callbacks
    assert "lead_mute_9999" in callbacks

    # Cleanup alarm
    AdminNotifier.stop_quest_alarm(9999)
    assert AdminNotifier.is_alarm_active(9999) is False


@pytest.mark.asyncio
async def test_lead_mute_callback_stops_alarm_and_keeps_ai_active():
    await init_db()
    uid = random.randint(800000000, 899999999)
    bot = TelegramBotEngine("mock_token", uid)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    async with async_session_maker() as session:
        u = User(telegram_id=uid, username=f"owner_{uid}")
        session.add(u)
        await session.commit()
        await session.refresh(u)

        c = Contact(
            user_id=u.id,
            tg_id=random.randint(600000000, 699999999),
            username=f"pending_man_{uid}",
            first_name="Алексей",
            funnel_stage="quest_agreed",
            status=ContactStatus.ACTIVE,
            ai_active=True,
            bot_paused=False,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        contact_id = c.id

    # Start mock alarm
    AdminNotifier._active_alarms[contact_id] = AsyncMock()

    cq = {
        "id": "cq_mute_1",
        "data": f"lead_mute_{contact_id}",
        "message": {"message_id": 101, "chat": {"id": uid}},
        "from": {"id": uid, "username": f"owner_{uid}"},
    }
    await bot.handle_callback(cq)

    # Alarm must be stopped
    assert AdminNotifier.is_alarm_active(contact_id) is False

    # Check contact in database
    async with async_session_maker() as session:
        stmt = select(Contact).where(Contact.id == contact_id)
        updated = (await session.execute(stmt)).scalars().first()
        assert updated.quest_alarm_muted is True
        assert updated.status == ContactStatus.LEAD_MEETING
        assert updated.ai_active is True
        assert updated.bot_paused is False


@pytest.mark.asyncio
async def test_leads_categorization_and_views():
    await init_db()
    uid = random.randint(800000000, 899999999)
    async with async_session_maker() as session:
        u = User(telegram_id=uid, username=f"owner_{uid}")
        session.add(u)
        await session.commit()
        await session.refresh(u)

        # 1. Pending lead (has reminder / awaiting decision)
        c_pending = Contact(
            user_id=u.id,
            tg_id=random.randint(600000000, 699999999),
            username=f"quest_guy_{uid}",
            first_name="Денис",
            funnel_stage="quest_agreed",
            status=ContactStatus.LEAD_MEETING,
            ai_active=True,
            bot_paused=False,
            alert_quest_sent=True,
        )
        # 2. Accepted lead (taken over by owner)
        c_accepted = Contact(
            user_id=u.id,
            tg_id=random.randint(600000000, 699999999),
            username=f"accepted_guy_{uid}",
            first_name="Игорь",
            funnel_stage="quest_agreed",
            status=ContactStatus.LEAD_MEETING,
            ai_active=False,
            bot_paused=True,
            paused_reason="quest_lead_accepted_by_owner",
            alert_quest_sent=True,
        )
        session.add_all([c_pending, c_accepted])
        await session.commit()

        leads = await StatsService.get_leads(session, u.id)
        assert len(leads) >= 2

        # Check categorization
        pending_list = [l for l in leads if not l["is_accepted"]]
        accepted_list = [l for l in leads if l["is_accepted"]]
        assert any(l["username"] == f"quest_guy_{uid}" for l in pending_list)
        assert any(l["username"] == f"accepted_guy_{uid}" for l in accepted_list)

        # Test view formatting
        text_all, kb_all = TelegramBotEngine.build_leads_view(leads, filter_mode="all")
        assert "НА КОМ НАПОМИНАНИЕ / ОЖИДАЮТ" in text_all
        assert "КОГО ЗАБРАЛИ" in text_all
        assert "Денис" in text_all
        assert "Игорь" in text_all

        # Check buttons in view
        callbacks = [btn["callback_data"] for row in kb_all["inline_keyboard"] for btn in row]
        assert "menu_leads_pending" in callbacks
        assert "menu_leads_accepted" in callbacks
        assert any(cb.startswith("lead_accept_") for cb in callbacks)
        assert any(cb.startswith("lead_resume_") for cb in callbacks)
