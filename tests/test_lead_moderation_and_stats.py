import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.models import (
    User,
    Persona,
    Contact,
    ContactMessage,
    PlanDuration,
    ContactStatus,
)
from companion.services.moderation_service import ModerationService
from companion.services.stats_service import StatsService
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from companion.models.db import Base


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session



@pytest.mark.asyncio
async def test_moderation_service_context():
    # 1. Direct targeted insult -> Flagged
    is_violation, reason = await ModerationService.analyze_incoming_content(
        text="Слышь ты конченная дура пошла нахуй отсюда",
        is_voice=False,
    )
    assert is_violation is True
    assert len(reason) > 0

    # 2. Benign slang / expressive swearing -> NOT flagged
    is_violation2, _ = await ModerationService.analyze_incoming_content(
        text="Бля, ну ты даешь ахах, я сегодня на работе так устал, просто жесть",
        is_voice=False,
    )
    assert is_violation2 is False

    # 3. Clean polite text -> NOT flagged
    is_violation3, _ = await ModerationService.analyze_incoming_content(
        text="Привет! Чем занимаешься?",
        is_voice=False,
    )
    assert is_violation3 is False


@pytest.mark.asyncio
async def test_lead_non_blocking_moderation_flow(test_session: AsyncSession):
    # Setup user & persona
    user = User(telegram_id=777111, username="test_owner")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    from companion.services.subscription_service import SubscriptionService
    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_14)

    persona = Persona(user_id=user.id, name="Алина", is_active=True)
    test_session.add(persona)
    await test_session.commit()

    # Incoming message with abuse
    rude_text = "Ты мразь тупая закрой рот"
    res = await UserbotHandler.process_incoming_message(
        session=test_session,
        user_id=user.id,
        sender_tg_id=999888,
        sender_username="toxic_guy",
        sender_first_name="Вася",
        text=rude_text,
        is_voice=False,
    )

    # 1. Must NOT block or stop dialogue automatically!
    assert res["action"] == "reply"
    assert res["detected_violation"] is True

    # 2. Contact must be marked with violation & tag, but stay ACTIVE
    contact = res["contact"]
    assert contact.has_violation is True
    assert contact.violation_count == 1
    assert "🔴 Оскорбления" in contact.tags
    assert contact.status == ContactStatus.ACTIVE
    assert contact.bot_paused is False
    assert contact.ai_active is True

    # 3. Message must be stored in ContactMessage table
    stmt_m = select(ContactMessage).where(ContactMessage.contact_id == contact.id)
    msgs = (await test_session.execute(stmt_m)).scalars().all()
    assert len(msgs) >= 2  # 1 incoming user msg + 1 outgoing bot reply
    in_msg = msgs[0]
    assert in_msg.sender_role == "user"
    assert in_msg.is_violation is True
    assert in_msg.text == rude_text


@pytest.mark.asyncio
async def test_stats_service_leads_and_csv_generation(test_session: AsyncSession):
    user = User(telegram_id=888222, username="stats_owner")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    from companion.services.subscription_service import SubscriptionService
    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_14)

    now = datetime.now(timezone.utc)
    # Create 2 contacts: 1 normal, 1 with violation
    c1 = Contact(
        user_id=user.id,
        tg_id=101,
        first_name="Alex",
        username="alex_normal",
        status=ContactStatus.ACTIVE,
        total_messages_in=5,
        total_messages_out=5,
        last_incoming_at=now,
        created_at=now,
        tags="🟢 Активный",
        dialogue_summary="Обсуждение музыки и работы",
        dialogue_topics="музыка, работа",
    )
    c2 = Contact(
        user_id=user.id,
        tg_id=102,
        first_name="Max",
        username="max_rude",
        status=ContactStatus.ACTIVE,
        total_messages_in=3,
        total_messages_out=3,
        last_incoming_at=now,
        created_at=now,
        has_violation=True,
        violation_count=2,
        last_violation_text="Ты дура",
        last_violation_analysis="Прямое оскорбление",
        tags="🔴 Оскорбления, 🟢 Активный",
        dialogue_summary="Грубые высказывания",
        dialogue_topics="конфликт",
    )
    test_session.add_all([c1, c2])
    await test_session.commit()

    # Add messages
    m1 = ContactMessage(
        contact_id=c1.id, sender_role="user", text="Привет!", is_voice=False, created_at=now
    )
    m2 = ContactMessage(
        contact_id=c1.id, sender_role="user", text="Вот голосовое", is_voice=True, voice_duration=5, voice_file_id="fid_123", created_at=now
    )
    m3 = ContactMessage(
        contact_id=c2.id, sender_role="user", text="Ты дура", is_voice=False, is_violation=True, created_at=now
    )
    test_session.add_all([m1, m2, m3])
    await test_session.commit()

    # 1. Insult leads
    insult_leads = await StatsService.get_insult_leads(test_session, user.id)
    assert len(insult_leads) == 1
    assert insult_leads[0].tg_id == 102

    # 2. Detailed stats
    stats = await StatsService.get_detailed_leads_stats(test_session, user.id, period="today")
    assert stats["total_leads"] >= 2
    assert stats["insult_leads"] >= 1
    assert stats["incoming_voice_count"] >= 1

    # 3. CSV generation
    csv_bytes = await StatsService.generate_leads_csv(test_session, user.id, period="today")
    assert isinstance(csv_bytes, bytes)
    # Check UTF-8 BOM
    assert csv_bytes.startswith(b"\xef\xbb\xbf")
    decoded = csv_bytes.decode("utf-8")
    assert "Alex" in decoded
    assert "Max" in decoded
    assert "Telegram ID" in decoded
    assert "101" in decoded
    assert "102" in decoded


@pytest.mark.asyncio
async def test_bot_engine_leads_insults_and_chats_ui():
    from companion.models import init_db, async_session_maker
    from companion.bot.telegram_bot_engine import TelegramBotEngine
    await init_db()

    admin_id = 8501711204
    bot = TelegramBotEngine("mock_token", admin_id)
    bot.edit_message = AsyncMock(return_value=True)
    bot.send_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)
    bot.send_document = AsyncMock(return_value=True)
    bot.send_voice_by_file_id = AsyncMock(return_value=True)

    test_user_tg = {"id": admin_id, "username": "test_admin"}

    async with async_session_maker() as session:
        # Ensure user exists
        u = (await session.execute(select(User).where(User.telegram_id == admin_id))).scalars().first()
        if not u:
            u = User(telegram_id=admin_id, username="test_admin")
            session.add(u)
            await session.commit()
            await session.refresh(u)

        now = datetime.now(timezone.utc)
        # Create test contact with violation
        c = Contact(
            user_id=u.id,
            tg_id=555001,
            first_name="Дмитрий",
            username="dima_toxic",
            status=ContactStatus.ACTIVE,
            has_violation=True,
            violation_count=2,
            last_violation_text="Ты дура тупая",
            last_violation_analysis="Прямое оскорбление в переписке",
            tags="🔴 Оскорбления, 🟢 Активный",
            dialogue_summary="Начали знакомство, затем возник конфликт",
            dialogue_topics="конфликт",
            last_incoming_at=now,
            created_at=now,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        contact_id = c.id

        # Add message
        msg = ContactMessage(
            contact_id=contact_id,
            sender_role="user",
            text="Ты дура тупая",
            is_voice=False,
            is_violation=True,
            violation_analysis="Прямое оскорбление в переписке",
            created_at=now,
        )
        session.add(msg)
        await session.commit()

    # 1. Test 'menu_leads_insults' callback
    cq_insults = {
        "id": "cq_insults",
        "data": "menu_leads_insults",
        "message": {"message_id": 201, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_insults)
    assert bot.edit_message.called
    text = bot.edit_message.call_args[0][2]
    assert "ЛИДЫ (ОСКОРБЛЕНИЯ И ГРУБОСТЬ)" in text

    # 2. Test 'insult_card_{id}'
    cq_card = {
        "id": "cq_card",
        "data": f"insult_card_{contact_id}",
        "message": {"message_id": 202, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_card)
    card_text = bot.edit_message.call_args[0][2]
    assert "КАРТОЧКА ЛИДА (ОСКОРБЛЕНИЯ)" in card_text
    assert "Дмитрий" in card_text
    assert "Ты дура тупая" in card_text
    assert "Прямое оскорбление" in card_text

    # 3. Test 'menu_leads_chats'
    cq_chats = {
        "id": "cq_chats",
        "data": "menu_leads_chats",
        "message": {"message_id": 203, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_chats)
    chats_text = bot.edit_message.call_args[0][2]
    assert "ЛИДЫ (ОБЩЕНИЕ И ДИАЛОГИ)" in chats_text
    # Clients are displayed as individual buttons with statuses
    kb = bot.edit_message.call_args[1].get("reply_markup") if "reply_markup" in bot.edit_message.call_args[1] else bot.edit_message.call_args[0][3]
    btn_texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
    assert any("Дмитрий" in b for b in btn_texts)

    # 4. Test 'chat_card_{id}'
    cq_chat_card = {
        "id": "cq_chat_card",
        "data": f"chat_card_{contact_id}",
        "message": {"message_id": 204, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_chat_card)
    chat_card_text = bot.edit_message.call_args[0][2]
    assert "КАРТОЧКА КЛИЕНТА (ОБЩЕНИЕ)" in chat_card_text
    assert "Дмитрий" in chat_card_text

    # 5. Test 'chat_history_{id}'
    cq_hist = {
        "id": "cq_hist",
        "data": f"chat_history_{contact_id}",
        "message": {"message_id": 205, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_hist)
    hist_text = bot.edit_message.call_args[0][2]
    assert "ИСТОРИЯ ПЕРЕПИСКИ: Дмитрий" in hist_text
    assert "Ты дура тупая" in hist_text

    # 6. Test 'chat_tag_toggle_{id}_vip'
    cq_tag = {
        "id": "cq_tag",
        "data": f"chat_tag_toggle_{contact_id}_vip",
        "message": {"message_id": 206, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_tag)
    async with async_session_maker() as session:
        stmt_c = select(Contact).where(Contact.id == contact_id)
        c_refreshed = (await session.execute(stmt_c)).scalars().first()
        assert "⭐️ Приоритетный" in c_refreshed.tags

    # 7. Test 'menu_leads_stats_today'
    cq_stats = {
        "id": "cq_stats",
        "data": "menu_leads_stats_today",
        "message": {"message_id": 207, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_stats)
    stats_text = bot.edit_message.call_args[0][2]
    assert "СТАТИСТИКА ОБЩЕНИЯ С ЛИДАМИ" in stats_text
    assert "ОБЩИЕ ПОКАЗАТЕЛИ" in stats_text

    # 8. Test 'export_leads_csv_today'
    cq_export = {
        "id": "cq_export",
        "data": "export_leads_csv_today",
        "message": {"message_id": 208, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_export)
    assert bot.send_document.called
    doc_args = bot.send_document.call_args[0]
    doc_kwargs = bot.send_document.call_args[1]
    assert doc_args[0] == admin_id
    assert doc_args[1].startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    filename = doc_kwargs.get("filename") or (doc_args[2] if len(doc_args) > 2 else "")
    assert "leads_stats_today" in filename

    # 9. Test 'lead_action_continue_{id}'
    cq_cont = {
        "id": "cq_cont",
        "data": f"lead_action_continue_{contact_id}",
        "message": {"message_id": 209, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_cont)
    async with async_session_maker() as session:
        stmt_c = select(Contact).where(Contact.id == contact_id)
        c_refreshed = (await session.execute(stmt_c)).scalars().first()
        assert c_refreshed.status == ContactStatus.ACTIVE
        assert c_refreshed.ai_active is True
        assert c_refreshed.bot_paused is False

    # 10. Test 'lead_action_block_{id}'
    cq_block = {
        "id": "cq_block",
        "data": f"lead_action_block_{contact_id}",
        "message": {"message_id": 210, "chat": {"id": admin_id}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_block)
    async with async_session_maker() as session:
        stmt_c = select(Contact).where(Contact.id == contact_id)
        c_refreshed = (await session.execute(stmt_c)).scalars().first()
        assert c_refreshed.status == ContactStatus.BLOCKED
        assert c_refreshed.ai_active is False

