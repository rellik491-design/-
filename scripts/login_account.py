import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telethon import TelegramClient
from telethon.sessions import StringSession
from companion.config import settings
from companion.models import init_db, async_session_maker, User, Persona, PlanDuration
from companion.services.subscription_service import SubscriptionService
from companion.services.account_service import AccountService


async def interactive_login():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("📲 ПОДКЛЮЧЕНИЕ TELEGRAM-АККАУНТА (USERBOT LOGIN)")
    print("=" * 60)

    api_id = settings.TELEGRAM_API_ID
    api_hash = settings.TELEGRAM_API_HASH

    if not api_id or not api_hash or api_id == 0:
        print("❌ ОШИБКА: Заполните TELEGRAM_API_ID и TELEGRAM_API_HASH в .env файле!")
        print("Их можно получить бесплатно на https://my.telegram.org (раздел API development tools).")
        return

    phone = input("\n👉 Введите номер телефона аккаунта (например, +79991234567): ").strip()
    if not phone:
        print("Номер не введен.")
        return

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print(f"\n📨 Отправляем запрос на код подтверждения в Telegram для номера {phone}...")
        try:
            sent_code = await client.send_code_request(phone)
        except Exception as e:
            print(f"❌ Ошибка отправки кода: {e}")
            return

        code = input("👉 Введите код, пришедший в Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            if "Two-steps verification" in str(e) or "SessionPasswordNeededError" in str(e):
                password = input("👉 На аккаунте включен 2FA. Введите пароль облачной защиты: ").strip()
                await client.sign_in(password=password)
            else:
                print(f"❌ Ошибка входа: {e}")
                return

    me = await client.get_me()
    session_string = client.session.save()
    await client.disconnect()

    print("\n" + "=" * 60)
    print(f"🎉 УСПЕШНО! Вы вошли в аккаунт: {me.first_name} (@{me.username or 'нет юзернейма'}, ID: {me.id})")
    print("=" * 60)

    # Save to Database
    await init_db()
    async with async_session_maker() as session:
        # Get or create owner user
        from sqlalchemy import select
        stmt = select(User).where(User.telegram_id == settings.ADMIN_TELEGRAM_ID or 999888777)
        res = await session.execute(stmt)
        user = res.scalars().first()
        if not user:
            user = User(telegram_id=settings.ADMIN_TELEGRAM_ID or 999888777, username="owner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # Ensure active subscription
        await SubscriptionService.activate_plan(session, user.id, PlanDuration.DAYS_31)

        # Register account in database
        ok, msg, acc = await AccountService.register_account(
            session=session,
            user_id=user.id,
            phone=phone,
            session_string=session_string,
            tg_user_id=me.id,
            first_name=me.first_name,
            username=me.username,
        )

        if ok:
            print(f"✅ Аккаунт привязан в базе данных (ID аккаунта: {acc.id}).")
            print("Теперь скрипт может автономно отвечать на входящие сообщения на этом номере!")
        else:
            print(f"⚠️ Предупреждение: {msg}")


if __name__ == "__main__":
    asyncio.run(interactive_login())
