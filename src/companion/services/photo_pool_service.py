import hashlib
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import select, func, and_, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import PersonaPhoto, ChatPhotoUsage, PhotoCooldown
from companion.services.photo_subsystem import (
    PHOTO_SEND_GATE,
    PhotoAdminService,
    PhotoCooldownService,
    PhotoDecision,
    PhotoDecisionValidator,
    PhotoEligibilityResult,
    PhotoEligibilityService,
    PhotoRepository,
    PhotoSelector,
    PhotoSendService,
    PhotoUploadManager,
    PhotoUsageRepository,
    log_photo_event,
)

logger = logging.getLogger(__name__)


class PhotoPoolService:
    """
    Persistent, context-aware Photo Pool service with absolute per-chat no-repeat rule,
    cryptographic SHA-256 and perceptual dHash deduplication, atomic reserve-before-send,
    and strictly independent 12-hour per-chat cooldowns.
    """

    ALLOWED_MIME_TYPES = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

    # Standard Categories
    CATEGORIES = [
        "selfie",
        "casual",
        "outdoors",
        "outside",
        "home",
        "food",
        "coffee",
        "travel",
        "city",
        "pet",
        "outfit",
        "evening",
        "daytime",
        "day",
        "night",
        "smiling",
        "serious",
        "cute",
        "funny",
        "mirror",
        "cinema",
        "quest",
        "seasonal",
        "weather",
        "hobby",
        "random_life",
        "mood",
        "event",
    ]

    # =========================================================================
    # 1. HASHING & DEDUPLICATION
    # =========================================================================

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        """Computes cryptographic SHA-256 hex digest of file bytes."""
        return PhotoUploadManager.compute_sha256(data)

    @staticmethod
    def compute_dhash(image_bytes: bytes, hash_size: int = 8) -> Optional[str]:
        """
        Computes 64-bit difference hash (dHash) for perceptual image deduplication.
        Returns 16-character hexadecimal string or None if Pillow fails.
        """
        return PhotoUploadManager.compute_dhash(image_bytes, hash_size=hash_size)

    @staticmethod
    def hamming_distance(h1: Optional[str], h2: Optional[str]) -> int:
        """Calculates hamming bit distance between two hex hashes (0-64)."""
        return PhotoUploadManager.hamming_distance(h1, h2)

    # =========================================================================
    # 2. UPLOAD & INGESTION
    # =========================================================================

    @classmethod
    async def add_photo(
        cls,
        session: AsyncSession,
        file_bytes: bytes,
        filename: str = "photo.jpg",
        category: str = "casual",
        tags: Optional[List[str]] = None,
        mood: Optional[List[str]] = None,
        contexts: Optional[List[str]] = None,
        description: str = "",
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        telegram_file_id: Optional[str] = None,
        enabled: bool = True,
        hamming_threshold: int = 5,
        sender_tg_id: Optional[int] = None,
        configured_owner_id: Optional[int] = None,
    ) -> Tuple[PersonaPhoto, bool]:
        """
        Ingests a photo into the persistent pool with PIL validation and deduplication.
        If an exact SHA-256 duplicate or perceptual duplicate (hamming distance <= threshold)
        already exists, returns (existing_photo, False).
        Otherwise saves to PHOTO_STORAGE_DIR, registers in DB, and returns (new_photo, True).
        """
        return await PhotoUploadManager.upload_photo(
            session=session,
            file_bytes=file_bytes,
            filename=filename,
            category=category,
            tags=tags,
            mood=mood,
            contexts=contexts,
            description=description,
            user_id=user_id,
            persona_id=persona_id,
            telegram_file_id=telegram_file_id,
            enabled=enabled,
            hamming_threshold=hamming_threshold,
            sender_tg_id=sender_tg_id,
            configured_owner_id=configured_owner_id,
        )

    # =========================================================================
    # 3. PER-CHAT USAGE, COOLDOWN & ELIGIBILITY
    # =========================================================================

    @classmethod
    async def can_send_photo(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        now: Optional[datetime] = None,
    ) -> bool:
        """Returns True if the 12-hour per-chat cooldown allows sending a photo."""
        return await PhotoCooldownService.can_send_photo(session, chat_id, now=now)

    @classmethod
    async def get_photo_cooldown(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Returns structured 12-hour cooldown info for chat_id."""
        return await PhotoCooldownService.get_photo_cooldown(session, chat_id, now=now)

    @classmethod
    async def record_photo_sent(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
        telegram_message_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> PhotoCooldown:
        """Records confirmed photo delivery and starts the 12-hour cooldown for chat_id."""
        return await PhotoCooldownService.record_photo_sent(
            session=session,
            chat_id=chat_id,
            photo_id=photo_id,
            telegram_message_id=telegram_message_id,
            now=now,
        )

    @classmethod
    async def check_eligibility(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        user_id: Optional[int] = None,
        now: Optional[datetime] = None,
        enforce_cooldown: bool = True,
    ) -> PhotoEligibilityResult:
        """Returns structured PhotoEligibilityResult for chat_id."""
        return await PhotoEligibilityService.check_eligibility(
            session=session,
            chat_id=chat_id,
            user_id=user_id,
            now=now,
            enforce_cooldown=enforce_cooldown,
        )

    @classmethod
    async def get_chat_state(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        user_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Returns per-chat internal photo state (Section 19)."""
        return await PhotoEligibilityService.get_chat_state(
            session=session,
            chat_id=chat_id,
            user_id=user_id,
            now=now,
        )

    @classmethod
    async def is_photo_used_in_chat(
        cls, session: AsyncSession, chat_id: Union[str, int], photo_id: str
    ) -> bool:
        """Checks whether photo_id was ever sent or reserved in chat_id."""
        return await PhotoUsageRepository.is_photo_used_in_chat(session, chat_id, photo_id)

    @classmethod
    async def get_used_photo_ids_for_chat(
        cls, session: AsyncSession, chat_id: Union[str, int]
    ) -> List[str]:
        """Returns list of all photo_ids that have ever been reserved or sent in this chat."""
        return await PhotoUsageRepository.get_used_photo_ids(session, chat_id)

    @classmethod
    async def check_chat_frequency_limits(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        is_explicit_request: bool = False,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Verifies 12-hour per-chat cooldown and optional global limits.
        """
        if is_explicit_request:
            return True, "explicit_user_request"

        cd_info = await PhotoCooldownService.get_photo_cooldown(session, chat_id, now=now)
        if not cd_info["eligible"]:
            hrs_left = round(cd_info["remaining_seconds"] / 3600.0, 1)
            return False, f"cooldown_active_{hrs_left}h_remaining"

        global_ok, global_reason = await PhotoEligibilityService.check_global_limits(session, now=now)
        if not global_ok:
            return False, global_reason or "GLOBAL_LIMIT"

        return True, "frequency_limits_ok"

    @classmethod
    async def get_recent_sent_categories(
        cls, session: AsyncSession, chat_id: Union[str, int], limit: int = 3
    ) -> List[str]:
        """Returns categories of most recently sent photos in this chat."""
        return await PhotoSelector.get_recent_sent_categories(session, chat_id, limit=limit)

    # =========================================================================
    # 4. PHOTO SELECTION & ATOMIC RESERVATION
    # =========================================================================

    @classmethod
    async def select_and_reserve_photo(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        preferred_category: Optional[str] = None,
        preferred_tags: Optional[List[str]] = None,
        is_explicit_request: bool = False,
        topic: Optional[str] = None,
        current_hour: Optional[int] = None,
        reason: str = "",
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Optional[PersonaPhoto]:
        """
        Executes candidate selection pipeline with absolute no-repeat enforcement
        and atomic reservation (reserve-before-send).
        """
        return await PhotoSelector.select_and_reserve(
            session=session,
            chat_id=chat_id,
            preferred_category=preferred_category,
            preferred_tags=preferred_tags,
            current_hour=current_hour,
            reason=reason or ("explicit_request" if is_explicit_request else "contextual"),
            user_id=user_id,
            now=now,
            enforce_cooldown=not is_explicit_request,
            require_disk_file=False,
        )

    # =========================================================================
    # 5. LIFECYCLE: SENT & FAILED STATE UPDATES
    # =========================================================================

    @classmethod
    async def mark_photo_sent(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
        message_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Transitions photo reservation status from 'reserved' to 'sent',
        records the Telegram message_id, and starts the 12-hour per-chat cooldown.
        """
        stmt = select(ChatPhotoUsage).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.photo_id == str(photo_id),
        )
        res = await session.execute(stmt)
        usage = res.scalars().first()
        if not usage:
            return False

        await PhotoCooldownService.record_photo_sent(
            session=session,
            chat_id=chat_id,
            photo_id=photo_id,
            telegram_message_id=message_id,
            now=now,
        )
        log_photo_event(
            "PHOTO_SEND_SUCCESS",
            chat_id=chat_id,
            photo_id=photo_id,
            result="SENT",
            message_id=message_id,
        )
        return True

    @classmethod
    async def mark_photo_failed(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
        release_reservation: bool = False,
    ) -> bool:
        """
        Handles sending failures.
        If release_reservation is True (definite failure before Telegram accepted the photo),
        the reservation is deleted and the 12-hour cooldown is NOT started.
        Otherwise, status is updated to 'send_failed' (photo remains permanently blocked
        in this chat to eliminate any chance of repetition).
        """
        if release_reservation:
            ok = await PhotoUsageRepository.release_reservation(session, chat_id, photo_id)
            if ok:
                log_photo_event(
                    "PHOTO_SEND_FAILED",
                    chat_id=chat_id,
                    photo_id=photo_id,
                    result="RELEASED",
                )
            return ok
        else:
            ok = await PhotoUsageRepository.mark_uncertain_or_failed(
                session=session,
                chat_id=chat_id,
                photo_id=photo_id,
                status="send_failed",
            )
            if ok:
                log_photo_event(
                    "PHOTO_SEND_FAILED",
                    chat_id=chat_id,
                    photo_id=photo_id,
                    result="SEND_FAILED_BLOCKED",
                    level=logging.WARNING,
                )
            return ok

    # =========================================================================
    # 6. POOL MANAGEMENT & INSPECTION
    # =========================================================================

    @classmethod
    async def list_photos(
        cls,
        session: AsyncSession,
        enabled_only: bool = False,
        category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PersonaPhoto]:
        """Lists photos with optional category and enabled filtering."""
        return await PhotoRepository.list_photos(
            session=session,
            active_only=enabled_only,
            category=category,
            limit=limit,
            offset=offset,
        )

    @classmethod
    async def get_photo(
        cls, session: AsyncSession, photo_id: str
    ) -> Optional[PersonaPhoto]:
        """Fetches a single photo by its stable photo_id."""
        return await PhotoRepository.get_by_photo_id(session, photo_id)

    @classmethod
    async def toggle_photo_status(
        cls, session: AsyncSession, photo_id: str, enabled: bool
    ) -> Optional[PersonaPhoto]:
        """Enables or disables a photo in the pool."""
        return await PhotoRepository.set_active(session, photo_id, enabled)

    @classmethod
    async def update_photo_metadata(
        cls,
        session: AsyncSession,
        photo_id: str,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        mood: Optional[List[str]] = None,
        contexts: Optional[List[str]] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[PersonaPhoto]:
        """Updates metadata for an existing photo asset."""
        photo = await cls.get_photo(session, photo_id)
        if not photo:
            return None

        if category is not None:
            photo.category = category.lower().strip()
        if tags is not None:
            photo.set_tags(tags)
        if mood is not None:
            photo.set_mood(mood)
        if contexts is not None:
            photo.set_contexts(contexts)
        if description is not None:
            photo.description = description.strip()
        if enabled is not None:
            photo.enabled = enabled

        await session.commit()
        await session.refresh(photo)
        return photo

    @classmethod
    async def delete_photo(
        cls, session: AsyncSession, photo_id: str, delete_file: bool = False
    ) -> bool:
        """Deletes a photo from the pool."""
        return await PhotoRepository.delete_photo(session, photo_id, delete_file=delete_file)

    @classmethod
    async def clear_all_photos(
        cls, session: AsyncSession, delete_files: bool = True
    ) -> int:
        """Clears all photos from the pool."""
        return await PhotoRepository.clear_all_photos(session, delete_files=delete_files)

    @classmethod
    async def get_pool_stats(
        cls,
        session: AsyncSession,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Returns comprehensive statistics of the photo pool (Section 37)."""
        return await PhotoAdminService.get_admin_statistics(session, now=now)

    @classmethod
    async def get_chat_usage_history(
        cls, session: AsyncSession, chat_id: Union[str, int]
    ) -> List[Dict[str, Any]]:
        """Returns all photos sent to a specific chat with timestamps and status."""
        stmt = (
            select(ChatPhotoUsage, PersonaPhoto)
            .outerjoin(PersonaPhoto, ChatPhotoUsage.photo_id == PersonaPhoto.photo_id)
            .where(ChatPhotoUsage.chat_id == str(chat_id))
            .order_by(desc(ChatPhotoUsage.first_used_at))
        )
        res = await session.execute(stmt)
        history = []
        for usage, photo in res.all():
            history.append({
                "photo_id": usage.photo_id,
                "status": usage.status,
                "first_used_at": usage.first_used_at.isoformat() if usage.first_used_at else None,
                "sent_at": usage.sent_at.isoformat() if getattr(usage, "sent_at", None) else None,
                "message_id": usage.message_id,
                "category": usage.category or (photo.category if photo else None),
                "description": photo.description if photo else None,
            })
        return history


__all__ = [
    "PhotoPoolService",
    "PhotoRepository",
    "PhotoUploadManager",
    "PhotoCooldownService",
    "PhotoUsageRepository",
    "PhotoEligibilityService",
    "PhotoEligibilityResult",
    "PhotoSelector",
    "PhotoDecision",
    "PhotoDecisionValidator",
    "PHOTO_SEND_GATE",
    "PhotoSendService",
    "PhotoAdminService",
    "log_photo_event",
]
