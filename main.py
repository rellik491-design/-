import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure src is discoverable when executing directly
sys.path.insert(0, str(Path(__file__).parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from companion.config import settings
from companion.models import init_db, async_session_maker, User, Persona, PlanDuration, ContactStatus
from companion.models.entities import Contact
from companion.services.subscription_service import SubscriptionService
from companion.services.stats_service import StatsService
from companion.services.timezone_service import TimezoneService
from companion.services.reengagement_service import ReengagementService
from companion.services.invite_service import InviteService
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier
from companion.bot.telegram_bot_engine import TelegramBotEngine


async def run_simulation_demo():
    print("=" * 60)
    print("🚀 TELEGRAM COMPANION AI — ДЕМОНСТРАЦИЯ РАБОТЫ ЯДРА")
    print("=" * 60)

    # 1. Init Database
    await init_db()
    print("✅ База данных успешно инициализирована.")

    async with async_session_maker() as session:
        # Get or create Demo User (Owner)
        from sqlalchemy import select
        stmt_user = select(User).where(User.telegram_id == 999888777)
        res_u = await session.execute(stmt_user)
        user = res_u.scalars().first()
        if not user:
            user = User(telegram_id=999888777, username="owner_demo")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # 2. Activate Subscription (14 days)
        sub = await SubscriptionService.activate_plan(
            session=session, user_id=user.id, plan=PlanDuration.DAYS_14
        )
        print(f"✅ Активирована подписка: {sub.plan.value} (осталось {sub.days_remaining()} дн., лимит аккаунтов: {sub.plan.max_accounts_allowed})")

        # 3. Setup Persona
        stmt_p = select(Persona).where(Persona.user_id == user.id)
        res_p = await session.execute(stmt_p)
        persona = res_p.scalars().first()
        if not persona:
            persona = Persona(
                user_id=user.id,
                name="Алина",
                age=22,
                city="Москва",
                timezone_name="Europe/Moscow",
                sleep_start="23:00",
                sleep_end="08:30",
                legend="Учусь на дизайнера, общительная, с чувством юмора, обожаю кофе и выставки.",
                current_style="adaptive_all",
            )
            session.add(persona)
            await session.commit()
            await session.refresh(persona)
        print(f"✅ Персона девушки: {persona.name}, {persona.city} ({persona.timezone_name})")

        # 4. Generate Chat Automation Invite Link
        invite_link, invite_obj = await InviteService.create_subscription_invite(
            session=session, user_id=user.id, plan=PlanDuration.DAYS_14, bot_username=settings.BOT_USERNAME
        )
        print(f"🔗 Персональная ссылка для Chat Automation: {invite_link}")
        print(f"   Срок действия ссылки: {invite_obj.expires_at.strftime('%Y-%m-%d %H:%M')} (авто-удаление по окончании)")

        # Reset test contact state for reproducible demo runs
        stmt_c = select(Contact).where(Contact.user_id == user.id, Contact.tg_id == 101)
        res_c = await session.execute(stmt_c)
        c_existing = res_c.scalars().first()
        if c_existing:
            c_existing.bot_paused = False
            c_existing.status = ContactStatus.ACTIVE
            c_existing.paused_reason = None
            await session.commit()

        print("\n" + "-" * 50)
        print("📨 ТЕСТ 1: Входящее сообщение от собеседника из другого города")
        incoming_1 = "Привет! Как твои дела? Я сам из Екатеринбурга, только с работы пришел."
        print(f"Собеседник (ID: 101, @mikhail_ekb): «{incoming_1}»")

        res_1 = await UserbotHandler.process_incoming_message(
            session=session,
            user_id=user.id,
            sender_tg_id=101,
            sender_username="mikhail_ekb",
            sender_first_name="Михаил",
            text=incoming_1,
        )

        contact = res_1["contact"]
        print(f"🤖 Ответ Алины: «{res_1.get('reply_text')}»")
        print(f"📍 Определен город собеседника: {contact.detected_city} (Таймзона: {contact.detected_timezone})")
        print(f"⏰ Временной контекст: {TimezoneService.format_time_context(persona.timezone_name, contact.detected_timezone)}")

        print("\n" + "-" * 50)
        print("📨 ТЕСТ 2: Мужчина предлагает встретиться (Перехват встречи / Лид)")
        incoming_2 = "Слушай, а давай встретимся на кофе в субботу и поболтаем вживую?"
        print(f"Собеседник: «{incoming_2}»")

        res_2 = await UserbotHandler.process_incoming_message(
            session=session,
            user_id=user.id,
            sender_tg_id=101,
            sender_username="mikhail_ekb",
            sender_first_name="Михаил",
            text=incoming_2,
        )

        print(f"⚡ Действие системы: {res_2['action']}")
        if res_2["action"] == "handover_to_owner":
            alert_text = AdminNotifier.format_meetup_alert(res_2["alert"])
            print("\n🚨 УВЕДОМЛЕНИЕ ВЛАДЕЛЬЦУ В УПРАВЛЯЮЩИЙ БОТ:")
            print(alert_text)
            print(f"🔒 Статус бота для этого чата: bot_paused = {contact.bot_paused}")

        print("\n" + "-" * 50)
        print("🌙 ТЕСТ 3: Проверка ночного режима («Не писать ночью»)")
        # Simulate night time (02:00 in Moscow)
        moscow_night_dt = datetime(2026, 10, 25, 23, 0, 0, tzinfo=timezone.utc) # 02:00 MSK
        is_sleeping = TimezoneService.is_sleeping_hours(
            "Europe/Moscow", persona.sleep_start, persona.sleep_end, ref_utc_dt=moscow_night_dt
        )
        print(f"Время в Москве 02:00 -> Девушка спит? {is_sleeping} (Пинги строго запрещены)")

        # Simulate daytime (14:00 in Moscow)
        moscow_day_dt = datetime(2026, 10, 25, 11, 0, 0, tzinfo=timezone.utc) # 14:00 MSK
        is_sleeping_day = TimezoneService.is_sleeping_hours(
            "Europe/Moscow", persona.sleep_start, persona.sleep_end, ref_utc_dt=moscow_day_dt
        )
        print(f"Время в Москве 14:00 -> Девушка спит? {is_sleeping_day} (Безопасно для общения)")

        print("\n" + "-" * 50)
        print("📊 ТЕСТ 4: Сводная статистика")
        stats = await StatsService.get_overview(session, user.id)
        report = AdminNotifier.format_stats_report(stats)
        print(report)

        print("\n📋 Список горячих лидов:")
        leads = await StatsService.get_leads(session, user.id)
        for lead in leads:
            print(f" - [{lead['name']}] ({lead['tg_link']}): {lead['proposal_summary']}")

    print("\n" + "=" * 60)
    print("✅ Все тесты и сценарии ядра успешно отработали!")
    print("=" * 60)


async def run_live_bot():
    print("=" * 60)
    print("👑 ЗАПУСК ЖИВОГО СЕРВЕРА TELEGRAM-БОТА")
    print(f"   Токен: {settings.ADMIN_BOT_TOKEN[:12]}...")
    print(f"   Главный Администратор (Ваш ID): {settings.ADMIN_TELEGRAM_ID}")
    print(f"   Юзернейм бота: @{settings.BOT_USERNAME}")
    print("=" * 60)

    await init_db()

    # Ensure Admin user & persona are present in database
    async with async_session_maker() as session:
        from sqlalchemy import select
        stmt_u = select(User).where(User.telegram_id == settings.ADMIN_TELEGRAM_ID)
        res_u = await session.execute(stmt_u)
        admin_u = res_u.scalars().first()
        if not admin_u:
            admin_u = User(telegram_id=settings.ADMIN_TELEGRAM_ID, username="admin")
            session.add(admin_u)
            await session.commit()
            await session.refresh(admin_u)

        stmt_p = select(Persona).where(Persona.user_id == admin_u.id)
        res_p = await session.execute(stmt_p)
        admin_p = res_p.scalars().first()
        if not admin_p:
            admin_p = Persona(
                user_id=admin_u.id,
                name="Алина",
                age=22,
                city="Москва",
                timezone_name="Europe/Moscow",
                sleep_start="23:00",
                sleep_end="08:30",
                legend="Ты общительная, милая и немного саркастичная девушка. Учишься на дизайне и любишь кофе.",
                current_style="adaptive_all",
            )
            session.add(admin_p)
            await session.commit()

    backoff = 2
    max_backoff = 30
    restart_count = 0

    while True:
        bot = TelegramBotEngine(settings.ADMIN_BOT_TOKEN, settings.ADMIN_TELEGRAM_ID)
        try:
            if restart_count > 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Восстановление сессии бота (попытка #{restart_count})...")
            await bot.start()
            break
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n🛑 Остановка сервера бота по сигналу пользователя.")
            try:
                await bot.stop()
            except Exception:
                pass
            break
        except Exception as e:
            restart_count += 1
            print(f"\n⚠️ Сбой сессии Telegram API ({type(e).__name__}: {e})")
            print(f"⏳ Автоматический самовосстанавливающийся перезапуск через {backoff} сек (24/7 Self-Healing)...")
            try:
                await bot.stop()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def main():
    if "--demo" in sys.argv:
        asyncio.run(run_simulation_demo())
    else:
        asyncio.run(run_live_bot())


if __name__ == "__main__":
    main()
