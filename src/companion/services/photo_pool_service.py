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
from companion.models.entities import PersonaPhoto, ChatPhotoUsage

logger = logging.getLogger(__name__)


class PhotoPoolService:
    """
    Persistent, context-aware Photo Pool service with absolute per-chat no-repeat rule,
    cryptographic SHA-256 and perceptual dHash deduplication, atomic reserve-before-send,
    and configurable frequency cooldowns.
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
        "home",
        "food",
        "coffee",
        "travel",
        "city",
        "pet",
        "outfit",
        "evening",
        "daytime",
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
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def compute_dhash(image_bytes: bytes, hash_size: int = 8) -> Optional[str]:
        """
        Computes 64-bit difference hash (dHash) for perceptual image deduplication.
        Returns 16-character hexadecimal string or None if Pillow fails.
        """
        try:
            from PIL import Image
            image = Image.open(io.BytesIO(image_bytes)).convert("L")
            # Resize to (width = hash_size + 1, height = hash_size) -> 9x8
            image = image.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            if hasattr(image, "get_flattened_data"):
                pixels = list(image.get_flattened_data())
            else:
                pixels = list(image.getdata())

            diff = []
            for row in range(hash_size):
                for col in range(hash_size):
                    left = pixels[row * (hash_size + 1) + col]
                    right = pixels[row * (hash_size + 1) + col + 1]
                    diff.append(1 if left > right else 0)

            # Convert 64 bits to 16 hex chars
            val = 0
            for bit in diff:
                val = (val << 1) | bit
            return f"{val:016x}"
        except Exception as e:
            logger.warning("[PHOTO_POOL] Could not compute dHash: %s", e)
            return None

    @staticmethod
    def hamming_distance(h1: Optional[str], h2: Optional[str]) -> int:
        """Calculates hamming bit distance between two hex hashes (0-64)."""
        if not h1 or not h2:
            return 999
        try:
            v1 = int(h1, 16)
            v2 = int(h2, 16)
            return bin(v1 ^ v2).count("1")
        except Exception:
            return 999

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
    ) -> Tuple[PersonaPhoto, bool]:
        """
        Ingests a photo into the persistent pool with deduplication.
        If an exact SHA-256 duplicate or perceptual duplicate (hamming distance <= threshold)
        already exists, returns (existing_photo, False).
        Otherwise saves to PHOTO_STORAGE_DIR, registers in DB, and returns (new_photo, True).
        """
        if not file_bytes:
            raise ValueError("Empty image data provided.")

        file_size = len(file_bytes)
        if file_size > cls.MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File size {file_size} exceeds maximum {cls.MAX_FILE_SIZE_BYTES} bytes.")

        # Compute Hashes
        sha256_hash = cls.compute_sha256(file_bytes)
        dhash = cls.compute_dhash(file_bytes)

        # 1. Exact cryptographic duplicate check
        stmt_exact = select(PersonaPhoto).where(PersonaPhoto.file_hash == sha256_hash)
        res_exact = await session.execute(stmt_exact)
        existing_exact = res_exact.scalars().first()
        if existing_exact:
            logger.info(
                "[PHOTO_POOL] Exact duplicate detected for sha256=%s (existing photo_id=%s)",
                sha256_hash[:10],
                existing_exact.photo_id,
            )
            return existing_exact, False

        # 2. Perceptual duplicate check
        if dhash:
            stmt_all_dhash = select(PersonaPhoto).where(PersonaPhoto.perceptual_hash.isnot(None))
            res_dhash = await session.execute(stmt_all_dhash)
            all_photos_with_dhash = res_dhash.scalars().all()
            for p in all_photos_with_dhash:
                dist = cls.hamming_distance(dhash, p.perceptual_hash)
                if dist <= hamming_threshold:
                    logger.info(
                        "[PHOTO_POOL] Perceptual duplicate detected: dist=%d <= %d with photo_id=%s",
                        dist,
                        hamming_threshold,
                        p.photo_id,
                    )
                    return p, False

        # 3. New photo: store file on disk
        photo_id = f"ph_{uuid.uuid4().hex[:12]}"
        clean_ext = Path(filename).suffix.lower()
        if clean_ext not in [".jpg", ".jpeg", ".png", ".webp"]:
            clean_ext = ".jpg"

        storage_dir = Path(getattr(settings, "PHOTO_STORAGE_DIR", "storage/photos"))
        storage_dir.mkdir(parents=True, exist_ok=True)
        disk_filename = f"{photo_id}{clean_ext}"
        storage_path = str(storage_dir / disk_filename)

        with open(storage_path, "wb") as f:
            f.write(file_bytes)

        # Determine MIME type
        mime = "image/jpeg"
        if clean_ext == ".png":
            mime = "image/png"
        elif clean_ext == ".webp":
            mime = "image/webp"

        cat = category.lower().strip() if category else "casual"
        if cat not in cls.CATEGORIES and cat:
            cat = cat

        new_photo = PersonaPhoto(
            photo_id=photo_id,
            user_id=user_id,
            persona_id=persona_id,
            telegram_file_id=telegram_file_id,
            file_hash=sha256_hash,
            perceptual_hash=dhash,
            storage_path=storage_path,
            file_size=file_size,
            mime_type=mime,
            category=cat,
            description=description.strip(),
            enabled=enabled,
            times_used=0,
            created_at=datetime.now(timezone.utc),
        )
        if tags:
            new_photo.set_tags(tags)
        if mood:
            new_photo.set_mood(mood)
        if contexts:
            new_photo.set_contexts(contexts)

        session.add(new_photo)
        await session.commit()
        await session.refresh(new_photo)

        logger.info(
            "[PHOTO_POOL] Added new photo: photo_id=%s | category=%s | size=%d bytes | path=%s",
            photo_id,
            cat,
            file_size,
            storage_path,
        )
        return new_photo, True

    # =========================================================================
    # 3. PER-CHAT USAGE & FREQUENCY LIMITS
    # =========================================================================

    @classmethod
    async def is_photo_used_in_chat(
        cls, session: AsyncSession, chat_id: Union[str, int], photo_id: str
    ) -> bool:
        """
        Checks whether photo_id was ever sent or reserved in chat_id.
        """
        stmt = (
            select(ChatPhotoUsage.id)
            .where(
                ChatPhotoUsage.chat_id == str(chat_id),
                ChatPhotoUsage.photo_id == photo_id,
                ChatPhotoUsage.status.in_(["reserved", "sent"]),
            )
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none() is not None

    @classmethod
    async def get_used_photo_ids_for_chat(
        cls, session: AsyncSession, chat_id: Union[str, int]
    ) -> List[str]:
        """
        Returns list of all photo_ids that have ever been reserved or sent in this chat.
        """
        stmt = select(ChatPhotoUsage.photo_id).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.status.in_(["reserved", "sent"]),
        )
        res = await session.execute(stmt)
        return [row[0] for row in res.fetchall()]

    @classmethod
    async def check_chat_frequency_limits(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        is_explicit_request: bool = False,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Verifies rarity constraints for spontaneous photos.
        Explicit user requests bypass spontaneous cooldown, but NEVER bypass the no-repeat rule.

        Returns:
            (allowed: bool, reason: str)
        """
        if is_explicit_request:
            return True, "explicit_user_request"

        current_time = now or datetime.now(timezone.utc)
        cooldown_hours = float(getattr(settings, "PHOTO_COOLDOWN_HOURS", 12.0))
        max_daily = int(getattr(settings, "MAX_SPONTANEOUS_PHOTOS_PER_DAY", 1))
        max_weekly = int(getattr(settings, "MAX_SPONTANEOUS_PHOTOS_PER_WEEK", 4))

        # 1. Check time since last sent photo
        stmt_last = (
            select(ChatPhotoUsage)
            .where(
                ChatPhotoUsage.chat_id == str(chat_id),
                ChatPhotoUsage.status == "sent",
            )
            .order_by(desc(ChatPhotoUsage.first_used_at))
            .limit(1)
        )
        res_last = await session.execute(stmt_last)
        last_usage = res_last.scalars().first()

        if last_usage and last_usage.first_used_at:
            last_ts = last_usage.first_used_at
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            delta = current_time - last_ts
            if delta < timedelta(hours=cooldown_hours):
                hrs_left = round((timedelta(hours=cooldown_hours) - delta).total_seconds() / 3600.0, 1)
                return False, f"cooldown_active_{hrs_left}h_remaining"

        # 2. Check 24-hour limit
        day_ago = current_time - timedelta(days=1)
        stmt_day = select(func.count(ChatPhotoUsage.id)).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.status == "sent",
            ChatPhotoUsage.first_used_at >= day_ago,
        )
        day_count = (await session.execute(stmt_day)).scalar() or 0
        if day_count >= max_daily:
            return False, f"daily_spontaneous_limit_reached_{day_count}/{max_daily}"

        # 3. Check 7-day limit
        week_ago = current_time - timedelta(days=7)
        stmt_week = select(func.count(ChatPhotoUsage.id)).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.status == "sent",
            ChatPhotoUsage.first_used_at >= week_ago,
        )
        week_count = (await session.execute(stmt_week)).scalar() or 0
        if week_count >= max_weekly:
            return False, f"weekly_spontaneous_limit_reached_{week_count}/{max_weekly}"

        return True, "frequency_limits_ok"

    @classmethod
    async def get_recent_sent_categories(
        cls, session: AsyncSession, chat_id: Union[str, int], limit: int = 3
    ) -> List[str]:
        """Returns categories of most recently sent photos in this chat."""
        stmt = (
            select(ChatPhotoUsage.category)
            .where(
                ChatPhotoUsage.chat_id == str(chat_id),
                ChatPhotoUsage.status == "sent",
                ChatPhotoUsage.category.isnot(None),
            )
            .order_by(desc(ChatPhotoUsage.first_used_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return [row[0] for row in res.fetchall() if row[0]]

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
    ) -> Optional[PersonaPhoto]:
        """
        Executes candidate selection pipeline with absolute no-repeat enforcement
        and atomic reservation (reserve-before-send):

        1. Check frequency limits (cooldown, daily, weekly) unless explicit user request.
        2. Query enabled photos.
        3. Exclude any photo already in chat_photo_usage for this chat_id.
        4. If candidates is empty -> return None (EXHAUSTED - NEVER REUSE OLD PHOTOS).
        5. Score and rank candidates based on:
           - Category matching
           - Tag matching
           - Time-of-day contextual suitability
           - Visual variety (soft penalty for recently sent category in this chat)
           - Times sent globally (prefer less used for global persona variety)
        6. Attempt ATOMIC reservation via INSERT INTO chat_photo_usage with status='reserved'.
           If a race condition occurs, UniqueConstraint catches it and retries next candidate.
        7. Returns reserved PersonaPhoto or None.
        """
        chat_id_str = str(chat_id)

        # 1. Frequency limits check
        freq_ok, freq_reason = await cls.check_chat_frequency_limits(
            session=session, chat_id=chat_id_str, is_explicit_request=is_explicit_request
        )
        if not freq_ok:
            logger.info(
                "[PHOTO_POOL] Photo sending rejected for chat %s: %s",
                chat_id_str,
                freq_reason,
            )
            return None

        # 2. Exclude all photos used or reserved in this chat
        subquery_used = (
            select(ChatPhotoUsage.photo_id)
            .where(
                ChatPhotoUsage.chat_id == chat_id_str,
                ChatPhotoUsage.status.in_(["reserved", "sent"]),
            )
        )

        # 3. Base candidate query
        stmt = (
            select(PersonaPhoto)
            .where(
                PersonaPhoto.enabled.is_(True),
                PersonaPhoto.photo_id.not_in(subquery_used),
            )
        )
        if user_id is not None:
            stmt = stmt.where(
                (PersonaPhoto.user_id == user_id) | (PersonaPhoto.user_id.is_(None))
            )

        res = await session.execute(stmt)
        candidates = list(res.scalars().all())

        if not candidates:
            logger.info(
                "[PHOTO_POOL] Pool exhausted for chat %s. Absolute no-repeat rule enforced: NO PHOTO SENT.",
                chat_id_str,
            )
            return None

        # 4. Contextual scoring & variety ranking
        recent_categories = await cls.get_recent_sent_categories(session, chat_id_str, limit=2)
        last_cat = recent_categories[0] if recent_categories else None

        scored_candidates: List[Tuple[float, PersonaPhoto]] = []
        target_tags = [t.lower().strip() for t in (preferred_tags or []) if t]
        target_cat = preferred_category.lower().strip() if preferred_category else None

        for photo in candidates:
            score = 10.0  # Base score
            p_cat = (photo.category or "").lower()
            p_tags = [t.lower() for t in photo.get_tags()]
            p_moods = [m.lower() for m in photo.get_mood()]
            p_ctx = [c.lower() for c in photo.get_contexts()]

            # Category matching
            if target_cat and p_cat == target_cat:
                score += 15.0

            # Tags matching
            for t in target_tags:
                if t in p_tags or t in p_ctx or t in p_moods:
                    score += 8.0

            # Variety: soft penalty if matching immediately previous category in this chat
            if last_cat and p_cat == last_cat and len(candidates) > 1:
                score -= 6.0

            # Time of day suitability
            if current_hour is not None:
                if 6 <= current_hour <= 11:  # Morning
                    if any(x in p_tags or x in p_ctx for x in ["morning", "coffee", "breakfast", "cafe"]):
                        score += 5.0
                elif 12 <= current_hour <= 17:  # Daytime / Afternoon
                    if any(x in p_tags or x in p_ctx for x in ["daytime", "walk", "city", "work", "lunch"]):
                        score += 3.0
                elif 18 <= current_hour <= 23:  # Evening
                    if any(x in p_tags or x in p_ctx for x in ["evening", "dinner", "sunset", "home", "relaxed"]):
                        score += 5.0
                else:  # Night
                    if any(x in p_tags or x in p_ctx for x in ["night", "cozy", "home"]):
                        score += 4.0

            # Global distribution: slight preference for photos sent fewer times globally
            score -= min(float(photo.times_used) * 0.5, 5.0)

            scored_candidates.append((score, photo))

        # Sort highest score first
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        # 5. Atomic reservation loop (handles potential concurrency race conditions)
        for _, chosen in scored_candidates:
            try:
                usage = ChatPhotoUsage(
                    chat_id=chat_id_str,
                    photo_id=chosen.photo_id,
                    status="reserved",
                    first_used_at=datetime.now(timezone.utc),
                    reason=reason or ("explicit_request" if is_explicit_request else "contextual"),
                    category=chosen.category,
                    selection_context=json.dumps(
                        {
                            "is_explicit": is_explicit_request,
                            "preferred_category": preferred_category,
                            "preferred_tags": preferred_tags,
                            "current_hour": current_hour,
                        },
                        ensure_ascii=False,
                    ),
                )
                session.add(usage)
                chosen.times_used += 1
                await session.commit()
                await session.refresh(chosen)

                logger.info(
                    "[PHOTO_POOL] Successfully RESERVED photo %s (category=%s) for chat %s",
                    chosen.photo_id,
                    chosen.category,
                    chat_id_str,
                )
                return chosen

            except IntegrityError:
                # Concurrent transaction already reserved or used this photo for this chat!
                await session.rollback()
                logger.warning(
                    "[PHOTO_POOL] Race condition caught: photo %s was concurrently reserved in chat %s. Trying next candidate.",
                    chosen.photo_id,
                    chat_id_str,
                )
                continue
            except Exception as e:
                await session.rollback()
                logger.error("[PHOTO_POOL] Error reserving photo %s: %s", chosen.photo_id, e)
                return None

        return None

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
    ) -> bool:
        """
        Transitions photo reservation status from 'reserved' to 'sent'
        and records the Telegram message_id.
        """
        stmt = select(ChatPhotoUsage).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.photo_id == photo_id,
        )
        res = await session.execute(stmt)
        usage = res.scalars().first()
        if usage:
            usage.status = "sent"
            if message_id:
                usage.message_id = message_id
            await session.commit()
            logger.info(
                "[PHOTO_POOL] Marked photo %s as SENT in chat %s (message_id=%s)",
                photo_id,
                chat_id,
                message_id,
            )
            return True
        return False

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
        If release_reservation is True (ONLY when 100% certain Telegram did not receive it),
        the reservation is deleted.
        Otherwise, status is updated to 'send_failed' (photo remains permanently blocked
        in this chat to eliminate any chance of repetition).
        """
        stmt = select(ChatPhotoUsage).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.photo_id == photo_id,
        )
        res = await session.execute(stmt)
        usage = res.scalars().first()
        if not usage:
            return False

        if release_reservation:
            await session.delete(usage)
            # Revert times_used counter
            stmt_p = select(PersonaPhoto).where(PersonaPhoto.photo_id == photo_id)
            p_res = await session.execute(stmt_p)
            p = p_res.scalars().first()
            if p and p.times_used > 0:
                p.times_used -= 1
            await session.commit()
            logger.info(
                "[PHOTO_POOL] Released reservation for photo %s in chat %s (verified unsent)",
                photo_id,
                chat_id,
            )
            return True
        else:
            usage.status = "send_failed"
            await session.commit()
            logger.warning(
                "[PHOTO_POOL] Marked photo %s as 'send_failed' in chat %s (remains BLOCKED)",
                photo_id,
                chat_id,
            )
            return True

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
        stmt = select(PersonaPhoto)
        if enabled_only:
            stmt = stmt.where(PersonaPhoto.enabled.is_(True))
        if category:
            stmt = stmt.where(PersonaPhoto.category == category.lower().strip())
        stmt = stmt.order_by(desc(PersonaPhoto.created_at)).offset(offset).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_photo(
        cls, session: AsyncSession, photo_id: str
    ) -> Optional[PersonaPhoto]:
        """Fetches a single photo by its stable photo_id."""
        stmt = select(PersonaPhoto).where(PersonaPhoto.photo_id == photo_id)
        res = await session.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def toggle_photo_status(
        cls, session: AsyncSession, photo_id: str, enabled: bool
    ) -> Optional[PersonaPhoto]:
        """Enables or disables a photo in the pool."""
        photo = await cls.get_photo(session, photo_id)
        if photo:
            photo.enabled = enabled
            await session.commit()
            await session.refresh(photo)
        return photo

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
        """
        Deletes a photo from the pool.
        Existing chat_photo_usage records are kept or cascade-deleted depending on schema,
        ensuring usage tracking integrity is preserved.
        """
        photo = await cls.get_photo(session, photo_id)
        if not photo:
            return False

        file_path = photo.storage_path
        await session.delete(photo)
        await session.commit()

        if delete_file and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.warning("[PHOTO_POOL] Failed to remove file %s: %s", file_path, e)

        return True

    @classmethod
    async def get_pool_stats(cls, session: AsyncSession) -> Dict[str, Any]:
        """Returns overview statistics of the photo pool."""
        total_stmt = select(func.count(PersonaPhoto.id))
        total_photos = (await session.execute(total_stmt)).scalar() or 0

        enabled_stmt = select(func.count(PersonaPhoto.id)).where(PersonaPhoto.enabled.is_(True))
        enabled_photos = (await session.execute(enabled_stmt)).scalar() or 0

        usages_stmt = select(func.count(ChatPhotoUsage.id)).where(ChatPhotoUsage.status == "sent")
        total_sent = (await session.execute(usages_stmt)).scalar() or 0

        distinct_chats_stmt = select(func.count(func.distinct(ChatPhotoUsage.chat_id)))
        active_chats = (await session.execute(distinct_chats_stmt)).scalar() or 0

        # Category distribution
        cat_stmt = select(PersonaPhoto.category, func.count(PersonaPhoto.id)).group_by(PersonaPhoto.category)
        cat_res = await session.execute(cat_stmt)
        categories = {row[0]: row[1] for row in cat_res.fetchall()}

        return {
            "total_photos": total_photos,
            "enabled_photos": enabled_photos,
            "disabled_photos": total_photos - enabled_photos,
            "total_sent_events": total_sent,
            "distinct_chats_reached": active_chats,
            "categories": categories,
        }

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
                "message_id": usage.message_id,
                "category": usage.category or (photo.category if photo else None),
                "description": photo.description if photo else None,
            })
        return history
