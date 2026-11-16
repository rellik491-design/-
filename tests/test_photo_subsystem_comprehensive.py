"""
Comprehensive Automated Tests for the Photo Upload and Per-Chat Photo Sending System (Sections 1–49).
Covers all 12 mandatory test scenarios from Section 35 plus persistence, concurrency, security, and sanitization.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
from PIL import Image, ImageDraw
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from companion.config import settings
from companion.models.db import Base
from companion.models.entities import ChatPhotoUsage, PersonaPhoto, PhotoCooldown
from companion.services.output_sanitizer import OutputSanitizer
from companion.services.photo_subsystem import (
    PHOTO_SEND_GATE,
    PhotoAdminService,
    PhotoCooldownService,
    PhotoDecision,
    PhotoDecisionValidator,
    PhotoEligibilityService,
    PhotoRepository,
    PhotoSelector,
    PhotoSendService,
    PhotoUploadManager,
    PhotoUsageRepository,
)


def make_test_image_bytes(seed: int = 1, size: tuple = (160, 120), fmt: str = "JPEG") -> bytes:
    """Creates valid, visually distinct test image bytes with high dHash distance across seeds."""
    w, h = size
    img = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(img)
    if seed == 1:
        draw.rectangle([0, 0, w // 2, h], fill="black")
    elif seed == 2:
        draw.rectangle([0, 0, w, h // 2], fill="black")
    elif seed == 3:
        draw.ellipse([w // 4, h // 4, 3 * w // 4, 3 * h // 4], fill="black")
    elif seed == 4:
        draw.polygon([(0, 0), (w, 0), (w // 2, h)], fill="black")
    elif seed == 5:
        draw.rectangle([w // 2, h // 2, w, h], fill="black")
    else:
        for i in range(0, w, 20):
            if (i // 20 + seed) % 2 == 0:
                draw.rectangle([i, 0, i + 10, h], fill="black")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
async def db_session(tmp_path: Path, monkeypatch):
    """Provides an isolated SQLite database and temporary photo storage directory."""
    storage_dir = tmp_path / "photos"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "PHOTO_STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "PHOTO_CHAT_COOLDOWN_HOURS", 12.0)
    monkeypatch.setattr(settings, "PHOTO_COOLDOWN_HOURS", 12.0)
    monkeypatch.setattr(settings, "PHOTO_SELECTION_ENABLED", True)
    monkeypatch.setattr(settings, "PHOTO_GLOBAL_HOURLY_LIMIT", None)
    monkeypatch.setattr(settings, "PHOTO_GLOBAL_DAILY_LIMIT", None)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session

    await engine.dispose()


# ==============================================================================
# TEST 1 — First photo in a chat is eligible immediately (Sections 8 & 35.1)
# ==============================================================================

@pytest.mark.asyncio
async def test_01_first_photo_eligible_immediately(db_session: AsyncSession):
    await PhotoUploadManager.upload_photo(
        session=db_session,
        file_bytes=make_test_image_bytes(1),
        category="selfie",
        tags=["home", "evening"],
    )

    chat_id = 100001
    can_send = await PhotoCooldownService.can_send_photo(db_session, chat_id)
    assert can_send is True

    cd_info = await PhotoCooldownService.get_photo_cooldown(db_session, chat_id)
    assert cd_info["last_photo_sent_at"] is None
    assert cd_info["eligible"] is True

    elig = await PhotoEligibilityService.check_eligibility(db_session, chat_id)
    assert elig.allowed is True
    assert elig.reason == "ALLOWED"
    assert elig.available_unused_photos == 1


# ==============================================================================
# TEST 2 — Cooldown blocks photo sent 5 hours ago (Sections 6 & 35.2)
# ==============================================================================

@pytest.mark.asyncio
async def test_02_cooldown_blocks_at_5_hours(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="casual")
    p2, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="city")

    chat_id = 100002
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    await PhotoCooldownService.record_photo_sent(db_session, chat_id, p1.photo_id, telegram_message_id=501, now=t0)

    # 5 hours later (15:00 UTC) -> must be blocked!
    t_5h = t0 + timedelta(hours=5)
    assert await PhotoCooldownService.can_send_photo(db_session, chat_id, now=t_5h) is False

    elig = await PhotoEligibilityService.check_eligibility(db_session, chat_id, now=t_5h)
    assert elig.allowed is False
    assert elig.reason == "CHAT_COOLDOWN"
    assert elig.next_allowed_at == "2026-11-16T22:00:00Z"


# ==============================================================================
# TEST 3 — Cooldown expired at 12 hours + 1 second (Sections 6 & 35.3)
# ==============================================================================

@pytest.mark.asyncio
async def test_03_cooldown_expired_at_12h_plus_1s(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="casual")
    p2, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="city")

    chat_id = 100003
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    await PhotoCooldownService.record_photo_sent(db_session, chat_id, p1.photo_id, telegram_message_id=502, now=t0)

    # 11 hours 59 minutes 59 seconds later -> still blocked
    t_before = t0 + timedelta(hours=12) - timedelta(seconds=1)
    assert await PhotoCooldownService.can_send_photo(db_session, chat_id, now=t_before) is False

    # 12 hours + 1 second later -> eligible!
    t_after = t0 + timedelta(hours=12, seconds=1)
    assert await PhotoCooldownService.can_send_photo(db_session, chat_id, now=t_after) is True

    elig = await PhotoEligibilityService.check_eligibility(db_session, chat_id, now=t_after)
    assert elig.allowed is True
    assert elig.reason == "ALLOWED"


# ==============================================================================
# TEST 4 — Independent per-chat cooldowns (Sections 7, 34, 35.4)
# ==============================================================================

@pytest.mark.asyncio
async def test_04_multi_chat_independence(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")
    p2, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="coffee")

    chat_a = "chat_A_101"
    chat_b = "chat_B_102"
    chat_c = "chat_C_103"
    chat_d = "chat_D_104"

    t_a = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    t_b = datetime(2026, 11, 16, 10, 5, 0, tzinfo=timezone.utc)
    t_c = datetime(2026, 11, 16, 10, 10, 0, tzinfo=timezone.utc)

    await PhotoCooldownService.record_photo_sent(db_session, chat_a, p1.photo_id, 1, now=t_a)
    await PhotoCooldownService.record_photo_sent(db_session, chat_b, p1.photo_id, 2, now=t_b)
    await PhotoCooldownService.record_photo_sent(db_session, chat_c, p1.photo_id, 3, now=t_c)

    # Check at 12:00 UTC
    t_check = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)

    elig_a = await PhotoEligibilityService.check_eligibility(db_session, chat_a, now=t_check)
    elig_b = await PhotoEligibilityService.check_eligibility(db_session, chat_b, now=t_check)
    elig_c = await PhotoEligibilityService.check_eligibility(db_session, chat_c, now=t_check)
    elig_d = await PhotoEligibilityService.check_eligibility(db_session, chat_d, now=t_check)

    assert elig_a.allowed is False and elig_a.next_allowed_at == "2026-11-16T22:00:00Z"
    assert elig_b.allowed is False and elig_b.next_allowed_at == "2026-11-16T22:05:00Z"
    assert elig_c.allowed is False and elig_c.next_allowed_at == "2026-11-16T22:10:00Z"
    assert elig_d.allowed is True  # Chat D has no photo yet -> eligible!

    # At 22:02 UTC: Chat A is now eligible (22:00 passed), while Chat B (22:05) and Chat C (22:10) are still blocked!
    t_22_02 = datetime(2026, 11, 16, 22, 2, 0, tzinfo=timezone.utc)
    assert (await PhotoEligibilityService.check_eligibility(db_session, chat_a, now=t_22_02)).allowed is True
    assert (await PhotoEligibilityService.check_eligibility(db_session, chat_b, now=t_22_02)).allowed is False
    assert (await PhotoEligibilityService.check_eligibility(db_session, chat_c, now=t_22_02)).allowed is False


# ==============================================================================
# TEST 5 — Exact photo repeat is permanently blocked in same chat (Sections 10, 35.5)
# ==============================================================================

@pytest.mark.asyncio
async def test_05_exact_photo_never_repeats_in_same_chat(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")

    chat_a = "chat_repeat_test"
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)

    # Send Photo #1 to Chat A at 10:00
    selected_1 = await PhotoSelector.select_and_reserve(db_session, chat_a, now=t0)
    assert selected_1 is not None and selected_1.photo_id == p1.photo_id
    await PhotoCooldownService.record_photo_sent(db_session, chat_a, p1.photo_id, 101, now=t0)

    # Advance 24 hours (cooldown long expired)
    t_next_day = t0 + timedelta(hours=24)

    # Since only Photo #1 exists and it was already sent to Chat A, Chat A cannot select it again!
    elig = await PhotoEligibilityService.check_eligibility(db_session, chat_a, now=t_next_day)
    assert elig.allowed is False
    assert elig.reason == "NO_UNUSED_PHOTOS"
    assert await PhotoSelector.select_and_reserve(db_session, chat_a, now=t_next_day) is None

    # Upload Photo #2 -> now Chat A selects Photo #2, never Photo #1
    p2, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="selfie")
    selected_2 = await PhotoSelector.select_and_reserve(db_session, chat_a, now=t_next_day)
    assert selected_2 is not None
    assert selected_2.photo_id == p2.photo_id


# ==============================================================================
# TEST 6 — Different chats can use the same photo (Section 35.6)
# ==============================================================================

@pytest.mark.asyncio
async def test_06_different_chats_can_use_same_photo(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="outfit")

    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    sel_a = await PhotoSelector.select_and_reserve(db_session, "chat_alpha", now=t0)
    assert sel_a is not None and sel_a.photo_id == p1.photo_id
    await PhotoCooldownService.record_photo_sent(db_session, "chat_alpha", p1.photo_id, 1, now=t0)

    # Chat Beta has never received p1 -> p1 is selected for Chat Beta!
    sel_b = await PhotoSelector.select_and_reserve(db_session, "chat_beta", now=t0)
    assert sel_b is not None
    assert sel_b.photo_id == p1.photo_id


# ==============================================================================
# TEST 7 — Persistence across application restart (Sections 33, 35.7)
# ==============================================================================

@pytest.mark.asyncio
async def test_07_persistence_after_restart(tmp_path: Path, monkeypatch):
    storage_dir = tmp_path / "persistent_photos"
    storage_dir.mkdir(parents=True, exist_ok=True)
    db_file = tmp_path / "persistent_bot.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    monkeypatch.setattr(settings, "PHOTO_STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "PHOTO_CHAT_COOLDOWN_HOURS", 12.0)

    t_10_00 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    chat_a = "chat_persistent_A"

    # --- PROCESS 1: Upload photos & send photo #1 at 10:00 ---
    engine1 = create_async_engine(db_url, echo=False)
    async with engine1.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker1 = async_sessionmaker(engine1, expire_on_commit=False, class_=AsyncSession)

    async with maker1() as session1:
        p1, _ = await PhotoUploadManager.upload_photo(session1, make_test_image_bytes(1), category="selfie")
        p2, _ = await PhotoUploadManager.upload_photo(session1, make_test_image_bytes(2), category="city")
        p1_id = p1.photo_id
        p2_id = p2.photo_id

        await PhotoCooldownService.record_photo_sent(session1, chat_a, p1_id, telegram_message_id=777, now=t_10_00)

    # Shut down engine1 completely (simulating bot restart)
    await engine1.dispose()

    # --- PROCESS 2: Reconnect after restart at 15:00 ---
    t_15_00 = datetime(2026, 11, 16, 15, 0, 0, tzinfo=timezone.utc)
    engine2 = create_async_engine(db_url, echo=False)
    maker2 = async_sessionmaker(engine2, expire_on_commit=False, class_=AsyncSession)

    async with maker2() as session2:
        # 1. At 15:00, Chat A must still be blocked until 22:00!
        elig_15 = await PhotoEligibilityService.check_eligibility(session2, chat_a, now=t_15_00)
        assert elig_15.allowed is False
        assert elig_15.reason == "CHAT_COOLDOWN"
        assert elig_15.next_allowed_at == "2026-11-16T22:00:00Z"

        # 2. At 22:05 (after cooldown expires), Photo #1 must still be permanently unavailable for Chat A!
        t_22_05 = datetime(2026, 11, 16, 22, 5, 0, tzinfo=timezone.utc)
        assert await PhotoUsageRepository.is_photo_used_in_chat(session2, chat_a, p1_id) is True

        chosen_after = await PhotoSelector.select_and_reserve(session2, chat_a, now=t_22_05)
        assert chosen_after is not None
        assert chosen_after.photo_id == p2_id  # Must select p2, never p1!

    await engine2.dispose()


# ==============================================================================
# TEST 8 — Telegram send failure handling (Sections 9, 14, 35.8)
# ==============================================================================

@pytest.mark.asyncio
async def test_08_telegram_send_failure_no_false_cooldown(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")
    chat_id = "chat_fail_test"
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Simulate definite Telegram send failure (returns None)
    async def failing_transport(photo: PersonaPhoto, caption: str | None):
        return None

    res_fail = await PhotoSendService.send_photo_if_allowed(
        session=db_session,
        chat_id=chat_id,
        ai_decision={"should_send_photo": True, "reason": "fit"},
        send_transport_fn=failing_transport,
        now=t0,
    )
    assert res_fail["sent"] is False
    assert res_fail["reason"] == "SEND_FAILED"

    # Cooldown must NOT have started, and p1 must still be available!
    assert await PhotoCooldownService.can_send_photo(db_session, chat_id, now=t0 + timedelta(minutes=1)) is True
    assert await PhotoUsageRepository.is_photo_used_in_chat(db_session, chat_id, p1.photo_id) is False

    # 2. Now retry with working transport -> succeeds!
    async def working_transport(photo: PersonaPhoto, caption: str | None):
        return 9001

    res_ok = await PhotoSendService.send_photo_if_allowed(
        session=db_session,
        chat_id=chat_id,
        ai_decision={"should_send_photo": True, "reason": "fit"},
        send_transport_fn=working_transport,
        now=t0 + timedelta(minutes=2),
    )
    assert res_ok["sent"] is True
    assert res_ok["photo_id"] == p1.photo_id
    assert res_ok["message_id"] == 9001

    # Now cooldown IS active!
    assert await PhotoCooldownService.can_send_photo(db_session, chat_id, now=t0 + timedelta(hours=1)) is False


# ==============================================================================
# TEST 9 — Duplicate concurrent requests race condition (Sections 13, 35.9)
# ==============================================================================

@pytest.mark.asyncio
async def test_09_concurrent_requests_prevent_duplicate_send(db_session: AsyncSession):
    await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")
    await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="casual")

    chat_id = "chat_concurrent_race"
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    send_calls = 0

    async def slow_transport(photo: PersonaPhoto, caption: str | None):
        nonlocal send_calls
        send_calls += 1
        await asyncio.sleep(0.05)
        return 1000 + send_calls

    # Launch two concurrent send requests for the same chat simultaneously
    r1, r2 = await asyncio.gather(
        PhotoSendService.send_photo_if_allowed(
            session=db_session,
            chat_id=chat_id,
            ai_decision={"should_send_photo": True},
            send_transport_fn=slow_transport,
            now=t0,
        ),
        PhotoSendService.send_photo_if_allowed(
            session=db_session,
            chat_id=chat_id,
            ai_decision={"should_send_photo": True},
            send_transport_fn=slow_transport,
            now=t0,
        ),
    )

    successes = [r for r in (r1, r2) if r["sent"] is True]
    failures = [r for r in (r1, r2) if r["sent"] is False]

    assert len(successes) == 1
    assert len(failures) == 1
    assert send_calls == 1


# ==============================================================================
# TEST 10 — Duplicate upload & file validation (Sections 4, 24, 35.10)
# ==============================================================================

@pytest.mark.asyncio
async def test_10_duplicate_upload_and_validation(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_ID", 8541203642)
    img_bytes = make_test_image_bytes(4, size=(240, 180))

    # 1. Authorized owner uploads photo
    p1, is_new1 = await PhotoUploadManager.upload_photo(
        session=db_session,
        file_bytes=img_bytes,
        filename="my_selfie.jpg",
        category="selfie",
        sender_tg_id=8541203642,
    )
    assert is_new1 is True
    assert p1.width == 240
    assert p1.height == 180
    assert p1.original_filename == "my_selfie.jpg"
    assert p1.is_active is True
    assert Path(p1.file_path).exists()

    # 2. Upload exact same image again -> duplicate detected, only 1 record in DB
    p2, is_new2 = await PhotoUploadManager.upload_photo(
        session=db_session,
        file_bytes=img_bytes,
        filename="copy_selfie.jpg",
        category="casual",
        sender_tg_id=8541203642,
    )
    assert is_new2 is False
    assert p2.photo_id == p1.photo_id

    count = (await db_session.execute(select(func.count(PersonaPhoto.id)))).scalar()
    assert count == 1

    # 3. Corrupted / non-image file is rejected
    with pytest.raises(ValueError, match="Invalid or corrupted image"):
        await PhotoUploadManager.upload_photo(
            session=db_session,
            file_bytes=b"not a real jpeg file content",
            filename="fake.jpg",
        )

    # 4. Non-owner upload is rejected
    with pytest.raises(PermissionError):
        await PhotoUploadManager.upload_photo(
            session=db_session,
            file_bytes=make_test_image_bytes(5),
            sender_tg_id=111222333,
        )


# ==============================================================================
# TEST 11 — AI tries to force photo while chat is in cooldown (Sections 32, 35.11, 43)
# ==============================================================================

@pytest.mark.asyncio
async def test_11_ai_cannot_force_photo_during_cooldown(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")
    p2, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="casual")

    chat_id = "chat_ai_force"
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    await PhotoCooldownService.record_photo_sent(db_session, chat_id, p1.photo_id, 1, now=t0)

    # 2 hours later, AI tries to force a photo and even specifies photo_id & bypass_cooldown
    t_2h = t0 + timedelta(hours=2)
    transport_called = False

    async def transport(photo: PersonaPhoto, caption: str | None):
        nonlocal transport_called
        transport_called = True
        return 999

    res = await PhotoSendService.send_photo_if_allowed(
        session=db_session,
        chat_id=chat_id,
        ai_decision={
            "should_send_photo": True,
            "photo_id": p2.photo_id,
            "bypass_cooldown": True,
            "confidence": 0.99,
        },
        send_transport_fn=transport,
        now=t_2h,
    )
    assert res["sent"] is False
    assert res["reason"] == "CHAT_COOLDOWN"
    assert transport_called is False

    # Direct PHOTO_SEND_GATE call also blocks it
    gate_ok, gate_reason = await PHOTO_SEND_GATE(db_session, chat_id, p2.photo_id, now=t_2h)
    assert gate_ok is False
    assert gate_reason == "CHAT_COOLDOWN"


# ==============================================================================
# TEST 12 — Cooldown expired but AI says no (Sections 16, 17, 35.12, 39)
# ==============================================================================

@pytest.mark.asyncio
async def test_12_cooldown_expired_but_ai_says_no(db_session: AsyncSession):
    await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")

    chat_id = "chat_ai_no"
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)

    # Chat is eligible
    elig = await PhotoEligibilityService.check_eligibility(db_session, chat_id, now=t0)
    assert elig.allowed is True

    # AI says should_send_photo = False -> no photo is sent!
    transport_called = False

    async def transport(photo: PersonaPhoto, caption: str | None):
        nonlocal transport_called
        transport_called = True
        return 123

    res = await PhotoSendService.send_photo_if_allowed(
        session=db_session,
        chat_id=chat_id,
        ai_decision={"should_send_photo": False, "reason": "not needed right now"},
        send_transport_fn=transport,
        now=t0,
    )
    assert res["sent"] is False
    assert res["reason"] == "AI_DECLINED"
    assert transport_called is False


# ==============================================================================
# TEST 13 — Chat State, Sanitization & Admin Statistics (Sections 19, 28, 29, 37)
# ==============================================================================

@pytest.mark.asyncio
async def test_13_chat_state_sanitization_and_admin_stats(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")
    p2, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(2), category="city")
    await PhotoRepository.set_active(db_session, p2.photo_id, False)

    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
    await PhotoCooldownService.record_photo_sent(db_session, 123456, p1.photo_id, 77, now=t0)

    # Section 19: Chat state inspection
    state = await PhotoEligibilityService.get_chat_state(db_session, 123456, now=t0 + timedelta(hours=1))
    assert state["chat_id"] == 123456
    assert state["photo_eligible"] is False
    assert state["last_photo_sent_at"] == "2026-11-16T10:00:00Z"
    assert state["next_photo_allowed_at"] == "2026-11-16T22:00:00Z"
    assert state["photos_sent_count"] == 1
    assert state["available_unused_photos"] == 0

    # Section 28 & 29: No internal leakage in captions or outgoing messages
    dirty_caption = f"Смотри какая погода 😄 [PHOTO_ID:123] [SEND_PHOTO] [DEBUG] {p1.photo_id} storage/photos/{p1.photo_id}.jpg"
    clean_caption = PhotoSendService.sanitize_caption(dirty_caption)
    assert "[PHOTO_ID" not in clean_caption
    assert "[SEND_PHOTO]" not in clean_caption
    assert "[DEBUG]" not in clean_caption
    assert p1.photo_id not in clean_caption
    assert "storage/photos" not in clean_caption
    assert "Смотри какая погода" in clean_caption

    # Section 37: Admin statistics
    stats = await PhotoAdminService.get_admin_statistics(db_session, now=t0 + timedelta(hours=1))
    assert stats["total_photos"] == 2
    assert stats["active_photos"] == 1
    assert stats["disabled_photos"] == 1
    assert stats["photos_sent_today"] == 1
    assert stats["photos_sent_this_week"] == 1
    assert stats["chats_with_active_cooldown"] == 1


# ==============================================================================
# TEST 14 — Uncertain transport timeout marks reservation uncertain (Sections 9, 14)
# ==============================================================================

@pytest.mark.asyncio
async def test_14_uncertain_timeout_marks_reservation_uncertain(db_session: AsyncSession):
    p1, _ = await PhotoUploadManager.upload_photo(db_session, make_test_image_bytes(1), category="selfie")
    chat_id = "chat_timeout_test"
    t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)

    async def timeout_transport(photo: PersonaPhoto, caption: str | None):
        raise asyncio.TimeoutError("ReadTimeout waiting for Telegram response")

    res = await PhotoSendService.send_photo_if_allowed(
        session=db_session,
        chat_id=chat_id,
        ai_decision={"should_send_photo": True},
        send_transport_fn=timeout_transport,
        now=t0,
    )
    assert res["sent"] is False
    assert res["reason"] == "TRANSPORT_UNCERTAIN"

    # Photo p1 must remain permanently blocked in chat_timeout_test to prevent duplicate delivery!
    assert await PhotoUsageRepository.is_photo_used_in_chat(db_session, chat_id, p1.photo_id) is True


# ==============================================================================
# TEST 15 — Owner-only /photos commands & non-admin isolation (Sections 2, 22, 29)
# ==============================================================================

@pytest.mark.asyncio
async def test_15_telegram_bot_owner_only_photo_commands(tmp_path: Path, monkeypatch):
    from unittest.mock import AsyncMock
    from companion.bot.telegram_bot_engine import TelegramBotEngine
    from companion.models.db import init_db

    await init_db()
    storage_dir = tmp_path / "bot_photos"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "PHOTO_STORAGE_DIR", str(storage_dir))

    owner_id = 8541203642
    stranger_id = 777111222
    bot = TelegramBotEngine("mock_token", owner_id)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)
    bot.download_file_bytes = AsyncMock(return_value=make_test_image_bytes(3))

    # 1. Normal user tries /photos -> must NOT expose internal photo pool stats
    await bot.handle_message({
        "text": "/photos",
        "chat": {"id": stranger_id},
        "from": {"id": stranger_id, "username": "stranger"},
    })
    assert bot.send_message.called is False

    # 2. Normal user tries to upload #photo -> must NOT upload
    await bot.handle_message({
        "photo": [{"file_id": "fid_1", "file_unique_id": "uid_1"}],
        "caption": "#photo selfie test",
        "chat": {"id": stranger_id},
        "from": {"id": stranger_id, "username": "stranger"},
    })
    assert bot.send_message.called is False

    # 3. Owner uploads photo directly (even without #photo prefix!) -> succeeds!
    await bot.handle_message({
        "photo": [{"file_id": "fid_owner", "file_unique_id": "uid_owner"}],
        "caption": "casual selfie,evening Вечернее фото",
        "chat": {"id": owner_id},
        "from": {"id": owner_id, "username": "owner"},
    })
    assert bot.send_message.called is True
    upload_reply = bot.send_message.call_args[0][1]
    assert "Фото успешно добавлено" in upload_reply or "Дубликат обнаружен" in upload_reply

    # 4. Owner runs /photos stats -> receives stats report
    bot.send_message.reset_mock()
    await bot.handle_message({
        "text": "/photos stats",
        "chat": {"id": owner_id},
        "from": {"id": owner_id, "username": "owner"},
    })
    assert bot.send_message.called is True
    stats_reply = bot.send_message.call_args[0][1]
    assert "УПРАВЛЕНИЕ PHOTO POOL" in stats_reply

