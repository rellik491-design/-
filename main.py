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

import uvicorn
from companion.config import settings
from companion.models import init_db, async_session_maker, User, Persona, PlanDuration, ContactStatus
from companion.models.entities import Contact
from companion.services.subscription_service import SubscriptionService
from companion.services.stats_service import StatsService
from companion.services.timezone_service import TimezoneService
from companion.services.invite_service import InviteService
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier
from companion.bot.telegram_bot_engine import TelegramBotEngine
from companion.api.app import app as fastapi_app


async def run_simulation_demo():
    print("=" * 60)
    print("🚀 TELEGRAM COMPANION AI — ДЕМОНСТРАЦИЯ РАБОТЫ ЯДРА")
    print("=" * 60)

    await init_db()
    print("✅ База данных успешно инициализирована.")

    async with async_session_maker() as session:
        from sqlalchemy import select
        stmt_user = select(User).where(User.telegram_id == 999888777)
        res_u = await session.execute(stmt_user)
        user = res_u.scalars().first()
        if not user:
            user = User(telegram_id=999888777, username="owner_demo")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        sub = await SubscriptionService.activate_plan(
            session=session, user_id=user.id, plan=PlanDuration.DAYS_14
        )
        print(f"✅ Активирована подписка: {sub.plan.value} (осталось {sub.days_remaining()} дн., лимит аккаунтов: {sub.plan.max_accounts_allowed})")

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

        invite_link, invite_obj = await InviteService.create_subscription_invite(
            session=session, user_id=user.id, plan=PlanDuration.DAYS_14, bot_username=settings.BOT_USERNAME
        )
        print(f"🔗 Персональная ссылка для Chat Automation: {invite_link}")
        print(f"   Срок действия ссылки: {invite_obj.expires_at.strftime('%Y-%m-%d %H:%M')} (авто-удаление по окончании)")

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
        print("📊 ТЕСТ 3: Сводная статистика")
        stats = await StatsService.get_overview(session, user.id)
        report = AdminNotifier.format_stats_report(stats)
        print(report)

    print("\n" + "=" * 60)
    print("✅ Все тесты и сценарии ядра успешно отработали!")
    print("=" * 60)


async def _run_bot_loop():
    if not settings.ADMIN_BOT_TOKEN:
        logging.warning("ADMIN_BOT_TOKEN is not configured; Telegram Bot polling skipped.")
        return

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
            try:
                await bot.stop()
            except Exception:
                pass
            break
        except Exception as e:
            restart_count += 1
            print(f"\n⚠️ Сбой сессии Telegram API ({type(e).__name__}: {e})")
            try:
                await bot.stop()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


async def _run_api_server():
    config = uvicorn.Config(
        app=fastapi_app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_live_bot(api_only: bool = False, bot_only: bool = False):
    print("=" * 60)
    print("👑 ЗАПУСК СЕРВЕРА TELEGRAM COMPANION AI (MINI APP + BOT ENGINE)")
    print(f"   Главный Администратор (ID): {settings.ADMIN_TELEGRAM_ID}")
    print(f"   Юзернейм бота: @{settings.BOT_USERNAME}")
    print(f"   Mini App / Backend API: http://{settings.API_HOST}:{settings.API_PORT}")
    print("=" * 60)

    await init_db()

    # Ensure Admin user & persona are present in database
    if settings.ADMIN_TELEGRAM_ID:
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

    tasks = []
    if not bot_only:
        tasks.append(asyncio.create_task(_run_api_server()))
    if not api_only:
        tasks.append(asyncio.create_task(_run_bot_loop()))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        for t in tasks:
            t.cancel()


def main():
    if "--demo" in sys.argv:
        asyncio.run(run_simulation_demo())
    elif "--api-only" in sys.argv:
        asyncio.run(run_live_bot(api_only=True))
    elif "--bot-only" in sys.argv:
        asyncio.run(run_live_bot(bot_only=True))
    else:
        asyncio.run(run_live_bot())


if __name__ == "__main__":
    main()
