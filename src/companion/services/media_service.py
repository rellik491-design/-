from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import select, and_, not_
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models.entities import MediaAsset, SentMediaHistory, Contact


class MediaService:
    @classmethod
    async def add_photo(
        cls, session: AsyncSession, user_id: int, file_path: str, tag: str, description: str
    ) -> MediaAsset:
        """Registers a new photo in the user's asset bank."""
        asset = MediaAsset(
            user_id=user_id,
            file_path=file_path,
            tag=tag.lower().strip(),
            description=description.strip(),
            times_sent=0,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset

    @classmethod
    async def get_photo_for_contact(
        cls, session: AsyncSession, user_id: int, contact_id: int, preferred_tag: str = "general"
    ) -> Optional[MediaAsset]:
        """
        Selects a photo matching the preferred tag that hasn't been sent to this contact yet.
        If all have been sent, chooses the one sent longest ago.
        """
        # IDs of media already sent to this contact
        subquery = (
            select(SentMediaHistory.media_asset_id)
            .where(SentMediaHistory.contact_id == contact_id)
        )

        # 1. Try finding un-sent media matching tag
        stmt = (
            select(MediaAsset)
            .where(
                MediaAsset.user_id == user_id,
                MediaAsset.tag == preferred_tag.lower(),
                not_(MediaAsset.id.in_(subquery)),
            )
            .order_by(MediaAsset.times_sent.asc())
        )
        res = await session.execute(stmt)
        asset = res.scalars().first()

        # 2. If no unsent photo for this tag, try any unsent photo
        if not asset and preferred_tag != "general":
            stmt_fallback = (
                select(MediaAsset)
                .where(
                    MediaAsset.user_id == user_id,
                    not_(MediaAsset.id.in_(subquery)),
                )
                .order_by(MediaAsset.times_sent.asc())
            )
            res_fb = await session.execute(stmt_fallback)
            asset = res_fb.scalars().first()

        # 3. If everything was already sent for this contact -> STRICT NO-REPEAT: return None
        if not asset:
            return None

        if asset:
            # Record sent history
            history = SentMediaHistory(
                contact_id=contact_id,
                media_asset_id=asset.id,
                sent_at=datetime.now(timezone.utc),
            )
            asset.times_sent += 1
            session.add(history)
            await session.commit()
            await session.refresh(asset)

        return asset
