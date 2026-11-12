import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User
from companion.models.enums import PlanDuration, AccountStatus
from companion.services.subscription_service import SubscriptionService
from companion.services.account_service import AccountService


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_account_subscription_limits(test_session: AsyncSession):
    user = User(telegram_id=123456, username="multi_user")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    # 1. No subscription -> cannot add accounts
    can_add, reason, _ = await AccountService.can_add_more_accounts(test_session, user.id)
    assert can_add is False
    assert "нет активной подписки" in reason.lower()

    # 2. Activate 3-day plan (limit: 1 account)
    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_3)

    can_add_1, _, max_allowed = await AccountService.can_add_more_accounts(test_session, user.id)
    assert can_add_1 is True
    assert max_allowed == 1

    # Connect 1st account
    ok1, msg1, acc1 = await AccountService.register_account(
        test_session, user.id, "+79991112233", "mock_session_str_1"
    )
    assert ok1 is True
    assert acc1.status == AccountStatus.CONNECTED

    # Attempt to connect 2nd account on 1-account limit -> Should be rejected
    ok2, msg2, acc2 = await AccountService.register_account(
        test_session, user.id, "+79992223344", "mock_session_str_2"
    )
    assert ok2 is False
    assert "достигнут лимит" in msg2.lower()
    assert acc2 is None

    # 3. Upgrade to 14-day plan (limit: 2 accounts)
    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_14)

    # Now 2nd account can be connected!
    ok2_after, _, acc2_after = await AccountService.register_account(
        test_session, user.id, "+79992223344", "mock_session_str_2"
    )
    assert ok2_after is True
    assert acc2_after is not None

    # 3rd account on 2-account limit -> Rejected
    ok3, msg3, _ = await AccountService.register_account(
        test_session, user.id, "+79993334455", "mock_session_str_3"
    )
    assert ok3 is False


@pytest.mark.asyncio
async def test_toggle_account(test_session: AsyncSession):
    user = User(telegram_id=98765, username="toggle_user")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    await SubscriptionService.activate_plan(test_session, user.id, PlanDuration.DAYS_7)
    ok, _, acc = await AccountService.register_account(
        test_session, user.id, "+79997778899", "mock_session_str"
    )
    assert ok is True
    assert acc.is_active is True

    # Pause account
    acc_paused = await AccountService.toggle_account(test_session, acc.id, is_active=False)
    assert acc_paused.is_active is False
    assert acc_paused.status == AccountStatus.DISCONNECTED

    # Resume account
    acc_resumed = await AccountService.toggle_account(test_session, acc.id, is_active=True)
    assert acc_resumed.is_active is True
    assert acc_resumed.status == AccountStatus.CONNECTED
