from datetime import datetime, timedelta, timezone
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models.entities import Subscription, User
from companion.models.enums import PlanDuration


class SubscriptionService:
    @staticmethod
    def calculate_expiry(
        plan: PlanDuration,
        current_expires_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> TupleExpiry:
        pass


class SubscriptionService:
    @classmethod
    async def get_active_subscription(
        cls, session: AsyncSession, user_id: int, now: Optional[datetime] = None
    ) -> Optional[Subscription]:
        """Fetches active subscription if it exists and hasn't expired."""
        current_time = now or datetime.now(timezone.utc)
        stmt = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active == True,
            )
            .order_by(Subscription.expires_at.desc())
        )
        result = await session.execute(stmt)
        sub = result.scalars().first()
        if sub and sub.is_valid(current_time):
            return sub
        return None

    @classmethod
    async def activate_plan(
        cls,
        session: AsyncSession,
        user_id: int,
        plan: PlanDuration,
        custom_days: Optional[int] = None,
        now: Optional[datetime] = None,
        extend: bool = True,
    ) -> Subscription:
        """
        Activates or sets a subscription by the plan's duration or custom_days.
        If extend=True and user currently has an active subscription, the new duration is appended.
        If extend=False, the subscription is cleanly set to exactly the new duration from now.
        """
        current_time = now or datetime.now(timezone.utc)
        days = custom_days if custom_days is not None else plan.days_count

        active_sub = await cls.get_active_subscription(session, user_id, current_time)

        if active_sub:
            if extend:
                active_sub.expires_at = active_sub.expires_at + timedelta(days=days)
            else:
                active_sub.starts_at = current_time
                active_sub.expires_at = current_time + timedelta(days=days)
            active_sub.plan = plan
            active_sub.max_accounts = plan.max_accounts_allowed
            active_sub.is_active = True
            await session.commit()
            await session.refresh(active_sub)
            return active_sub
        else:
            # Create new subscription
            new_sub = Subscription(
                user_id=user_id,
                plan=plan,
                starts_at=current_time,
                expires_at=current_time + timedelta(days=days),
                is_active=True,
                max_accounts=plan.max_accounts_allowed,
            )
            session.add(new_sub)
            await session.commit()
            await session.refresh(new_sub)
            return new_sub

    @classmethod
    async def cancel_subscription(
        cls, session: AsyncSession, user_id: int, now: Optional[datetime] = None
    ) -> bool:
        """Immediately terminates active subscription for the user."""
        current_time = now or datetime.now(timezone.utc)
        active_sub = await cls.get_active_subscription(session, user_id, current_time)
        if not active_sub:
            return False

        active_sub.is_active = False
        await session.commit()
        return True

    @classmethod
    def get_detailed_status(cls, sub: Optional[Subscription], now: Optional[datetime] = None) -> dict:
        """Generates human-readable time remaining and status details."""
        if not sub or not sub.is_valid(now):
            return {
                "is_active": False,
                "plan_name": "Нет активной подписки",
                "expires_at_str": "—",
                "remaining_str": "Истекла",
                "days_left": 0,
                "hours_left": 0,
            }

        current_time = now or datetime.now(timezone.utc)
        exp = sub.expires_at
        if exp.tzinfo is None and current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        elif exp.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        diff = exp - current_time
        total_seconds = max(0, int(diff.total_seconds()))
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        remaining_parts = []
        if days > 0:
            remaining_parts.append(f"{days} дн.")
        if hours > 0 or days > 0:
            remaining_parts.append(f"{hours} ч.")
        remaining_parts.append(f"{minutes} мин.")

        return {
            "is_active": True,
            "plan_name": sub.plan.value,
            "expires_at_str": exp.strftime("%d.%m.%Y %H:%M"),
            "remaining_str": " ".join(remaining_parts),
            "days_left": days,
            "hours_left": hours,
            "minutes_left": minutes,
            "max_accounts": sub.max_accounts,
            "max_daily_voice": sub.max_daily_voice,
            "voice_used_today": sub.voice_used_today,
        }

    @classmethod
    async def check_and_expire_subscriptions(
        cls, session: AsyncSession, now: Optional[datetime] = None
    ) -> List[int]:
        """
        Finds all subscriptions that passed expires_at and sets is_active=False.
        Returns list of affected user_ids.
        """
        current_time = now or datetime.now(timezone.utc)
        # Use timezone naive or matching if stored naive
        stmt = select(Subscription).where(
            Subscription.is_active == True,
            Subscription.expires_at <= current_time,
        )
        result = await session.execute(stmt)
        expired_subs = result.scalars().all()

        expired_user_ids = []
        for sub in expired_subs:
            sub.is_active = False
            expired_user_ids.append(sub.user_id)

        if expired_subs:
            await session.commit()

        return list(set(expired_user_ids))
