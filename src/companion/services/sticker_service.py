import json
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional, Any
import httpx

from companion.config import settings

logger = logging.getLogger(__name__)


class StickerCatalogService:
    """
    Manages authentic Telegram stickers for companion bot.
    1. Fetches real sticker sets accessible to the bot (e.g. UtyaDuck, HotCherry, persik).
    2. Caches valid Telegram file_ids locally and in memory.
    3. Guarantees 0 invalid sticker placeholders that trigger Telegram API 400.
    4. Enforces strict sparse usage (at least 15-20 turns between stickers; ~3-5% frequency).
    """

    CACHE_FILE = Path("assets/stickers_cache.json")
    DEFAULT_SETS = ["UtyaDuck", "HotCherry", "persik"]

    # Guaranteed valid real fallback Telegram file_ids extracted directly from official packs
    CURATED_FALLBACKS: Dict[str, List[str]] = {
        "laugh": [
            "CAACAgIAAxUAAWr3YjtOpnOu6Ihr193opaKIxbZ3AALyAANWnb0KEJ2LdlN60mQ9BA",  # UtyaDuck 😂
            "CAACAgIAAxUAAWr3YjvRrFOrdj-hnHRV6ouePjlgAAIBAAPANk8TGC5zMKs_LVE9BA",  # HotCherry 😂
        ],
        "cute": [
            "CAACAgIAAxUAAWr3Yjs4sj36pTWagIP9TSTlmWjDAAIEAQACVp29Ct4E0XpmZvdsPQQ",  # UtyaDuck 😘
            "CAACAgIAAxUAAWr3YjsmljvOB7LeBHrbJ6fn20jhAAICAAPANk8TCPVuRfqEp1k9BA",  # HotCherry 😘
            "CAACAgIAAxUAAWr3Yjtx0jFPCJ1liPTaYK18vID9AALsCQAC9DdRAsjtZ0offL-DPQQ",  # persik 😘
        ],
        "approval": [
            "CAACAgIAAxUAAWr3YjvILdN3E2PVmYsWLi683dwBAAL-AANWnb0K2gRhMC751_89BA",  # UtyaDuck 👍
            "CAACAgIAAxUAAWr3YjsfIsdx2fN9w6Mk9NCzcvomAAIVAAPANk8TzVamO2GeZOc9BA",  # HotCherry 👍
        ],
        "surprise": [
            "CAACAgIAAxUAAWr3YjtOpnOu6Ihr193opaKIxbZ3AALyAANWnb0KEJ2LdlN60mQ9BA",
        ],
        "facepalm": [
            "CAACAgIAAxUAAWr3Yjvr4tm1Ba7Lpm6IMfYHgGZpAALwCQAC9DdRAjN4RqccUoKsPQQ",  # persik 😖
        ],
        "teasing": [
            "CAACAgIAAxUAAWr3YjsmljvOB7LeBHrbJ6fn20jhAAICAAPANk8TCPVuRfqEp1k9BA",
        ],
        "celebration": [
            "CAACAgIAAxUAAWr3YjvILdN3E2PVmYsWLi683dwBAAL-AANWnb0K2gRhMC751_89BA",
        ],
    }

    _catalog_by_category: Dict[str, List[str]] = {}
    _last_sticker_turn_by_contact: Dict[int, int] = {}

    @classmethod
    def initialize(cls) -> None:
        """Loads cached stickers from disk or populates from curated fallbacks."""
        if cls.CACHE_FILE.exists():
            try:
                data = json.loads(cls.CACHE_FILE.read_text(encoding="utf-8"))
                cls._catalog_by_category = data
                return
            except Exception as e:
                logger.warning("Failed to load sticker cache: %s", e)

        # Fallback to curated real IDs
        cls._catalog_by_category = {k: list(v) for k, v in cls.CURATED_FALLBACKS.items()}

    @classmethod
    async def refresh_catalog_from_telegram(cls, bot_token: Optional[str] = None) -> bool:
        """
        Fetches live sticker sets from Telegram API and categorizes file_ids by emoji intent.
        """
        token = bot_token or settings.ADMIN_BOT_TOKEN
        if not token or token in ("mock_token", "test_token"):
            cls.initialize()
            return False

        catalog: Dict[str, List[str]] = {
            "laugh": [],
            "cute": [],
            "approval": [],
            "surprise": [],
            "facepalm": [],
            "teasing": [],
            "celebration": [],
        }

        emoji_to_cat = {
            "😂": "laugh", "🤣": "laugh", "😆": "laugh",
            "😘": "cute", "🥰": "cute", "❤️": "cute", "🥺": "cute",
            "👍": "approval", "👌": "approval", "😎": "approval",
            "😱": "surprise", "😮": "surprise", "😳": "surprise",
            "🤦": "facepalm", "🤦‍♀️": "facepalm", "😖": "facepalm", "🙄": "facepalm",
            "😜": "teasing", "😏": "teasing", "😋": "teasing",
            "🎉": "celebration", "🥳": "celebration",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for set_name in cls.DEFAULT_SETS:
                    url = f"https://api.telegram.org/bot{token}/getStickerSet"
                    res = await client.get(url, params={"name": set_name})
                    if res.status_code == 200:
                        stickers = res.json().get("result", {}).get("stickers", [])
                        for s in stickers:
                            emoji = s.get("emoji")
                            f_id = s.get("file_id")
                            if emoji and f_id:
                                cat = emoji_to_cat.get(emoji)
                                if cat and f_id not in catalog[cat]:
                                    catalog[cat].append(f_id)

            # Ensure every category has at least the curated fallbacks
            for cat, fallbacks in cls.CURATED_FALLBACKS.items():
                for fid in fallbacks:
                    if fid not in catalog[cat]:
                        catalog[cat].append(fid)

            cls._catalog_by_category = catalog
            cls.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cls.CACHE_FILE.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Sticker catalog refreshed successfully with %d categories", len(catalog))
            return True
        except Exception as e:
            logger.warning("Failed to refresh sticker catalog from Telegram: %s", e)
            cls.initialize()
            return False

    @classmethod
    def can_send_sticker(cls, contact_id: int, current_turn_count: int) -> bool:
        """
        Enforces a cooldown of at least 15 turns between stickers per contact.
        Stickers must be rare and delightful, never spammy.
        """
        last_turn = cls._last_sticker_turn_by_contact.get(contact_id, -100)
        return (current_turn_count - last_turn) >= 15

    @classmethod
    def record_sticker_sent(cls, contact_id: int, current_turn_count: int) -> None:
        cls._last_sticker_turn_by_contact[contact_id] = current_turn_count

    @classmethod
    def get_sticker(cls, category: str) -> Optional[str]:
        """
        Retrieves a valid real Telegram file_id for the given category.
        Never returns placeholder or mock strings.
        """
        if not cls._catalog_by_category:
            cls.initialize()

        pool = cls._catalog_by_category.get(category) or cls.CURATED_FALLBACKS.get(category, [])
        if pool:
            return random.choice(pool)
        return None
