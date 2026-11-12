import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Subscription
from companion.models.enums import PlanDuration
from companion.services.subscription_service import SubscriptionService


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_activate_and_extend_plans(test_session: AsyncSession):
    user = User(telegram_id=12345, username="test_sub_user")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Activate 3-day plan
    sub_3 = await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_3, now=now)
    assert sub_3.is_active is True
    assert sub_3.days_remaining(now=now) == 3

    # 2. Extend with 7-day plan
    sub_extended = await SubscriptionService.activate_plan(
        test_session, user.id, PlanDuration.DAYS_7, now=now
    )
    # Total remaining should be 3 + 7 = 10 days
    assert sub_extended.days_remaining(now=now) == 10


@pytest.mark.asyncio
async def test_check_and_expire_subscriptions(test_session: AsyncSession):
    user = User(telegram_id=54321, username="expired_user")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    # Sub expired yesterday
    sub = Subscription(
        user_id=user.id,
        plan=PlanDuration.DAYS_3,
        starts_at=now - timedelta(days=4),
        expires_at=now - timedelta(days=1),
        is_active=True,
    )
    test_session.add(sub)
    await test_session.commit()

    expired_ids = await SubscriptionService.check_and_expire_subscriptions(test_session, now=now)
    assert user.id in expired_ids
    assert sub.is_active is False
