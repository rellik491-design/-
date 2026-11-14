"""
Persistent Behavioral Memory & Controlled Feedback Learning Layer.
Implements the 3-Level Memory Architecture:
- Level 1: Short-term conversation memory (recent turns, active context)
- Level 2: Long-term user memory (facts, age, city, job, discovered Daivinchik profile)
- Level 3: Behavioral learning memory (learned communication preferences, outcome signals, explicit feedback overrides)
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models import Contact, ContactBehavioralPreference, ContactMemory

logger = logging.getLogger(__name__)


class ControlledFeedbackLearner:
    """
    Safely captures explicit and implicit user feedback signals
    and updates persistent behavioral preferences in the database.
    """

    # Explicit feedback triggers
    TRIGGER_SHORTER = re.compile(
        r'\b(пиши\s+короче|отвечай\s+короче|слишком\s+много\s+текста|много\s+букв|покороче|меньше\s+текста|talk\s+shorter|be\s+brief|too\s+long)\b',
        re.IGNORECASE
    )
    TRIGGER_LONGER = re.compile(
        r'\b(пиши\s+подробнее|расскажи\s+подробнее|побольше\s+текста|почему\s+так\s+кратко|разверни\s+мысль|tell\s+more)\b',
        re.IGNORECASE
    )
    TRIGGER_NO_BRACKETS = re.compile(
        r'\b(хватит\s+скоб(ок|очек)|не\s+ставь\s+скоб(ки|очки)|без\s+скоб(ок|очек)|зачем\s+скоб(ки|очки)|раздражают\s+скоб(ки|очки)|no\s+brackets)\b',
        re.IGNORECASE
    )
    TRIGGER_ALLOW_BRACKETS = re.compile(
        r'\b(ставь\s+скоб(ки|очки)|можно\s+со\s+скобками|со\s+скобками\s+милее)\b',
        re.IGNORECASE
    )
    TRIGGER_FEWER_EMOJIS = re.compile(
        r'\b(меньше\s+смайл(ов|иков)|хватит\s+(?:эмодзи|смайл(?:ов|иков))|не\s+ставь\s+смайл(ы|ики)|без\s+смайл(ов|иков)|не\s+используй\s+(?:эмодзи|смайлики)|fewer\s+emojis|no\s+emojis)\b',
        re.IGNORECASE
    )
    TRIGGER_MORE_EMOJIS = re.compile(
        r'\b(больше\s+смайл(ов|иков)|ставь\s+эмодзи|люблю\s+смайл(ики|ы)|more\s+emojis)\b',
        re.IGNORECASE
    )
    TRIGGER_PLAYFUL_HUMOR = re.compile(
        r'\b(будь\s+веселее|пошути|ты\s+слишком\s+серьезн(ая|о)|давай\s+повеселее|больше\s+юмора|шути)\b',
        re.IGNORECASE
    )
    TRIGGER_SERIOUS = re.compile(
        r'\b(давай\s+серьезн(о|ее)|хватит\s+шутить|не\s+до\s+шуток|будь\s+серьезн(ой|ее)|без\s+приколов)\b',
        re.IGNORECASE
    )

    # Positive outcome triggers (laughter, amusement, complimenting humor)
    LAUGHTER_REGEX = re.compile(r'(?:\bахах[ах]*\b|\bхаха[ах]*\b|\bхех[ех]*\b|[😂🤣😁😅])', re.IGNORECASE)

    @classmethod
    async def analyze_and_learn_feedback(
        cls,
        session: AsyncSession,
        contact: Contact,
        user_message: str
    ) -> Dict[str, Any]:
        """
        Analyzes the user message for explicit behavioral feedback signals.
        Explicit feedback receives HIGH confidence and immediate override priority.
        """
        if not contact or not user_message:
            return {"updated": False, "changes": []}

        text = user_message.strip()
        changes = []

        async def _set_pref(key: str, val: str, evidence: str):
            # 1. Update contact column if available
            if key == "response_length":
                contact.preferred_response_length = val
            elif key == "brackets":
                contact.no_brackets = (val == "forbidden")
            elif key == "emojis":
                contact.emoji_frequency = val
            elif key == "style":
                contact.communication_style = val

            # 2. Persist in contact_behavioral_preferences table
            stmt = select(ContactBehavioralPreference).where(
                ContactBehavioralPreference.contact_id == contact.id,
                ContactBehavioralPreference.preference_key == key
            )
            res = await session.execute(stmt)
            pref = res.scalars().first()
            if not pref:
                pref = ContactBehavioralPreference(
                    contact_id=contact.id,
                    preference_key=key,
                    preference_value=val,
                    confidence="high",
                    source="explicit_user_feedback",
                    evidence=evidence,
                )
                session.add(pref)
            else:
                pref.preference_value = val
                pref.confidence = "high"
                pref.source = "explicit_user_feedback"
                pref.evidence = evidence
                pref.updated_at = datetime.now(timezone.utc)

        # 1. Message Length preference
        if cls.TRIGGER_SHORTER.search(text):
            await _set_pref("response_length", "short", f"User said: «{text}»")
            changes.append("response_length -> short (explicit)")
        elif cls.TRIGGER_LONGER.search(text):
            await _set_pref("response_length", "long", f"User said: «{text}»")
            changes.append("response_length -> long (explicit)")

        # 2. Parentheses preference
        if cls.TRIGGER_NO_BRACKETS.search(text):
            await _set_pref("brackets", "forbidden", f"User said: «{text}»")
            changes.append("brackets -> forbidden (explicit)")
        elif cls.TRIGGER_ALLOW_BRACKETS.search(text):
            await _set_pref("brackets", "allowed", f"User said: «{text}»")
            changes.append("brackets -> allowed (explicit)")

        # 3. Emoji preference
        if cls.TRIGGER_FEWER_EMOJIS.search(text):
            await _set_pref("emojis", "none", f"User said: «{text}»")
            changes.append("emojis -> none (explicit)")
        elif cls.TRIGGER_MORE_EMOJIS.search(text):
            await _set_pref("emojis", "high", f"User said: «{text}»")
            changes.append("emojis -> high (explicit)")

        # 4. Humor & Style preference
        if cls.TRIGGER_PLAYFUL_HUMOR.search(text):
            await _set_pref("style", "playful", f"User said: «{text}»")
            changes.append("style -> playful (explicit)")
        elif cls.TRIGGER_SERIOUS.search(text):
            await _set_pref("style", "serious", f"User said: «{text}»")
            changes.append("style -> serious (explicit)")

        if changes:
            await session.commit()
            logger.info(
                "[CONTROLLED LEARNING] Updated explicit preferences for contact %s: %s",
                contact.id, ", ".join(changes)
            )
            return {"updated": True, "changes": changes}

        return {"updated": False, "changes": []}

    @classmethod
    async def analyze_outcome_learning(
        cls,
        session: AsyncSession,
        contact: Contact,
        user_message: str,
        is_voice: bool = False
    ) -> None:
        """
        Observes interaction outcomes (laughter, voice notes, engagement continuity)
        and updates Level 3 preferences with medium confidence.
        """
        if not contact:
            return

        text = user_message.strip() if user_message else ""

        # 1. User laughs or reacts warmly to humor
        if cls.LAUGHTER_REGEX.search(text):
            stmt = select(ContactBehavioralPreference).where(
                ContactBehavioralPreference.contact_id == contact.id,
                ContactBehavioralPreference.preference_key == "humor_receptivity"
            )
            res = await session.execute(stmt)
            pref = res.scalars().first()
            if not pref:
                pref = ContactBehavioralPreference(
                    contact_id=contact.id,
                    preference_key="humor_receptivity",
                    preference_value="high",
                    confidence="medium",
                    source="observed_outcome",
                    evidence="User laughed at humor in conversation",
                )
                session.add(pref)
                await session.commit()
            elif pref.source != "explicit_user_feedback":
                pref.preference_value = "high"
                pref.updated_at = datetime.now(timezone.utc)
                await session.commit()

        # 2. User sends voice notes -> records voice receptivity
        if is_voice:
            stmt = select(ContactBehavioralPreference).where(
                ContactBehavioralPreference.contact_id == contact.id,
                ContactBehavioralPreference.preference_key == "voice_notes"
            )
            res = await session.execute(stmt)
            pref = res.scalars().first()
            if not pref:
                pref = ContactBehavioralPreference(
                    contact_id=contact.id,
                    preference_key="voice_notes",
                    preference_value="prefers_voice",
                    confidence="medium",
                    source="observed_outcome",
                    evidence="User initiated or responded with voice notes",
                )
                session.add(pref)
                await session.commit()

    @classmethod
    async def get_level3_behavioral_profile(
        cls,
        session: AsyncSession,
        contact: Contact
    ) -> Dict[str, Any]:
        """
        Compiles the full Level 3 behavioral profile, merging database preferences
        with contact columns, prioritizing explicit feedback over observed patterns.
        """
        if not contact:
            return {
                "response_length": "medium",
                "no_brackets": False,
                "emoji_pref": "medium",
                "style": "casual",
                "humor_receptivity": "normal",
                "voice_notes": "neutral",
            }

        profile = {
            "response_length": getattr(contact, "preferred_response_length", "medium") or "medium",
            "no_brackets": bool(getattr(contact, "no_brackets", False)),
            "emoji_pref": getattr(contact, "emoji_frequency", "medium") or "medium",
            "style": getattr(contact, "communication_style", "casual") or "casual",
            "humor_receptivity": "normal",
            "voice_notes": "neutral",
            "explicit_rules": [],
        }

        # Query contact_behavioral_preferences
        try:
            stmt = select(ContactBehavioralPreference).where(
                ContactBehavioralPreference.contact_id == contact.id
            )
            res = await session.execute(stmt)
            for row in res.scalars().all():
                if row.preference_key == "response_length":
                    profile["response_length"] = row.preference_value
                elif row.preference_key == "brackets":
                    profile["no_brackets"] = (row.preference_value == "forbidden")
                elif row.preference_key == "emojis":
                    profile["emoji_pref"] = row.preference_value
                elif row.preference_key == "style":
                    profile["style"] = row.preference_value
                elif row.preference_key == "humor_receptivity":
                    profile["humor_receptivity"] = row.preference_value
                elif row.preference_key == "voice_notes":
                    profile["voice_notes"] = row.preference_value

                if row.source == "explicit_user_feedback":
                    profile["explicit_rules"].append(f"{row.preference_key}: {row.preference_value} ({row.evidence})")
        except Exception as e:
            logger.debug("Failed to query behavioral preferences: %s", e)

        return profile

    @classmethod
    def format_behavioral_prompt(cls, profile: Dict[str, Any]) -> str:
        """
        Formats Level 3 behavioral profile into a compact, directive prompt block.
        """
        lines = []
        if profile.get("response_length") == "short":
            lines.append("- Длина ответов: КОРОТКО (собеседник просил писать лаконично, 1-2 предложения максимум).")
        elif profile.get("response_length") == "long":
            lines.append("- Длина ответов: развернуто, с подробностями.")

        if profile.get("no_brackets"):
            lines.append("- Скобки-смайлики: СТРОГО ЗАПРЕЩЕНЫ. Не ставь ')' и '(' в конце предложений.")

        if profile.get("emoji_pref") == "none":
            lines.append("- Эмодзи: НЕ используй эмодзи (собеседник не любит смайлы).")
        elif profile.get("emoji_pref") == "high":
            lines.append("- Эмодзи: можно чаще использовать милые эмодзи (✨, ☕, 🙈).")

        if profile.get("humor_receptivity") == "high":
            lines.append("- Юмор: собеседник отлично понимает и любит шутки, смеется, можно легко поддразнивать.")

        if profile.get("voice_notes") == "prefers_voice":
            lines.append("- Формат: собеседник любит голосовые сообщения.")

        if not lines:
            return ""

        return "\n[ВЫУЧЕННЫЕ ПРЕДПОЧТЕНИЯ СОБЕСЕДНИКА (УРОВЕНЬ 3)]:\n" + "\n".join(lines)


class ThreeLevelMemoryManager:
    """
    Coordinates Level 1, Level 2, and Level 3 memory layers for the prompt builder.
    """

    @classmethod
    async def build_three_level_prompt_context(
        cls,
        session: Optional[AsyncSession],
        contact: Optional[Contact],
        incoming_message: str
    ) -> str:
        """
        Assembles all 3 memory levels into structured context blocks for the AI prompt.
        """
        blocks = []

        if not contact:
            return ""

        # Level 2: Factual User Memory & Daivinchik Profile
        disc_profile = contact.get_discovered_profile()
        if disc_profile:
            p_parts = []
            if disc_profile.get("name"): p_parts.append(f"Имя: {disc_profile['name']}")
            if disc_profile.get("age"): p_parts.append(f"Возраст: {disc_profile['age']}")
            if disc_profile.get("city"): p_parts.append(f"Город: {disc_profile['city']}")
            if disc_profile.get("description"): p_parts.append(f"Текст анкеты/био: {disc_profile['description']}")
            if disc_profile.get("hobbies"): p_parts.append(f"Интересы: {', '.join(disc_profile['hobbies'])}")

            blocks.append(
                "[КОНТЕКСТ ЗНАКОМСТВА / АНКЕТА ДАЙВИНЧИК]:\n"
                f"Собеседник нашел твою анкету в Дайвинчике (или переслал свою/твою анкету).\n"
                f"Данные анкеты: {', '.join(p_parts)}.\n"
                "Правило: Не навязывай эту тему первой, но если он упоминает анкету, Дайвинчик или знакомство — "
                "отвечай органично (была там пару дней, перешла в тг, потому что там много странных, ценишь нормальное общение)."
            )

        # Level 3: Behavioral Learning Profile
        if session:
            prof = await ControlledFeedbackLearner.get_level3_behavioral_profile(session, contact)
            beh_prompt = ControlledFeedbackLearner.format_behavioral_prompt(prof)
            if beh_prompt:
                blocks.append(beh_prompt)

        return "\n\n".join(blocks)
