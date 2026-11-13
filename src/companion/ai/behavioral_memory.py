"""
Persistent Behavioral Memory & Controlled Feedback Learning Layer.
Tracks conversation habits, user preferences, and adapts style dynamically
without modifying source code or base system prompts.
"""

import logging
import re
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from companion.models import Contact

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
        r'\b(меньше\s+смайл(ов|иков)|хватит\s+эмодзи|не\s+ставь\s+смайл(ы|ики)|без\s+смайл(ов|иков)|не\s+используй\s+эмодзи|fewer\s+emojis|no\s+emojis)\b',
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

    @classmethod
    async def analyze_and_learn_feedback(
        cls,
        session: AsyncSession,
        contact: Contact,
        user_message: str
    ) -> Dict[str, Any]:
        """
        Analyzes the user message for explicit behavioral feedback signals.
        Gradually and persistently updates contact behavioral preferences.
        """
        if not contact or not user_message:
            return {"updated": False, "changes": []}

        text = user_message.strip()
        changes = []

        # 1. Message Length preference
        if cls.TRIGGER_SHORTER.search(text):
            if contact.preferred_response_length != "short":
                contact.preferred_response_length = "short"
                changes.append("preferred_response_length -> short")
        elif cls.TRIGGER_LONGER.search(text):
            if contact.preferred_response_length != "long":
                contact.preferred_response_length = "long"
                changes.append("preferred_response_length -> long")

        # 2. Parentheses preference
        if cls.TRIGGER_NO_BRACKETS.search(text):
            if not getattr(contact, "no_brackets", False):
                contact.no_brackets = True
                changes.append("no_brackets -> True")
        elif cls.TRIGGER_ALLOW_BRACKETS.search(text):
            if getattr(contact, "no_brackets", False):
                contact.no_brackets = False
                changes.append("no_brackets -> False")

        # 3. Emoji preference
        if cls.TRIGGER_FEWER_EMOJIS.search(text):
            if contact.emoji_frequency != "none":
                contact.emoji_frequency = "none"
                changes.append("emoji_frequency -> none")
        elif cls.TRIGGER_MORE_EMOJIS.search(text):
            if contact.emoji_frequency != "high":
                contact.emoji_frequency = "high"
                changes.append("emoji_frequency -> high")

        # 4. Humor & Style preference
        if cls.TRIGGER_PLAYFUL_HUMOR.search(text):
            if contact.communication_style != "playful":
                contact.communication_style = "playful"
                changes.append("communication_style -> playful")
        elif cls.TRIGGER_SERIOUS.search(text):
            if contact.communication_style != "serious":
                contact.communication_style = "serious"
                changes.append("communication_style -> serious")

        if changes:
            await session.commit()
            logger.info(
                "[CONTROLLED LEARNING] Updated behavioral preferences for contact %s (%s): %s",
                contact.id, contact.first_name or contact.username, ", ".join(changes)
            )
            return {"updated": True, "changes": changes}

        return {"updated": False, "changes": []}

    @classmethod
    def get_behavioral_style_context(cls, contact: Contact) -> Dict[str, Any]:
        """
        Compiles the contact's persistent behavioral memory for style post-processing and LLM prompt.
        """
        if not contact:
            return {
                "no_brackets": False,
                "preferred_length": "medium",
                "emoji_pref": "medium",
                "style": "casual"
            }

        return {
            "no_brackets": bool(getattr(contact, "no_brackets", False)),
            "preferred_length": getattr(contact, "preferred_response_length", "medium") or "medium",
            "emoji_pref": getattr(contact, "emoji_frequency", "medium") or "medium",
            "style": getattr(contact, "communication_style", "casual") or "casual",
        }
