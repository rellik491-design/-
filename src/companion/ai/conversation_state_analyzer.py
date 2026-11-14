import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


@dataclass
class ConversationAnalysis:
    """Detailed contextual, emotional, and behavioral analysis of the current turn."""
    mood: str = "neutral"
    irony_or_sarcasm_detected: bool = False
    humor_detected: bool = False
    humor_intensity: Optional[str] = None  # None | "mild" | "moderate" | "strong"
    laughter_appropriate: bool = False
    recommended_laughter: Optional[str] = None  # None | "хах" | "ахах" | "ахахах" | "ХАХА"
    emoji_required: bool = False
    recommended_emoji: Optional[str] = None
    slang_terms_detected: List[str] = field(default_factory=list)
    is_calling_out_bot: bool = False
    is_serious_or_sensitive: bool = False
    recommended_response_length: str = "short"  # "very_short" | "short" | "medium" | "long"
    suggested_reaction: Optional[str] = None  # Native Telegram reaction emoji e.g. "😂", "👍", "🔥"
    guidance_notes: str = ""


class ConversationStateAnalyzer:
    """
    Production analyzer that deeply understands:
    1. Overall situation of the continuous conversation trajectory.
    2. Mood, irony, sarcasm, Russian slang, and true meaning behind messages.
    3. Strict contextual laughter gating (favors NO LAUGHTER; never laughs at serious/questions/call-outs).
    4. Strict contextual emoji policy (default emoji_required = False; max 1 emoji).
    5. Native Telegram reaction selection.
    """

    # In-memory history of turns since last laughter per contact
    _turns_since_last_bot_laugh: Dict[int, int] = {}

    # Comprehensive Russian slang & idiom dictionary with meaning and emotional cues
    SLANG_DICTIONARY: Dict[str, Dict[str, Any]] = {
        r"\bзуб\s+на\s+зуб\s+не\s+попада(ет|ют)\b": {
            "meaning": "трясется от сильного холода или нервов; подкол за постоянный нервный смех или дрожь",
            "is_call_out": True,
            "forbid_laughter": True,
            "guidance": "Собеседник подкалывает тебя за постоянный смех или дрожь («зуб на зуб не попадает»). НЕ СМЕЙСЯ! Ответь с легким смущением и живой самоиронией: отопление плохо греет или просто чай горячий пошла пить.",
        },
        r"\b(монстр|чудовище)\b": {
            "meaning": "удивление или подкол по поводу странного/пугающего поведения, звука или вида",
            "is_call_out": True,
            "forbid_laughter": True,
            "guidance": "Собеседника удивила твоя реакция или странный тон («что за монстр»). НЕ СМЕЙСЯ. Отшутись мягко по-человечески: «эй, какой еще монстр)) я милая и домашняя».",
        },
        r"\bвпадло\b": {
            "meaning": "лень, неохота, тяжело заставить себя что-то сделать",
            "mood": "tired",
            "guidance": "Собеседнику лень («впадло»). Раздели это настроение, не пытайся его гиперактивно веселить.",
        },
        r"\b(голова|красава|красавчик)\b": {
            "meaning": "уважение, похвала за сообразительность или верный поступок",
            "mood": "playful",
            "guidance": "Собеседник выражает одобрение («голова»/«красавчик»). Прими похвалу тепло и кокетливо.",
        },
        r"\b(орнул|ору|ржу|угар|угарно)\b": {
            "meaning": "очень сильно рассмеялся, нашел ситуацию смешной",
            "humor": True,
            "humor_intensity": "moderate",
        },
        r"\b(забей|проехали)\b": {
            "meaning": "не обращай внимания, отпусти тему, это не важно",
            "mood": "neutral",
            "guidance": "Собеседник просит не зацикливаться («забей»). Спокойно смени фокус или прими без навязчивых вопросов.",
        },
        r"\bжиза\b": {
            "meaning": "жизненная ситуация, знакомо до боли",
            "mood": "neutral",
            "guidance": "Собеседник узнал себя в ситуации («жиза»). Поддержи ощущение общности.",
        },
        r"\bкринж\b": {
            "meaning": "испанский стыд, неловкая или дурацкая ситуация",
            "mood": "playful",
            "guidance": "Собеседник считает что-то кринжем. Отвечай с понимающей легкой иронией.",
        },
        r"\bдушн(ить|ила|о)\b": {
            "meaning": "занудствовать, зануда, занудный разговор",
            "mood": "playful",
            "guidance": "Подкол про душноту. Сбрось важность, поддразни в ответ.",
        },
        r"\bшаришь\b": {
            "meaning": "разбираешься в теме, понимаешь суть",
            "mood": "playful",
        },
        r"\bне\s+ссы\b": {
            "meaning": "не бойся, расслабься, всё под контролем",
            "mood": "playful",
            "guidance": "Собеседник успокаивает на грубоватом сленге («не ссы»). Реагируй спокойно и с легкой улыбкой: «да я спокойна, ты чего))».",
        },
        r"\bмж\b": {
            "meaning": "мирный житель (из игры Мафия) либо сокращение",
            "mood": "playful",
            "guidance": "МЖ = мирный житель (отсылка к Мафии). Не путай с аббревиатурами организаций.",
        },
        r"\bна\s+чилле\b": {
            "meaning": "на расслабоне, отдыхает",
            "mood": "neutral",
        },
    }

    # Calling out bot / uncanny behavior triggers
    CALL_OUT_PATTERNS = [
        r"\b(ты\s+бот|ты\s+ии|ты\s+робот|ты\s+реальн(ая|ый)|докажи\s+что\s+ты|нейросет)\b",
        r"\b(хватит\s+(ржать|смеяться|ахахать)|чего\s+ты\s+ржешь|почему\s+столько\s+смеха|постоянно\s+ахаха|зуб\s+на\s+зуб)\b",
        r"\b(что\s+за\s+монстр|ты\s+странн(ая|ый)|кто\s+там\s+пишет|что\s+с\s+тобой)\b",
    ]

    # Serious / emotional distress triggers
    SERIOUS_PATTERNS = [
        r"\b(умер|погиб|авария|больниц|болею|тяжело|депресс|плачу|слезы|трагедия|похороны)\b",
        r"\b(устал|выгорел|нет\s+сил|проблем|уволил|долги|кризис|жесть\s+на\s+работе)\b",
        r"\b(серьезн(о|ый)|без\s+шуток|серьезный\s+вопрос)\b",
    ]

    @classmethod
    def record_bot_outgoing(cls, contact_id: int, has_laughter: bool) -> None:
        """Updates turning history for laughter cooldown."""
        if has_laughter:
            cls._turns_since_last_bot_laugh[contact_id] = 0
        else:
            current = cls._turns_since_last_bot_laugh.get(contact_id, 10)
            cls._turns_since_last_bot_laugh[contact_id] = current + 1

    @classmethod
    def analyze_dialogue_state(
        cls,
        incoming_text: str,
        chat_history: Optional[List[Dict[str, Any]]] = None,
        contact_id: int = 0,
        contact_preferences: Optional[Dict[str, Any]] = None,
    ) -> ConversationAnalysis:
        """
        Runs comprehensive deep situational analysis on the conversation state.
        Produces structured guidelines ensuring zero laughter overuse, natural emotions,
        and deep context perception.
        """
        analysis = ConversationAnalysis()
        text = (incoming_text or "").strip()
        low_text = text.lower()

        # 1. Slang & Idiom detection
        slang_matches = []
        guidance_items = []
        for pattern, info in cls.SLANG_DICTIONARY.items():
            if re.search(pattern, low_text, re.IGNORECASE):
                slang_matches.append(info.get("meaning", pattern))
                if info.get("is_call_out"):
                    analysis.is_calling_out_bot = True
                if info.get("forbid_laughter"):
                    analysis.laughter_appropriate = False
                if info.get("guidance"):
                    guidance_items.append(info["guidance"])
                if info.get("humor"):
                    analysis.humor_detected = True
                    analysis.humor_intensity = info.get("humor_intensity", "mild")
                if info.get("mood"):
                    analysis.mood = info["mood"]

        analysis.slang_terms_detected = slang_matches

        # 2. Call-out & Uncanny behavior detection
        for pat in cls.CALL_OUT_PATTERNS:
            if re.search(pat, low_text, re.IGNORECASE):
                analysis.is_calling_out_bot = True
                analysis.mood = "mocking_or_suspicious"
                guidance_items.append(
                    "ВНИМАНИЕ: Собеседник обратил внимание на неестественность или частый смех. "
                    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать «ахаха»/«хаха»! "
                    "Ответь просто, спокойно, с живой человеческой самоиронией и прямотой."
                )
                break

        # 3. Serious / Sensitive context detection
        for pat in cls.SERIOUS_PATTERNS:
            if re.search(pat, low_text, re.IGNORECASE):
                analysis.is_serious_or_sensitive = True
                analysis.mood = "serious"
                guidance_items.append(
                    "СИТУАЦИЯ СЕРЬЕЗНАЯ: собеседник говорит о проблемах, усталости или грустном. "
                    "НИКАКОГО СМЕХА И НИКАКИХ СКОБОК! Прояви теплое, взрослое сочувствие."
                )
                break

        # 4. Irony and Sarcasm detection
        # e.g. "Спасибо, но я не могу сказать тоже аналогичное", "ну конечно", "очень смешно"
        if (
            re.search(r"\b(ну\s+конечно|ага\s+конечно|очень\s+верю|спасибо,\s*но\s*я\s*не\s*могу)\b", low_text)
            or (("спасибо" in low_text or "верю" in low_text) and ("не могу" in low_text or "но" in low_text))
            or re.search(r"[!?.]{2,}", text) and ("ну" in low_text or "да уж" in low_text)
        ):
            analysis.irony_or_sarcasm_detected = True
            if analysis.mood == "neutral":
                analysis.mood = "playful"
            guidance_items.append("Собеседник иронизирует/подкалывает. Понимай иронию, не принимай слова буквально.")

        # 5. Question detection: Questions rarely warrant starting with laughter
        is_question = "?" in text

        # 6. Humor detection
        caps_humor = bool(re.search(r"[А-ЯA-Z]{4,}", text)) and any(e in text for e in ("😂", "🤣", "ХА", "ОР"))
        emoji_humor = any(e in text for e in ("😂", "🤣", "😆"))
        word_humor = any(w in low_text for w in ("ахах", "хаха", "ору", "ржу", "угар", "смешн", "прикол", "мем"))

        if caps_humor:
            analysis.humor_detected = True
            analysis.humor_intensity = "strong"
        elif emoji_humor or word_humor:
            analysis.humor_detected = True
            analysis.humor_intensity = "moderate"
        elif any(w in low_text for w in ("подколол", "пошутил", "дразн", "забавн")):
            analysis.humor_detected = True
            analysis.humor_intensity = "mild"

        # 7. STRICT LAUGHTER POLICY (Requirements 2 & 3)
        # Default is ALWAYS False!
        turns_since_laugh = cls._turns_since_last_bot_laugh.get(contact_id, 10)
        
        # Absolute bans on laughter:
        if (
            analysis.is_serious_or_sensitive
            or analysis.is_calling_out_bot
            or is_question
            or analysis.mood in ("serious", "sad", "annoyed", "tired", "mocking_or_suspicious")
        ):
            analysis.laughter_appropriate = False
            analysis.recommended_laughter = None
        elif turns_since_laugh < 3 and not caps_humor:
            # Cooldown: bot must NOT laugh multiple turns in a row!
            analysis.laughter_appropriate = False
            analysis.recommended_laughter = None
        elif analysis.humor_detected:
            # Laughter permitted only on genuine humor with cooldown passed
            analysis.laughter_appropriate = True
            if analysis.humor_intensity == "strong":
                analysis.recommended_laughter = "АХАХ"
            elif analysis.humor_intensity == "moderate":
                analysis.recommended_laughter = "хах"
            else:
                analysis.recommended_laughter = None  # Even mild humor often needs NO laughter
        else:
            analysis.laughter_appropriate = False
            analysis.recommended_laughter = None

        # 8. EMOJI POLICY (Requirement 4)
        # Default: emoji_required = False, maximum 1 emoji
        fewer_emoji_pref = contact_preferences.get("emoji_frequency") == "fewer" if contact_preferences else False
        if fewer_emoji_pref or analysis.is_serious_or_sensitive or analysis.is_calling_out_bot:
            analysis.emoji_required = False
            analysis.recommended_emoji = None
        elif analysis.humor_detected and analysis.humor_intensity == "strong":
            analysis.emoji_required = True
            analysis.recommended_emoji = "😂"
        elif analysis.mood == "flirty":
            analysis.emoji_required = True
            analysis.recommended_emoji = "😉"
        elif analysis.mood == "playful" and turns_since_laugh >= 2:
            analysis.emoji_required = False  # Keep sparse
            analysis.recommended_emoji = "😌"
        else:
            analysis.emoji_required = False
            analysis.recommended_emoji = None

        # 9. NATIVE TELEGRAM REACTION SELECTION (Requirement 34)
        # Very sparse (~10-15%), only when high emotional resonance exists
        if analysis.is_calling_out_bot or analysis.is_serious_or_sensitive:
            analysis.suggested_reaction = None
        elif caps_humor or (emoji_humor and "😂" in text):
            analysis.suggested_reaction = "😂"
        elif any(w in low_text for w in ("люблю", "обожаю", "красивая", "милая", "скучаю")):
            analysis.suggested_reaction = "❤️"
        elif any(w in low_text for w in ("огонь", "круто", "пушка", "мощно")):
            analysis.suggested_reaction = "🔥"
        elif any(w in low_text for w in ("договорились", "заметано", "по рукам", "ок")):
            analysis.suggested_reaction = "👍"
        elif any(w in low_text for w in ("в шоке", "офигеть", "серьезно?", "не верю")):
            analysis.suggested_reaction = "😮"

        # 10. RESPONSE LENGTH PLANNING (Requirement 3)
        msg_len = len(text)
        if analysis.is_calling_out_bot:
            analysis.recommended_response_length = "short"  # Direct, grounded reply
        elif is_question and msg_len < 30:
            analysis.recommended_response_length = "short"
        elif msg_len > 150:
            analysis.recommended_response_length = "medium"
        else:
            analysis.recommended_response_length = "short"

        analysis.guidance_notes = " ".join(guidance_items)
        return analysis

    @classmethod
    def sanitize_bot_reply(cls, reply_text: str, analysis: ConversationAnalysis) -> str:
        """
        Post-generation safeguard:
        1. If laughter is not appropriate, completely strips any unprompted leading or standalone 'ахаха'/'АХАХ'.
        2. Limits emojis to maximum 1.
        """
        if not reply_text:
            return reply_text

        cleaned = reply_text

        # 1. Strip unapproved laughter
        if not analysis.laughter_appropriate:
            # Remove leading laughter like "АХАХАХ", "ахаха", "хаха," etc. at the start of sentences
            cleaned = re.sub(
                r"^\s*([п]?[ах]{3,12}|хаха|ахах)[,!?.\s—–-]*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            # Remove laughter inside bubbles around "||"
            cleaned = re.sub(
                r"\|\|\s*([п]?[ах]{3,12}|хаха|ахах)[,!?.\s—–-]*",
                "||",
                cleaned,
                flags=re.IGNORECASE,
            )

        # 2. Limit emojis to at most 1 across the entire response
        emoji_pattern = re.compile(r"[\U00010000-\U0010ffff\u2600-\u27ff\u2300-\u23ff\u2b50\u200d\ufe0f]")
        emojis_found = emoji_pattern.findall(cleaned)
        if len(emojis_found) > 1:
            first_emoji = emojis_found[0]
            # Replace all occurrences with empty string, then append first_emoji once at the end
            text_no_emojis = emoji_pattern.sub("", cleaned).strip()
            # If the user specifically wanted an emoji or the model had one, keep the first one
            cleaned = f"{text_no_emojis} {first_emoji}".strip()

        return cleaned
