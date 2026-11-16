import asyncio
import io
import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, AccountSession, Persona, PersonaPhoto, AccountStatus
from companion.services.photo_subsystem import (
    PhotoRepository,
    PhotoUploadManager,
    PhotoEligibilityService,
    PhotoCooldownService,
    PhotoSelector,
    PhotoSendService,
)
from companion.services.photo_pool_service import PhotoPoolService


def create_test_image(pattern: str = "squares") -> bytes:
    img = Image.new("RGB", (120, 120), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    if pattern == "left":
        draw.rectangle([0, 0, 60, 120], fill=(20, 40, 200))
    elif pattern == "right":
        draw.rectangle([60, 0, 120, 120], fill=(200, 40, 20))
    elif pattern == "top":
        draw.rectangle([0, 0, 120, 60], fill=(40, 200, 40))
    elif pattern == "bottom":
        draw.rectangle([0, 60, 120, 120], fill=(180, 180, 30))
    else:
        draw.ellipse([20, 20, 100, 100], fill=(120, 30, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
async def test_session():
    test_db_url = f"sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(test_db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_account_photo_isolation(test_session: AsyncSession):
    # 1. Setup Owner User
    user = User(telegram_id=8541203642, username="owner")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    # 2. Setup Persona A & Model Account A (e.g. Karina)
    persona_a = Persona(user_id=user.id, name="Карина", age=26, city="London")
    test_session.add(persona_a)
    await test_session.commit()
    await test_session.refresh(persona_a)

    acc_a = AccountSession(
        user_id=user.id,
        persona_id=persona_a.id,
        first_name="Карина",
        username="karinabrit1",
        tg_user_id=11111,
        status=AccountStatus.CONNECTED,
    )
    test_session.add(acc_a)

    # 3. Setup Persona B & Model Account B (e.g. Alisa)
    persona_b = Persona(user_id=user.id, name="Алиса", age=21, city="Москва")
    test_session.add(persona_b)
    await test_session.commit()
    await test_session.refresh(persona_b)

    acc_b = AccountSession(
        user_id=user.id,
        persona_id=persona_b.id,
        first_name="Алиса",
        username="alisabest",
        tg_user_id=22222,
        status=AccountStatus.CONNECTED,
    )
    test_session.add(acc_b)
    await test_session.commit()

    # 4. Upload 2 photos for Model A (Karina)
    img_a1 = create_test_image("left")
    img_a2 = create_test_image("right")
    photo_a1, is_new_a1 = await PhotoPoolService.add_photo(
        session=test_session,
        file_bytes=img_a1,
        filename="karina_1.jpg",
        category="selfie",
        tags=["london", "home"],
        user_id=user.id,
        persona_id=persona_a.id,
    )
    assert is_new_a1 is True
    assert photo_a1.persona_id == persona_a.id

    photo_a2, is_new_a2 = await PhotoPoolService.add_photo(
        session=test_session,
        file_bytes=img_a2,
        filename="karina_2.jpg",
        category="casual",
        tags=["walk"],
        user_id=user.id,
        persona_id=persona_a.id,
    )
    assert is_new_a2 is True
    assert photo_a2.persona_id == persona_a.id

    # 5. Upload 1 photo for Model B (Alisa)
    img_b1 = create_test_image("top")
    photo_b1, is_new_b1 = await PhotoPoolService.add_photo(
        session=test_session,
        file_bytes=img_b1,
        filename="alisa_1.jpg",
        category="coffee",
        tags=["cafe"],
        user_id=user.id,
        persona_id=persona_b.id,
    )
    assert is_new_b1 is True
    assert photo_b1.persona_id == persona_b.id

    # 6. Verify Persona Photo Stats Isolation
    stats_a = await PhotoPoolService.get_persona_photo_stats(test_session, persona_a.id)
    assert stats_a["total_photos"] == 2
    assert stats_a["active_photos"] == 2
    assert "selfie" in stats_a["categories"]
    assert "casual" in stats_a["categories"]
    assert "coffee" not in stats_a["categories"]

    stats_b = await PhotoPoolService.get_persona_photo_stats(test_session, persona_b.id)
    assert stats_b["total_photos"] == 1
    assert stats_b["active_photos"] == 1
    assert "coffee" in stats_b["categories"]
    assert "selfie" not in stats_b["categories"]

    # 7. Verify List Photos by Persona
    photos_a = await PhotoPoolService.list_photos(test_session, persona_id=persona_a.id)
    assert len(photos_a) == 2
    assert all(p.persona_id == persona_a.id for p in photos_a)

    photos_b = await PhotoPoolService.list_photos(test_session, persona_id=persona_b.id)
    assert len(photos_b) == 1
    assert photos_b[0].persona_id == persona_b.id

    # 8. Test Photo Selection for Model A in Chat 1001
    chat_id_a = 1001
    selected_a = await PhotoSelector.select_and_reserve(
        session=test_session,
        chat_id=chat_id_a,
        persona_id=persona_a.id,
    )
    assert selected_a is not None
    assert selected_a.persona_id == persona_a.id
    assert selected_a.photo_id in (photo_a1.photo_id, photo_a2.photo_id)

    # 9. Test Photo Selection for Model B in Chat 2001
    chat_id_b = 2001
    selected_b = await PhotoSelector.select_and_reserve(
        session=test_session,
        chat_id=chat_id_b,
        persona_id=persona_b.id,
    )
    assert selected_b is not None
    assert selected_b.persona_id == persona_b.id
    assert selected_b.photo_id == photo_b1.photo_id

    # 10. Test Toggle Status on Model A photo
    await PhotoPoolService.toggle_photo_status(test_session, photo_a1.photo_id, False)
    stats_a_after = await PhotoPoolService.get_persona_photo_stats(test_session, persona_a.id)
    assert stats_a_after["total_photos"] == 2
    assert stats_a_after["active_photos"] == 1
    assert stats_a_after["disabled_photos"] == 1

    # 11. Test Clear Photos for Model A
    deleted_a = await PhotoPoolService.clear_persona_photos(test_session, persona_a.id, delete_files=False)
    assert deleted_a == 2

    # Verify Model A has 0 photos now
    stats_a_cleared = await PhotoPoolService.get_persona_photo_stats(test_session, persona_a.id)
    assert stats_a_cleared["total_photos"] == 0

    # Verify Model B photos are completely intact
    stats_b_cleared = await PhotoPoolService.get_persona_photo_stats(test_session, persona_b.id)
    assert stats_b_cleared["total_photos"] == 1
    photos_b_remaining = await PhotoPoolService.list_photos(test_session, persona_id=persona_b.id)
    assert len(photos_b_remaining) == 1
    assert photos_b_remaining[0].photo_id == photo_b1.photo_id


@pytest.mark.asyncio
async def test_legend_and_accounts_keyboard_markup():
    from companion.bot.telegram_bot_engine import TelegramBotEngine
    engine = TelegramBotEngine(token="123456:ABC-DEF", admin_id=8541203642)

    # Test build_legend_keyboard with account_id
    kb = engine.build_legend_keyboard(account_id=95, has_multiple_accounts=True, photo_count=3)
    rows = kb.get("inline_keyboard", [])
    all_callbacks = [btn["callback_data"] for row in rows for btn in row]
    all_texts = [btn["text"] for row in rows for btn in row]

    assert "photos_acc_95" in all_callbacks
    assert any("Фотографии модели (3 шт.)" in t for t in all_texts)

    # Test build_legend_keyboard without account_id (default template)
    kb_def = engine.build_legend_keyboard(account_id=None, has_multiple_accounts=False, photo_count=0)
    rows_def = kb_def.get("inline_keyboard", [])
    callbacks_def = [btn["callback_data"] for row in rows_def for btn in row]
    assert "photos_acc_default" in callbacks_def

    # Test format_legend_card includes photo line when provided
    p = Persona(name="Карина", age=26, city="London")
    card_text = engine.format_legend_card(p, photo_count=5)
    assert "Фотографии:" in card_text
    assert "5 шт." in card_text


@pytest.mark.asyncio
async def test_subscriber_can_access_and_upload_photos(test_session: AsyncSession):
    from companion.models.entities import Subscription, PlanDuration
    from companion.services.subscription_service import SubscriptionService

    # 1. Normal subscriber user (NOT admin)
    sub_user = User(telegram_id=9273502814, username="subscriber_user")
    test_session.add(sub_user)
    await test_session.commit()
    await test_session.refresh(sub_user)

    # 2. Give active 31-day subscription
    await SubscriptionService.activate_plan(test_session, sub_user.id, PlanDuration.DAYS_31)

    # 3. Model account for subscriber
    persona = Persona(user_id=sub_user.id, name="Карина", age=26, city="London")
    test_session.add(persona)
    await test_session.commit()
    await test_session.refresh(persona)

    acc = AccountSession(
        user_id=sub_user.id,
        persona_id=persona.id,
        first_name="Карина",
        username="karinabrit1",
        tg_user_id=999888,
        status=AccountStatus.CONNECTED,
    )
    test_session.add(acc)
    await test_session.commit()

    # 4. Verify PhotoUploadManager authorizes this subscriber
    is_auth = await PhotoUploadManager.verify_sender_authorization(test_session, sub_user.telegram_id)
    assert is_auth is True

    # 5. Non-subscriber without sub is NOT authorized
    non_sub = User(telegram_id=123999444, username="unpaid_user")
    test_session.add(non_sub)
    await test_session.commit()
    is_non_sub_auth = await PhotoUploadManager.verify_sender_authorization(test_session, non_sub.telegram_id)
    assert is_non_sub_auth is False

    # 6. Subscriber can upload photo with sender_tg_id
    img = create_test_image("top")
    photo, is_new = await PhotoUploadManager.upload_photo(
        session=test_session,
        file_bytes=img,
        filename="sub_photo.jpg",
        category="selfie",
        user_id=sub_user.id,
        persona_id=persona.id,
        sender_tg_id=sub_user.telegram_id,
    )
    assert is_new is True
    assert photo.persona_id == persona.id

    # 7. Unpaid user upload raises PermissionError
    with pytest.raises(PermissionError):
        await PhotoUploadManager.upload_photo(
            session=test_session,
            file_bytes=img,
            filename="unpaid.jpg",
            category="casual",
            user_id=non_sub.id,
            persona_id=persona.id,
            sender_tg_id=non_sub.telegram_id,
        )
