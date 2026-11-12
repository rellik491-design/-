import asyncio
import logging
from typing import Optional
from telethon import TelegramClient, events, Button
from sqlalchemy import select

from companion.config import settings
from companion.models import async_session_maker, User, Persona, Contact, PlanDuration
from companion.models.enums import ContactStatus
from companion.services.stats_service import StatsService
from companion.services.subscription_service import SubscriptionService
from companion.services.handover_service import HandoverService
from companion.bot.admin_notifier import AdminNotifier

logger = logging.getLogger(__name__)


from companion.services.account_service import AccountService


def create_admin_bot(bot_token: str) -> TelegramClient:
    """
    Creates a Telethon bot client using standard Bot API token
    to serve as the interactive control panel for the owner.
    """
    api_id = settings.TELEGRAM_API_ID or 12345
    api_hash = settings.TELEGRAM_API_HASH or "0123456789abcdef0123456789abcdef"

    bot = TelegramClient("admin_bot_session", api_id, api_hash)

    @bot.on(events.NewMessage(pattern=r"^/start"))
    async def start_handler(event):
        sender = await event.get_sender()
        async with async_session_maker() as session:
            # Ensure user exists in DB
            stmt = select(User).where(User.telegram_id == sender.id)
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                user = User(telegram_id=sender.id, username=sender.username)
                session.add(user)
                await session.commit()
                # Activate default 14-day trial
                await SubscriptionService.activate_plan(session, user.id, PlanDuration.DAYS_14)

        buttons = [
            [Button.inline("📱 Мои аккаунты", b"accounts"), Button.inline("➕ Добавить аккаунт", b"add_account")],
            [Button.inline("📊 Статистика", b"stats"), Button.inline("🔥 Лиды (Встречи)", b"leads")],
            [Button.inline("💎 Подписка", b"sub"), Button.inline("⚙️ Персона", b"persona")],
        ]
        text = (
            f"👋 **Привет! Это панель управления AI-компаньоном.**\n\n"
            f"Здесь вы можете подключать аккаунты, получать мгновенные уведомления о встречах, "
            f"ставить бота на паузу и отслеживать конверсию переписок."
        )
        await event.respond(text, buttons=buttons)

    @bot.on(events.CallbackQuery(data=b"accounts"))
    async def accounts_callback(event):
        sender = await event.get_sender()
        async with async_session_maker() as session:
            stmt = select(User).where(User.telegram_id == sender.id)
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                await event.respond("Пользователь не найден. Нажмите /start")
                return

            accounts = await AccountService.get_user_accounts(session, user.id)
            if not accounts:
                await event.respond("📭 У вас пока нет подключенных Telegram аккаунтов. Нажмите '➕ Добавить аккаунт'.")
                return

            text = "📱 **ВАШИ ПОДКЛЮЧЕННЫЕ АККАУНТЫ:**\n\n"
            for idx, acc in enumerate(accounts, 1):
                status_icon = "🟢 Активен" if acc.is_active else "⏸ На паузе"
                text += f"{idx}. **{acc.phone}** ({acc.first_name or acc.username or 'Без имени'})\n   Статус: {status_icon}\n\n"
            await event.respond(text)

    @bot.on(events.CallbackQuery(data=b"add_account"))
    async def add_account_callback(event):
        sender = await event.get_sender()
        async with async_session_maker() as session:
            stmt = select(User).where(User.telegram_id == sender.id)
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                await event.respond("Пользователь не найден. Нажмите /start")
                return

            can_add, reason, max_allowed = await AccountService.can_add_more_accounts(session, user.id)
            if not can_add:
                await event.respond(f"❌ {reason}")
                return

            text = (
                f"📲 **Подключение нового аккаунта Telegram:**\n\n"
                f"Ваш тариф позволяет подключить до **{max_allowed}** аккаунтов.\n"
                f"Отправьте команду:\n`/connect <номер_телефона>`\n"
                f"Например: `/connect +79991234567`"
            )
            await event.respond(text)

    @bot.on(events.CallbackQuery(data=b"stats"))
    async def stats_callback(event):
        sender = await event.get_sender()
        async with async_session_maker() as session:
            stmt = select(User).where(User.telegram_id == sender.id)
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                await event.respond("Пользователь не найден. Нажмите /start")
                return

            stats = await StatsService.get_overview(session, user.id)
            report = AdminNotifier.format_stats_report(stats)
            await event.respond(report)

    @bot.on(events.CallbackQuery(data=b"leads"))
    async def leads_callback(event):
        sender = await event.get_sender()
        async with async_session_maker() as session:
            stmt = select(User).where(User.telegram_id == sender.id)
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                await event.respond("Пользователь не найден. Нажмите /start")
                return

            leads = await StatsService.get_leads(session, user.id)
            if not leads:
                await event.respond("📭 Пока нет предложений о встрече.")
                return

            text = f"🔥 **СПИСОК ЛИДОВ (ПРЕДЛОЖЕНИЯ ВСТРЕЧИ):**\n\n"
            for idx, lead in enumerate(leads, 1):
                city_str = f" ({lead['city']})" if lead['city'] else ""
                status_str = "⏸ на паузе" if lead['bot_paused'] else "▶️ активен"
                text += (
                    f"**{idx}. [{lead['name']}]({lead['tg_link']})**{city_str}\n"
                    f"💬 _{lead['proposal_summary']}_\n"
                    f"⏰ Дата: `{lead['detected_at']}` | Статус: {status_str}\n"
                    f"Команда: `/resume {lead['contact_id']}` (возобновить) или `/pause {lead['contact_id']}`\n\n"
                )
            await event.respond(text, link_preview=False)

    @bot.on(events.CallbackQuery(data=b"sub"))
    async def sub_callback(event):
        sender = await event.get_sender()
        async with async_session_maker() as session:
            stmt = select(User).where(User.telegram_id == sender.id)
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                await event.respond("Пользователь не найден. Нажмите /start")
                return

            sub = await SubscriptionService.get_active_subscription(session, user.id)
            if sub:
                days = sub.days_remaining()
                text = (
                    f"💎 **Ваша подписка:** Активна\n"
                    f"📅 Тариф: **{sub.plan.value}**\n"
                    f"⏳ Осталось дней: **{days}**\n"
                    f"🎙 Использовано голосовых сегодня: {sub.voice_used_today}/{sub.max_daily_voice}"
                )
            else:
                text = "❌ Подписка не активна. Доступные тарифы: 3, 7, 14, 31 дней."
            await event.respond(text)

    @bot.on(events.NewMessage(pattern=r"^/pause\s+(\d+)"))
    async def pause_handler(event):
        contact_id = int(event.pattern_match.group(1))
        async with async_session_maker() as session:
            c = await HandoverService.set_contact_pause_state(session, contact_id, paused=True, reason="manual_command")
            if c:
                await event.respond(f"⏸ Бот поставлен на паузу для контакта ID {c.tg_id}.")
            else:
                await event.respond("Контакт не найден.")

    @bot.on(events.NewMessage(pattern=r"^/resume\s+(\d+)"))
    async def resume_handler(event):
        contact_id = int(event.pattern_match.group(1))
        async with async_session_maker() as session:
            c = await HandoverService.set_contact_pause_state(session, contact_id, paused=False)
            if c:
                await event.respond(f"▶️ Бот возобновил работу для контакта ID {c.tg_id}.")
            else:
                await event.respond("Контакт не найден.")

    return bot
