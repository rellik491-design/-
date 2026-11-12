import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.models.entities import InviteToken, AccountSession, Subscription, User
from companion.models.enums import PlanDuration, AccountStatus
from companion.services.subscription_service import SubscriptionService


class InviteService:
    @classmethod
    async def create_subscription_invite(
        cls,
        session: AsyncSession,
        user_id: int,
        plan: PlanDuration,
        bot_username: str = "MyCompanionBot",
        now: Optional[datetime] = None,
    ) -> Tuple[str, InviteToken]:
        """
        Creates a unique subscription deep-link containing plan duration,
        account limits, and expiration date.
        Example: https://t.me/MyCompanionBot?start=sub_7d_a8f9c1...
        """
        current_time = now or datetime.now(timezone.utc)
        unique_hash = uuid.uuid4().hex[:12]
        token_str = f"sub_{plan.value}_{unique_hash}"
        expires_at = current_time + timedelta(days=plan.days_count)

        invite = InviteToken(
            token=token_str,
            user_id=user_id,
            plan=plan,
            max_accounts=plan.max_accounts_allowed,
            max_messages=1000,
            is_active=True,
            expires_at=expires_at,
            created_at=current_time,
        )
        session.add(invite)
        await session.commit()
        await session.refresh(invite)

        link = f"https://t.me/{bot_username}?start={token_str}"
        return link, invite

    @classmethod
    async def verify_invite(
        cls, session: AsyncSession, token_str: str, now: Optional[datetime] = None
    ) -> Optional[InviteToken]:
        """Validates that the invite token exists, is active, and hasn't expired."""
        current_time = now or datetime.now(timezone.utc)
        stmt = select(InviteToken).where(
            InviteToken.token == token_str,
            InviteToken.is_active == True,
        )
        res = await session.execute(stmt)
        invite = res.scalars().first()

        if not invite:
            return None

        # Check expiration
        exp = invite.expires_at
        if exp.tzinfo is None and current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        elif exp.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        if exp <= current_time:
            invite.is_active = False
            await session.commit()
            return None

        return invite

    @classmethod
    async def register_chat_automation(
        cls,
        session: AsyncSession,
        token_str: str,
        business_connection_id: str,
        tg_user_id: int,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str, Optional[AccountSession]]:
        """
        Binds Telegram's Chat Automation (business_connection_id) to the subscription.
        Activated automatically when the user adds the bot via the unique link.
        """
        invite = await cls.verify_invite(session, token_str, now=now)
        if not invite:
            return False, "Ссылка недействительна или срок её действия истек.", None

        # Activate or extend user's subscription
        await SubscriptionService.activate_plan(
            session=session,
            user_id=invite.user_id,
            plan=invite.plan,
            now=now,
        )

        # Check if this business connection is already registered
        stmt = select(AccountSession).where(
            AccountSession.business_connection_id == business_connection_id
        )
        res = await session.execute(stmt)
        account = res.scalars().first()

        if not account:
            account = AccountSession(
                user_id=invite.user_id,
                business_connection_id=business_connection_id,
                auth_type="business_automation",
                tg_user_id=tg_user_id,
                first_name=first_name,
                username=username,
                is_active=True,
                status=AccountStatus.CONNECTED,
            )
            session.add(account)
        else:
            account.is_active = True
            account.status = AccountStatus.CONNECTED
            account.tg_user_id = tg_user_id
            if first_name:
                account.first_name = first_name
            if username:
                account.username = username

        await session.commit()
        await session.refresh(account)
        from companion.services.account_service import AccountService
        await AccountService.ensure_account_persona(session, account)
        return True, "Автоматизация чатов успешно подключена!", account

    @classmethod
    async def is_business_connection_valid(
        cls, session: AsyncSession, business_connection_id: str, now: Optional[datetime] = None
    ) -> bool:
        """
        Quick check when an incoming business_message arrives:
        Verifies that this connection is active and its subscription has not expired.
        If connected on behalf of the admin or an active instance, auto-heals subscription.
        """
        stmt = select(AccountSession).where(
            AccountSession.business_connection_id == business_connection_id,
        )
        res = await session.execute(stmt)
        account = res.scalars().first()

        if not account:
            return False

        current_time = now or datetime.now(timezone.utc)
        sub = await SubscriptionService.get_active_subscription(session, account.user_id, now=current_time)

        if not sub:
            # Check if this account belongs to an admin or if admin has active subscription
            from companion.models.entities import User
            from companion.config import settings
            admin_stmt = select(User).where(User.telegram_id == settings.ADMIN_TELEGRAM_ID)
            admin_user = (await session.execute(admin_stmt)).scalars().first()
            if admin_user:
                admin_sub = await SubscriptionService.get_active_subscription(session, admin_user.id, now=current_time)
                if admin_sub and admin_sub.is_active:
                    # Auto-extend / grant subscription to managed business account
                    sub = await SubscriptionService.activate_plan(
                        session=session,
                        user_id=account.user_id,
                        plan=PlanDuration.DAYS_31,
                        custom_days=365,
                        extend=False,
                        now=current_time,
                    )

        if not sub or not sub.is_valid(current_time):
            account.is_active = False
            account.status = AccountStatus.DISCONNECTED
            await session.commit()
            return False

        if not account.is_active or account.status != AccountStatus.CONNECTED:
            account.is_active = True
            account.status = AccountStatus.CONNECTED
            await session.commit()

        return True
