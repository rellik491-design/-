import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from companion.services.typing_simulator import TypingSimulator
from companion.bot.reply_validator import ReplyValidator

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. CURATED STICKER LIBRARY (CATEGORIZED BY INTENT)
# ==============================================================================

STICKER_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "laugh": {
        "description": "Funny reaction / laughing out loud",
        "pack_names": ["UtyaDuck", "HotCherry", "Animals"],
        "emoji_matches": ["😂", "🤣", "😆"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK2lnJ8_laugh_curated_01",
            "CAACAgIAAxkBAAEBK2pnJ8_laugh_curated_02",
        ],
    },
    "surprise": {
        "description": "Shock / unexpected news / disbelief",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["😱", "😳", "😮", "😭"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK2tnJ8_surprise_curated_01",
        ],
    },
    "confusion": {
        "description": "Puzzled / questioning / suspicious",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["🤔", "🤨", "👀"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK2xnJ8_confusion_curated_01",
        ],
    },
    "facepalm": {
        "description": "Ridiculous statement / playful exasperation",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["🤦‍♀️", "🙄", "😅"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK21nJ8_facepalm_curated_01",
        ],
    },
    "cute": {
        "description": "Warm / shy / affectionate reaction",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["🥰", "😊", "🙈", "❤️"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK25nJ8_cute_curated_01",
        ],
    },
    "celebration": {
        "description": "Excitement / agreement / yay",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["🎉", "🥳", "✨"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK29nJ8_celebration_curated_01",
        ],
    },
    "teasing": {
        "description": "Playful / coy / smirking reaction",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["😏", "😉", "🙃"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK3BnJ8_teasing_curated_01",
        ],
    },
    "approval": {
        "description": "Thumbs up / deal / respect",
        "pack_names": ["UtyaDuck", "HotCherry"],
        "emoji_matches": ["👍", "👌", "😌"],
        "fallback_file_ids": [
            "CAACAgIAAxkBAAEBK3FnJ8_approval_curated_01",
        ],
    },
}


# ==============================================================================
# 2. NATURAL VARIABLE LAUGHTER GENERATOR
# ==============================================================================


class LaughterGenerator:
    """
    Generates and normalizes Russian and English conversational laughter with
    contextual intensity and a minimum length of 5+ characters for Russian laughs.
    Prevents repetitive 'ахах' spam.
    """

    LAUGH_POOLS_RU: Dict[str, List[str]] = {
        "casual": [
            "ахаха",
            "хахах",
            "ахахах",
            "пхахах",
            "хахаха",
        ],
        "medium": [
            "ахахах",
            "ахахаха",
            "ахахахах",
            "хахахах",
            "пхахахах",
        ],
        "strong": [
            "АХАХАХ",
            "АХАХАХА",
            "АХАХАХАХ",
            "АХАХАХАХА",
            "ахахахахах",
        ],
    }

    LAUGH_POOLS_EN: Dict[str, List[str]] = {
        "casual": ["hahah", "ahaha", "lmao"],
        "medium": ["hahaha", "ahahaha", "lmaoo"],
        "strong": ["AHAHAHA", "HAHAHA", "LMAOOO"],
    }

    _recent_laughs_by_contact: Dict[int, List[str]] = {}

    @classmethod
    def detect_humor_intensity(cls, incoming_text: str, reply_text: str = "") -> Optional[str]:
        """
        Determines whether laughter is appropriate and at what intensity:
        None | 'casual' | 'medium' | 'strong'.
        """
        combined = f"{incoming_text or ''} {reply_text or ''}"
        low = combined.lower()

        # Strong triggers: caps laughter, multiple 😂/😭/🤣, or 'жесть'/'ор'/'угар'/'капец смешно'
        if (
            re.search(r"[А-ЯA-Z]{5,}", incoming_text or "")
            or any(e in combined for e in ("😂😂", "🤣", "😭"))
            or any(w in low for w in ("вообще жесть", "ахахах", "угар", "ору", "ржу", "анекдот", "мем"))
        ):
            return "strong"

        # Medium triggers: clear joke, funny situation, 😂
        if any(e in combined for e in ("😂", "😅", "😆")) or any(
            w in low for w in ("смешн", "шутк", "прикол", "забавн", "хаха", "ахах", "пхах", "funny", "lol", "lmao")
        ):
            return "medium"

        # Mild / casual triggers
        if any(w in low for w in ("улыбн", "ирони", "подкол", "дразн", "аха", "хах")):
            return "casual"

        return None

    @classmethod
    def pick_laugh(
        cls,
        intensity: str = "casual",
        contact_id: Optional[int] = None,
        lang: str = "ru",
    ) -> str:
        pools = cls.LAUGH_POOLS_EN if lang == "en" else cls.LAUGH_POOLS_RU
        pool = pools.get(intensity, pools["casual"])
        key = contact_id or 0
        history = cls._recent_laughs_by_contact.get(key, [])

        candidates = [item for item in pool if item not in history[-2:]]
        if not candidates:
            candidates = pool

        chosen = random.choice(candidates)
        history.append(chosen)
        cls._recent_laughs_by_contact[key] = history[-5:]
        return chosen

    @classmethod
    def generate_laugh(
        cls,
        incoming_text: str = "",
        reply_text: str = "",
        contact_id: Optional[int] = None,
        lang: str = "ru",
    ) -> str:
        intensity = cls.detect_humor_intensity(incoming_text, reply_text) or "casual"
        return cls.pick_laugh(intensity=intensity, contact_id=contact_id, lang=lang)

    @classmethod
    def naturalize_laughter_in_text(
        cls,
        text: str,
        incoming_text: str = "",
        contact_id: Optional[int] = None,
    ) -> str:
        """
        Replaces short/repetitive laughs ('ах', 'аха', 'ахах', 'хах') in generated text
        with natural, context-weighted variants of at least 5 characters.
        """
        if not text:
            return text

        intensity = cls.detect_humor_intensity(incoming_text, text) or "casual"

        # Match standalone Russian laugh tokens of 2 to 12 chars (e.g. 'аха', 'ахах', 'хах', 'пхах', 'ахахах')
        laugh_pattern = re.compile(
            r"(?<![а-яёa-z])([п]?[ах]{3,12})(?![а-яёa-z])",
            re.IGNORECASE,
        )

        def _replace_laugh(match: re.Match) -> str:
            token = match.group(1)
            tok_low = token.lower()
            # Verify it actually looks like laughter (contains both 'а' and 'х')
            if "а" not in tok_low or "х" not in tok_low:
                return token
            # Avoid touching words like 'страх', 'крах', 'мах', 'шах', 'пах'
            if tok_low in ("мах", "шах", "пах", "бах", "тах", "вах", "ага"):
                return token

            is_upper = token.isupper() and len(token) >= 4
            eff_intensity = "strong" if is_upper else intensity
            replacement = cls.pick_laugh(
                intensity=eff_intensity,
                contact_id=contact_id,
                lang="ru",
            )
            # Ensure minimum 5 characters
            if len(replacement) < 5:
                replacement = "ахаха"
            return replacement

        return laugh_pattern.sub(_replace_laugh, text)


# ==============================================================================
# 3. PARENTHESES & FLIRT CONTROLLER
# ==============================================================================


class ParenthesesController:
    """
    Controls closing parentheses `)` so they are NEVER used as a default sentence ending.
    Parentheses are stripped in normal/factual conversation and allowed only occasionally
    in playful, coy, or flirty contexts.
    """

    FLIRT_USER_PATTERNS = re.compile(
        r"\b("
        r"дразн\w*|специально|флирт\w*|кокет\w*|красив\w*|милая|симпот\w*|сексуальн\w*|"
        r"нравишься|влюбил\w*|скуча\w*|поцел\w*|обним\w*|шалун\w*|хитрая|загадочн\w*|"
        r"спорим|угадай|секрет\w*|интриг\w*|соблазн\w*|пристава\w*|подкат\w*|"
        r"teas\w*|flirt\w*|cute|hot|pretty|miss you|kiss"
        r")\b",
        re.IGNORECASE,
    )

    COY_BOT_PATTERNS = re.compile(
        r"\b("
        r"может быть|не скажу|секрет|посмотрим|размечтался|ну конечно|ты специально|"
        r"кто знает|угадай|попробуй|смотря как|не все сразу|а ты угадай|хитрый|"
        r"наглый|быстрый конечно|так сразу|засмущал|перестань смущать|"
        r"maybe|who knows|we'll see|secret"
        r")\b",
        re.IGNORECASE,
    )

    FACTUAL_OR_NEUTRAL_PATTERNS = re.compile(
        r"^(?:"
        r"да,?\s+я\s+\w+|нет,?\s+я\s+\w+|договорились|хорошо|ладно|поняла|понятно|"
        r"окей|ок|да,?\s+это\s+уже\s+готово|уже\s+сделала|я\s+дома|я\s+поела|и\s+да,?\s+ела"
        r")[\)\s\.]*$",
        re.IGNORECASE,
    )

    @classmethod
    def is_flirty_or_playful(
        cls,
        incoming_text: str,
        bot_text: str,
        style: str = "adaptive_all",
    ) -> bool:
        inc = (incoming_text or "").strip()
        rep = (bot_text or "").strip()

        if cls.FACTUAL_OR_NEUTRAL_PATTERNS.match(rep):
            return False

        if cls.COY_BOT_PATTERNS.search(rep):
            return True

        if cls.FLIRT_USER_PATTERNS.search(inc):
            return True

        if any(e in inc for e in ("😏", "😉", "😘", "😍", "🔥", ";)")):
            return True

        if style == "playful" and any(w in rep.lower() for w in ("ну ", "а ты", "думаешь", "посмотрим")):
            return True

        return False

    @classmethod
    def apply_parentheses_policy(
        cls,
        bubble_text: str,
        incoming_text: str = "",
        style: str = "adaptive_all",
        no_brackets: bool = False,
        allow_flirt_paren: bool = True,
        tone: Optional[str] = None,
        rng: Optional[Any] = None,
    ) -> str:
        """
        Enforces rare, intentional parentheses:
        - If `no_brackets` is True: strips all `)` and `(`.
        - If factual/neutral: strips all trailing `)`.
        - If normal conversation (not flirty/playful): strips trailing `)` so normal messages
          do not end with `)`.
        - If flirty/playful/coy: allows a single `)` intentionally (not `))` or `)))` spam).
        """
        if not bubble_text:
            return bubble_text

        if no_brackets:
            return re.sub(r"[\)\(]+", "", bubble_text).strip()

        eff_style = tone or style
        rand = rng if rng is not None else random

        # Remove internal `)` mid-sentence (leaving at most the end of the bubble)
        parts = re.split(r"\)+", bubble_text)
        had_trailing_paren = bubble_text.rstrip().endswith(")")
        if len(parts) > 1:
            clean_chunks = []
            for p in parts:
                p_str = p.strip()
                if p_str:
                    clean_chunks.append(p_str)
            core_text = ", ".join(clean_chunks) if len(clean_chunks) > 1 else (clean_chunks[0] if clean_chunks else "")
            # Fix double punctuation if any
            core_text = re.sub(r"([?!.,])\s*,", r"\1", core_text)
        else:
            core_text = bubble_text.strip()

        core_text = core_text.rstrip(")").strip()

        # Never put `)` if there is already an emoji or `?` or `!` at the end
        if not core_text or core_text.endswith(("?", "!")):
            return core_text
        if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]$", core_text):
            return core_text

        is_playful = cls.is_flirty_or_playful(incoming_text, core_text, style=eff_style)

        if is_playful and allow_flirt_paren:
            # If it's a classic short coy phrase ("может быть", "не скажу", "ну конечно") or LLM had `)`
            if cls.COY_BOT_PATTERNS.search(core_text):
                if had_trailing_paren or rand.random() < 0.65:
                    return f"{core_text})"
            elif had_trailing_paren and rand.random() < 0.45:
                return f"{core_text})"

        return core_text


# ==============================================================================
# 4. EMOJI & STICKER SELECTOR
# ==============================================================================


class EmojiAndStickerSelector:
    """
    Manages natural Unicode emoji frequency (many messages have 0 emojis; never every message)
    and occasional curated Telegram sticker selection.
    """

    UNICODE_EMOJI_RE = re.compile(
        r"[\U0001F300-\U0001FAFF\u2600-\u27BF\U0001F1E6-\U0001F1FF]+"
    )

    CONTEXT_EMOJIS: List[Tuple[re.Pattern, List[str]]] = [
        (re.compile(r"\b(ахах\w*|хаха\w*|смешн\w*|ору|ржу|жесть)\b", re.I), ["😂", "😭"]),
        (re.compile(r"\b(подожди|чего|серьезно|в смысле|откуда|wait what)\b", re.I), ["😭", "🤨", "👀"]),
        (re.compile(r"\b(специально|дразнишь|посмотрим|хитрый|наглый|может быть)\b", re.I), ["😏", "👀", "🙃"]),
        (re.compile(r"\b(спасибо|приятно|обнимаю|конечно|мило|доброй ночи|отдыхай)\b", re.I), ["❤️", "😌", "🙂"]),
        (re.compile(r"\b(ела|поела|кофе|чай|отдыхаю|валяюсь|дома)\b", re.I), ["😌", "🙂"]),
    ]

    _last_sticker_turn_by_contact: Dict[int, int] = {}
    _turn_counter_by_contact: Dict[int, int] = {}
    _last_sticker_turn = _last_sticker_turn_by_contact
    _turn_counter = _turn_counter_by_contact
    _resolved_pack_stickers: Dict[str, List[str]] = {}

    @classmethod
    def has_emoji(cls, text: str) -> bool:
        return bool(cls.UNICODE_EMOJI_RE.search(text or ""))

    @classmethod
    def detect_sticker_category(cls, incoming_text: str, reply_text: str) -> Optional[str]:
        inc = (incoming_text or "").lower()
        rep = (reply_text or "").lower()
        combined = f"{inc} {rep}"

        if any(w in combined for w in ("ахахаха", "ору", "угар", "смешно", "анекдот", "мем", "😂😂", "lmao")):
            return "laugh"
        if any(w in combined for w in ("офигеть", "не может быть", "жесть", "шок", "серьезно?!", "no way")):
            return "surprise"
        if any(w in combined for w in ("треш", "капец", "кринж", "фейспалм", "ну ты даешь", "бред")):
            return "facepalm"
        if any(w in combined for w in ("мило", "обожаю", "обнимаю", "красотка", "солнце", "приятно")):
            return "cute"
        if any(w in combined for w in ("ура", "супер", "договорились", "погнали", "отлично")):
            return "celebration"
        if any(w in combined for w in ("дразнишь", "специально", "ну-ну", "посмотрим", "размечтался")):
            return "teasing"
        if any(w in combined for w in ("в смысле", "не поняла", "подозрительно", "странно")):
            return "confusion"
        return None

    @classmethod
    def should_attach_sticker(
        cls,
        incoming_text: str = "",
        reply_text: str = "",
        contact_id: Optional[int] = None,
        explicit_category: Optional[str] = None,
        probability: float = 0.12,
        rng: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Determines whether to send an occasional sticker and picks one from the curated library.
        Enforces a minimum cooldown of 5 turns per contact so stickers never feel constant.
        """
        cid = contact_id or 0
        turn_idx = cls._turn_counter_by_contact.get(cid, 0) + 1
        cls._turn_counter_by_contact[cid] = turn_idx

        last_sticker_turn = cls._last_sticker_turn_by_contact.get(cid, -10)
        if not explicit_category and (turn_idx - last_sticker_turn) < 5:
            return None

        category = explicit_category if explicit_category in STICKER_CATEGORIES else cls.detect_sticker_category(incoming_text, reply_text)
        if not category:
            return None

        rand = rng if rng is not None else random
        if not explicit_category and rand.random() > probability:
            return None

        cls._last_sticker_turn_by_contact[cid] = turn_idx
        cat_info = STICKER_CATEGORIES[category]
        cached_ids = cls._resolved_pack_stickers.get(category) or cat_info["fallback_file_ids"]
        file_id = random.choice(cached_ids)
        return {
            "category": category,
            "file_id": file_id,
            "emoji": random.choice(cat_info["emoji_matches"]),
        }

    @classmethod
    def balance_emojis_across_bubbles(
        cls,
        bubbles: List[str],
        incoming_text: str = "",
        no_brackets: bool = False,
    ) -> List[str]:
        """
        Ensures natural emoji frequency:
        - Many messages have 0 emojis.
        - In a multi-bubble response, at most 1 bubble contains an emoji (unless the user sent multiple emojis).
        - Replaces or strips excess emojis so we never produce `sentence + emoji` on every bubble.
        """
        if not bubbles:
            return bubbles

        result = list(bubbles)
        emoji_bubble_indices = [
            i for i, b in enumerate(result) if cls.UNICODE_EMOJI_RE.search(b)
        ]

        # If more than 1 bubble has emojis, strip emojis from all except the most expressive one (usually last or first reaction)
        if len(emoji_bubble_indices) > 1:
            keep_idx = emoji_bubble_indices[-1]
            for idx in emoji_bubble_indices:
                if idx != keep_idx:
                    result[idx] = cls.UNICODE_EMOJI_RE.sub("", result[idx]).strip()
                    result[idx] = re.sub(r"\s+", " ", result[idx]).strip()
        elif len(emoji_bubble_indices) == 1 and len(result) == 1:
            # Single bubble that already has an emoji: if it's a dry/factual statement, strip it;
            # otherwise keep it ~60% of the time so emojis don't appear in 100% of single messages.
            b_text = result[0]
            if ParenthesesController.FACTUAL_OR_NEUTRAL_PATTERNS.match(cls.UNICODE_EMOJI_RE.sub("", b_text).strip()):
                result[0] = cls.UNICODE_EMOJI_RE.sub("", b_text).strip()
            elif random.random() < 0.35:
                stripped = cls.UNICODE_EMOJI_RE.sub("", b_text).strip()
                if stripped:
                    result[0] = stripped
        elif len(emoji_bubble_indices) == 0 and not no_brackets:
            # No emoji in any bubble yet: check if the last bubble (or emotional reaction bubble) strongly calls for one (~28% chance)
            target_idx = len(result) - 1
            target_text = result[target_idx]
            if not ParenthesesController.FACTUAL_OR_NEUTRAL_PATTERNS.match(target_text):
                for pattern, emoji_choices in cls.CONTEXT_EMOJIS:
                    if pattern.search(target_text) or pattern.search(incoming_text or ""):
                        if random.random() < 0.30 and not target_text.endswith(")"):
                            chosen_emoji = random.choice(emoji_choices)
                            result[target_idx] = f"{target_text} {chosen_emoji}"
                        break

        return [b for b in result if b]


# ==============================================================================
# 4B. CONVERSATION RHYTHM & AGGRESSIVE BURST CONTROLLER (SECTIONS 23–28)
# ==============================================================================


class ConversationRhythmAnalyzer:
    """
    Analyzes incoming message batches to determine the conversation rhythm state:
    1. 'CALM': Normal response frequency and length.
    2. 'FAST_ACTIVE': Lively multi-message exchange (2–3 short natural bubbles).
    3. 'AGGRESSIVE_IMPATIENT': User is spamming impatient/urgent pings ('АЛЛО', 'ТЫ ГДЕ',
       '????', 'ПОЧЕМУ НЕ ОТВЕЧАЕШЬ', 'БЛЯ', 'ДАВАЙ БЫСТРЕЕ', etc.).
       Switches to controlled burst mode (short, immediate acknowledgement bubbles,
       strictly capped by MAX_BURST_MESSAGES, never abusive or infinite).
    """

    IMPATIENT_KEYWORDS = (
        "алло",
        "ало",
        "ты где",
        "где ты",
        "почему не отвечаешь",
        "почему молчишь",
        "че молчишь",
        "чо молчишь",
        "ты тут",
        "давай быстрее",
        "быстрее",
        "ответь",
        "отвечай",
        "ау",
        "ауу",
        "игноришь",
        "игнор",
        "слышь",
        "эй ты",
    )

    AGGRESSIVE_MARKERS = (
        "бля",
        "блять",
        "какого хера",
        "че за игнор",
        "ты офигела",
        "давай уже",
        "але",
        "алё",
    )

    BURST_ACK_SETS: List[List[str]] = [
        ["я тут", "вижу", "секунду"],
        ["да вижу 😭", "не игнорю тебя", "сейчас"],
        ["сек", "вижу", "сейчас отвечу"],
        ["я тут, тут", "не теряй", "секунду"],
    ]

    @classmethod
    def analyze_rhythm(
        cls,
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
        max_burst_messages: int = 4,
    ) -> Dict[str, Any]:
        batch = incoming_batch or []
        texts = [(m.get("text") or "").strip() for m in batch if (m.get("text") or "").strip()]
        combined_low = " ".join(texts).lower()

        impatient_hits = 0
        caps_msgs = 0
        question_spam = False

        for t in texts:
            t_low = t.lower().strip()
            if any(k in t_low for k in cls.IMPATIENT_KEYWORDS) or any(k in t_low for k in cls.AGGRESSIVE_MARKERS):
                impatient_hits += 1
            if t_low in ("давай", "ну", "эй", "ало", "алло", "ау"):
                impatient_hits += 1
            if re.search(r"\?{3,}|!{3,}", t):
                question_spam = True
                impatient_hits += 1
            alpha_chars = [c for c in t if c.isalpha()]
            if len(alpha_chars) >= 2 and all(c.isupper() for c in alpha_chars):
                caps_msgs += 1

        is_aggressive = (
            impatient_hits >= 2
            or (impatient_hits >= 1 and (caps_msgs >= 1 or question_spam or len(batch) >= 3))
            or (caps_msgs >= 2 and len(batch) >= 3)
        )

        if is_aggressive:
            return {
                "state": "AGGRESSIVE_IMPATIENT",
                "is_aggressive": True,
                "max_messages": max(1, min(max_burst_messages, 4)),
                "max_burst_messages": max(1, min(max_burst_messages, 4)),
                "inter_message_delay": (0.35, 0.75),
                "sticker_probability_multiplier": 0.0,
            }

        if len(batch) >= 3:
            return {
                "state": "FAST_ACTIVE",
                "is_aggressive": False,
                "max_messages": max(1, min(max_burst_messages, 4)),
                "max_burst_messages": max(1, min(max_burst_messages, 4)),
                "inter_message_delay": (0.45, 0.95),
                "sticker_probability_multiplier": 1.0,
            }

        return {
            "state": "CALM",
            "is_aggressive": False,
            "max_messages": max(1, min(max_burst_messages, 3)),
            "max_burst_messages": max(1, min(max_burst_messages, 4)),
            "inter_message_delay": (0.55, 1.15),
            "sticker_probability_multiplier": 1.0,
        }

    @classmethod
    def apply_controlled_burst(
        cls,
        bubbles: List[str],
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
        max_burst_messages: int = 4,
    ) -> List[str]:
        """
        Shapes outgoing messages when the user is aggressive/impatient:
        - Converts long paragraphs into short, immediate human burst bubbles
          (e.g. 'я тут' / 'вижу' / 'секунду' or 'да вижу 😭' / 'не игнорю тебя' / 'сейчас')
        - Preserves any concise substantive answer if the user also asked a real question
        - Enforces a hard upper bound (`max_burst_messages`) so the bot NEVER floods or loops.
        """
        cap = max(1, min(max_burst_messages, 4))
        batch = incoming_batch or []
        texts = [(m.get("text") or "").strip() for m in batch if (m.get("text") or "").strip()]

        # Check if the batch is purely impatient pings ("АЛЛО", "ТЫ ГДЕ", "????", "ДАВАЙ")
        # or if it also contains a substantive topic
        non_ping_msgs = []
        for t in texts:
            t_low = t.lower().strip("?!. ")
            if not t_low:
                continue
            if (
                t_low in ("алло", "ало", "ты где", "где ты", "давай", "давай быстрее", "ау", "эй", "ответь", "отвечай", "че молчишь", "почему не отвечаешь", "бля")
                or any(k == t_low for k in cls.IMPATIENT_KEYWORDS)
            ):
                continue
            non_ping_msgs.append(t)

        # If model already generated 2..cap short burst bubbles (each <= 45 chars), keep them capped
        if 2 <= len(bubbles) <= cap and all(len(b) <= 50 for b in bubbles):
            return bubbles[:cap]

        chosen_ack = list(random.choice(cls.BURST_ACK_SETS))
        if not non_ping_msgs:
            # Pure impatience burst -> respond with 2-3 rapid short human acknowledgements
            return chosen_ack[: min(3, cap)]

        # There is also a substantive message in the batch: prepend 2 quick burst acks + concise answer
        concise_answer = bubbles[-1] if bubbles else "сейчас гляну"
        if len(concise_answer) > 90:
            # Split or trim overly long paragraph when user is impatient
            parts = re.split(r"[.!?]\s+", concise_answer)
            if parts and parts[0]:
                concise_answer = parts[0].strip()
        burst_out = chosen_ack[:2] + [concise_answer]
        return burst_out[:cap]


# ==============================================================================
# 5. RESPONSE PLANNER (SEGMENTATION + SELECTIVE TELEGRAM REPLIES)
# ==============================================================================


class ResponsePlanner:
    """
    Plans how the bot's response should be structured and delivered in Telegram:
    - Natural multi-message segmentation (1, 2, 3+ messages at natural boundaries)
    - Selective native Telegram Reply (`reply_to_message_id`) when answering a specific
      earlier message or multi-topic batch
    - Controlled burst behavior when the user is impatient/aggressive
    - Graceful fallback when voice message transcription fails
    - Natural variable laughter
    - Rare/contextual parentheses
    - Balanced Unicode emojis and occasional curated stickers
    """

    VOICE_FAILED_MARKER = "[VOICE_TRANSCRIPTION_FAILED]"
    VOICE_FALLBACK_RESPONSES = [
        "не смогла разобрать голосовое 😭 напиши текстом плиз",
        "плохо слышно на голосовом 😭 что ты там сказал?",
        "не разобрала голосовое 😭 можешь написать?",
    ]

    # Conversational connectors where a long single message naturally splits into 2 Telegram bubbles
    NATURAL_SPLIT_PATTERNS = [
        re.compile(r"\s+(?=(?:и\s+кстати|а\s+кстати|кстати,|и\s+да,|а\s+вообще|хотя\s+если\s+честно|слушай,\s+а\s+))\b", re.I),
        re.compile(r"(?<=[?!])\s+(?=[а-яёa-z])", re.I),
        re.compile(r"\.\s+(?=(?:я\s+если\s+честно|я\s+вообще|но\s+я|а\s+ты|и\s+да|кстати|хотя|просто\s+))", re.I),
    ]

    TRIVIAL_OPENERS = {
        "эй", "слушай", "слышь", "привет", "хай", "ку", "короче", "кстати",
        "я тут подумал", "я сейчас подумал", "я сейчас понял", "я хотел спросить",
        "подожди", "стой", "не важно", "неважно", "ладно неважно", "ладно забей",
        "забей", "вспомнил", "да ладно", "ахаха", "ахахах", "хаха", "хахах",
        "алло", "ало", "ты где", "где ты", "давай", "ау",
        "hey", "listen", "wait", "actually", "never mind", "forget it",
        "i wanted to ask you something", "i wanted to ask something", "i remembered",
    }

    TOPIC_SYNONYM_GROUPS = [
        {"фотк", "фото", "картинк", "нравится", "красив", "выглядит", "кадр"},
        {"дизайн", "вариант", "интерфейс", "стиль", "видела", "смотрится", "лучше"},
        {"выставк", "гараж", "музей", "сходила", "экспозиц"},
        {"ела", "поела", "кушала", "голодн", "перекусила", "обед", "ужин", "завтрак", "салат", "кофе"},
        {"завтра", "свободн", "планы", "вечером", "выходн", "встрет", "центр", "будешь"},
        {"нашел", "нашёл", "штук", "потерял", "искал", "какую"},
        {"почему", "зачем", "вопрос", "сделали", "помню", "логично", "момент"},
        {"фильм", "кино", "сериал", "смотрела", "интерстеллар"},
        {"работ", "учеб", "универ", "дела", "занят", "устал"},
        {"голосов", "войс", "аудио", "слышно", "сказал"},
    ]

    @classmethod
    def format_batch_for_ai(cls, incoming_batch: List[Dict[str, Any]]) -> str:
        """
        Formats a chronological batch of incoming messages (text, voice, media)
        into a structured prompt turn for the AI conversation engine, preserving
        original Telegram message IDs, media types, and voice transcriptions.
        """
        if not incoming_batch:
            return ""

        if len(incoming_batch) == 1:
            item = incoming_batch[0]
            mid = item.get("telegram_message_id") if item.get("telegram_message_id") is not None else item.get("message_id")
            prefix = f"[msg_id={mid}] " if mid is not None else ""
            is_voice = bool(item.get("is_voice") or item.get("media_type") in ("voice", "video_note"))
            txt = (item.get("text") or "").strip()
            if is_voice:
                dur = int(item.get("voice_duration") or 0)
                dur_str = f", {dur}s" if dur > 0 else ""
                if not txt or txt == cls.VOICE_FAILED_MARKER or txt in ("[Голосовое сообщение]", "[Видео-сообщение (кружок)]"):
                    return f"{prefix}[VOICE MESSAGE{dur_str}] [TRANSCRIPTION_FAILED: Не удалось разобрать голосовое сообщение]"
                return f'{prefix}[VOICE MESSAGE{dur_str}] Transcription: "{txt}"'
            return f"{prefix}{txt}" if mid is not None else txt

        lines: List[str] = []
        for idx, item in enumerate(incoming_batch, 1):
            mid = item.get("telegram_message_id") if item.get("telegram_message_id") is not None else item.get("message_id")
            prefix = f"[msg_id={mid}]" if mid is not None else f"[{idx}]"
            rep_to = item.get("reply_to_msg_id")
            rep_tag = f" (in_reply_to={rep_to})" if rep_to else ""
            is_voice = bool(item.get("is_voice") or item.get("media_type") in ("voice", "video_note"))
            m_type = item.get("media_type") or ("voice" if is_voice else "text")
            txt = (item.get("text") or "").strip()

            if is_voice:
                dur = int(item.get("voice_duration") or 0)
                dur_str = f", {dur}s" if dur > 0 else ""
                if not txt or txt == cls.VOICE_FAILED_MARKER or txt in ("[Голосовое сообщение]", "[Видео-сообщение (кружок)]"):
                    lines.append(f"{prefix}{rep_tag} [VOICE MESSAGE{dur_str}] [TRANSCRIPTION_FAILED]")
                else:
                    lines.append(f'{prefix}{rep_tag} [VOICE MESSAGE{dur_str}] Transcription: "{txt}"')
            elif m_type in ("photo", "sticker", "animation"):
                lines.append(f"{prefix}{rep_tag} [{m_type.upper()}] {txt}")
            else:
                lines.append(f"{prefix}{rep_tag} {txt}")

        return "\n".join(lines)

    @classmethod
    def extract_inline_tags(cls, text: str) -> Tuple[str, Optional[int], Optional[str]]:
        """
        Extracts optional inline planner tags from a bubble:
        e.g. `[REPLY:103]` and `[STICKER:laugh]`.
        Returns (clean_text, explicit_reply_id, explicit_sticker_category).
        """
        reply_id: Optional[int] = None
        sticker_cat: Optional[str] = None

        m_rep = re.search(r"\[REPLY:\s*(\d+)\s*\]", text, re.IGNORECASE)
        if m_rep:
            reply_id = int(m_rep.group(1))
            text = text.replace(m_rep.group(0), "").strip()

        m_stk = re.search(r"\[STICKER:\s*([a-z_]+)\s*\]", text, re.IGNORECASE)
        if m_stk:
            cand = m_stk.group(1).lower().strip()
            if cand in STICKER_CATEGORIES:
                sticker_cat = cand
            text = text.replace(m_stk.group(0), "").strip()

        return text, reply_id, sticker_cat

    @classmethod
    def segment_response(
        cls,
        raw_text: str,
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Tuple[str, Optional[int], Optional[str]]]:
        """
        Segments a raw response into 1..3 (occasionally 4) natural Telegram messages.
        Never splits mid-phrase or into artificial 1-word fragments.
        """
        if not raw_text or not raw_text.strip():
            return []

        # 1. Check if LLM already provided `||` bubble separators
        if "||" in raw_text:
            raw_parts = [p.strip() for p in raw_text.split("||") if p.strip()]
        else:
            raw_parts = [raw_text.strip()]

        # 2. If there is only 1 part, decide contextually whether to split at a natural boundary
        if len(raw_parts) == 1:
            single = raw_parts[0]
            clean_single, rep_id, stk_cat = cls.extract_inline_tags(single)

            # Short factual/casual response (<= 55 chars) -> keep as 1 message
            if len(clean_single) <= 55 and "\n" not in clean_single:
                return [(clean_single, rep_id, stk_cat)]

            # If multi-line, split by newline first
            if "\n" in clean_single:
                lines = [ln.strip() for ln in clean_single.split("\n") if ln.strip()]
                if len(lines) > 1 and all(len(ln) >= 4 for ln in lines):
                    return [
                        (ln, rep_id if i == 0 else None, stk_cat if i == len(lines) - 1 else None)
                        for i, ln in enumerate(lines[:4])
                    ]

            # Try natural conversational boundary split if message has 2 distinct clauses/topics
            for pattern in cls.NATURAL_SPLIT_PATTERNS:
                chunks = [c.strip() for c in pattern.split(clean_single) if c.strip()]
                # Only accept the split if every chunk is a genuine phrase (>= 8 chars or >= 2 words)
                if 2 <= len(chunks) <= 3 and all(len(c) >= 8 or len(c.split()) >= 2 for c in chunks):
                    return [
                        (c.rstrip("."), rep_id if i == 0 else None, stk_cat if i == len(chunks) - 1 else None)
                        for i, c in enumerate(chunks)
                    ]

            # If no regex connector matched, apply empirical female rhythm burst splitting (56% single, 40% double)
            from companion.services.female_style_service import FemaleStyleLayer, FemaleEmotionalState
            combined_inc = " ".join((m.get("text") or "") for m in (incoming_batch or [])).strip()
            e_state = FemaleStyleLayer.detect_emotional_state(combined_inc)
            rhythm_split = FemaleStyleLayer._split_into_female_rhythm(
                text=clean_single,
                emotional_state=e_state,
                preferred_length="medium",
                prof=FemaleStyleLayer.DEFAULT_PROFILE
            )
            if len(rhythm_split) > 1:
                return [
                    (c, rep_id if i == 0 else None, stk_cat if i == len(rhythm_split) - 1 else None)
                    for i, c in enumerate(rhythm_split)
                ]

            return [(clean_single, rep_id, stk_cat)]

        # Multiple `||` parts provided
        parsed_parts: List[Tuple[str, Optional[int], Optional[str]]] = []
        for part in raw_parts[:4]:
            c_txt, r_id, s_cat = cls.extract_inline_tags(part)
            if c_txt:
                parsed_parts.append((c_txt, r_id, s_cat))
        return parsed_parts

    @classmethod
    def _is_question_or_substantive_topic(cls, msg_text: str) -> bool:
        clean = (msg_text or "").strip().lower().strip("?!., ")
        if not clean or clean in cls.TRIVIAL_OPENERS:
            return False
        if "?" in (msg_text or ""):
            return True
        question_markers = (
            "почему", "зачем", "как думаешь", "что думаешь", "ты видела", "видела",
            "ты ела", "поела", "где ты", "когда", "куда", "кто", "помнишь", "свободна",
            "нашел", "нашёл", "будешь",
            "what do you think", "did you", "have you", "where are", "why did", "how did",
        )
        if any(m in clean for m in question_markers):
            return True
        return len(clean.split()) >= 3

    @classmethod
    def _score_topic_match(cls, bubble_text: str, incoming_text: str) -> int:
        """
        Computes a topic-relevance score between an outgoing bubble and an incoming message
        using stem overlap (4+ char prefixes) and semantic topic groups.
        """
        b_low = (bubble_text or "").lower()
        i_low = (incoming_text or "").lower()
        if not b_low or not i_low:
            return 0

        score = 0
        b_words = [w for w in re.findall(r"[а-яёa-z]{3,}", b_low) if w not in ("это", "как", "что", "мне", "тоже", "вообще", "если", "просто", "очень", "вроде")]
        i_words = [w for w in re.findall(r"[а-яёa-z]{3,}", i_low) if w not in ("это", "как", "что", "мне", "тоже", "вообще", "если", "просто", "очень", "вроде")]

        # 1. Stem overlap (first 4 chars of words >= 4 chars)
        b_stems = {w[:4] for w in b_words if len(w) >= 4}
        i_stems = {w[:4] for w in i_words if len(w) >= 4}
        common_stems = b_stems.intersection(i_stems)
        score += len(common_stems) * 3

        # Exact short keyword overlap (e.g. 'ела', 'зал', 'чай')
        common_exact = set(b_words).intersection(set(i_words))
        score += len(common_exact) * 2

        # 2. Semantic topic synonym group overlap
        for group in cls.TOPIC_SYNONYM_GROUPS:
            b_hit = any(stem in b_low for stem in group)
            i_hit = any(stem in i_low for stem in group)
            if b_hit and i_hit:
                score += 4

        return score

    @classmethod
    def _is_greeting_or_smalltalk(cls, msg_text: str) -> bool:
        low = (msg_text or "").strip().lower().strip("?!., ")
        greetings = {
            "привет", "хай", "ку", "приветик", "добрый день", "доброе утро", "добрый вечер",
            "как дела", "как ты", "как поживаешь", "как жизнь", "как успехи",
            "привет как дела", "привет, как дела", "привет как ты", "привет, как ты",
            "hello", "hi", "hey", "how are you", "how are you doing",
        }
        return low in greetings or any(low == g for g in greetings)

    @classmethod
    def select_reply_targets(
        cls,
        bubbles: List[Tuple[str, Optional[int], Optional[str]]],
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Optional[int]]:
        """
        Determines `reply_to_message_id` for each outgoing bubble.
        Key Rules (Sections 2–9, 35–38):
        - Never attach a Reply to every message!
        - If there is only 1 incoming message:
          * If it is a direct question/substantive inquiry, reply to that message ID.
          * If it is a generic greeting ('привет, как дела?', 'ок', 'ясно'), do NOT reply (None).
        - When the user sends multiple messages / multiple topics in a batch:
          Identify the specific earlier message (e.g. Topic A / Question 1) that an outgoing
          bubble answers and set `reply_to_message_id` on that bubble, while normal continuation
          bubbles get `reply_to_message_id = None`.
        """
        if not bubbles:
            return []

        # Normalize bubbles if passed as list of strings or tuples
        normalized_bubbles: List[Tuple[str, Optional[int], Optional[str]]] = []
        for b in bubbles:
            if isinstance(b, tuple) and len(b) >= 2:
                txt = b[0]
                rep_id = b[1]
                stk = b[2] if len(b) >= 3 else None
                normalized_bubbles.append((txt, rep_id, stk))
            elif isinstance(b, str):
                normalized_bubbles.append((b, None, None))
            else:
                normalized_bubbles.append((str(b), None, None))

        batch = incoming_batch or []
        valid_msg_ids = set()
        for m in batch:
            raw_mid = m.get("telegram_message_id") if m.get("telegram_message_id") is not None else m.get("message_id")
            if raw_mid is not None and str(raw_mid).isdigit():
                valid_msg_ids.add(int(raw_mid))

        reply_targets: List[Optional[int]] = [None] * len(normalized_bubbles)

        # 1. Respect explicit valid `[REPLY:id]` from the model
        used_ids = set()
        for idx, (_, exp_rep_id, _) in enumerate(normalized_bubbles):
            if exp_rep_id is not None and exp_rep_id in valid_msg_ids and exp_rep_id not in used_ids:
                reply_targets[idx] = exp_rep_id
                used_ids.add(exp_rep_id)

        if any(r is not None for r in reply_targets):
            # Guarantee we NEVER reply on every bubble when there are multiple bubbles
            if len(bubbles) >= 2 and all(r is not None for r in reply_targets):
                for i in range(1, len(reply_targets)):
                    reply_targets[i] = None
            return reply_targets

        if not valid_msg_ids or not batch:
            return reply_targets

        # 2. Single incoming message in batch:
        # If it is a substantive question or topic inquiry and NOT simple greeting, attach reply
        if len(batch) == 1:
            single_item = batch[0]
            s_text = (single_item.get("text") or "").strip()
            s_mid = single_item.get("telegram_message_id") if single_item.get("telegram_message_id") is not None else single_item.get("message_id")
            if s_mid is not None and int(s_mid) in valid_msg_ids:
                if cls._is_question_or_substantive_topic(s_text) and not cls._is_greeting_or_smalltalk(s_text):
                    reply_targets[0] = int(s_mid)
            return reply_targets

        # 3. Multiple incoming messages in the batch!
        # Find substantive/question messages that have a valid message_id
        substantive_msgs: List[Tuple[int, Dict[str, Any]]] = []
        for idx, item in enumerate(batch):
            mid = item.get("telegram_message_id") if item.get("telegram_message_id") is not None else item.get("message_id")
            txt = (item.get("text") or "").strip()
            if mid is not None and int(mid) in valid_msg_ids and cls._is_question_or_substantive_topic(txt):
                substantive_msgs.append((idx, item))

        if not substantive_msgs:
            return reply_targets

        last_batch_idx = len(batch) - 1
        earlier_substantive = [
            (idx, item) for (idx, item) in substantive_msgs if idx < last_batch_idx
        ]

        # 3a. Try semantic/topic matching between outgoing bubbles and earlier substantive messages in the batch
        # Never attach a Reply to every bubble in a multi-bubble response (leave at least the last bubble as None)
        max_reply_bubbles = max(1, len(bubbles) - 1) if len(bubbles) >= 2 else 1
        assigned_any = False

        for b_idx in range(max_reply_bubbles):
            b_text = bubbles[b_idx][0]
            scored_earlier: List[Tuple[int, int, Dict[str, Any]]] = []
            for idx, item in earlier_substantive:
                t_mid = item.get("telegram_message_id") if item.get("telegram_message_id") is not None else item.get("message_id")
                if t_mid is None or int(t_mid) in used_ids:
                    continue
                sc = cls._score_topic_match(b_text, item.get("text") or "")
                if "?" in (item.get("text") or "") and sc > 0:
                    sc += 1
                scored_earlier.append((sc, idx, item))

            if scored_earlier:
                scored_earlier.sort(key=lambda x: (x[0], -x[1]), reverse=True)
                best_score, _, best_item = scored_earlier[0]
                min_threshold = 1 if b_idx == 0 else 3
                if best_score >= min_threshold:
                    t_mid = best_item.get("telegram_message_id") if best_item.get("telegram_message_id") is not None else best_item.get("message_id")
                    reply_targets[b_idx] = int(t_mid)
                    used_ids.add(int(t_mid))
                    assigned_any = True

        if assigned_any:
            return reply_targets

        # 3b. Fallback when there is an earlier question/substantive topic in the batch
        if earlier_substantive:
            questions_earlier = [
                (idx, item)
                for (idx, item) in earlier_substantive
                if "?" in (item.get("text") or "")
                or any(
                    q in (item.get("text") or "").lower()
                    for q in ("почему", "зачем", "как", "что", "видела", "думаешь", "what", "did you", "why", "how")
                )
            ]
            # Prefer the first question in the batch when multiple bubbles answer topics in order,
            # or the primary question if only one question exists
            target_item = questions_earlier[0][1] if questions_earlier else earlier_substantive[0][1]
            t_mid = target_item.get("telegram_message_id") if target_item.get("telegram_message_id") is not None else target_item.get("message_id")
            reply_targets[0] = int(t_mid)

        return reply_targets

    @classmethod
    def plan_response(
        cls,
        raw_reply_text: str,
        incoming_batch: Optional[List[Dict[str, Any]]] = None,
        contact: Optional[Any] = None,
        persona: Optional[Any] = None,
        no_brackets: bool = False,
        enable_stickers: bool = True,
        contact_id: Optional[int] = None,
        tone: Optional[str] = None,
        max_burst_messages: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Builds a complete, structured delivery plan for Telegram from the AI's raw response
        and the batch of incoming messages.
        """
        from companion.config import settings
        from companion.services.styles_service import DialogueStyleEngine

        batch = incoming_batch or []
        eff_max_burst = max_burst_messages or getattr(settings, "MAX_BURST_MESSAGES", 4)
        rhythm = ConversationRhythmAnalyzer.analyze_rhythm(batch, max_burst_messages=eff_max_burst)

        # Check if any voice message in the batch failed transcription
        failed_voice_items = [
            m for m in batch
            if bool(m.get("is_voice") or m.get("media_type") in ("voice", "video_note"))
            and (
                m.get("transcription_failed")
                or (m.get("text") or "").strip() in ("", cls.VOICE_FAILED_MARKER, "[Голосовое сообщение]", "[Видео-сообщение (кружок)]")
            )
        ]
        valid_text_items = [
            m for m in batch
            if m not in failed_voice_items and (m.get("text") or "").strip()
        ]

        # Graceful non-hallucinating fallback if voice transcription failed
        if failed_voice_items and not valid_text_items:
            fallback_msg = random.choice(cls.VOICE_FALLBACK_RESPONSES)
            failed_mid = (
                failed_voice_items[-1].get("telegram_message_id")
                if failed_voice_items[-1].get("telegram_message_id") is not None
                else failed_voice_items[-1].get("message_id")
            )
            rep_target = int(failed_mid) if (failed_mid is not None and len(batch) > 1) else None
            msg_entry = {
                "text": fallback_msg,
                "reply_to_message_id": rep_target,
                "send_type": "text",
                "sticker_category": None,
                "emoji": "😭",
            }
            return {
                "response_type": "TEXT + EMOJI",
                "messages": [msg_entry],
                "responses": [msg_entry],
                "sticker": None,
                "combined_text": fallback_msg,
                "rhythm_state": rhythm["state"],
                "is_aggressive": rhythm["is_aggressive"],
                "inter_message_delay": rhythm["inter_message_delay"],
            }

        combined_incoming = " ".join((m.get("text") or "") for m in batch).strip()
        eff_contact_id = contact_id if contact_id is not None else (getattr(contact, "id", None) if contact else None)
        style = tone or (getattr(persona, "current_style", "adaptive_all") if persona else "adaptive_all")

        # 1. Segment into natural bubbles
        raw_segments = cls.segment_response(raw_reply_text, batch)
        if not raw_segments:
            raw_segments = [("привет, ты как?", None, None)]

        # 1b. If user is aggressive/impatient, shape into controlled short burst messages
        if rhythm["is_aggressive"]:
            burst_texts = ConversationRhythmAnalyzer.apply_controlled_burst(
                [seg[0] for seg in raw_segments],
                incoming_batch=batch,
                max_burst_messages=rhythm["max_messages"],
            )
            raw_segments = [(bt, None, None) for bt in burst_texts]
        else:
            raw_segments = raw_segments[: rhythm["max_messages"]]

        # 2. Select reply_to_message_id targets across the bubbles
        reply_targets = cls.select_reply_targets(raw_segments, batch)

        # 3. Process each bubble: humanize formatting, naturalize laughter, control parentheses
        from companion.services.female_style_service import FemaleStyleLayer, FemaleEmotionalState
        emotional_state = FemaleStyleLayer.detect_emotional_state(combined_incoming)
        pref_len = getattr(contact, "preferred_response_length", "medium") or "medium"
        pref_emo = getattr(contact, "emoji_frequency", "medium") or "medium"

        processed_texts: List[str] = []
        explicit_sticker_cat: Optional[str] = None
        for idx, (seg_text, _, stk_cat) in enumerate(raw_segments):
            if stk_cat and not explicit_sticker_cat:
                explicit_sticker_cat = stk_cat

            # Standard anti-dash / lowercase humanization
            humanized = DialogueStyleEngine.humanize_text(seg_text, no_brackets=no_brackets)

            # Natural variable laughter (>= 5 chars, context-weighted)
            humanized = LaughterGenerator.naturalize_laughter_in_text(
                humanized,
                incoming_text=combined_incoming,
                contact_id=eff_contact_id,
            )

            # Rare/contextual parentheses policy (allow at most on 1 bubble if flirty, never when aggressive)
            allow_paren_here = (idx == len(raw_segments) - 1) and (not rhythm["is_aggressive"])
            humanized = ParenthesesController.apply_parentheses_policy(
                humanized,
                incoming_text=combined_incoming,
                style=style,
                no_brackets=no_brackets,
                allow_flirt_paren=allow_paren_here,
            )

            # Empirical Female Style post-processing (lowercase rate, boundary cleanup, emoji rate)
            humanized = FemaleStyleLayer._post_process_bubble(
                bubble=humanized,
                is_first=(idx == 0),
                is_last=(idx == len(raw_segments) - 1),
                emotional_state=emotional_state,
                no_brackets=no_brackets,
                emoji_pref=pref_emo,
                profile=FemaleStyleLayer.DEFAULT_PROFILE
            )

            if humanized:
                processed_texts.append(humanized)

        if not processed_texts:
            processed_texts = ["привет"]

        # If a voice note failed alongside valid text messages, append a short note if not already mentioned
        if failed_voice_items and valid_text_items:
            joined_low = " ".join(processed_texts).lower()
            if "голосов" not in joined_low and "слышн" not in joined_low and "разобрал" not in joined_low:
                if len(processed_texts) < eff_max_burst:
                    processed_texts.append("кстати голосовое не смогла разобрать 😭")

        # 4. Balance Unicode emojis across bubbles (many messages have 0 emojis)
        processed_texts = EmojiAndStickerSelector.balance_emojis_across_bubbles(
            processed_texts,
            incoming_text=combined_incoming,
            no_brackets=no_brackets,
        )

        # Respect persistent user profile preferences (Sections 40, 56, 57)
        if getattr(contact, "emoji_frequency", None) == "none":
            processed_texts = [EmojiAndStickerSelector.UNICODE_EMOJI_RE.sub("", t).strip() for t in processed_texts if t.strip()]
        if getattr(contact, "preferred_response_length", None) == "short" and len(processed_texts) > 2:
            processed_texts = processed_texts[:2]

        # 5. Occasional curated sticker selection (disabled during aggressive/impatient bursts)
        sticker_plan: Optional[Dict[str, Any]] = None
        if enable_stickers and not rhythm["is_aggressive"]:
            sticker_plan = EmojiAndStickerSelector.should_attach_sticker(
                incoming_text=combined_incoming,
                reply_text=" ".join(processed_texts),
                contact_id=eff_contact_id,
                explicit_category=explicit_sticker_cat,
            )

        # 6. Assemble structured messages list & unified `responses` list (Section 35 schema)
        planned_messages: List[Dict[str, Any]] = []
        unified_responses: List[Dict[str, Any]] = []
        for idx, b_text in enumerate(processed_texts):
            rep_id = reply_targets[idx] if idx < len(reply_targets) else None
            emoji_match = EmojiAndStickerSelector.UNICODE_EMOJI_RE.search(b_text)
            t_dur = TypingSimulator.calculate_typing_duration(b_text, is_aggressive_burst=rhythm["is_aggressive"])
            msg_dict = {
                "text": b_text,
                "reply_to_message_id": rep_id,
                "send_type": "text",
                "typing_duration": t_dur,
                "sticker_category": sticker_plan["category"] if (sticker_plan and idx == len(processed_texts) - 1) else None,
                "emoji": emoji_match.group(0) if emoji_match else None,
            }
            planned_messages.append(msg_dict)
            unified_responses.append(
                {
                    "text": b_text,
                    "reply_to_message_id": rep_id,
                    "send_type": "text",
                    "typing_duration": t_dur,
                }
            )

        if sticker_plan:
            unified_responses.append(
                {
                    "text": None,
                    "reply_to_message_id": None,
                    "send_type": "sticker",
                    "sticker_category": sticker_plan["category"],
                    "file_id": sticker_plan["file_id"],
                }
            )

        # Determine high-level response_type
        has_multi = len(planned_messages) > 1
        has_reply = any(m["reply_to_message_id"] is not None for m in planned_messages)
        has_emoji = any(m["emoji"] is not None for m in planned_messages)
        has_sticker = sticker_plan is not None

        if has_sticker and planned_messages:
            response_type = "TEXT + STICKER"
        elif has_multi and has_reply:
            response_type = "MULTIPLE TEXT MESSAGES + SELECTIVE REPLIES"
        elif has_multi:
            response_type = "MULTIPLE TEXT MESSAGES"
        elif has_reply:
            response_type = "TEXT + REPLY"
        elif has_emoji:
            response_type = "TEXT + EMOJI"
        else:
            response_type = "TEXT"

        return {
            "response_type": response_type,
            "messages": planned_messages,
            "responses": unified_responses,
            "sticker": sticker_plan,
            "combined_text": "||".join(m["text"] for m in planned_messages),
            "rhythm_state": rhythm["state"],
            "is_aggressive": rhythm["is_aggressive"],
            "inter_message_delay": rhythm["inter_message_delay"],
        }


# ==============================================================================
# 6. ASYNC READING CYCLE & MESSAGE BATCH COORDINATOR (15–20s READING CYCLE)
# ==============================================================================


@dataclass
class PendingChatBatch:
    items: List[Dict[str, Any]] = field(default_factory=list)
    first_arrival_loop_time: float = 0.0
    last_arrival_loop_time: float = 0.0
    target_read_cycle_seconds: float = 15.0
    update_event: asyncio.Event = field(default_factory=asyncio.Event)
    processing: bool = False
    result_future: Optional[asyncio.Future] = None


class AsyncMessageBatcher:
    """
    Coordinates the periodic 15–20 second reading cycle per chat:
    - When messages arrive for a chat, they are immediately persisted as 'NEW' and
      registered with the active reading cycle for that chat.
    - When the reading cycle timer (15–20 seconds in live mode, or configurable
      `debounce_seconds` in tests) fires, ALL pending messages for that chat are
      collected at once in chronological order and processed in ONE AI call.
    - Once the batch is collected, response generation and Telegram delivery happen
      immediately (no extra 15–20s wait after collection).
    """

    def __init__(
        self,
        default_debounce_seconds: float = 15.0,
        max_batch_wait_seconds: float = 20.0,
    ):
        self.default_debounce_seconds = default_debounce_seconds
        self.max_batch_wait_seconds = max_batch_wait_seconds
        self._batches: Dict[str, PendingChatBatch] = {}

    @staticmethod
    def compute_adaptive_debounce(text: str, base_debounce: float) -> float:
        if base_debounce <= 0:
            return 0.0
        clean = (text or "").strip().lower()
        if clean in ResponsePlanner.TRIVIAL_OPENERS or len(clean.split()) <= 2:
            return min(base_debounce * 1.15, max(base_debounce, 2.2))
        return base_debounce

    def pick_reading_cycle_interval(self, explicit_seconds: Optional[float] = None) -> float:
        if explicit_seconds is not None:
            return max(0.0, float(explicit_seconds))
        from companion.config import settings
        low = float(getattr(settings, "MESSAGE_READ_INTERVAL_MIN", 15.0))
        high = float(getattr(settings, "MESSAGE_READ_INTERVAL_MAX", 20.0))
        if high < low:
            high = low
        return random.uniform(low, high)

    async def enqueue_and_wait(
        cls_self,
        scope_key: str,
        item: Dict[str, Any],
        processor: Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, Dict[str, Any]]],
        debounce_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Adds `item` to the pending reading-cycle batch for `scope_key`.
        - The first message to arrive starts the reading cycle timer (`15–20s` in production,
          or `debounce_seconds` when explicitly specified).
        - All subsequent messages arriving during that reading cycle append to `batch.items`
          and share the single batch execution!
        """
        cycle_seconds = cls_self.pick_reading_cycle_interval(debounce_seconds)
        loop = asyncio.get_running_loop()
        now_t = loop.time()

        batch = cls_self._batches.get(scope_key)
        if batch is not None and not batch.processing:
            # Join the active reading cycle!
            batch.items.append(item)
            batch.last_arrival_loop_time = now_t
            batch.update_event.set()
            fut = batch.result_future
            if fut is not None:
                lead_res = await fut
                return {
                    **(lead_res if isinstance(lead_res, dict) else {}),
                    "action": "batched_into_active_turn",
                    "batched_count": len(batch.items),
                }

        # Create a new reading-cycle batch as leader
        new_batch = PendingChatBatch(
            items=[item],
            first_arrival_loop_time=now_t,
            last_arrival_loop_time=now_t,
            target_read_cycle_seconds=cycle_seconds,
            result_future=loop.create_future(),
        )
        cls_self._batches[scope_key] = new_batch

        try:
            if cycle_seconds > 0:
                # If cycle_seconds >= 5.0, we are in reading-cycle mode (e.g. 15-20s window from first arrival,
                # with a small 1.0s tail settle if the user is actively typing right as the window ends).
                is_fixed_reading_cycle = cycle_seconds >= 5.0
                max_cap = max(cycle_seconds + 3.0, cls_self.max_batch_wait_seconds)

                while True:
                    now_loop = loop.time()
                    elapsed_total = now_loop - new_batch.first_arrival_loop_time
                    if elapsed_total >= max_cap:
                        break

                    if is_fixed_reading_cycle:
                        # If user becomes aggressive/impatient during the reading cycle (>=3 msgs),
                        # respond faster (after 1.0s quiet settle)
                        rhythm_check = ConversationRhythmAnalyzer.analyze_rhythm(new_batch.items)
                        if rhythm_check["is_aggressive"] and len(new_batch.items) >= 3:
                            time_since_last = now_loop - new_batch.last_arrival_loop_time
                            if time_since_last >= 0.8:
                                break
                            wait_sec = 0.8 - time_since_last
                        else:
                            remaining_cycle = cycle_seconds - elapsed_total
                            if remaining_cycle <= 0.05:
                                # Reading cycle of 15-20s has elapsed! Check if a message arrived in the last 0.9s
                                time_since_last = now_loop - new_batch.last_arrival_loop_time
                                if time_since_last < 0.9 and (max_cap - elapsed_total) > 0.2:
                                    wait_sec = min(0.9 - time_since_last, max_cap - elapsed_total)
                                else:
                                    break
                            else:
                                wait_sec = remaining_cycle
                    else:
                        # Short debounce mode (used in unit tests or fast local overrides)
                        latest_text = (new_batch.items[-1].get("text") or "") if new_batch.items else ""
                        wait_sec = cls_self.compute_adaptive_debounce(latest_text, cycle_seconds)
                        remaining_cap = max_cap - elapsed_total
                        if remaining_cap <= 0.05:
                            break
                        wait_sec = min(wait_sec, remaining_cap)

                    new_batch.update_event.clear()
                    try:
                        await asyncio.wait_for(new_batch.update_event.wait(), timeout=max(0.02, wait_sec))
                        continue
                    except asyncio.TimeoutError:
                        if is_fixed_reading_cycle and (loop.time() - new_batch.first_arrival_loop_time) < (cycle_seconds - 0.05):
                            continue
                        break

            new_batch.processing = True
            collected_items = list(new_batch.items)
            if cls_self._batches.get(scope_key) is new_batch:
                cls_self._batches.pop(scope_key, None)

            result = await processor(collected_items)
            if isinstance(result, dict):
                result.setdefault("batch_size", len(collected_items))
                result.setdefault(
                    "batched_message_ids",
                    [
                        m.get("telegram_message_id") if m.get("telegram_message_id") is not None else m.get("message_id")
                        for m in collected_items
                        if (m.get("telegram_message_id") is not None or m.get("message_id") is not None)
                    ],
                )
            if new_batch.result_future and not new_batch.result_future.done():
                new_batch.result_future.set_result(result)
            return result
        except Exception as exc:
            if cls_self._batches.get(scope_key) is new_batch:
                cls_self._batches.pop(scope_key, None)
            if new_batch.result_future and not new_batch.result_future.done():
                new_batch.result_future.set_exception(exc)
            raise
