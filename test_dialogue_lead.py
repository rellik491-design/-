import asyncio
import os
import sys

# Ensure src in sys.path
sys.path.insert(0, os.path.abspath("src"))

from companion.models import async_session_maker, Persona, Contact
from companion.services.ai_service import AIService
from companion.services.intent_service import IntentService
from companion.bot.admin_notifier import AdminNotifier
from companion.config import settings
from sqlalchemy import select

async def main():
    async with async_session_maker() as session:
        # Load persona
        stmt = select(Persona).where(Persona.name.in_(["Карина", "17", "Алина"]))
        res = await session.execute(stmt)
        persona = res.scalars().first()
        if not persona:
            stmt = select(Persona).limit(1)
            persona = (await session.execute(stmt)).scalars().first()

        contact = Contact(
            id=9999,
            first_name="Павел",
            username="pavel_lead",
            tg_id=777888999,
            detected_city="Москва",
            has_violation=False,
            last_violation_text=None,
            funnel_stage="chatting",
            total_messages_in=0,
            total_messages_out=0,
        )
        contact.alert_date_sent = False
        contact.alert_quest_sent = False
        setattr(contact, "last_event", None)

        print(f"=== ТЕСТОВЫЙ ДИАЛОГ С ДЕВУШКОЙ: {persona.name} ({persona.age} лет, {persona.city}) ===")
        print(f"Легенда: {persona.legend}")
        print(f"Администратор (ID для алармов): {settings.ADMIN_TELEGRAM_ID}\n")
        
        test_dialogue = [
            "Привет! Как ты?",
            "Да нормально, на работе завал, устал как собака",
            "Я кстати в центре работаю, на Тверской. Ты чем вообще занимаешься?",
            "Прикольно, дизайн это круто. А на выходных что обычно делаешь?",
            "Слушай, может на выходных пересечемся, выпьем кофе?",
            "О, квест это огонь идея, я обожаю такое! Я за, погнали в субботу!"
        ]

        chat_history = []
        notifications_sent = []

        for turn_idx, user_msg in enumerate(test_dialogue, 1):
            print(f"👤 Мужчина: {user_msg}")
            
            # Check intent on incoming user message
            meetup_intent = IntentService.detect_meetup_proposal(user_msg)
            meetup_agreement = IntentService.detect_meetup_agreement(user_msg)
            
            reply = await AIService.generate_reply(
                persona=persona,
                contact=contact,
                chat_history=chat_history,
                new_incoming_message=user_msg,
                session=session,
            )
            
            detected_event = getattr(contact, "last_event", None)
            clean_reply = reply.replace("||", "\n👧 Карина: ")
            print(f"👧 Карина: {clean_reply}")

            # Notification check logic (same as in telegram_bot_engine and business_bot)
            alert_type = None
            if detected_event == "QUEST_AGREED" or (meetup_agreement and contact.funnel_stage in ("date_proposed", "quest_offered", "quest_agreed")):
                if not contact.alert_quest_sent:
                    contact.alert_quest_sent = True
                    contact.funnel_stage = "quest_agreed"
                    alert_type = "🚨🔥 КВЕСТ / ВСТРЕЧА СОГЛАСОВАНА (QUEST_AGREED_ALARM)"
                    sent = await AdminNotifier.send_quest_agreed_alarm(
                        owner_tg_id=settings.ADMIN_TELEGRAM_ID,
                        contact_name=contact.first_name or "Павел",
                        contact_username=contact.username or "pavel_lead",
                        contact_tg_id=contact.tg_id or 777888999,
                        user_message=user_msg,
                        contact_id=contact.id,
                    )
                    notifications_sent.append((turn_idx, alert_type, sent))
            elif detected_event == "DATE_PROPOSED" or meetup_intent.is_proposal or meetup_agreement:
                if not contact.alert_date_sent:
                    contact.alert_date_sent = True
                    contact.funnel_stage = "date_proposed"
                    alert_type = "🔔 ПРЕДЛОЖЕНИЕ ВСТРЕЧИ (DATE_PROPOSED_ALERT)"
                    sent = await AdminNotifier.send_date_proposed_alert(
                        owner_tg_id=settings.ADMIN_TELEGRAM_ID,
                        contact_name=contact.first_name or "Павел",
                        contact_username=contact.username or "pavel_lead",
                        contact_tg_id=contact.tg_id or 777888999,
                        user_message=user_msg,
                        contact_id=contact.id,
                    )
                    notifications_sent.append((turn_idx, alert_type, sent))

            if alert_type:
                print(f"   ⚡️ [МОМЕНТАЛЬНЫЙ ПУШ В ТЕЛЕГРАМ АДМИНУ {settings.ADMIN_TELEGRAM_ID}] -> {alert_type}")
            print()

            chat_history.append({"role": "user", "content": user_msg})
            chat_history.append({"role": "assistant", "content": reply})

        print("==================================================")
        print("РЕЗУЛЬТАТЫ ДОСТАВКИ УВЕДОМЛЕНИЙ АДМИНИСТРАТОРУ:")
        for turn, n_type, status in notifications_sent:
            status_str = "ДОСТАВЛЕНО УСПЕШНО ✅" if status else "ОШИБКА ❌"
            print(f"- Реплика #{turn}: {n_type} -> {status_str}")
        print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
