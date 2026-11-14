"""
TypingSimulator: Natural human typing simulation service.
Calculates realistic per-message typing durations based on character count,
word count, punctuation pauses, and natural human CPS (characters-per-second) variance.
Separates AI generation latency from human message typing.
"""

import random
import re
from typing import Dict, Any, Optional


class TypingSimulator:
    """
    Physics-based typing speed model (Sections 10–14 & 54).
    Calculates typing duration:
    typing_time = base_delay + (characters / chars_per_sec) + punctuation_penalties + natural_jitter

    Benchmarks:
    - 2 chars ('да'): ~0.25–0.5s (nearly instantaneous)
    - 20 chars: ~0.8–1.5s (short)
    - 100 chars: ~2.8–4.8s (noticeably longer)
    - 500 chars: ~7.5–12.0s (substantially longer)
    """

    # Human mobile typing speed: 20–32 characters per second on touch keyboards
    CPS_MIN = 22.0
    CPS_MAX = 32.0

    # Base physical hand-to-keyboard preparation delay
    BASE_DELAY_MIN = 0.20
    BASE_DELAY_MAX = 0.45

    # Pause penalties for punctuation (human pauses at commas, question marks, and line breaks)
    PUNCTUATION_PAUSE = 0.12
    NEWLINE_PAUSE = 0.25

    @classmethod
    def calculate_typing_duration(
        cls,
        text: str,
        is_aggressive_burst: bool = False,
        rng: Optional[Any] = None,
    ) -> float:
        """
        Calculates realistic typing duration in seconds for an outgoing text bubble.
        """
        if not text or not text.strip():
            return 0.0

        clean = text.strip()
        if "[STICKER:" in clean:
            return 0.0

        char_count = len(clean)

        # 1. Very short messages (1–4 chars, e.g. 'да', 'ок', 'ага', 'неа')
        if char_count <= 4:
            return round(random.uniform(0.25, 0.48), 2)

        # 2. Short acknowledgments (5–12 chars, e.g. 'привет)', 'я тут', 'секунду')
        if char_count <= 12:
            return round(random.uniform(0.50, 0.95), 2)

        rand = rng if rng is not None else random

        # Fast burst mode for aggressive/impatient rhythm (types ~25% faster)
        cps_range = (cls.CPS_MIN * 1.25, cls.CPS_MAX * 1.25) if is_aggressive_burst else (cls.CPS_MIN, cls.CPS_MAX)
        cps = rand.uniform(cps_range[0], cps_range[1])
        base_delay = rand.uniform(cls.BASE_DELAY_MIN, cls.BASE_DELAY_MAX)

        # Punctuation pauses
        punct_count = len(re.findall(r"[,;:\?\!\.]", clean))
        newline_count = clean.count("\n")
        punct_delay = (punct_count * cls.PUNCTUATION_PAUSE) + (newline_count * cls.NEWLINE_PAUSE)
        # Cap punctuation delay so long texts don't get absurdly inflated
        punct_delay = min(punct_delay, 1.8)

        # Pure typing time
        pure_typing = char_count / cps

        total = base_delay + pure_typing + punct_delay

        # Natural bounded range: minimum 0.45s, maximum 3.5s for fast, responsive typing
        total = max(0.45, min(total, 3.5))
        return round(total, 2)

    @classmethod
    def should_show_typing(cls, send_type: str = "text", text: str = "") -> bool:
        """
        Rule: Do NOT show typing for stickers, audio voice notes (which use record_voice),
        or empty messages (Section 13).
        """
        if send_type in ("sticker", "voice", "audio"):
            return False
        if not text or not text.strip():
            return False
        return True
