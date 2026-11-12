from typing import Tuple, List, Optional, Dict, Any
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models.entities import AccountSession, Subscription, User
from companion.models.enums import AccountStatus
from companion.services.subscription_service import SubscriptionService


class AccountService:
    @classmethod
    async def can_add_more_accounts(
        cls, session: AsyncSession, user_id: int
    ) -> Tuple[bool, str, int]:
        """
        Validates if the user's subscription allows connecting an additional Telegram account.
        Returns (is_allowed, reason, max_allowed).
        """
        sub = await SubscriptionService.get_active_subscription(session, user_id)
        if not sub:
            return False, "У вас нет активной подписки. Оформите тариф для подключения аккаунтов.", 0

        # Max allowed accounts for this subscription plan
        max_allowed = max(sub.max_accounts, sub.plan.max_accounts_allowed)

        # Count current active accounts
        stmt = select(AccountSession).where(
            AccountSession.user_id == user_id,
            AccountSession.status != AccountStatus.BANNED,
        )
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        current_count = len(accounts)
        if current_count >= max_allowed:
            return (
                False,
                f"Достигнут лимит аккаунтов ({current_count}/{max_allowed}) для тарифа {sub.plan.value}. "
                f"Улучшите тариф для подключения большего числа аккаунтов.",
                max_allowed,
            )

        return True, "Разрешено", max_allowed

    @classmethod
    async def register_account(
        cls,
        session: AsyncSession,
        user_id: int,
        phone: str,
        session_string: str,
        tg_user_id: Optional[int] = None,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
        persona_id: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[AccountSession]]:
        """
        Saves a newly authenticated Telegram session string for the user.
        """
        is_allowed, reason, _ = await cls.can_add_more_accounts(session, user_id)
        if not is_allowed:
            return False, reason, None

        cleaned_phone = phone.strip().replace(" ", "").replace("-", "")

        # Check if already exists for this user
        stmt = select(AccountSession).where(
            AccountSession.user_id == user_id,
            AccountSession.phone == cleaned_phone,
        )
        res = await session.execute(stmt)
        account = res.scalars().first()

        if not account:
            account = AccountSession(
                user_id=user_id,
                phone=cleaned_phone,
                session_string=session_string,
                tg_user_id=tg_user_id,
                first_name=first_name,
                username=username,
                persona_id=persona_id,
                is_active=True,
                status=AccountStatus.CONNECTED,
            )
            session.add(account)
        else:
            account.session_string = session_string
            account.is_active = True
            account.status = AccountStatus.CONNECTED
            if tg_user_id:
                account.tg_user_id = tg_user_id
            if first_name:
                account.first_name = first_name
            if username:
                account.username = username

        await session.commit()
        await session.refresh(account)
        return True, "Аккаунт успешно подключен", account

    @classmethod
    async def get_user_accounts(
        cls, session: AsyncSession, user_id: int
    ) -> List[AccountSession]:
        """Lists all accounts linked to a user."""
        stmt = (
            select(AccountSession)
            .where(AccountSession.user_id == user_id)
            .order_by(AccountSession.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def toggle_account(
        cls, session: AsyncSession, account_id: int, is_active: bool
    ) -> Optional[AccountSession]:
        """Enables or pauses autonomous answering on a specific account."""
        stmt = select(AccountSession).where(AccountSession.id == account_id)
        res = await session.execute(stmt)
        account = res.scalars().first()
        if account:
            account.is_active = is_active
            account.status = AccountStatus.CONNECTED if is_active else AccountStatus.DISCONNECTED
            await session.commit()
            await session.refresh(account)
        return account

    @classmethod
    async def disconnect_account(
        cls, session: AsyncSession, account_id: int
    ) -> bool:
        """Disconnects an account and disables business automation."""
        stmt = select(AccountSession).where(AccountSession.id == account_id)
        res = await session.execute(stmt)
        account = res.scalars().first()
        if account:
            account.is_active = False
            account.status = AccountStatus.DISCONNECTED
            account.business_connection_id = None
            await session.commit()
            return True
        return False

    @classmethod
    async def delete_account(
        cls, session: AsyncSession, account_id: int
    ) -> bool:
        """Removes an account record."""
        stmt = select(AccountSession).where(AccountSession.id == account_id)
        res = await session.execute(stmt)
        account = res.scalars().first()
        if account:
            await session.delete(account)
            await session.commit()
            return True
        return False

    @classmethod
    async def ensure_account_persona(
        cls, session: AsyncSession, account: AccountSession
    ) -> Persona:
        """
        Ensures the given AccountSession has its own dedicated Persona.
        If not yet created, creates a unique Persona initialized from the user's defaults
        and binds it to the account.
        """
        from companion.models.entities import Persona

        if account.persona_id:
            stmt = select(Persona).where(Persona.id == account.persona_id)
            res = await session.execute(stmt)
            p = res.scalars().first()
            if p:
                return p

        # Look up user's primary/default persona as template
        stmt_dp = select(Persona).where(Persona.user_id == account.user_id).order_by(Persona.id.asc())
        res_dp = await session.execute(stmt_dp)
        base_p = res_dp.scalars().first()

        from companion.services.timezone_service import TimezoneService

        acc_name = account.first_name or (base_p.name if base_p else "Алина")
        city_val = base_p.city if base_p else "Москва"
        tz_val = (
            base_p.timezone_name
            if (base_p and base_p.timezone_name)
            else TimezoneService.resolve_timezone_for_city(city_val)
        )
        acc_persona = Persona(
            user_id=account.user_id,
            name=acc_name,
            age=base_p.age if base_p else 22,
            city=city_val,
            district=getattr(base_p, "district", "центр") if base_p else "центр",
            dating_app_context=getattr(base_p, "dating_app_context", None) if base_p else "была в дайвинчике буквально пару дней, вчера удалила анкету потому что надоело, перешла в тг",
            study_work_hobbies=getattr(base_p, "study_work_hobbies", None) if base_p else "Учусь на 4 курсе графического дизайна, беру заказы на фрилансе.",
            extra_facts=getattr(base_p, "extra_facts", None) if base_p else "Люблю атмосферные места, не люблю банальные свидания в душных кафе, давно хотела сходить на крутой квест.",
            legend=base_p.legend if base_p else "Общительная, милая и немного саркастичная девушка.",
            current_style=base_p.current_style if base_p else "adaptive_all",
            timezone_name=tz_val,
        )
        session.add(acc_persona)
        await session.commit()
        await session.refresh(acc_persona)

        account.persona_id = acc_persona.id
        await session.commit()
        return acc_persona

    @classmethod
    async def get_admin_subscribers_overview(
        cls, session: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Retrieves a comprehensive snapshot of all registered users, their subscription status,
        and all connected Telegram accounts for the Admin Panel.
        """
        from companion.models.entities import Contact, Persona
        from companion.models.enums import ContactStatus
        from companion.services.styles_service import StyleType

        stmt_users = select(User).order_by(User.id.desc())
        users = list((await session.execute(stmt_users)).scalars().all())

        overview_list = []
        for u in users:
            # 1. Subscription status
            sub = await SubscriptionService.get_active_subscription(session, u.id)
            sub_info = SubscriptionService.get_detailed_status(sub)

            # 2. Accounts
            stmt_accs = (
                select(AccountSession)
                .where(AccountSession.user_id == u.id)
                .order_by(AccountSession.id.asc())
            )
            accs = list((await session.execute(stmt_accs)).scalars().all())

            # 3. Leads count
            stmt_leads = select(func.count(Contact.id)).where(
                Contact.user_id == u.id,
                Contact.status == ContactStatus.LEAD_MEETING,
            )
            leads_count = (await session.execute(stmt_leads)).scalar_one_or_none() or 0

            acc_details = []
            for a in accs:
                p = None
                if a.persona_id:
                    stmt_p = select(Persona).where(Persona.id == a.persona_id)
                    p = (await session.execute(stmt_p)).scalars().first()
                if not p:
                    p = await cls.ensure_account_persona(session, a)

                cur_style = p.current_style or "adaptive_all"
                style_title = (
                    StyleType(cur_style).title
                    if cur_style in StyleType._value2member_map_
                    else cur_style
                )

                acc_details.append({
                    "id": a.id,
                    "first_name": a.first_name or p.name,
                    "username": a.username,
                    "tg_user_id": a.tg_user_id,
                    "is_active": a.is_active,
                    "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                    "business_connection_id": a.business_connection_id,
                    "persona_name": p.name,
                    "persona_age": p.age,
                    "persona_city": p.city,
                    "style_title": style_title,
                })

            overview_list.append({
                "user_id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "created_at": u.created_at,
                "sub_info": sub_info,
                "accounts": acc_details,
                "total_accounts": len(acc_details),
                "active_accounts": sum(
                    1 for a in acc_details if a["is_active"] and a["status"] == AccountStatus.CONNECTED.value
                ),
                "leads_count": leads_count,
            })

        # Sort so active subscribers and users with accounts appear at the very top
        overview_list.sort(
            key=lambda x: (
                1 if x["sub_info"]["is_active"] else 0,
                x["total_accounts"],
                x["user_id"],
            ),
            reverse=True,
        )

        return overview_list


