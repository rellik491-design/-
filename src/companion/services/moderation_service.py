import json
import logging
import re
from typing import Optional, List, Dict, Any, Tuple

from companion.config import settings

logger = logging.getLogger(__name__)


class ModerationService:
    """
    Context-aware moderation and toxicity analysis for male interlocutors.
    Distinguishes targeted abuse/insults from casual profanity, expressive slang,
    humor, sarcasm, and playful banter.
    """

    # Strong direct abuse patterns directed at the interlocutor
    DIRECT_ABUSE_PATTERNS = [
        r"(?i)\b(ты\s+(?:конченная|ебанутая|дура|шлюха|мразь|тварь|тупая|сука|дебилка|уродка|овца))\b",
        r"(?i)\b(пош(?:ел|ла)\s+на\s*хуй|иди\s+на\s*хуй|пошла\s+в\s*пизду|пососи|отсоси|рот\s+закрой|завали\s+ебало)\b",
        r"(?i)\b(мразь|шлюха|пидорас|уебище|гандон|чмоня|хуесоска)\b",
        r"(?i)\b(отвали\s+нахуй|заебала\s+ты|пошла\s+ты\s+нах)\b",
    ]

    # Expressive colloquial interjections that are NOT targeted insults
    BENIGN_EXPRESSIONS = [
        r"(?i)\b(бля(?:ть)?|пиздец|охуеть|ебать|нифига|нихуя|капец|жесть|да\s+ну\s+нах)\b",
        r"(?i)\b(ахах|хах|лол|кек|шучу|прикол)\b",
    ]

    @classmethod
    def quick_heuristic_check(cls, text: str) -> Tuple[bool, str]:
        """
        Fast heuristic analysis if Gemini API is unavailable:
        - If direct abuse targeted at the person is found -> (True, explanation)
        - If only emotional/situational swearing or banter -> (False, "")
        """
        clean = text.strip()
        for pat in cls.DIRECT_ABUSE_PATTERNS:
            m = re.search(pat, clean)
            if m:
                return True, f"Использовано прямое грубое оскорбление: «{m.group(0)}»"

        return False, ""

    @classmethod
    async def analyze_incoming_content(
        cls,
        text: str,
        is_voice: bool = False,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[bool, str]:
        """
        Analyzes message content and conversational context:
        - Accurately identifies targeted abuse, threats, insults, and harassment.
        - Excludes benign slang, jokes, sarcasm, and casual expressive swearing.
        Returns: (is_insult: bool, reason_explanation: str)
        """
        if not text or len(text.strip()) < 2:
            return False, ""

        clean_text = text.strip()

        # Fast pre-check: if text has zero trigger words or letters, skip API call
        has_swear_trigger = any(
            w in clean_text.lower()
            for w in [
                "хуй", "пизд", "еб", "бля", "сук", "шлюх", "мраз", "твар", "нахуй",
                "кончен", "дур", "туп", "урод", "рот", "закрой", "пошл", "завал", "чмо"
            ]
        )
        if not has_swear_trigger:
            return False, ""

        # Use Gemini for deep contextual understanding if API key is present
        api_key = settings.GEMINI_API_KEY
        if api_key:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)

                recent_context = ""
                if chat_history:
                    last_turns = chat_history[-4:]
                    recent_context = "\n".join([f"{t.get('role', 'user')}: {t.get('text', '')}" for t in last_turns])

                prompt = (
                    "Ты — беспристрастный эксперт-модератор дейтинг-диалогов.\n"
                    "Определи, является ли входящее сообщение от парня девушке ЦЕЛЕНАПРАВЛЕННЫМ ОСКОРБЛЕНИЕМ, ГРУБОСТЬЮ, ХАМСТВОМ или АГРЕССИЕЙ.\n\n"
                    "ПРАВИЛА ОЦЕНКИ:\n"
                    "1. Обычный разговорный мат для экспрессии («бля, ну ты даешь», «пиздец устал на работе», «охуеть как красиво», «да ладно бля») — НЕ ЯВЛЯЕТСЯ НАРУШЕНИЕМ.\n"
                    "2. Шутки, ирония, сарказм, дружеские подколы («ну ты и жук», «ахах врешь ведь») — НЕ ЯВЛЯЕТСЯ НАРУШЕНИЕМ.\n"
                    "3. Прямые оскорбления девушки («ты дура», «пошла нахуй», «шлюха», «мразь», «закрой рот», «тупая овца»), грубые наезды, мат в ее адрес — ЭТО НАРУШЕНИЕ.\n\n"
                    f"Контекст диалога (если есть):\n{recent_context}\n\n"
                    f"Сообщение для анализа ({'голосовое' if is_voice else 'текстовое'}):\n"
                    f"«{clean_text}»\n\n"
                    "Ответь СТРОГО в формате валидного JSON (без лишних слов и markdown):\n"
                    "{\n"
                    '  "is_insult": true,\n'
                    '  "reason": "Краткое описание на русском (1 предложение, что именно произошло)"\n'
                    "}"
                )

                models = ["gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-2.5-flash"]
                for model in models:
                    try:
                        resp = await client.aio.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                temperature=0.1,
                                max_output_tokens=150,
                            ),
                        )
                        raw = (resp.text or "").strip()
                        raw = re.sub(r"^```json\s*", "", raw)
                        raw = re.sub(r"\s*```$", "", raw).strip()
                        data = json.loads(raw)
                        is_insult = bool(data.get("is_insult", False))
                        reason = str(data.get("reason", "Грубое высказывание в диалоге"))
                        return is_insult, reason
                    except Exception as mod_err:
                        logger.debug("Model %s moderation check error: %s", model, mod_err)
                        continue
            except Exception as e:
                logger.warning("Gemini moderation API error: %s (%s). Falling back to heuristic.", type(e).__name__, e)

        # Fallback to local heuristic
        return cls.quick_heuristic_check(clean_text)

    @classmethod
    def generate_topics_and_summary(
        cls,
        messages: List[Dict[str, Any]],
        default_summary: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Generates brief dialogue summary and key conversation topics.
        Returns: (summary, topics)
        """
        if not messages:
            return default_summary or "Диалог только начался", "знакомство"

        user_msgs = [m.get("text", "") for m in messages if m.get("sender_role") == "user" or m.get("role") == "user"]
        combined = " ".join(user_msgs).lower()

        topics = []
        if any(w in combined for w in ["работ", "сто", "машин", "сервис", "авто", "офис"]):
            topics.append("работа/авто")
        if any(w in combined for w in ["музык", "трек", "рок", "слуша"]):
            topics.append("музыка")
        if any(w in combined for w in ["город", "москв", "питер", "киев", "жил", "перее"]):
            topics.append("города/жизнь")
        if any(w in combined for w in ["кофе", "кафе", "чай", "гуля", "парк", "встрет"]):
            topics.append("встреча/прогулка")
        if any(w in combined for w in ["квест", "особняк"]):
            topics.append("квест")
        if any(w in combined for w in ["учеба", "дизайн", "универ"]):
            topics.append("учеба")

        topics_str = ", ".join(topics) if topics else "знакомство, общение"

        last_user = user_msgs[-1] if user_msgs else ""
        if len(user_msgs) <= 2:
            summary = "Первые сообщения, первичное знакомство"
        elif "квест" in combined:
            summary = "Обсуждение планов на вечер и предложения квеста"
        elif any(w in combined for w in ["встрет", "увид", "кофе"]):
            summary = "Собеседник проявляет активный интерес и предлагает увидеться"
        else:
            summary = f"Активная беседа. Последнее от клиента: «{last_user[:60]}...»" if last_user else "Обсуждение повседневных тем"

        return summary, topics_str
