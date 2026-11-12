import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.entities import PaymentInvoice, Subscription, User
from companion.models.enums import PlanDuration
from companion.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


class CryptoPayService:
    """
    Integration with @CryptoBot (Telegram Crypto Pay API).
    Provides instant crypto invoice creation, payment polling, and automated subscription issuance.
    """

    PLAN_PRICES: Dict[PlanDuration, float] = {
        PlanDuration.DAYS_3: 3.0,
        PlanDuration.DAYS_7: 7.0,
        PlanDuration.DAYS_14: 12.0,
        PlanDuration.DAYS_31: 25.0,
    }

    @classmethod
    def get_api_base_url(cls) -> str:
        if settings.CRYPTO_PAY_NET.lower() == "testnet":
            return "https://testnet-pay.crypt.bot/api"
        return "https://pay.crypt.bot/api"

    @classmethod
    async def create_invoice(
        cls,
        session: AsyncSession,
        user_id: int,
        plan: PlanDuration,
        asset: str = "USDT",
    ) -> Tuple[PaymentInvoice, str]:
        """
        Creates an invoice for subscription payment.
        If CRYPTO_PAY_TOKEN is configured in .env, calls official @CryptoBot API.
        Otherwise creates a fully operational mock invoice with instant verification support.
        """
        amount = cls.PLAN_PRICES.get(plan, 7.0)
        token = settings.CRYPTO_PAY_TOKEN
        unique_ref = uuid.uuid4().hex[:8]

        invoice_id = ""
        pay_url = ""

        if token:
            url = f"{cls.get_api_base_url()}/createInvoice"
            headers = {"Crypto-Pay-API-Token": token}
            payload = {
                "asset": asset,
                "amount": f"{amount:.2f}",
                "description": f"Подписка {plan.value} в AI-Компаньон",
                "paid_btn_name": "openBot",
                "paid_btn_url": f"https://t.me/{settings.BOT_USERNAME}",
                "payload": f"sub_{plan.value}_{user_id}_{unique_ref}",
            }
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.post(url, json=payload, headers=headers)
                    data = res.json()
                    if data.get("ok"):
                        inv_data = data.get("result", {})
                        invoice_id = str(inv_data.get("invoice_id"))
                        pay_url = inv_data.get("pay_url") or inv_data.get("bot_invoice_url") or ""
            except Exception as e:
                logger.error("Error creating CryptoBot invoice: %s", e)

        if not invoice_id:
            # Fallback / Demo invoice ID
            invoice_id = f"demo_inv_{unique_ref}"
            pay_url = f"https://t.me/CryptoBot?start=invoice_{unique_ref}"

        invoice = PaymentInvoice(
            invoice_id=invoice_id,
            user_id=user_id,
            plan=plan,
            amount=amount,
            asset=asset,
            pay_url=pay_url,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)

        return invoice, pay_url

    @classmethod
    async def check_invoice(
        cls, session: AsyncSession, invoice_id: str
    ) -> Tuple[bool, Optional[PaymentInvoice], Optional[Subscription]]:
        """
        Checks payment status with @CryptoBot API or verifies demo invoice.
        If paid, immediately activates and returns the user subscription.
        """
        stmt = select(PaymentInvoice).where(PaymentInvoice.invoice_id == invoice_id)
        res = await session.execute(stmt)
        invoice = res.scalars().first()
        if not invoice:
            return False, None, None

        if invoice.status == "paid":
            sub = await SubscriptionService.get_active_subscription(session, invoice.user_id)
            return True, invoice, sub

        token = settings.CRYPTO_PAY_TOKEN
        is_paid = False

        if token and not invoice_id.startswith("demo_"):
            url = f"{cls.get_api_base_url()}/getInvoices"
            headers = {"Crypto-Pay-API-Token": token}
            params = {"invoice_ids": invoice_id}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.get(url, params=params, headers=headers)
                    data = res.json()
                    if data.get("ok"):
                        items = data.get("result", {}).get("items", [])
                        if items and items[0].get("status") == "paid":
                            is_paid = True
            except Exception as e:
                logger.error("Error querying CryptoBot API: %s", e)
        else:
            # In demo mode, clicking verify activates the payment
            is_paid = True

        if is_paid:
            invoice.status = "paid"
            invoice.paid_at = datetime.now(timezone.utc)
            sub = await SubscriptionService.activate_plan(
                session=session, user_id=invoice.user_id, plan=invoice.plan, extend=True
            )
            await session.commit()
            return True, invoice, sub

        return False, invoice, None

    @classmethod
    async def process_pending_invoices(
        cls, session: AsyncSession
    ) -> List[Tuple[PaymentInvoice, Subscription]]:
        """
        Periodic poller checking all active invoices.
        Returns list of newly confirmed paid invoices and their activated subscriptions.
        """
        token = settings.CRYPTO_PAY_TOKEN
        if not token:
            return []

        stmt = select(PaymentInvoice).where(PaymentInvoice.status == "active")
        res = await session.execute(stmt)
        active_invoices = res.scalars().all()
        if not active_invoices:
            return []

        activated_list = []
        for inv in active_invoices:
            if inv.invoice_id.startswith("demo_"):
                continue
            is_paid, _, sub = await cls.check_invoice(session, inv.invoice_id)
            if is_paid and sub:
                activated_list.append((inv, sub))

        return activated_list
