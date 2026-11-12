import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Persona, Contact
from companion.models.enums import ContactStatus, PlanDuration
from companion.services.subscription_service import SubscriptionService
from companion.services.reengagement_service import ReengagementService
from companion.bot.userbot_handler import UserbotHandler


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_userbot_incoming_pipeline(test_session: AsyncSession):
    user = User(telegram_id=999, username="boss")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    # 1. Without subscription -> should be ignored
    res_no_sub = await UserbotHandler.process_incoming_message(
        session=test_session,
        user_id=user.id,
        sender_tg_id=123,
        sender_username="guy",
        sender_first_name="Павел",
        text="Привет!",
    )
    assert res_no_sub["action"] == "ignored"
    assert res_no_sub["reason"] == "subscription_expired"

    # 2. Activate 7-day subscription
    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_7)

    # 3. Normal conversation with city detection
    res_msg = await UserbotHandler.process_incoming_message(
        session=test_session,
        user_id=user.id,
        sender_tg_id=123,
        sender_username="guy",
        sender_first_name="Павел",
        text="Привет! Я из Казани, как дела?",
    )
    assert res_msg["action"] == "reply"
    contact = res_msg["contact"]
    assert contact.detected_city == "Казань"
    assert contact.detected_timezone == "Europe/Moscow"
    assert contact.is_initiated_by_user is True

    # 4. Meetup proposal -> triggers handover
    res_meetup = await UserbotHandler.process_incoming_message(
        session=test_session,
        user_id=user.id,
        sender_tg_id=123,
        sender_username="guy",
        sender_first_name="Павел",
        text="Давай встретимся на кофе в эти выходные?",
    )
    assert res_meetup["action"] == "handover_to_owner"
    assert contact.status == ContactStatus.LEAD_MEETING
    assert contact.bot_paused is True


@pytest.mark.asyncio
async def test_reengagement_candidate_filtering(test_session: AsyncSession):
    user = User(telegram_id=888, username="boss2")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    persona = Persona(
        user_id=user.id,
        timezone_name="Europe/Moscow",
        sleep_start="23:00",
        sleep_end="08:30",
    )
    test_session.add(persona)

    # Reference time: 14:00 Moscow time (UTC 11:00) - Daytime!
    now = datetime(2026, 10, 25, 11, 0, 0, tzinfo=timezone.utc)

    # Candidate 1: Inactivity 30 hours, daytime in Moscow -> Eligible
    c1 = Contact(
        user_id=user.id,
        tg_id=1,
        status=ContactStatus.ACTIVE,
        bot_paused=False,
        is_initiated_by_user=True,
        detected_timezone="Europe/Moscow",
        last_incoming_at=now - timedelta(hours=30),
    )
    # Candidate 2: Inactivity 30 hours, but Vladivostok (UTC+10). At 14:00 MSK it is 21:00 VVO.
    # If sleep starts at 21:00 or later, let's test a contact in Tokyo/Sydney where it's 01:00 night.
    # At 11:00 UTC, in Asia/Kamchatka (UTC+12) it is 23:00 (night!).
    c2 = Contact(
        user_id=user.id,
        tg_id=2,
        status=ContactStatus.ACTIVE,
        bot_paused=False,
        is_initiated_by_user=True,
        detected_timezone="Asia/Kamchatka",
        last_incoming_at=now - timedelta(hours=30),
    )
    # Candidate 3: Inactivity 30 hours, but cold stranger (is_initiated_by_user=False) -> NEVER PING!
    c3 = Contact(
        user_id=user.id,
        tg_id=3,
        status=ContactStatus.ACTIVE,
        bot_paused=False,
        is_initiated_by_user=False,
        detected_timezone="Europe/Moscow",
        last_incoming_at=now - timedelta(hours=30),
    )
    # Candidate 4: Paused chat -> Do not ping
    c4 = Contact(
        user_id=user.id,
        tg_id=4,
        status=ContactStatus.ACTIVE,
        bot_paused=True,
        is_initiated_by_user=True,
        detected_timezone="Europe/Moscow",
        last_incoming_at=now - timedelta(hours=30),
    )

    test_session.add_all([c1, c2, c3, c4])
    await test_session.commit()

    candidates = await ReengagementService.find_reengagement_candidates(
        session=test_session,
        user_id=user.id,
        persona=persona,
        min_hours=24,
        max_hours=72,
        now=now,
    )

    candidate_ids = [c.tg_id for c in candidates]
    assert 1 in candidate_ids, "Candidate 1 (daytime, warm) should be eligible"
    assert 2 not in candidate_ids, "Candidate 2 (Kamchatka night 23:00) should be protected from night ping"
    assert 3 not in candidate_ids, "Candidate 3 (stranger) should never be pinged"
    assert 4 not in candidate_ids, "Candidate 4 (paused) should not be pinged"
