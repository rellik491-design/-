import pytest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Contact
from companion.models.enums import ContactStatus
from companion.services.intent_service import IntentService
from companion.services.handover_service import HandoverService


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_process_meetup_proposal_handover(test_session: AsyncSession):
    user = User(telegram_id=111, username="owner")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    contact = Contact(
        user_id=user.id,
        tg_id=777,
        username="misha",
        first_name="Михаил",
        status=ContactStatus.ACTIVE,
        bot_paused=False,
    )
    test_session.add(contact)
    await test_session.commit()
    await test_session.refresh(contact)

    raw_proposal = "Давай поужинаем сегодня в ресторане?"
    intent = IntentService.detect_meetup_proposal(raw_proposal)
    assert intent.is_proposal is True

    alert = await HandoverService.process_meetup_proposal(
        session=test_session,
        contact=contact,
        intent=intent,
        raw_message=raw_proposal,
        auto_pause=True,
    )

    assert contact.status == ContactStatus.LEAD_MEETING
    assert contact.bot_paused is True
    assert "meetup_proposal" in contact.paused_reason
    assert alert["type"] == "lead_meetup_alert"
    assert alert["tg_id"] == 777


@pytest.mark.asyncio
async def test_manual_pause_and_resume(test_session: AsyncSession):
    user = User(telegram_id=222, username="owner2")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    contact = Contact(
        user_id=user.id,
        tg_id=888,
        status=ContactStatus.ACTIVE,
        bot_paused=False,
    )
    test_session.add(contact)
    await test_session.commit()
    await test_session.refresh(contact)

    # Pause manually
    await HandoverService.set_contact_pause_state(test_session, contact.id, paused=True, reason="manual")
    assert contact.bot_paused is True
    assert contact.status == ContactStatus.PAUSED_MANUAL

    # Resume
    await HandoverService.set_contact_pause_state(test_session, contact.id, paused=False)
    assert contact.bot_paused is False
    assert contact.status == ContactStatus.ACTIVE
