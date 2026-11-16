"""
Dedicated Photo Subsystem (Sections 1–49).

Logical Components:
- PhotoRepository: Database CRUD for persistent photo records (PersonaPhoto).
- PhotoUploadManager: Owner authentication, PIL image validation, SHA-256 & dHash deduplication, persistent disk storage.
- PhotoCooldownService: Strictly per-chat 12-hour cooldown management keyed by chat_id.
- PhotoUsageRepository: Per-chat permanent no-repeat tracking & atomic reservation (UNIQUE(chat_id, photo_id)).
- PhotoEligibilityService: Centralized eligibility evaluation (cooldown, pool availability, global limits, active locks).
- PhotoSelector: Contextual & diversity-aware candidate ranking and atomic reservation.
- PhotoDecisionValidator: Validates semantic AI photo intent without trusting AI-generated IDs or cooldown overrides.
- PHOTO_SEND_GATE: Mandatory 10-point application-level hard gate before any photo delivery.
- PhotoSendService: End-to-end concurrency-safe photo delivery pipeline.
- PhotoAdminService: Owner-only photo pool management and statistics.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union
import uuid

from PIL import Image, UnidentifiedImageError
from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import ChatPhotoUsage, Contact, PersonaPhoto, PhotoCooldown
from companion.services.output_sanitizer import OutputSanitizer

logger = logging.getLogger(__name__)


# ==============================================================================
# 0. STRUCTURED OBSERVABILITY LOGGING (Section 36)
# ==============================================================================

def log_photo_event(
    event: str,
    chat_id: Optional[Union[str, int]] = None,
    photo_id: Optional[str] = None,
    result: str = "OK",
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    """
    Emits structured observability logs for the photo subsystem without leaking secrets or user content.
    Events:
      PHOTO_UPLOAD, PHOTO_DUPLICATE, PHOTO_SELECTED, PHOTO_RESERVATION_CREATED,
      PHOTO_SEND_STARTED, PHOTO_SEND_SUCCESS, PHOTO_SEND_FAILED,
      PHOTO_COOLDOWN_BLOCKED, PHOTO_ALREADY_USED, PHOTO_POOL_EMPTY, PHOTO_RESERVATION_CONFLICT
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "event": event,
        "chat_id": str(chat_id) if chat_id is not None else None,
        "photo_id": photo_id,
        "timestamp": ts,
        "result": result,
    }
    for k, v in extra.items():
        if k not in ("token", "api_key", "secret", "text", "caption"):
            payload[k] = v
    logger.log(level, "[PHOTO_SUBSYSTEM] %s | %s", event, json.dumps(payload, ensure_ascii=False))


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    utc_dt = _ensure_utc(dt)
    if utc_dt is None:
        return None
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ==============================================================================
# 1. DATA STRUCTURES: ELIGIBILITY RESULT & AI DECISION (Sections 15, 31)
# ==============================================================================

@dataclass
class PhotoEligibilityResult:
    """
    Structured eligibility result (Section 31).
    Reasons:
      ALLOWED, CHAT_COOLDOWN, NO_UNUSED_PHOTOS, GLOBAL_LIMIT,
      PHOTO_DISABLED, PHOTO_NOT_FOUND, RESERVATION_CONFLICT, STORAGE_ERROR
    """
    allowed: bool
    reason: str
    next_allowed_at: Optional[str] = None
    last_photo_sent_at: Optional[str] = None
    photos_sent_count: int = 0
    available_unused_photos: int = 0

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "next_allowed_at": self.next_allowed_at,
            "last_photo_sent_at": self.last_photo_sent_at,
            "photos_sent_count": self.photos_sent_count,
            "available_unused_photos": self.available_unused_photos,
        }


@dataclass
class PhotoDecision:
    """
    Semantic AI recommendation for whether a photo fits the conversation (Section 15 & 16).
    Default is should_send_photo = False.
    The AI cannot specify photo_id or override cooldowns.
    """
    should_send_photo: bool = False
    reason: str = ""
    preferred_category: Optional[str] = None
    preferred_tags: List[str] = field(default_factory=list)
    confidence: float = 0.0
    is_repeat_request: bool = False


class PhotoDecisionValidator:
    """
    Validates AI photo decisions and enforces Section 15, 16, 17, 32:
    - Default is should_send_photo = False (photos are rare and contextual).
    - Strips/ignores any AI-generated photo_id, file_path, or cooldown bypasses.
    """

    VALID_CATEGORIES = {
        "selfie", "casual", "outfit", "home", "outside", "outdoors", "city",
        "travel", "night", "day", "daytime", "evening", "smiling", "serious",
        "cute", "funny", "mirror", "food", "coffee", "cinema", "quest",
        "seasonal", "pet", "weather", "hobby", "random_life", "mood", "event",
    }

    @classmethod
    def validate_decision(cls, raw_decision: Optional[Union[Dict[str, Any], PhotoDecision, bool]]) -> PhotoDecision:
        if raw_decision is None or raw_decision is False:
            return PhotoDecision(should_send_photo=False, reason="no_photo_requested")

        if isinstance(raw_decision, PhotoDecision):
            return PhotoDecision(
                should_send_photo=bool(raw_decision.should_send_photo),
                reason=str(raw_decision.reason or ""),
                preferred_category=cls._clean_category(raw_decision.preferred_category),
                preferred_tags=cls._clean_tags(raw_decision.preferred_tags),
                confidence=float(raw_decision.confidence or 0.0),
                is_repeat_request=bool(raw_decision.is_repeat_request),
            )

        if isinstance(raw_decision, bool):
            return PhotoDecision(
                should_send_photo=raw_decision,
                reason="boolean_decision",
                confidence=0.8 if raw_decision else 0.0,
            )

        if isinstance(raw_decision, dict):
            # Section 32: Never trust an AI-generated photo_id or internal fields
            for forbidden_key in ("photo_id", "id", "file_path", "storage_path", "telegram_file_id", "bypass_cooldown"):
                if forbidden_key in raw_decision:
                    logger.warning(
                        "[PHOTO_VALIDATOR] Stripped untrusted AI field '%s' from photo decision",
                        forbidden_key,
                    )

            should_send = bool(raw_decision.get("should_send_photo", False))
            conf = float(raw_decision.get("confidence", 0.8 if should_send else 0.0))
            if conf < 0.5:
                should_send = False

            return PhotoDecision(
                should_send_photo=should_send,
                reason=str(raw_decision.get("reason") or "ai_contextual_decision")[:200],
                preferred_category=cls._clean_category(raw_decision.get("preferred_category") or raw_decision.get("category")),
                preferred_tags=cls._clean_tags(raw_decision.get("preferred_tags") or raw_decision.get("tags")),
                confidence=conf,
                is_repeat_request=bool(raw_decision.get("is_repeat_request", False)),
            )

        return PhotoDecision(should_send_photo=False, reason="invalid_decision_format")

    @classmethod
    def _clean_category(cls, cat: Any) -> Optional[str]:
        if not cat or not isinstance(cat, str):
            return None
        c = cat.strip().lower()
        return c if c else None

    @classmethod
    def _clean_tags(cls, tags: Any) -> List[str]:
        if not tags:
            return []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        if isinstance(tags, (list, tuple, set)):
            return [str(t).strip().lower() for t in tags if str(t).strip()]
        return []


# ==============================================================================
# 2. PHOTO REPOSITORY (Sections 1, 3, 5)
# ==============================================================================

class PhotoRepository:
    """Persistent database repository for PersonaPhoto records."""

    @classmethod
    async def get_by_photo_id(cls, session: AsyncSession, photo_id: str) -> Optional[PersonaPhoto]:
        if not photo_id:
            return None
        stmt = select(PersonaPhoto).where(PersonaPhoto.photo_id == str(photo_id))
        res = await session.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def get_by_sha256(cls, session: AsyncSession, sha256_hash: str) -> Optional[PersonaPhoto]:
        stmt = select(PersonaPhoto).where(PersonaPhoto.file_hash == sha256_hash)
        res = await session.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def get_all_with_dhash(cls, session: AsyncSession) -> List[PersonaPhoto]:
        stmt = select(PersonaPhoto).where(PersonaPhoto.perceptual_hash.isnot(None))
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def list_photos(
        cls,
        session: AsyncSession,
        active_only: bool = False,
        category: Optional[str] = None,
        persona_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PersonaPhoto]:
        stmt = select(PersonaPhoto)
        if active_only:
            stmt = stmt.where(PersonaPhoto.enabled.is_(True))
        if category:
            stmt = stmt.where(PersonaPhoto.category == category.lower().strip())
        if persona_id is not None:
            stmt = stmt.where(PersonaPhoto.persona_id == persona_id)
        elif user_id is not None:
            stmt = stmt.where(PersonaPhoto.user_id == user_id)
        stmt = stmt.order_by(desc(PersonaPhoto.created_at), desc(PersonaPhoto.id)).offset(offset).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_persona_photo_stats(
        cls,
        session: AsyncSession,
        persona_id: Optional[int],
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Returns photo counts and category breakdown for a specific model persona."""
        conds = []
        if persona_id is not None:
            conds.append(PersonaPhoto.persona_id == persona_id)
        elif user_id is not None:
            conds.append(PersonaPhoto.user_id == user_id)

        stmt_total = select(func.count(PersonaPhoto.id))
        stmt_active = select(func.count(PersonaPhoto.id)).where(PersonaPhoto.enabled.is_(True))
        stmt_cats = select(PersonaPhoto.category, func.count(PersonaPhoto.id)).group_by(PersonaPhoto.category)
        if conds:
            stmt_total = stmt_total.where(*conds)
            stmt_active = stmt_active.where(*conds)
            stmt_cats = stmt_cats.where(*conds)

        total = (await session.execute(stmt_total)).scalar() or 0
        active = (await session.execute(stmt_active)).scalar() or 0
        cats = {row[0]: row[1] for row in (await session.execute(stmt_cats)).fetchall() if row[0]}
        return {
            "total_photos": total,
            "active_photos": active,
            "disabled_photos": max(0, total - active),
            "categories": cats,
        }

    @classmethod
    async def clear_persona_photos(
        cls,
        session: AsyncSession,
        persona_id: int,
        delete_files: bool = True,
    ) -> int:
        """Deletes all photos bound to a specific model persona_id."""
        photos = await cls.list_photos(session, persona_id=persona_id, limit=10000)
        if not photos:
            return 0
        photo_ids = [p.photo_id for p in photos]
        file_paths = [p.storage_path for p in photos if p.storage_path]
        await session.execute(delete(ChatPhotoUsage).where(ChatPhotoUsage.photo_id.in_(photo_ids)))
        await session.execute(delete(PersonaPhoto).where(PersonaPhoto.persona_id == persona_id))
        await session.commit()
        if delete_files:
            for fp in file_paths:
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
        return len(photos)

    @classmethod
    async def set_active(cls, session: AsyncSession, photo_id: str, is_active: bool) -> Optional[PersonaPhoto]:
        photo = await cls.get_by_photo_id(session, photo_id)
        if photo:
            photo.enabled = bool(is_active)
            await session.commit()
            await session.refresh(photo)
        return photo

    @classmethod
    async def delete_photo(cls, session: AsyncSession, photo_id: str, delete_file: bool = True) -> bool:
        photo = await cls.get_by_photo_id(session, photo_id)
        if not photo:
            return False
        file_path = photo.storage_path
        await session.delete(photo)
        await session.commit()
        if delete_file and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.warning("[PHOTO_REPO] Failed to remove file %s: %s", file_path, e)
        return True

    @classmethod
    async def clear_all_photos(cls, session: AsyncSession, delete_files: bool = True) -> int:
        photos = await cls.list_photos(session, limit=10000)
        count = len(photos)
        file_paths = [p.storage_path for p in photos if p.storage_path]
        await session.execute(delete(ChatPhotoUsage))
        await session.execute(delete(PersonaPhoto))
        await session.commit()
        if delete_files:
            for fp in file_paths:
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
        return count


# ==============================================================================
# 3. PHOTO UPLOAD MANAGER (Sections 2, 4, 23, 24, 25)
# ==============================================================================

class PhotoUploadManager:
    """
    Handles owner-authenticated photo upload, strict image validation,
    SHA-256 & perceptual dHash duplicate detection, and atomic file + DB storage.
    """

    FORMAT_TO_MIME_EXT = {
        "JPEG": ("image/jpeg", ".jpg"),
        "JPG": ("image/jpeg", ".jpg"),
        "PNG": ("image/png", ".png"),
        "WEBP": ("image/webp", ".webp"),
    }

    @staticmethod
    def verify_owner(sender_id: Optional[int], configured_owner_id: Optional[int] = None) -> bool:
        """Verifies that sender_id is the configured owner/admin."""
        if sender_id is None:
            return False
        owner_id = configured_owner_id or getattr(settings, "ADMIN_TELEGRAM_ID", 0)
        return bool(owner_id and int(sender_id) == int(owner_id))

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def compute_dhash(image_bytes: bytes, hash_size: int = 8) -> Optional[str]:
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("L")
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

            val = 0
            for bit in diff:
                val = (val << 1) | bit
            return f"{val:016x}"
        except Exception as e:
            logger.warning("[PHOTO_UPLOAD] Could not compute dHash: %s", e)
            return None

    @staticmethod
    def hamming_distance(h1: Optional[str], h2: Optional[str]) -> int:
        if not h1 or not h2:
            return 999
        try:
            return bin(int(h1, 16) ^ int(h2, 16)).count("1")
        except Exception:
            return 999

    @classmethod
    def validate_image_bytes(
        cls,
        file_bytes: bytes,
        max_size_mb: Optional[float] = None,
    ) -> Tuple[int, int, str, str]:
        """
        Section 24: Validates uploaded photo bytes.
        Checks: non-empty, max size, actual readable image (not trusting filename extension),
        allowed format (JPEG/PNG/WEBP), and valid non-zero dimensions.
        Returns: (width, height, mime_type, file_extension)
        """
        if not file_bytes or not isinstance(file_bytes, (bytes, bytearray)):
            raise ValueError("Empty image data provided.")

        max_mb = float(max_size_mb or getattr(settings, "PHOTO_MAX_FILE_SIZE_MB", 10))
        max_bytes = int(max_mb * 1024 * 1024)
        file_size = len(file_bytes)
        if file_size > max_bytes:
            raise ValueError(f"File size {file_size} bytes exceeds maximum {max_bytes} bytes ({max_mb} MB).")

        try:
            # 1. Verify image integrity
            with Image.open(io.BytesIO(file_bytes)) as img_verify:
                img_verify.verify()

            # 2. Re-open to inspect dimensions and format
            with Image.open(io.BytesIO(file_bytes)) as img:
                img.load()
                fmt = (img.format or "").upper()
                width, height = img.size
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise ValueError(f"Invalid or corrupted image file: {exc}") from exc

        if fmt not in cls.FORMAT_TO_MIME_EXT:
            raise ValueError(f"Unsupported image format '{fmt}'. Allowed: JPEG, PNG, WEBP.")

        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid image dimensions: {width}x{height}.")

        mime_type, ext = cls.FORMAT_TO_MIME_EXT[fmt]
        return width, height, mime_type, ext

    @classmethod
    async def upload_photo(
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
        Executes the full Section 23 Photo Upload Flow:
        Authenticate Owner (if sender_tg_id provided) -> Validate File -> Calculate SHA-256 ->
        Check Exact Duplicate -> Calculate dHash & Check Perceptual Duplicate ->
        Store File on Persistent Disk -> Create DB Record (cleaning up file if DB fails).
        Returns: (PersonaPhoto, is_new)
        """
        if sender_tg_id is not None and not cls.verify_owner(sender_tg_id, configured_owner_id):
            raise PermissionError(f"Sender {sender_tg_id} is not authorized to upload persona photos.")

        width, height, mime_type, clean_ext = cls.validate_image_bytes(file_bytes)
        file_size = len(file_bytes)

        sha256_hash = cls.compute_sha256(file_bytes)
        dhash = cls.compute_dhash(file_bytes)

        # 1. Exact duplicate check (SHA-256)
        existing_exact = await PhotoRepository.get_by_sha256(session, sha256_hash)
        if existing_exact:
            if persona_id is not None and existing_exact.persona_id != persona_id:
                existing_exact.persona_id = persona_id
                if user_id is not None:
                    existing_exact.user_id = user_id
                await session.commit()
                await session.refresh(existing_exact)
                log_photo_event(
                    "PHOTO_UPLOAD",
                    photo_id=existing_exact.photo_id,
                    result="REBOUND_TO_PERSONA",
                    persona_id=persona_id,
                )
                return existing_exact, True
            log_photo_event(
                "PHOTO_DUPLICATE",
                photo_id=existing_exact.photo_id,
                result="EXACT_SHA256_MATCH",
                sha256=sha256_hash[:12],
            )
            return existing_exact, False

        # 2. Perceptual duplicate check (dHash) within same persona scope
        if dhash and hamming_threshold >= 0:
            all_dhash_photos = await PhotoRepository.get_all_with_dhash(session)
            for p in all_dhash_photos:
                if persona_id is not None and p.persona_id is not None and p.persona_id != persona_id:
                    continue
                dist = cls.hamming_distance(dhash, p.perceptual_hash)
                if dist <= hamming_threshold:
                    if persona_id is not None and p.persona_id is None:
                        p.persona_id = persona_id
                        if user_id is not None:
                            p.user_id = user_id
                        await session.commit()
                        await session.refresh(p)
                        return p, True
                    log_photo_event(
                        "PHOTO_DUPLICATE",
                        photo_id=p.photo_id,
                        result="PERCEPTUAL_DHASH_MATCH",
                        distance=dist,
                    )
                    return p, False

        # 3. Store file on persistent storage
        photo_id = f"ph_{uuid.uuid4().hex[:12]}"
        storage_dir = Path(getattr(settings, "PHOTO_STORAGE_DIR", "storage/photos"))
        storage_dir.mkdir(parents=True, exist_ok=True)
        disk_filename = f"{photo_id}{clean_ext}"
        storage_path = str(storage_dir / disk_filename)

        try:
            with open(storage_path, "wb") as f:
                f.write(file_bytes)

            if not os.path.exists(storage_path) or os.path.getsize(storage_path) != file_size:
                raise OSError("Failed to verify written photo file on disk.")

            cat = (category or "casual").strip().lower() or "casual"
            new_photo = PersonaPhoto(
                photo_id=photo_id,
                user_id=user_id,
                persona_id=persona_id,
                telegram_file_id=telegram_file_id,
                file_hash=sha256_hash,
                perceptual_hash=dhash,
                storage_path=storage_path,
                original_filename=os.path.basename(filename) if filename else disk_filename,
                file_size=file_size,
                width=width,
                height=height,
                mime_type=mime_type,
                category=cat,
                description=(description or "").strip(),
                enabled=bool(enabled),
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

            log_photo_event(
                "PHOTO_UPLOAD",
                photo_id=photo_id,
                result="CREATED",
                category=cat,
                width=width,
                height=height,
                size_bytes=file_size,
            )
            return new_photo, True

        except Exception as exc:
            await session.rollback()
            # Clean up partial file on disk (Section 23)
            if os.path.exists(storage_path):
                try:
                    os.remove(storage_path)
                except Exception:
                    pass
            logger.error("[PHOTO_UPLOAD] Failed to store photo: %s", exc)
            raise


# ==============================================================================
# 4. PHOTO COOLDOWN SERVICE (Sections 6, 7, 8, 9, 20, 21, 30)
# ==============================================================================

class PhotoCooldownService:
    """
    Centralized per-chat 12-hour photo cooldown service.
    Strictly keyed by chat_id. Never uses a global last_photo_sent_at.
    Starts the 12-hour timer ONLY after confirmed Telegram delivery.
    """

    @staticmethod
    def get_cooldown_duration() -> timedelta:
        hours = float(
            getattr(settings, "PHOTO_CHAT_COOLDOWN_HOURS", None)
            or getattr(settings, "PHOTO_COOLDOWN_HOURS", 12.0)
        )
        return timedelta(hours=hours)

    @classmethod
    async def get_last_photo_sent_at(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
    ) -> Optional[datetime]:
        """
        Returns the UTC timestamp of the most recent confirmed photo sent to chat_id,
        checking both PhotoCooldown and ChatPhotoUsage (status='sent') for full durability.
        """
        chat_key = str(chat_id)

        # 1. Check dedicated PhotoCooldown table
        stmt_cd = select(PhotoCooldown).where(PhotoCooldown.chat_id == chat_key)
        res_cd = await session.execute(stmt_cd)
        cd_row = res_cd.scalars().first()
        ts_cd = _ensure_utc(cd_row.last_photo_sent_at) if (cd_row and cd_row.last_photo_sent_at) else None

        # 2. Check ChatPhotoUsage table (status == 'sent')
        stmt_u = (
            select(ChatPhotoUsage)
            .where(
                ChatPhotoUsage.chat_id == chat_key,
                ChatPhotoUsage.status == "sent",
            )
            .order_by(desc(func.coalesce(ChatPhotoUsage.sent_at, ChatPhotoUsage.first_used_at)))
            .limit(1)
        )
        res_u = await session.execute(stmt_u)
        u_row = res_u.scalars().first()
        ts_u = None
        if u_row:
            raw_u_ts = getattr(u_row, "sent_at", None) or u_row.first_used_at
            ts_u = _ensure_utc(raw_u_ts)

        if ts_cd and ts_u:
            return max(ts_cd, ts_u)
        return ts_cd or ts_u

    @classmethod
    async def get_photo_cooldown(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Returns structured cooldown information for chat_id (Section 30).
        If the chat has never received a photo, last_photo_sent_at is None and eligible is True.
        """
        chat_key = str(chat_id)
        current_time = _ensure_utc(now) or datetime.now(timezone.utc)
        cooldown_delta = cls.get_cooldown_duration()

        last_sent = await cls.get_last_photo_sent_at(session, chat_key)
        if last_sent is None:
            return {
                "chat_id": chat_key,
                "eligible": True,
                "cooldown_active": False,
                "last_photo_sent_at": None,
                "next_allowed_at": None,
                "last_photo_sent_at_iso": None,
                "next_allowed_at_iso": None,
                "remaining_seconds": 0.0,
            }

        next_allowed = last_sent + cooldown_delta
        elapsed = current_time - last_sent
        is_eligible = elapsed >= cooldown_delta
        remaining = max(0.0, (next_allowed - current_time).total_seconds())

        return {
            "chat_id": chat_key,
            "eligible": is_eligible,
            "cooldown_active": not is_eligible,
            "last_photo_sent_at": last_sent,
            "next_allowed_at": next_allowed,
            "last_photo_sent_at_iso": _format_utc_iso(last_sent),
            "next_allowed_at_iso": _format_utc_iso(next_allowed),
            "remaining_seconds": round(remaining, 2),
        }

    @classmethod
    async def can_send_photo(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        now: Optional[datetime] = None,
    ) -> bool:
        """Returns True if the 12-hour per-chat cooldown allows sending a photo right now."""
        info = await cls.get_photo_cooldown(session, chat_id, now=now)
        return bool(info["eligible"])

    @classmethod
    async def record_photo_sent(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
        telegram_message_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> PhotoCooldown:
        """
        Records confirmed photo delivery for chat_id (Section 9 & 30).
        Starts the 12-hour cooldown timer at `now` (UTC) and marks ChatPhotoUsage as 'sent'.
        """
        chat_key = str(chat_id)
        sent_time = _ensure_utc(now) or datetime.now(timezone.utc)

        # 1. Upsert PhotoCooldown row for chat_id
        stmt_cd = select(PhotoCooldown).where(PhotoCooldown.chat_id == chat_key)
        res_cd = await session.execute(stmt_cd)
        cd_row = res_cd.scalars().first()
        if cd_row:
            cd_row.last_photo_sent_at = sent_time
            cd_row.last_photo_id = str(photo_id)
            cd_row.updated_at = sent_time
        else:
            cd_row = PhotoCooldown(
                chat_id=chat_key,
                last_photo_sent_at=sent_time,
                last_photo_id=str(photo_id),
                updated_at=sent_time,
            )
            session.add(cd_row)

        # 2. Upsert ChatPhotoUsage row as 'sent'
        stmt_u = select(ChatPhotoUsage).where(
            ChatPhotoUsage.chat_id == chat_key,
            ChatPhotoUsage.photo_id == str(photo_id),
        )
        res_u = await session.execute(stmt_u)
        usage = res_u.scalars().first()
        if usage:
            usage.status = "sent"
            usage.sent_at = sent_time
            usage.first_used_at = sent_time
            if telegram_message_id is not None:
                usage.message_id = telegram_message_id
        else:
            photo_obj = await PhotoRepository.get_by_photo_id(session, str(photo_id))
            usage = ChatPhotoUsage(
                chat_id=chat_key,
                photo_id=str(photo_id),
                status="sent",
                first_used_at=sent_time,
                sent_at=sent_time,
                message_id=telegram_message_id,
                category=photo_obj.category if photo_obj else "casual",
                reason="confirmed_send",
            )
            session.add(usage)
            if photo_obj:
                photo_obj.times_used = (photo_obj.times_used or 0) + 1

        await session.commit()
        return cd_row


# ==============================================================================
# 5. PHOTO USAGE REPOSITORY (Sections 10, 13, 14)
# ==============================================================================

class PhotoUsageRepository:
    """
    Manages persistent per-chat photo usage & atomic reservations.
    Enforces UNIQUE(chat_id, photo_id) so a photo is NEVER repeated in the same chat.
    """

    @classmethod
    async def is_photo_used_in_chat(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
    ) -> bool:
        stmt = (
            select(ChatPhotoUsage.id)
            .where(
                ChatPhotoUsage.chat_id == str(chat_id),
                ChatPhotoUsage.photo_id == str(photo_id),
                ChatPhotoUsage.status.in_(["reserved", "sent", "send_failed", "uncertain"]),
            )
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none() is not None

    @classmethod
    async def is_photo_permanently_sent_in_chat(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
    ) -> bool:
        stmt = (
            select(ChatPhotoUsage.id)
            .where(
                ChatPhotoUsage.chat_id == str(chat_id),
                ChatPhotoUsage.photo_id == str(photo_id),
                ChatPhotoUsage.status.in_(["sent", "send_failed", "uncertain"]),
            )
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none() is not None

    @classmethod
    async def get_used_photo_ids(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
    ) -> List[str]:
        stmt = select(ChatPhotoUsage.photo_id).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.status.in_(["reserved", "sent", "send_failed", "uncertain"]),
        )
        res = await session.execute(stmt)
        return [row[0] for row in res.fetchall()]

    @classmethod
    async def has_active_reservation_in_chat(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        exclude_photo_id: Optional[str] = None,
    ) -> bool:
        """Checks if chat_id currently has an in-flight 'reserved' photo send."""
        stmt = select(ChatPhotoUsage.id).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.status == "reserved",
        )
        if exclude_photo_id:
            stmt = stmt.where(ChatPhotoUsage.photo_id != str(exclude_photo_id))
        stmt = stmt.limit(1)
        res = await session.execute(stmt)
        return res.scalar_one_or_none() is not None

    @classmethod
    async def reserve_photo_atomically(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo: PersonaPhoto,
        reason: str = "contextual",
        selection_context: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Atomically reserves (chat_id, photo.photo_id) with status='reserved'.
        Returns True if reservation succeeded, False on conflict.
        """
        chat_key = str(chat_id)
        ts = _ensure_utc(now) or datetime.now(timezone.utc)

        # Check if another photo is already reserved in this chat
        if await cls.has_active_reservation_in_chat(session, chat_key):
            log_photo_event(
                "PHOTO_RESERVATION_CONFLICT",
                chat_id=chat_key,
                photo_id=photo.photo_id,
                result="CHAT_ALREADY_HAS_ACTIVE_RESERVATION",
                level=logging.WARNING,
            )
            return False

        try:
            usage = ChatPhotoUsage(
                chat_id=chat_key,
                photo_id=photo.photo_id,
                status="reserved",
                first_used_at=ts,
                reason=reason,
                category=photo.category,
                selection_context=json.dumps(selection_context or {}, ensure_ascii=False),
            )
            session.add(usage)
            photo.times_used = (photo.times_used or 0) + 1
            await session.commit()
            await session.refresh(photo)

            log_photo_event(
                "PHOTO_RESERVATION_CREATED",
                chat_id=chat_key,
                photo_id=photo.photo_id,
                result="RESERVED",
                category=photo.category,
            )
            return True
        except IntegrityError:
            await session.rollback()
            log_photo_event(
                "PHOTO_RESERVATION_CONFLICT",
                chat_id=chat_key,
                photo_id=photo.photo_id,
                result="UNIQUE_CONSTRAINT_CONFLICT",
                level=logging.WARNING,
            )
            return False
        except Exception as exc:
            await session.rollback()
            logger.error("[PHOTO_USAGE] Failed to reserve photo %s in chat %s: %s", photo.photo_id, chat_key, exc)
            return False

    @classmethod
    async def release_reservation(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
    ) -> bool:
        """
        Releases a reservation when Telegram send definitely failed before delivery (Section 14).
        Deletes the ChatPhotoUsage row and leaves PhotoCooldown unchanged.
        """
        chat_key = str(chat_id)
        stmt = select(ChatPhotoUsage).where(
            ChatPhotoUsage.chat_id == chat_key,
            ChatPhotoUsage.photo_id == str(photo_id),
        )
        res = await session.execute(stmt)
        usage = res.scalars().first()
        if not usage:
            return False

        await session.delete(usage)
        photo = await PhotoRepository.get_by_photo_id(session, str(photo_id))
        if photo and (photo.times_used or 0) > 0:
            photo.times_used -= 1
        await session.commit()
        return True

    @classmethod
    async def mark_uncertain_or_failed(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        photo_id: str,
        status: str = "send_failed",
    ) -> bool:
        """
        Marks reservation as 'uncertain' or 'send_failed' when transport outcome is ambiguous,
        keeping the photo permanently blocked in this chat to prevent duplicate delivery.
        """
        chat_key = str(chat_id)
        stmt = select(ChatPhotoUsage).where(
            ChatPhotoUsage.chat_id == chat_key,
            ChatPhotoUsage.photo_id == str(photo_id),
        )
        res = await session.execute(stmt)
        usage = res.scalars().first()
        if not usage:
            return False
        usage.status = status
        await session.commit()
        return True


# ==============================================================================
# 6. PHOTO ELIGIBILITY SERVICE (Sections 11, 12, 18, 19, 31)
# ==============================================================================

class PhotoEligibilityService:
    """
    Evaluates whether a chat is eligible to receive a photo BEFORE any photo selection occurs.
    Checks:
      1. Subsystem enabled (PHOTO_SELECTION_ENABLED)
      2. Active concurrent reservation lock on chat_id
      3. Per-chat 12-hour cooldown (PhotoCooldownService)
      4. Optional global hourly/daily safety limits
      5. Unused active photos with valid files on disk for this chat
    """

    @classmethod
    async def check_global_limits(
        cls,
        session: AsyncSession,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        current_time = _ensure_utc(now) or datetime.now(timezone.utc)

        hourly_limit = getattr(settings, "PHOTO_GLOBAL_HOURLY_LIMIT", None)
        if hourly_limit and int(hourly_limit) > 0:
            hour_ago = current_time - timedelta(hours=1)
            stmt_h = select(func.count(ChatPhotoUsage.id)).where(
                ChatPhotoUsage.status == "sent",
                func.coalesce(ChatPhotoUsage.sent_at, ChatPhotoUsage.first_used_at) >= hour_ago,
            )
            sent_last_hour = (await session.execute(stmt_h)).scalar() or 0
            if sent_last_hour >= int(hourly_limit):
                return False, "GLOBAL_LIMIT"

        daily_limit = getattr(settings, "PHOTO_GLOBAL_DAILY_LIMIT", None)
        if daily_limit and int(daily_limit) > 0:
            day_ago = current_time - timedelta(days=1)
            stmt_d = select(func.count(ChatPhotoUsage.id)).where(
                ChatPhotoUsage.status == "sent",
                func.coalesce(ChatPhotoUsage.sent_at, ChatPhotoUsage.first_used_at) >= day_ago,
            )
            sent_last_day = (await session.execute(stmt_d)).scalar() or 0
            if sent_last_day >= int(daily_limit):
                return False, "GLOBAL_LIMIT"

        return True, None

    @classmethod
    async def count_unused_active_photos(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        verify_disk: bool = False,
    ) -> int:
        chat_key = str(chat_id)
        subq_used = select(ChatPhotoUsage.photo_id).where(
            ChatPhotoUsage.chat_id == chat_key,
            ChatPhotoUsage.status.in_(["reserved", "sent", "send_failed", "uncertain"]),
        )
        stmt = select(PersonaPhoto).where(
            PersonaPhoto.enabled.is_(True),
            PersonaPhoto.photo_id.not_in(subq_used),
        )
        if persona_id is not None:
            stmt = stmt.where(
                (PersonaPhoto.persona_id == persona_id)
                | (PersonaPhoto.persona_id.is_(None) & PersonaPhoto.user_id.is_(None))
            )
        elif user_id is not None:
            stmt = stmt.where(
                (PersonaPhoto.user_id == user_id) | (PersonaPhoto.user_id.is_(None))
            )
        res = await session.execute(stmt)
        photos = list(res.scalars().all())
        if not verify_disk:
            return len(photos)
        return sum(1 for p in photos if p.storage_path and os.path.exists(p.storage_path))

    @classmethod
    async def count_photos_sent_to_chat(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
    ) -> int:
        stmt = select(func.count(ChatPhotoUsage.id)).where(
            ChatPhotoUsage.chat_id == str(chat_id),
            ChatPhotoUsage.status == "sent",
        )
        return (await session.execute(stmt)).scalar() or 0

    @classmethod
    async def check_eligibility(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        now: Optional[datetime] = None,
        enforce_cooldown: bool = True,
        verify_disk: bool = False,
    ) -> PhotoEligibilityResult:
        """
        Determines whether a photo is allowed to be sent to chat_id right now (Section 31).
        """
        chat_key = str(chat_id)
        sent_count = await cls.count_photos_sent_to_chat(session, chat_key)
        cd_info = await PhotoCooldownService.get_photo_cooldown(session, chat_key, now=now)

        # 1. Check if photo selection is enabled globally
        if not getattr(settings, "PHOTO_SELECTION_ENABLED", True):
            return PhotoEligibilityResult(
                allowed=False,
                reason="PHOTO_DISABLED",
                next_allowed_at=cd_info["next_allowed_at_iso"],
                last_photo_sent_at=cd_info["last_photo_sent_at_iso"],
                photos_sent_count=sent_count,
                available_unused_photos=0,
            )

        # 2. Check active concurrent reservation in this chat
        if await PhotoUsageRepository.has_active_reservation_in_chat(session, chat_key):
            log_photo_event(
                "PHOTO_RESERVATION_CONFLICT",
                chat_id=chat_key,
                result="RESERVATION_IN_PROGRESS",
                level=logging.WARNING,
            )
            return PhotoEligibilityResult(
                allowed=False,
                reason="RESERVATION_CONFLICT",
                next_allowed_at=cd_info["next_allowed_at_iso"],
                last_photo_sent_at=cd_info["last_photo_sent_at_iso"],
                photos_sent_count=sent_count,
                available_unused_photos=0,
            )

        # 3. Check 12-hour per-chat cooldown
        if enforce_cooldown and not cd_info["eligible"]:
            log_photo_event(
                "PHOTO_COOLDOWN_BLOCKED",
                chat_id=chat_key,
                result="CHAT_COOLDOWN",
                next_allowed_at=cd_info["next_allowed_at_iso"],
            )
            unused_cnt = await cls.count_unused_active_photos(
                session, chat_key, user_id=user_id, persona_id=persona_id, verify_disk=verify_disk
            )
            return PhotoEligibilityResult(
                allowed=False,
                reason="CHAT_COOLDOWN",
                next_allowed_at=cd_info["next_allowed_at_iso"],
                last_photo_sent_at=cd_info["last_photo_sent_at_iso"],
                photos_sent_count=sent_count,
                available_unused_photos=unused_cnt,
            )

        # 4. Check optional global limits
        global_ok, global_reason = await cls.check_global_limits(session, now=now)
        if not global_ok:
            return PhotoEligibilityResult(
                allowed=False,
                reason=global_reason or "GLOBAL_LIMIT",
                next_allowed_at=cd_info["next_allowed_at_iso"],
                last_photo_sent_at=cd_info["last_photo_sent_at_iso"],
                photos_sent_count=sent_count,
                available_unused_photos=0,
            )

        # 5. Check available unused photos for this chat
        unused_cnt = await cls.count_unused_active_photos(
            session, chat_key, user_id=user_id, persona_id=persona_id, verify_disk=verify_disk
        )
        if unused_cnt <= 0:
            log_photo_event(
                "PHOTO_POOL_EMPTY",
                chat_id=chat_key,
                result="NO_UNUSED_PHOTOS",
            )
            return PhotoEligibilityResult(
                allowed=False,
                reason="NO_UNUSED_PHOTOS",
                next_allowed_at=cd_info["next_allowed_at_iso"],
                last_photo_sent_at=cd_info["last_photo_sent_at_iso"],
                photos_sent_count=sent_count,
                available_unused_photos=0,
            )

        return PhotoEligibilityResult(
            allowed=True,
            reason="ALLOWED",
            next_allowed_at=cd_info["next_allowed_at_iso"],
            last_photo_sent_at=cd_info["last_photo_sent_at_iso"],
            photos_sent_count=sent_count,
            available_unused_photos=unused_cnt,
        )

    @classmethod
    async def get_chat_state(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Returns the internal per-chat photo state (Section 19).
        """
        elig = await cls.check_eligibility(
            session=session,
            chat_id=chat_id,
            user_id=user_id,
            persona_id=persona_id,
            now=now,
            enforce_cooldown=True,
        )
        return {
            "chat_id": chat_id,
            "photo_eligible": elig.allowed,
            "last_photo_sent_at": elig.last_photo_sent_at,
            "next_photo_allowed_at": elig.next_allowed_at,
            "photos_sent_count": elig.photos_sent_count,
            "available_unused_photos": elig.available_unused_photos,
        }


# ==============================================================================
# 7. PHOTO SELECTOR (Sections 11, 12, 40)
# ==============================================================================

class PhotoSelector:
    """
    Selects and atomically reserves an eligible photo for a chat.
    Enforces Section 12: Checks eligibility FIRST before selecting a photo.
    Ranks candidates by context, tags, time of day, and category diversity.
    """

    @classmethod
    async def get_recent_sent_categories(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        limit: int = 3,
    ) -> List[str]:
        stmt = (
            select(ChatPhotoUsage.category)
            .where(
                ChatPhotoUsage.chat_id == str(chat_id),
                ChatPhotoUsage.status == "sent",
                ChatPhotoUsage.category.isnot(None),
            )
            .order_by(desc(func.coalesce(ChatPhotoUsage.sent_at, ChatPhotoUsage.first_used_at)))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return [row[0] for row in res.fetchall() if row[0]]

    @classmethod
    async def select_and_reserve(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        preferred_category: Optional[str] = None,
        preferred_tags: Optional[List[str]] = None,
        current_hour: Optional[int] = None,
        reason: str = "contextual",
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        now: Optional[datetime] = None,
        enforce_cooldown: bool = True,
        require_disk_file: bool = True,
    ) -> Optional[PersonaPhoto]:
        chat_key = str(chat_id)

        # 1. Check eligibility FIRST (Section 12)
        elig = await PhotoEligibilityService.check_eligibility(
            session=session,
            chat_id=chat_key,
            user_id=user_id,
            persona_id=persona_id,
            now=now,
            enforce_cooldown=enforce_cooldown,
            verify_disk=require_disk_file,
        )
        if not elig.allowed:
            return None

        # 2. Fetch unused active candidates for this chat & specific model persona
        subq_used = select(ChatPhotoUsage.photo_id).where(
            ChatPhotoUsage.chat_id == chat_key,
            ChatPhotoUsage.status.in_(["reserved", "sent", "send_failed", "uncertain"]),
        )
        stmt = select(PersonaPhoto).where(
            PersonaPhoto.enabled.is_(True),
            PersonaPhoto.photo_id.not_in(subq_used),
        )
        if persona_id is not None:
            stmt = stmt.where(
                (PersonaPhoto.persona_id == persona_id)
                | (PersonaPhoto.persona_id.is_(None) & PersonaPhoto.user_id.is_(None))
            )
        elif user_id is not None:
            stmt = stmt.where(
                (PersonaPhoto.user_id == user_id) | (PersonaPhoto.user_id.is_(None))
            )
        res = await session.execute(stmt)
        candidates = list(res.scalars().all())

        if require_disk_file:
            candidates = [
                p for p in candidates
                if p.storage_path and os.path.exists(p.storage_path)
            ]

        if not candidates:
            log_photo_event("PHOTO_POOL_EMPTY", chat_id=chat_key, result="NO_CANDIDATES")
            return None

        # 3. Contextual & diversity ranking (Section 40)
        recent_cats = await cls.get_recent_sent_categories(session, chat_key, limit=3)
        last_cat = recent_cats[0] if recent_cats else None

        target_cat = preferred_category.strip().lower() if preferred_category else None
        target_tags = [t.strip().lower() for t in (preferred_tags or []) if t]

        scored: List[Tuple[float, PersonaPhoto]] = []
        for photo in candidates:
            score = 10.0
            p_cat = (photo.category or "").lower()
            p_tags = [t.lower() for t in photo.get_tags()]
            p_moods = [m.lower() for m in photo.get_mood()]
            p_ctx = [c.lower() for c in photo.get_contexts()]

            if target_cat and p_cat == target_cat:
                score += 15.0

            for t in target_tags:
                if t in p_tags or t in p_ctx or t in p_moods or t == p_cat:
                    score += 8.0

            # Diversity penalty: avoid repeating the same category consecutively
            if last_cat and p_cat == last_cat and len(candidates) > 1:
                score -= 6.0
            if recent_cats.count(p_cat) >= 2 and len(candidates) > 1:
                score -= 4.0

            # Time of day relevance
            if current_hour is not None:
                if 6 <= current_hour <= 11:
                    if any(x in p_tags or x in p_ctx for x in ("morning", "coffee", "breakfast", "cafe", "day")):
                        score += 5.0
                elif 12 <= current_hour <= 17:
                    if any(x in p_tags or x in p_ctx for x in ("day", "daytime", "walk", "city", "outside", "outdoors")):
                        score += 3.0
                elif 18 <= current_hour <= 23:
                    if any(x in p_tags or x in p_ctx for x in ("evening", "dinner", "sunset", "home", "cinema", "quest")):
                        score += 5.0
                else:
                    if any(x in p_tags or x in p_ctx for x in ("night", "cozy", "home")):
                        score += 4.0

            score -= min(float(photo.times_used or 0) * 0.5, 5.0)
            scored.append((score, photo))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        # 4. Attempt atomic reservation
        for _, candidate in scored:
            reserved_ok = await PhotoUsageRepository.reserve_photo_atomically(
                session=session,
                chat_id=chat_key,
                photo=candidate,
                reason=reason,
                selection_context={
                    "preferred_category": preferred_category,
                    "preferred_tags": preferred_tags,
                    "current_hour": current_hour,
                },
                now=now,
            )
            if reserved_ok:
                log_photo_event(
                    "PHOTO_SELECTED",
                    chat_id=chat_key,
                    photo_id=candidate.photo_id,
                    result="SELECTED_AND_RESERVED",
                    category=candidate.category,
                )
                return candidate

        return None


# ==============================================================================
# 8. APPLICATION-LEVEL HARD GATE: PHOTO_SEND_GATE (Section 43)
# ==============================================================================

async def PHOTO_SEND_GATE(
    session: AsyncSession,
    chat_id: Union[str, int],
    photo_id: str,
    now: Optional[datetime] = None,
    allow_existing_reservation: bool = True,
    enforce_cooldown: bool = True,
    persona_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Mandatory 10-point application-level hard gate before ANY photo is sent (Section 43).
    Verifies:
      1. Bot photo selection is healthy / enabled
      2. Photo exists in database
      3. Photo is active (is_active / enabled) and matches persona_id if specified
      4. Photo file exists on persistent storage and is readable
      5. Chat identity is valid
      6. 12-hour per-chat cooldown has expired
      7. Photo has NEVER been sent to this chat
      8. Optional global hourly/daily limits allow sending
      9. Reservation succeeds or valid reservation exists without conflict
      10. Telegram send is permitted
    Returns: (passed: bool, reason: str)
    """
    chat_key = str(chat_id).strip() if chat_id is not None else ""

    # 1. Bot / photo subsystem enabled
    if not getattr(settings, "PHOTO_SELECTION_ENABLED", True):
        return False, "PHOTO_DISABLED"

    # 5. Valid chat identity
    if not chat_key:
        return False, "INVALID_CHAT_ID"

    # 2. Photo exists in DB
    photo = await PhotoRepository.get_by_photo_id(session, str(photo_id))
    if not photo:
        return False, "PHOTO_NOT_FOUND"

    # 3. Photo is active & bound to matching persona_id
    if not photo.enabled:
        return False, "PHOTO_DISABLED"
    if persona_id is not None and photo.persona_id is not None and photo.persona_id != persona_id:
        return False, "PERSONA_MISMATCH"

    # 4. Photo file exists in persistent storage
    if not photo.storage_path or not os.path.exists(photo.storage_path) or os.path.getsize(photo.storage_path) <= 0:
        return False, "STORAGE_ERROR"

    # 6. 12-hour per-chat cooldown expired
    if enforce_cooldown:
        cd_ok = await PhotoCooldownService.can_send_photo(session, chat_key, now=now)
        if not cd_ok:
            log_photo_event("PHOTO_COOLDOWN_BLOCKED", chat_id=chat_key, photo_id=photo.photo_id, result="GATE_BLOCKED")
            return False, "CHAT_COOLDOWN"

    # 7. Photo has NEVER been sent to this chat
    if await PhotoUsageRepository.is_photo_permanently_sent_in_chat(session, chat_key, photo.photo_id):
        log_photo_event("PHOTO_ALREADY_USED", chat_id=chat_key, photo_id=photo.photo_id, result="GATE_BLOCKED")
        return False, "PHOTO_ALREADY_USED"

    # 8. Optional global limits allow it
    global_ok, global_reason = await PhotoEligibilityService.check_global_limits(session, now=now)
    if not global_ok:
        return False, global_reason or "GLOBAL_LIMIT"

    # 9. Reservation check / creation
    stmt_u = select(ChatPhotoUsage).where(
        ChatPhotoUsage.chat_id == chat_key,
        ChatPhotoUsage.photo_id == photo.photo_id,
    )
    res_u = await session.execute(stmt_u)
    existing_usage = res_u.scalars().first()

    if existing_usage:
        if existing_usage.status == "reserved" and allow_existing_reservation:
            pass
        else:
            return False, "RESERVATION_CONFLICT"
    else:
        reserved_ok = await PhotoUsageRepository.reserve_photo_atomically(
            session=session,
            chat_id=chat_key,
            photo=photo,
            reason="gate_reservation",
            now=now,
        )
        if not reserved_ok:
            return False, "RESERVATION_CONFLICT"

    # 10. All checks passed
    return True, "ALLOWED"


# ==============================================================================
# 9. PHOTO SEND SERVICE (Sections 13, 14, 26, 27, 28, 42, 43, 44)
# ==============================================================================

class PhotoSendService:
    """
    Concurrency-safe orchestrator for the photo send pipeline (Section 26).
    Guarantees:
    - Per-chat asyncio lock prevents concurrent duplicate photo sends to the same chat.
    - AI decision cannot bypass eligibility or PHOTO_SEND_GATE.
    - Cooldown starts ONLY after confirmed Telegram delivery.
    - Definite transport failure releases reservation without starting cooldown.
    - Ambiguous timeout marks reservation as 'uncertain' for safety.
    """

    _chat_locks: Dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_chat_lock(cls, chat_id: Union[str, int]) -> asyncio.Lock:
        chat_key = str(chat_id)
        if chat_key not in cls._chat_locks:
            cls._chat_locks[chat_key] = asyncio.Lock()
        return cls._chat_locks[chat_key]

    @staticmethod
    def sanitize_caption(caption: Optional[str]) -> Optional[str]:
        """Sanitizes photo caption, removing any internal markers, tags, or photo IDs (Section 28 & 29)."""
        if not caption or not caption.strip():
            return None
        cleaned = OutputSanitizer.sanitize_outgoing_text(caption)
        # Strip any residual photo markers or internal photo_id patterns
        cleaned = OutputSanitizer.strip_photo_internal_markers(cleaned)
        return cleaned.strip() or None

    @classmethod
    async def send_photo_if_allowed(
        cls,
        session: AsyncSession,
        chat_id: Union[str, int],
        ai_decision: Union[Dict[str, Any], PhotoDecision, bool],
        send_transport_fn: Callable[[PersonaPhoto, Optional[str]], Awaitable[Optional[int]]],
        caption: Optional[str] = None,
        user_id: Optional[int] = None,
        persona_id: Optional[int] = None,
        current_hour: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Executes the full Section 26 Photo Send Pipeline:
        AI Decision -> PhotoDecisionValidator -> Per-Chat Lock -> PhotoEligibilityService ->
        PhotoSelector (Atomic Reservation) -> PHOTO_SEND_GATE -> Telegram Transport ->
        Confirm Delivery (start 12h cooldown) OR Release Reservation on failure.
        """
        chat_key = str(chat_id)

        # 1. Validate AI semantic decision
        decision = PhotoDecisionValidator.validate_decision(ai_decision)
        if not decision.should_send_photo:
            return {
                "sent": False,
                "reason": "AI_DECLINED",
                "photo_id": None,
                "message_id": None,
            }

        lock = cls._get_chat_lock(chat_key)
        async with lock:
            # 2. Check eligibility
            elig = await PhotoEligibilityService.check_eligibility(
                session=session,
                chat_id=chat_key,
                user_id=user_id,
                persona_id=persona_id,
                now=now,
                enforce_cooldown=True,
                verify_disk=True,
            )
            if not elig.allowed:
                return {
                    "sent": False,
                    "reason": elig.reason,
                    "next_allowed_at": elig.next_allowed_at,
                    "photo_id": None,
                    "message_id": None,
                }

            # 3. Select & atomically reserve candidate photo
            photo = await PhotoSelector.select_and_reserve(
                session=session,
                chat_id=chat_key,
                preferred_category=decision.preferred_category,
                preferred_tags=decision.preferred_tags,
                current_hour=current_hour,
                reason=decision.reason or "ai_contextual",
                user_id=user_id,
                persona_id=persona_id,
                now=now,
                enforce_cooldown=True,
                require_disk_file=True,
            )
            if not photo:
                return {
                    "sent": False,
                    "reason": "NO_UNUSED_PHOTOS",
                    "photo_id": None,
                    "message_id": None,
                }

            # 4. Execute mandatory 10-point PHOTO_SEND_GATE
            gate_ok, gate_reason = await PHOTO_SEND_GATE(
                session=session,
                chat_id=chat_key,
                photo_id=photo.photo_id,
                now=now,
                allow_existing_reservation=True,
                enforce_cooldown=True,
                persona_id=persona_id,
            )
            if not gate_ok:
                await PhotoUsageRepository.release_reservation(session, chat_key, photo.photo_id)
                return {
                    "sent": False,
                    "reason": gate_reason,
                    "photo_id": None,
                    "message_id": None,
                }

            # 5. Sanitize caption
            clean_caption = cls.sanitize_caption(caption)

            # 6. Deliver via Telegram transport
            log_photo_event("PHOTO_SEND_STARTED", chat_id=chat_key, photo_id=photo.photo_id, result="SENDING")
            try:
                msg_id = await send_transport_fn(photo, clean_caption)
            except ( asyncio.TimeoutError, TimeoutError ) as timeout_exc:
                # Uncertain transport result: prefer safety over duplicate send (Section 9 & 14)
                await PhotoUsageRepository.mark_uncertain_or_failed(
                    session=session,
                    chat_id=chat_key,
                    photo_id=photo.photo_id,
                    status="uncertain",
                )
                log_photo_event(
                    "PHOTO_SEND_FAILED",
                    chat_id=chat_key,
                    photo_id=photo.photo_id,
                    result="UNCERTAIN_TIMEOUT",
                    level=logging.WARNING,
                    error=type(timeout_exc).__name__,
                )
                return {
                    "sent": False,
                    "reason": "TRANSPORT_UNCERTAIN",
                    "photo_id": photo.photo_id,
                    "message_id": None,
                }
            except Exception as exc:
                err_name = type(exc).__name__.lower()
                if "timeout" in err_name:
                    await PhotoUsageRepository.mark_uncertain_or_failed(
                        session=session,
                        chat_id=chat_key,
                        photo_id=photo.photo_id,
                        status="uncertain",
                    )
                    log_photo_event(
                        "PHOTO_SEND_FAILED",
                        chat_id=chat_key,
                        photo_id=photo.photo_id,
                        result="UNCERTAIN_TIMEOUT",
                        level=logging.WARNING,
                    )
                    return {
                        "sent": False,
                        "reason": "TRANSPORT_UNCERTAIN",
                        "photo_id": photo.photo_id,
                        "message_id": None,
                    }

                # Definite failure before Telegram accepted the photo: release reservation & do not start cooldown
                await PhotoUsageRepository.release_reservation(session, chat_key, photo.photo_id)
                log_photo_event(
                    "PHOTO_SEND_FAILED",
                    chat_id=chat_key,
                    photo_id=photo.photo_id,
                    result="DEFINITE_FAILURE_RELEASED",
                    level=logging.ERROR,
                    error=type(exc).__name__,
                )
                return {
                    "sent": False,
                    "reason": "SEND_FAILED",
                    "photo_id": None,
                    "message_id": None,
                }

            if msg_id is not None and msg_id is not False:
                # 7. Confirmed delivery -> Record sent & start 12-hour per-chat cooldown!
                delivery_ts = _ensure_utc(now) or datetime.now(timezone.utc)
                real_msg_id = int(msg_id) if isinstance(msg_id, int) and not isinstance(msg_id, bool) else 1
                await PhotoCooldownService.record_photo_sent(
                    session=session,
                    chat_id=chat_key,
                    photo_id=photo.photo_id,
                    telegram_message_id=real_msg_id,
                    now=delivery_ts,
                )
                log_photo_event(
                    "PHOTO_SEND_SUCCESS",
                    chat_id=chat_key,
                    photo_id=photo.photo_id,
                    result="DELIVERED",
                    message_id=real_msg_id,
                )
                return {
                    "sent": True,
                    "reason": "ALLOWED",
                    "photo_id": photo.photo_id,
                    "photo": photo,
                    "message_id": real_msg_id,
                }
            else:
                # Transport returned None / False -> definite failure, release reservation
                await PhotoUsageRepository.release_reservation(session, chat_key, photo.photo_id)
                log_photo_event(
                    "PHOTO_SEND_FAILED",
                    chat_id=chat_key,
                    photo_id=photo.photo_id,
                    result="DEFINITE_FAILURE_RELEASED",
                    level=logging.WARNING,
                )
                return {
                    "sent": False,
                    "reason": "SEND_FAILED",
                    "photo_id": None,
                    "message_id": None,
                }


# ==============================================================================
# 10. PHOTO ADMIN SERVICE (Sections 22, 37)
# ==============================================================================

class PhotoAdminService:
    """
    Owner-only photo management and statistics service (Sections 22 & 37).
    """

    @classmethod
    async def get_admin_statistics(
        cls,
        session: AsyncSession,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current_time = _ensure_utc(now) or datetime.now(timezone.utc)
        cooldown_delta = PhotoCooldownService.get_cooldown_duration()
        cooldown_cutoff = current_time - cooldown_delta
        day_ago = current_time - timedelta(days=1)
        week_ago = current_time - timedelta(days=7)

        total_photos = (await session.execute(select(func.count(PersonaPhoto.id)))).scalar() or 0
        active_photos = (
            await session.execute(select(func.count(PersonaPhoto.id)).where(PersonaPhoto.enabled.is_(True)))
        ).scalar() or 0
        disabled_photos = max(0, total_photos - active_photos)

        ts_col = func.coalesce(ChatPhotoUsage.sent_at, ChatPhotoUsage.first_used_at)
        sent_today = (
            await session.execute(
                select(func.count(ChatPhotoUsage.id)).where(
                    ChatPhotoUsage.status == "sent",
                    ts_col >= day_ago,
                )
            )
        ).scalar() or 0

        sent_week = (
            await session.execute(
                select(func.count(ChatPhotoUsage.id)).where(
                    ChatPhotoUsage.status == "sent",
                    ts_col >= week_ago,
                )
            )
        ).scalar() or 0

        total_sent = (
            await session.execute(
                select(func.count(ChatPhotoUsage.id)).where(ChatPhotoUsage.status == "sent")
            )
        ).scalar() or 0

        # Active cooldown chats vs eligible chats
        # Combine chats from PhotoCooldown, ChatPhotoUsage, and Contact
        cd_rows = (await session.execute(select(PhotoCooldown))).scalars().all()
        active_cd_chats = set()
        all_known_chats = set()

        for row in cd_rows:
            all_known_chats.add(str(row.chat_id))
            last_ts = _ensure_utc(row.last_photo_sent_at)
            if last_ts and last_ts > cooldown_cutoff:
                active_cd_chats.add(str(row.chat_id))

        usage_chats_stmt = select(
            ChatPhotoUsage.chat_id,
            func.max(func.coalesce(ChatPhotoUsage.sent_at, ChatPhotoUsage.first_used_at)),
        ).where(ChatPhotoUsage.status == "sent").group_by(ChatPhotoUsage.chat_id)
        for c_id, max_ts in (await session.execute(usage_chats_stmt)).fetchall():
            c_key = str(c_id)
            all_known_chats.add(c_key)
            if isinstance(max_ts, str):
                try:
                    max_ts = datetime.fromisoformat(max_ts)
                except ValueError:
                    max_ts = None
            max_ts_utc = _ensure_utc(max_ts)
            if max_ts_utc and max_ts_utc > cooldown_cutoff:
                active_cd_chats.add(c_key)

        contact_tg_ids = (await session.execute(select(Contact.tg_id))).scalars().all()
        for tg_id in contact_tg_ids:
            if tg_id:
                all_known_chats.add(str(tg_id))

        chats_with_active_cooldown = len(active_cd_chats)
        chats_eligible = max(0, len(all_known_chats) - chats_with_active_cooldown)

        cat_stmt = select(PersonaPhoto.category, func.count(PersonaPhoto.id)).group_by(PersonaPhoto.category)
        categories = {row[0]: row[1] for row in (await session.execute(cat_stmt)).fetchall() if row[0]}

        return {
            "total_photos": total_photos,
            "active_photos": active_photos,
            "enabled_photos": active_photos,
            "disabled_photos": disabled_photos,
            "photos_sent_today": sent_today,
            "photos_sent_this_week": sent_week,
            "total_sent_events": total_sent,
            "chats_with_active_cooldown": chats_with_active_cooldown,
            "chats_eligible_for_photo": chats_eligible,
            "distinct_chats_reached": len(all_known_chats),
            "categories": categories,
        }

    @classmethod
    def format_stats_message(cls, stats: Dict[str, Any]) -> str:
        cats = stats.get("categories") or {}
        cat_lines = "\n".join([f"  • <code>{k}</code>: {v} шт." for k, v in cats.items()]) or "  <i>Пусто</i>"
        cooldown_h = int( getattr(settings, "PHOTO_CHAT_COOLDOWN_HOURS", 12) )
        return (
            "📸 <b>УПРАВЛЕНИЕ PHOTO POOL (ПУЛ ФОТО ПЕРСОНЫ)</b>\n\n"
            f"📦 <b>Всего фото в пуле:</b> {stats['total_photos']}\n"
            f"✅ <b>Активных (доступных):</b> {stats['active_photos']}\n"
            f"🚫 <b>Отключенных:</b> {stats['disabled_photos']}\n\n"
            f"📅 <b>Отправлено сегодня:</b> {stats['photos_sent_today']}\n"
            f"📆 <b>Отправлено за неделю:</b> {stats['photos_sent_this_week']}\n"
            f"📤 <b>Всего отправок за все время:</b> {stats['total_sent_events']}\n\n"
            f"⏳ <b>Чатов на кулдауне ({cooldown_h}ч):</b> {stats['chats_with_active_cooldown']}\n"
            f"🟢 <b>Чатов, доступных для фото:</b> {stats['chats_eligible_for_photo']}\n\n"
            f"📂 <b>Категории:</b>\n{cat_lines}\n\n"
            "🛡 <b>Активные правила безопасности:</b>\n"
            f"• <b>Per-Chat {cooldown_h}h Lock:</b> независимый таймер {cooldown_h} часов для каждого чата.\n"
            "• <b>Absolute No-Repeat:</b> одно и то же фото никогда не отправляется дважды в один чат.\n"
            "• <b>SHA-256 + dHash:</b> защита от загрузки дубликатов.\n\n"
            "💡 <i>Команды администратора:</i>\n"
            "<code>/photos list</code> — список фото\n"
            "<code>/photos stats</code> — статистика\n"
            "<code>/photos enable &lt;photo_id&gt;</code> — включить\n"
            "<code>/photos disable &lt;photo_id&gt;</code> — отключить\n"
            "<code>/photos delete &lt;photo_id&gt;</code> — удалить\n"
            "<code>/photos clear</code> — очистить пул"
        )
