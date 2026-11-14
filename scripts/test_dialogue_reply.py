import asyncio
from companion.models.db import async_session_maker
from companion.models.entities import Contact, Persona
from companion.services.ai_service import AIService
from sqlalchemy import select

async def main():
    async with async_session_maker() as session:
        contact = (await session.execute(select(Contact).where(Contact.id == 81))).scalar_one_or_none()
        if not contact:
            print("Contact 81 not found!")
            return
        persona = (await session.execute(select(Persona).where(Persona.user_id == contact.user_id, Persona.is_active == True))).scalars().first()
        if not persona:
            persona = (await session.execute(select(Persona).where(Persona.user_id == contact.user_id))).scalars().first()
        print(f"Contact ID: {contact.id}, Name: {contact.first_name}, Username: {contact.username}")
        print(f"Persona: {persona.name if persona else 'None'}")
        
        history = AIService.get_chat_history(81)
        print(f"History in memory: {len(history)}")
        for h in history[-8:]:
            print("  ", h)

        import logging
        logging.basicConfig(level=logging.DEBUG)

        from companion.ai.context_manager import ContextManager
        context_pkg = await ContextManager.build_seven_level_context(
            persona=persona,
            contact=contact,
            new_incoming_message="На что отвлеклась?",
            session=session,
            explicit_recent_history=history,
        )
        sys_inst = AIService.build_system_prompt(persona, contact, "На что отвлеклась?")
        print("\n=== SYSTEM INSTRUCTION ===")
        print(sys_inst[:600])
        print("\n=== PROMPT CONTENTS ===")
        for c in context_pkg["prompt_contents"]:
            print(c)
        print("=======================")
        reply = await AIService.generate_reply(
            persona=persona,
            contact=contact,
            chat_history=history,
            new_incoming_message="На что отвлеклась?",
            session=session,
        )
        print("FINAL Generated Reply:", repr(reply))

if __name__ == "__main__":
    asyncio.run(main())
