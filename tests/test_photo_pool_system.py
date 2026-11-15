import io
import os
import pytest
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Persona, PersonaPhoto, ChatPhotoUsage
from companion.services.photo_pool_service import PhotoPoolService
from companion.services.intent_service import IntentService


def create_test_image(pattern_id: int = 1, size: tuple = (100, 100)) -> bytes:
    """Generates synthetic JPEG image bytes with distinct geometrical patterns for hashing."""
    img = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(img)
    if pattern_id == 1:
        draw.rectangle([0, 0, 50, 100], fill="black")
    elif pattern_id == 2:
        draw.rectangle([0, 0, 100, 50], fill="blue")
    elif pattern_id == 3:
        draw.ellipse([20, 20, 80, 80], fill="red")
    elif pattern_id == 4:
        draw.polygon([(10, 10), (90, 10), (50, 90)], fill="green")
    elif pattern_id == 5:
        draw.rectangle([25, 25, 75, 75], fill="purple")
    elif pattern_id == 6:
        draw.line([(0, 0), (100, 100)], fill="black", width=8)
    elif pattern_id == 7:
        draw.ellipse([10, 30, 90, 70], fill="orange")
    else:
        draw.rectangle([10, 10, 40, 40], fill="teal")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
async def test_session():
    """Isolated in-memory SQLite database for test execution."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_photo_ingestion_and_metadata(test_session: AsyncSession):
    """Verifies that photos are stored with full metadata, hashes, and internal IDs."""
    img_bytes = create_test_image(1)
    photo, is_new = await PhotoPoolService.add_photo(
        session=test_session,
        file_bytes=img_bytes,
        filename="coffee_morning.jpg",
        category="coffee",
        tags=["cafe", "morning", "relax"],
        mood=["relaxed", "happy"],
        contexts=["morning", "cafe"],
        description="Утренний кофе в уютном кафе",
    )

    assert is_new is True
    assert photo.photo_id.startswith("ph_")
    assert photo.category == "coffee"
    assert "morning" in photo.get_tags()
    assert "relaxed" in photo.get_mood()
    assert photo.file_size == len(img_bytes)
    assert len(photo.file_hash) == 64  # SHA-256
    assert photo.perceptual_hash is not None
    assert photo.enabled is True
    assert photo.times_used == 0


@pytest.mark.asyncio
async def test_exact_sha256_deduplication(test_session: AsyncSession):
    """Verifies that identical bytes are detected and rejected as exact duplicate."""
    img_bytes = create_test_image(2)

    photo1, is_new1 = await PhotoPoolService.add_photo(
        session=test_session,
        file_bytes=img_bytes,
        filename="pattern_2.jpg",
        category="outdoors",
    )
    assert is_new1 is True

    # Upload exact same bytes again under different filename and category
    photo2, is_new2 = await PhotoPoolService.add_photo(
        session=test_session,
        file_bytes=img_bytes,
        filename="pattern_2_reupload.jpg",
        category="city",
    )
    assert is_new2 is False
    assert photo2.photo_id == photo1.photo_id
    assert photo2.file_hash == photo1.file_hash


@pytest.mark.asyncio
async def test_perceptual_dhash_deduplication(test_session: AsyncSession):
    """Verifies that near-identical images (perceptual copies) are detected via dHash."""
    img = Image.new("RGB", (200, 200), color="purple")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 180, 180], fill="yellow")

    buf1 = io.BytesIO()
    img.save(buf1, format="JPEG", quality=95)
    bytes1 = buf1.getvalue()

    buf2 = io.BytesIO()
    img.save(buf2, format="JPEG", quality=80)  # Re-compressed copy
    bytes2 = buf2.getvalue()

    photo1, is_new1 = await PhotoPoolService.add_photo(
        session=test_session, file_bytes=bytes1, filename="art_95.jpg", category="hobby"
    )
    assert is_new1 is True

    photo2, is_new2 = await PhotoPoolService.add_photo(
        session=test_session, file_bytes=bytes2, filename="art_80.jpg", category="hobby"
    )
    assert is_new2 is False
    assert photo2.photo_id == photo1.photo_id


@pytest.mark.asyncio
async def test_per_chat_usage_and_isolation(test_session: AsyncSession):
    """
    CRITICAL REQUIREMENT:
    Photo used in Chat A must become permanently unavailable in Chat A.
    The same photo MUST remain available in Chat B!
    """
    img_bytes = create_test_image(3)
    photo, _ = await PhotoPoolService.add_photo(
        session=test_session, file_bytes=img_bytes, category="selfie"
    )

    # 1. Chat A selects and reserves the photo
    chosen_a = await PhotoPoolService.select_and_reserve_photo(
        session=test_session,
        chat_id="chat_A",
        preferred_category="selfie",
        is_explicit_request=True,
    )
    assert chosen_a is not None
    assert chosen_a.photo_id == photo.photo_id

    # Mark sent in Chat A
    await PhotoPoolService.mark_photo_sent(
        session=test_session, chat_id="chat_A", photo_id=photo.photo_id, message_id=1001
    )

    # 2. Chat A tries to get a photo again -> Pool is EXHAUSTED for Chat A
    chosen_a_again = await PhotoPoolService.select_and_reserve_photo(
        session=test_session,
        chat_id="chat_A",
        preferred_category="selfie",
        is_explicit_request=True,
    )
    assert chosen_a_again is None  # ABSOLUTE NO REPEAT in Chat A!

    # 3. Chat B requests a photo -> Photo IS available in Chat B!
    chosen_b = await PhotoPoolService.select_and_reserve_photo(
        session=test_session,
        chat_id="chat_B",
        preferred_category="selfie",
        is_explicit_request=True,
    )
    assert chosen_b is not None
    assert chosen_b.photo_id == photo.photo_id  # Available in Chat B


@pytest.mark.asyncio
async def test_reserve_before_send_lifecycle(test_session: AsyncSession):
    """
    Verifies state transitions:
    SELECT -> Atomically RESERVE (status='reserved') -> SEND -> status='sent' (with message_id).
    On failure: status='send_failed' (remains blocked for this chat unless safely released).
    """
    img1 = create_test_image(4)
    photo1, _ = await PhotoPoolService.add_photo(test_session, img1, category="food")

    # Reserve
    reserved = await PhotoPoolService.select_and_reserve_photo(
        session=test_session, chat_id="chat_123", is_explicit_request=True
    )
    assert reserved is not None
    assert reserved.photo_id == photo1.photo_id

    # Check status in DB is 'reserved'
    stmt = select(ChatPhotoUsage).where(
        ChatPhotoUsage.chat_id == "chat_123", ChatPhotoUsage.photo_id == photo1.photo_id
    )
    usage = (await test_session.execute(stmt)).scalars().first()
    assert usage.status == "reserved"

    # Mark sent
    ok_sent = await PhotoPoolService.mark_photo_sent(
        session=test_session, chat_id="chat_123", photo_id=photo1.photo_id, message_id=98765
    )
    assert ok_sent is True
    await test_session.refresh(usage)
    assert usage.status == "sent"
    assert usage.message_id == 98765

    # Test failure case on another photo
    img2 = create_test_image(5)
    photo2, _ = await PhotoPoolService.add_photo(test_session, img2, category="coffee")

    reserved2 = await PhotoPoolService.select_and_reserve_photo(
        session=test_session, chat_id="chat_123", is_explicit_request=True
    )
    assert reserved2 is not None
    assert reserved2.photo_id == photo2.photo_id

    # Mark failed without release -> status is send_failed and remains blocked
    await PhotoPoolService.mark_photo_failed(
        session=test_session, chat_id="chat_123", photo_id=photo2.photo_id, release_reservation=False
    )
    stmt2 = select(ChatPhotoUsage).where(
        ChatPhotoUsage.chat_id == "chat_123", ChatPhotoUsage.photo_id == photo2.photo_id
    )
    usage2 = (await test_session.execute(stmt2)).scalars().first()
    assert usage2.status == "send_failed"


@pytest.mark.asyncio
async def test_rarity_cooldown_and_explicit_bypass(test_session: AsyncSession):
    """
    Verifies that:
    1. Spontaneous photos respect cooldown (12h default).
    2. Explicit user request bypasses spontaneous cooldown.
    3. Even with explicit request, the ABSOLUTE NO-REPEAT rule is NEVER bypassed.
    """
    img1 = create_test_image(6)
    img2 = create_test_image(7)
    p1, _ = await PhotoPoolService.add_photo(test_session, img1, category="travel")
    p2, _ = await PhotoPoolService.add_photo(test_session, img2, category="city")

    # Send first photo spontaneously
    res1 = await PhotoPoolService.select_and_reserve_photo(
        test_session, chat_id="chat_cooldown", is_explicit_request=False
    )
    assert res1 is not None
    await PhotoPoolService.mark_photo_sent(test_session, "chat_cooldown", res1.photo_id, 101)

    # Immediately attempt spontaneous photo -> MUST BE BLOCKED BY COOLDOWN
    res_spontaneous_blocked = await PhotoPoolService.select_and_reserve_photo(
        test_session, chat_id="chat_cooldown", is_explicit_request=False
    )
    assert res_spontaneous_blocked is None  # Blocked!

    # Explicit user request -> BYPASSES spontaneous cooldown!
    res_explicit = await PhotoPoolService.select_and_reserve_photo(
        test_session, chat_id="chat_cooldown", is_explicit_request=True
    )
    assert res_explicit is not None
    assert res_explicit.photo_id != res1.photo_id  # Never repeats first photo!
    await PhotoPoolService.mark_photo_sent(test_session, "chat_cooldown", res_explicit.photo_id, 102)

    # Now both photos have been sent in chat_cooldown -> even explicit request returns None (No Repeat!)
    res_exhausted = await PhotoPoolService.select_and_reserve_photo(
        test_session, chat_id="chat_cooldown", is_explicit_request=True
    )
    assert res_exhausted is None


@pytest.mark.asyncio
async def test_visual_variety_category_avoidance(test_session: AsyncSession):
    """Verifies soft preference for visual variety (avoiding immediate category repeats)."""
    p_selfie1, _ = await PhotoPoolService.add_photo(test_session, create_test_image(1), category="selfie")
    p_selfie2, _ = await PhotoPoolService.add_photo(test_session, create_test_image(2), category="selfie")
    p_food, _ = await PhotoPoolService.add_photo(test_session, create_test_image(3), category="food")

    # 1. Send first selfie
    c1 = await PhotoPoolService.select_and_reserve_photo(test_session, "chat_variety", preferred_category="selfie", is_explicit_request=True)
    await PhotoPoolService.mark_photo_sent(test_session, "chat_variety", c1.photo_id, 1)

    # 2. Next request without specified category: food should be preferred over a second consecutive selfie
    c2 = await PhotoPoolService.select_and_reserve_photo(test_session, "chat_variety", is_explicit_request=True)
    assert c2 is not None
    assert c2.category == "food"


@pytest.mark.asyncio
async def test_intent_same_photo_detection():
    """Verifies that asking for the same photo again is detected and flagged for natural explanation."""
    intent1 = IntentService.detect_photo_request("скинь фотку")
    assert intent1.requested is True
    assert intent1.is_repeat_request is False

    intent2 = IntentService.detect_photo_request("скинь ту же фотку")
    assert intent2.requested is True
    assert intent2.is_repeat_request is True

    intent3 = IntentService.detect_photo_request("покажи еще раз то фото")
    assert intent3.requested is True
    assert intent3.is_repeat_request is True


@pytest.mark.asyncio
async def test_admin_management_crud(test_session: AsyncSession):
    """Verifies admin listing, toggle enable/disable, deletion, and pool statistics."""
    p, _ = await PhotoPoolService.add_photo(test_session, create_test_image(8), category="night", description="ночь")
    pid = p.photo_id

    # Toggle disable
    p_off = await PhotoPoolService.toggle_photo_status(test_session, pid, enabled=False)
    assert p_off.enabled is False

    # Disabled photo cannot be selected
    c = await PhotoPoolService.select_and_reserve_photo(test_session, "chat_disabled", is_explicit_request=True)
    assert c is None

    # Toggle enable
    p_on = await PhotoPoolService.toggle_photo_status(test_session, pid, enabled=True)
    assert p_on.enabled is True

    # Stats
    stats = await PhotoPoolService.get_pool_stats(test_session)
    assert stats["total_photos"] >= 1
    assert stats["enabled_photos"] >= 1

    # Delete
    deleted = await PhotoPoolService.delete_photo(test_session, pid, delete_file=False)
    assert deleted is True
    assert await PhotoPoolService.get_photo(test_session, pid) is None
