import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models import User, Persona, PlanDuration, ContactStatus
from companion.models.entities import Contact, AccountSession
from companion.services.subscription_service import SubscriptionService
from companion.services.styles_service import DialogueStyleEngine
from companion.services.ai_service import AIService
from companion.bot.business_bot import BusinessChatBot
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.telegram_bot_engine import TelegramBotEngine


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_anti_hallucination_cleans_bot_delusions(test_session: AsyncSession):
    user = User(telegram_id=987654321, username="test_girl")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    persona = Persona(user_id=user.id, name="Карина", city="Лондон")
    contact = Contact(user_id=user.id, tg_id=112233, first_name="Алекс")
    test_session.add(persona)
    test_session.add(contact)
    await test_session.commit()

    # If the user says a standard greeting, any accidental 'я не бот вообще-то' must be stripped
    cleaned = AIService.sanitize_anti_ai_delusions("привет да я не бот вообще-то)) ты как сам?", "здарова")
    assert "я не бот" not in cleaned
    assert "вообще-то" not in cleaned
    assert "ты как сам" in cleaned

    # If the model hallucinates "давно в нью-йорке живешь?", it must be sanitized
    geo_cleaned = AIService.sanitize_anti_ai_delusions(
        "а если серьезно, ты сам-то откуда родом давно в нью-йорке живешь?",
        "убираюсь в туалетах нарколаборатории, а кем ты работаешь голова?",
        contact=contact
    )
    assert "нью-йорк" not in geo_cleaned.lower()
    assert "ты сам-то откуда родом?" in geo_cleaned


@pytest.mark.asyncio
async def test_owner_manual_takeover_auto_resumes_after_15_minutes(test_session: AsyncSession):
    user = User(telegram_id=881122, username="owner_karina")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_31)

    now = datetime.now(timezone.utc)
    # Contact was paused by owner 20 minutes ago
    contact = Contact(
        user_id=user.id,
        tg_id=554433,
        first_name="Иван",
        bot_paused=True,
        ai_active=False,
        paused_reason="owner_manual_takeover",
        last_outgoing_at=now - timedelta(minutes=20),
    )
    test_session.add(contact)
    await test_session.commit()
    await test_session.refresh(contact)

    # When male contact sends a message after 20 minutes of owner inactivity
    res = await UserbotHandler.process_incoming_message(
        session=test_session,
        user_id=user.id,
        sender_tg_id=554433,
        sender_username="ivan_nsk",
        sender_first_name="Иван",
        text="Привет, как твои дела?",
        now=now,
    )

    # Contact must have been auto-resumed and received a reply!
    assert res["action"] == "reply"
    await test_session.refresh(contact)
    assert contact.bot_paused is False
    assert contact.ai_active is True
    assert contact.paused_reason is None


@pytest.mark.asyncio
async def test_recent_owner_takeover_within_15_minutes_remains_paused(test_session: AsyncSession):
    user = User(telegram_id=881123, username="owner_active")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_31)

    now = datetime.now(timezone.utc)
    # Owner typed 2 minutes ago
    contact = Contact(
        user_id=user.id,
        tg_id=554434,
        first_name="Сергей",
        bot_paused=True,
        ai_active=False,
        paused_reason="owner_manual_takeover",
        last_outgoing_at=now - timedelta(minutes=2),
    )
    test_session.add(contact)
    await test_session.commit()

    res = await UserbotHandler.process_incoming_message(
        session=test_session,
        user_id=user.id,
        sender_tg_id=554434,
        sender_username="sergey_active",
        sender_first_name="Сергей",
        text="Да, понял тебя",
        now=now,
    )

    # Must stay paused because owner is actively typing in chat
    assert res["action"] == "ignored"
    assert "owner_manual_takeover_recent" in res["reason"]


@pytest.mark.asyncio
async def test_business_connection_can_reply_and_instructions(test_session: AsyncSession):
    conn_data = {
        "id": "biz_conn_check_1",
        "user": {"id": 990011, "first_name": "Карина", "username": "karina_test"},
        "is_enabled": True,
        "can_reply": True,
    }
    res = await BusinessChatBot.process_business_connection(
        session=test_session,
        connection_data=conn_data,
        bot_token="mock_token",
    )
    assert res["status"] in ("connected", "no_subscription")
    assert res["can_reply"] is True


@pytest.mark.asyncio
async def test_media_without_caption_fallback(test_session: AsyncSession):
    user = User(telegram_id=990022, username="owner_media")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_31)

    acc = AccountSession(
        user_id=user.id,
        business_connection_id="biz_conn_photo_test",
        tg_user_id=990022,
        is_active=True,
    )
    test_session.add(acc)
    await test_session.commit()

    msg_photo = {
        "business_connection_id": "biz_conn_photo_test",
        "chat": {"id": 2002},
        "from": {"id": 2002, "first_name": "Дмитрий"},
        "photo": [{"file_id": "photo_123", "width": 100, "height": 100}],
        # No 'text' or 'caption' provided!
    }

    res = await BusinessChatBot.process_business_message(
        session=test_session,
        message_data=msg_photo,
        bot_token="mock_token",
    )

    # Must NOT be ignored with 'missing_fields'!
    assert res["action"] == "reply"
