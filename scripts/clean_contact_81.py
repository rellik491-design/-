import asyncio
from sqlalchemy import text
from companion.models.db import async_session_maker
from companion.services.ai_service import AIService

async def clean_history():
    async with async_session_maker() as session:
        # Update messages 857, 859, 870, 872 with natural replies
        await session.execute(text("UPDATE contact_messages SET text = 'да по учёбе проект сдавала, в экран уткнулась)' WHERE id = 857"))
        await session.execute(text("UPDATE contact_messages SET text = 'хах, ну на улице слякоть сплошная)' WHERE id = 859"))
        await session.execute(text("UPDATE contact_messages SET text = 'да говорю, день суматошный выдался)' WHERE id = 870"))
        await session.execute(text("UPDATE contact_messages SET text = 'да о том, как бы поскорее отдохнуть) ты как сам?' WHERE id = 872"))
        # Reset conversation summary for contact 81
        await session.execute(text("DELETE FROM conversation_summaries WHERE contact_id = 81"))
        await session.commit()
        # Reset memory cache
        AIService._contact_chat_history.pop(81, None)
        AIService._last_fallbacks.pop(81, None)
        print("History cleaned and memory cache cleared successfully for Contact 81!")

if __name__ == "__main__":
    asyncio.run(clean_history())
