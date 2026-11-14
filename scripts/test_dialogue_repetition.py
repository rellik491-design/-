import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from companion.services.ai_service import AIService
from companion.models.entities import Contact, Persona
from companion.models.db import async_session_maker

async def main():
    print("--- 1. Testing Contextual Fallback for Specific Questions ---")
    persona = Persona(id=1, name="Карина", current_style="adaptive_all")
    contact = Contact(id=81, first_name="Паша")

    test_queries = [
        "На что отвлеклась?",
        "О чем думала?",
        "В смысле??",
        "Ох, какая грязь! Просто грязь",
    ]

    used = []
    for q in test_queries:
        reply = AIService.generate_contextual_fallback(contact, persona, q, excluded_replies=used)
        print(f"Q: '{q}'\n  -> Fallback Reply: '{reply}'\n")
        assert reply not in used, f"Repetition detected: {reply}"
        used.append(reply)

    print("--- 2. Testing Anti-Repetition Detector ---")
    assert AIService._is_repetitive_reply(
        "да, понимаю тебя как раз думала об этом)",
        ["да, понимаю тебя как раз думала об этом)"]
    )
    assert AIService._is_repetitive_reply(
        "да понимаю тебя как раз думала об этом",
        ["да, понимаю тебя как раз думала об этом)"]
    )
    assert not AIService._is_repetitive_reply(
        "да по учёбе проект сдавала, в экран уткнулась)",
        ["да, понимаю тебя как раз думала об этом)"]
    )
    print("Anti-repetition checks PASSED!\n")

    print("--- 3. Testing Real Gemini Generation with Anti-Repetition ---")
    async with async_session_maker() as session:
        # Load contact 81
        c = await session.get(Contact, 81)
        p = await session.get(Persona, 1)
        history = [
            {"role": "user", "parts": ["На что отвлеклась?"]},
            {"role": "model", "parts": ["да по учёбе проект сдавала, в экран уткнулась)"]},
        ]
        reply1 = await AIService.generate_reply(
            persona=p,
            contact=c,
            chat_history=history,
            new_incoming_message="О чем думала?",
            session=session,
        )
        print(f"User: 'О чем думала?'\nBot: '{reply1}'\n")

        history.append({"role": "user", "parts": ["О чем думала?"]})
        history.append({"role": "model", "parts": [reply1]})

        reply2 = await AIService.generate_reply(
            persona=p,
            contact=c,
            chat_history=history,
            new_incoming_message="В смысле??",
            session=session,
        )
        print(f"User: 'В смысле??'\nBot: '{reply2}'\n")

    print("ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main())
