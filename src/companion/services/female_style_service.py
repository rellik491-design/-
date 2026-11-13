"""
Female Communication Style Profile & Post-Processing Layer.
Derived empirically from 473+ real female messages across Telegram dating archives.
"""

import os
import json
import random
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


class FemaleEmotionalState(str):
    NEUTRAL_FRIENDLY = "neutral_friendly"       # "привет) как сам, чем занимаешься?"
    PLAYFUL_TEASING = "playful_teasing"         # "куда ты так гонишь ахах 😂", "устроил тут допрос с пристрастием 😂"
    CURIOUS_INTERESTED = "curious_interested"   # "А кем работаешь вообще?", "ты сам откуда? Давно тут живешь?"
    SETTING_BOUNDARIES = "setting_boundaries"   # "я кружки вообще не записываю, стесняюсь на камеру) успеешь насмотреться"
    SKEPTICAL_ASSERTIVE = "skeptical_assertive" # "Чего? 😂 ты меня с кем-то перепутал", "детский сад какой-то 😂"
    COZY_TIRED = "cozy_tired"                   # "тоже вот за компом сижу, работаю", "сонный посреди дня это классика 😂"
    EVENING_ADVENTUROUS = "evening_adventurous" # "сижу думаю чем заняться вечером, лень дома тухнуть", "погнали на квест"


@dataclass
class FemaleCommunicationProfile:
    """
    Empirical communication profile capturing the natural conversational mechanics of young women in Telegram.
    Stored persistently and loaded dynamically.
    """
    # 1. Message Length & Volume Distribution
    avg_char_length: float = 80.4
    avg_word_length: float = 13.6
    short_msg_pct: float = 0.10     # <25 chars: quick reactions like "Привет)", "Лови", "Спасибо!"
    medium_msg_pct: float = 0.47    # 25-75 chars: standard conversational answers
    long_msg_pct: float = 0.43      # >75 chars: elaborate stories, boundary explanations, banter

    # 2. Burst / Splitting Distribution
    single_msg_prob: float = 0.563  # 56.3% single message
    double_burst_prob: float = 0.396# 39.6% doublet (reaction/answer + follow-up question/transition)
    triple_burst_prob: float = 0.035# 3.5% triplet (reaction + explanation + question)

    # 3. Punctuation & Capitalization Tendencies
    parentheses_rate: float = 0.647 # 64.7% of friendly messages end with a single warm ')'
    lowercase_start_rate: float = 0.486 # 48.6% of messages start lowercase (casual mobile texting)
    exclamation_rate: float = 0.011 # Only 1.1% use '!' (girls almost never shout or sound like corporate bots)
    question_rate: float = 0.404    # 40.4% contain a question (active initiative, keeping conversation moving)

    # 4. Laughter & Humor Repertoire
    laughter_tokens: List[str] = field(default_factory=lambda: [
        "ахах", "хах", "хпхп", "ахаха"
    ])
    primary_laugh_emoji: str = "😂"

    # 5. Emoji Repertoire
    top_emojis: List[str] = field(default_factory=lambda: [
        "😂", "😉", "🙃", "🙈", "😴"
    ])
    emoji_frequency_rate: float = 0.431 # 43.1% messages have 1 emoji

    # 6. Conversational Techniques & Boundaries
    deflection_patterns: Dict[str, List[str]] = field(default_factory=lambda: {
        "video_notes": [
            "я кружки вообще не записываю, стесняюсь на камеру) вживую успеешь насмотреться 😂",
            "я не фанат кружочков, стесняюсь немного) давай пока так переписываться)",
            "терпеть не могу кружочки на камеру записывать) твое любопытство подождет 😂",
        ],
        "phone_calls": [
            "я стесняюсь разговоров с незнакомцами по телефону и в целом не люблю по телефону говорить) давай пока тут поболтаем)",
            "голосом по телефону с малознакомыми вообще не моё) давай пока здесь переписываться, куда ты так гонишь ахах 😂",
        ],
        "instagram": [
            "инсту давно снесла, не сижу там) держи лучше тут фотку",
            "я инстаграм забросила миллион лет назад, времени жрет кучу) общаюсь только в телеге",
        ],
        "early_meetup_push": [
            "ахах ты быстрый конечно) мы же только начали общаться, давай хоть узнаем друг друга немного)",
            "слушай, ну я так сразу на встречи не бегу с малознакомыми) расскажи сначала о себе, чем занимаешься вообще?)",
        ],
        "playful_pushback": [
            "ой, началось, детектив включился 😂",
            "устроил тут допрос с пристрастием ахах 😂",
            "легко и с улыбкой 😂",
            "куда ты так гонишь ахах)",
        ]
    })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FemaleCommunicationProfile":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def save_to_file(self, filepath: str = "female_style_profile.json") -> None:
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("Saved FemaleCommunicationProfile to %s", filepath)
        except Exception as e:
            logger.warning("Failed to save style profile: %s", e)

    @classmethod
    def load_from_file(cls, filepath: str = "female_style_profile.json") -> "FemaleCommunicationProfile":
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls.from_dict(data)
            except Exception as e:
                logger.warning("Failed to load style profile from %s: %s. Using default.", filepath, e)
        return cls()


class FemaleStyleLayer:
    """
    Independent style engine that enforces female conversational mechanics,
    natural message segmentation, dynamic emotional coloring, and rhythm.
    """

    DEFAULT_PROFILE = FemaleCommunicationProfile()

    @classmethod
    def detect_emotional_state(
        cls,
        user_message: str,
        dialogue_history: Optional[List[Dict[str, Any]]] = None,
        last_bot_state: Optional[str] = None
    ) -> FemaleEmotionalState:
        """
        Determines the current emotional conversational state from the interlocutor's tone and context.
        """
        u_lower = (user_message or "").lower()

        # 1. Pushing for calls / video notes / external contacts / phone numbers
        if any(w in u_lower for w in ("кружоч", "круглях", "видеозв", "позвони", "созвон", "звон", "телефон", "набери", "номер", "инст", "инста")):
            return FemaleEmotionalState.SETTING_BOUNDARIES

        # 2. Pushy meetups or weird accusations
        if any(w in u_lower for w in ("докажи", "ты бот", "фейк", "чс", "обман", "врешь", "странная")):
            return FemaleEmotionalState.SKEPTICAL_ASSERTIVE

        # 3. Flirting, jokes, playful banter, teasing
        if any(w in u_lower for w in ("красив", "милая", "фигур", "заигрыв", "ахах", "хах", "подкат", "ванн", "пенк", "секси", "соблазн")):
            return FemaleEmotionalState.PLAYFUL_TEASING

        # 4. Evening, relaxing, proposals, activities
        if any(w in u_lower for w in ("вечер", "планы", "отдых", "квест", "погнали", "развея", "прогул", "уста")):
            return FemaleEmotionalState.EVENING_ADVENTUROUS

        # 5. Work, study, daytime fatigue
        if any(w in u_lower for w in ("работ", "учеб", "комп", "сонн", "кофе", "сижу", "дела")):
            return FemaleEmotionalState.COZY_TIRED

        # 6. Questions about origin, location, background
        if any(w in u_lower for w in ("откуда", "где жив", "каком районе", "чем занима", "давно тут", "сколько лет")):
            return FemaleEmotionalState.CURIOUS_INTERESTED

        return FemaleEmotionalState.NEUTRAL_FRIENDLY

    @classmethod
    def format_female_bursts(
        cls,
        text: str,
        emotional_state: FemaleEmotionalState,
        no_brackets: bool = False,
        preferred_length: str = "medium",
        emoji_pref: str = "medium",
        profile: Optional[FemaleCommunicationProfile] = None
    ) -> List[str]:
        """
        Applies empirical female communication mechanics to split or reformat the reply text into
        natural Telegram bubbles (1, 2, or 3 messages) with authentic rhythm and punctuation.
        """
        prof = profile or cls.DEFAULT_PROFILE

        if not text or not text.strip():
            return ["привет)"]

        # If already formatted with explicit '||', respect the explicit split
        if "||" in text:
            bubbles = [b.strip() for b in text.split("||") if b.strip()]
        else:
            bubbles = cls._split_into_female_rhythm(text, emotional_state, preferred_length, prof)

        # Apply stylistic post-processing to each bubble
        processed_bubbles = []
        for idx, bubble in enumerate(bubbles):
            p_bubble = cls._post_process_bubble(
                bubble=bubble,
                is_first=(idx == 0),
                is_last=(idx == len(bubbles) - 1),
                emotional_state=emotional_state,
                no_brackets=no_brackets,
                emoji_pref=emoji_pref,
                profile=prof
            )
            if p_bubble:
                processed_bubbles.append(p_bubble)

        return processed_bubbles or [text]

    @classmethod
    def _split_into_female_rhythm(
        cls,
        text: str,
        emotional_state: FemaleEmotionalState,
        preferred_length: str,
        prof: FemaleCommunicationProfile
    ) -> List[str]:
        """
        Splits a single monolithic text into authentic female conversational bursts:
        - 56% single message
        - 40% doublet (reaction/answer + follow-up/question)
        - 4% triplet (only for high excitement or complex boundaries)
        """
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
        if len(sentences) <= 1:
            # If it's a long sentence with a question mark at the end, consider splitting before the question
            q_match = re.search(r'([,.])\s+([А-Яа-яA-Za-z0-9\s]+?\?)$', text)
            if q_match and len(text) > 70 and random.random() < prof.double_burst_prob:
                part1 = text[:q_match.start(1)].strip()
                part2 = q_match.group(2).strip()
                if part1 and part2:
                    return [part1, part2]
            return [text]

        # Determine target burst count based on emotional state and empirical distribution
        # Highly teasing, boundary-setting or adventurous states have higher burst rates
        burst_bias = {
            FemaleEmotionalState.PLAYFUL_TEASING: 0.55,
            FemaleEmotionalState.SETTING_BOUNDARIES: 0.50,
            FemaleEmotionalState.EVENING_ADVENTUROUS: 0.45,
            FemaleEmotionalState.SKEPTICAL_ASSERTIVE: 0.45,
            FemaleEmotionalState.CURIOUS_INTERESTED: 0.40,
            FemaleEmotionalState.COZY_TIRED: 0.30,
            FemaleEmotionalState.NEUTRAL_FRIENDLY: 0.35,
        }.get(emotional_state, 0.40)

        if preferred_length == "short":
            burst_bias *= 0.6  # If user asked for shorter, prefer compact replies

        # Decide whether to send 1 or 2 messages
        should_split = (random.random() < burst_bias) and len(sentences) >= 2

        if not should_split:
            return [" ".join(sentences)]

        # Find the best split point:
        # Message 1 = emotional reaction or statement
        # Message 2 = question or forward-moving thought
        split_idx = 1
        for i, s in enumerate(sentences[:-1], 1):
            if any(s.endswith(q) for q in ("?", "😂", ")")):
                split_idx = i
                break

        bubble1 = " ".join(sentences[:split_idx]).strip()
        bubble2 = " ".join(sentences[split_idx:]).strip()

        # Rare triplet (3.5%): if bubble 2 is still very long and ends with a question
        if len(bubble2) > 100 and random.random() < prof.triple_burst_prob:
            sub = [s.strip() for s in re.split(r'(?<=[.!?])\s+', bubble2) if s.strip()]
            if len(sub) >= 2:
                return [bubble1, sub[0], " ".join(sub[1:])]

        return [bubble1, bubble2]

    @classmethod
    def _post_process_bubble(
        cls,
        bubble: str,
        is_first: bool,
        is_last: bool,
        emotional_state: FemaleEmotionalState,
        no_brackets: bool,
        emoji_pref: str,
        profile: FemaleCommunicationProfile
    ) -> str:
        """Fine-tunes capitalization, parentheses, emojis, and punctuation for a bubble."""
        b = bubble.strip()
        if not b:
            return ""

        # 1. Lowercase start (48.6% of messages start lowercase)
        if random.random() < profile.lowercase_start_rate:
            if not b.startswith("http") and not b.startswith("Я "):
                b = b[0].lower() + b[1:]

        # 2. Clean up robotic exclamation marks (only 1.1% in archives)
        # Convert trailing '!' to ')' or '.'
        if b.endswith("!") and not b.endswith("!!"):
            if not no_brackets and random.random() < profile.parentheses_rate:
                b = b[:-1] + ")"
            else:
                b = b[:-1]

        # 3. Clean trailing dots (girls don't put dry dots at the end of messages)
        if b.endswith(".") and not b.endswith("..") and not b.endswith("..."):
            b = b[:-1]

        # 4. Parentheses management:
        # In archives, 64.7% of friendly messages end with a soft ')', but NEVER '?))'
        if no_brackets:
            # Strictly eliminate brackets if user requested
            b = re.sub(r'[\(\)]+', '', b).strip()
        else:
            # Never allow '?)' or '!)'
            b = re.sub(r'\?\)+', '?', b)
            b = re.sub(r'!\)+', '!', b)
            # Avoid repeating '))))'
            b = re.sub(r'\){3,}', '))', b)

            # If message doesn't end with question, emoji, or bracket, softly append ')' with ~60% probability on last bubble
            ends_with_symbol = b.endswith(("?", "!", ")", profile.primary_laugh_emoji)) or bool(re.search(r'[\U00010000-\U0010ffff]$', b))
            if is_last and not ends_with_symbol and random.random() < 0.60:
                b = b + ")"

        # 5. Emoji styling:
        if emoji_pref == "none":
            b = re.sub(r'[\U00010000-\U0010ffff]', '', b).strip()
        elif emoji_pref == "low":
            # Keep at most 1 emoji
            all_emo = re.findall(r'[\U00010000-\U0010ffff]', b)
            if len(all_emo) > 1:
                # Remove extra
                for extra in all_emo[1:]:
                    b = b.replace(extra, "", 1).strip()

        return b
