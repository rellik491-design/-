import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from companion.config import settings
from companion.models import init_db
from companion.bot.telegram_bot_engine import TelegramBotEngine


async def run_test():
    print(f"Testing bot token: {settings.ADMIN_BOT_TOKEN[:10]}... for admin: {settings.ADMIN_TELEGRAM_ID}")
    await init_db()
    bot = TelegramBotEngine(settings.ADMIN_BOT_TOKEN, settings.ADMIN_TELEGRAM_ID)
    task = asyncio.create_task(bot.start())
    print("Bot started, waiting 4 seconds to test connection and initial greeting...")
    await asyncio.sleep(4)
    await bot.stop()
    await task
    print("Bot engine test completed successfully!")


if __name__ == "__main__":
    asyncio.run(run_test())
