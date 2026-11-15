"""
Contextual Persona Lore & Geographic Connection Service.

Handles fictional persona backstory connections to cities (family ties, prior residence,
and dynamically calculated upcoming/active/completed visits) without polluting user memory
or forcing irrelevant lore into unrelated conversations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional


ALLOWED_RELATIVES = {
    "aunt",
    "uncle",
    "cousin",
    "grandmother",
    "sibling",
    "close family member",
    "тётя",
    "тетя",
    "дядя",
    "кузен",
    "кузина",
    "бабушка",
    "сестра",
    "брат",
    "родственник",
}

RELATIVE_TO_RU = {
    "aunt": "тётя",
    "uncle": "дядя",
    "cousin": "двоюродный брат/сестра",
    "grandmother": "бабушка",
    "sibling": "брат/сестра",
    "close family member": "близкий родственник",
}

RELATIVE_KEYWORDS: Dict[str, List[str]] = {
    "aunt": ["тёт", "тет", "aunt"],
    "тётя": ["тёт", "тет", "aunt"],
    "тетя": ["тёт", "тет", "aunt"],
    "uncle": ["дяд", "uncle"],
    "дядя": ["дяд", "uncle"],
    "cousin": ["кузен", "кузин", "двоюродн", "cousin"],
    "кузен": ["кузен", "кузин", "двоюродн", "cousin"],
    "кузина": ["кузен", "кузин", "двоюродн", "cousin"],
    "grandmother": ["бабуш", "бабк", "grandm", "grandma"],
    "бабушка": ["бабуш", "бабк", "grandm", "grandma"],
    "sibling": ["брат", "сестр", "sibling", "brother", "sister"],
    "сестра": ["сестр", "sister"],
    "брат": ["брат", "brother"],
    "close family member": ["родствен", "семь", "family", "relative"],
    "родственник": ["родствен", "семь", "family", "relative"],
}

COUNTRY_ALIASES = {
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "united kingdom": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "великобритания": "united kingdom",
    "англия": "united kingdom",
    "британия": "united kingdom",
    "usa": "united states",
    "us": "united states",
    "u.s.": "united states",
    "united states": "united states",
    "сша": "united states",
    "америка": "united states",
    "russia": "russia",
    "россия": "russia",
    "рф": "russia",
    "ukraine": "ukraine",
    "украина": "ukraine",
    "україна": "ukraine",
    "poland": "poland",
    "польша": "poland",
    "польща": "poland",
    "germany": "germany",
    "германия": "germany",
    "німеччина": "germany",
    "france": "france",
    "франция": "france",
    "франція": "france",
    "spain": "spain",
    "испания": "spain",
    "іспанія": "spain",
    "italy": "italy",
    "италия": "italy",
    "італія": "italy",
    "canada": "canada",
    "канада": "canada",
}

CITY_ALIASES = {
    "london": "london",
    "лондон": "london",
    "лондоне": "london",
    "лондона": "london",
    "manchester": "manchester",
    "манчестер": "manchester",
    "манчестере": "manchester",
    "манчестера": "manchester",
    "birmingham": "birmingham",
    "бирмингем": "birmingham",
    "бирмингеме": "birmingham",
    "liverpool": "liverpool",
    "ливерпуль": "liverpool",
    "ливерпуле": "liverpool",
    "edinburgh": "edinburgh",
    "эдинбург": "edinburgh",
    "эдинбурге": "edinburgh",
    "moscow": "moscow",
    "москва": "moscow",
    "москве": "moscow",
    "москву": "moscow",
    "saint petersburg": "saint petersburg",
    "st petersburg": "saint petersburg",
    "санкт-петербург": "saint petersburg",
    "питер": "saint petersburg",
    "питере": "saint petersburg",
    "kyiv": "kyiv",
    "kiev": "kyiv",
    "киев": "kyiv",
    "киеве": "kyiv",
    "київ": "kyiv",
    "києві": "kyiv",
    "lviv": "lviv",
    "львов": "lviv",
    "львів": "lviv",
    "odesa": "odesa",
    "odessa": "odesa",
    "одесса": "odesa",
    "одеса": "odesa",
    "paris": "paris",
    "париж": "paris",
    "париже": "paris",
    "warsaw": "warsaw",
    "варшава": "warsaw",
    "варшаве": "warsaw",
    "berlin": "berlin",
    "берлин": "berlin",
    "берлине": "berlin",
}

CITY_STEMS: Dict[str, List[str]] = {
    "manchester": ["manchester", "манчестер"],
    "london": ["london", "лондон"],
    "birmingham": ["birmingham", "бирмингем"],
    "liverpool": ["liverpool", "ливерпул"],
    "edinburgh": ["edinburgh", "эдинбург"],
    "moscow": ["moscow", "москв"],
    "saint petersburg": ["petersburg", "петербург", "питер"],
    "kyiv": ["kyiv", "kiev", "киев", "київ", "києв"],
    "lviv": ["lviv", "львов", "львів"],
    "odesa": ["odesa", "odessa", "одес"],
    "paris": ["paris", "париж"],
    "warsaw": ["warsaw", "варшав"],
    "berlin": ["berlin", "берлин"],
}

RELEVANT_TOPIC_PATTERNS = [
    r"\b(живу|живешь|живёшь|переехал|переехала|нахожусь|сейчас\s+в|я\s+в|я\s+из|родом|город|район|метро|автобус|поезд|транспорт)\b",
    r"\b(погода|дождь|холодно|тепло|снег|ветер|солнце|пасмурно)\b",
    r"\b(кафе|кофе|ресторан|бар|паб|поесть|погулять|парк|музей|концерт|событи|мероприяти|куда\s+сходить|посоветуй)\b",
    r"\b(встрет|увидеться|кофе\s+попить|далеко|сколько\s+ехать|поездка|приеду|приезжай|билет|путешеств|поехать)\b",
    r"\b(live|living|city|town|neighborhood|area|weather|rain|transport|train|bus|cafe|restaurant|pub|park|event|plans|meet|distance|travel|trip|visit)\b",
    r"\b(живу|зараз\s+у|зараз\s+в|місто|район|погода|кав'?ярня|ресторан|погуляти|зустріт|поїздка|подорож)\b",
]

VISIT_REPLY_PATTERNS = [
    r"\b(поеду|еду|собираюсь|буду\s+там|уже\s+там|приеду|приезжаю|завтра|послезавтра)\b",
    r"\b(через\s+(пару|несколько|пару-тройку|\d+)\s+(дн|недел)|на\s+следующей\s+неделе)\b",
    r"\b(going\s+there|visiting|there\s+soon|in\s+a\s+few\s+days|tomorrow|next\s+week|already\s+there)\b",
]

DEFAULT_COOLDOWN_HOURS = 24.0
DEFAULT_COOLDOWN_MESSAGES = 8


@dataclass
class GeoConnectionResolution:
    matched: bool = False
    geographic_relation: Dict[str, bool] = field(
        default_factory=lambda: {
            "same_country": False,
            "same_city": False,
            "persona_has_family_connection": False,
            "persona_upcoming_visit": False,
        }
    )
    connection: Optional[Dict[str, Any]] = None
    travel: Optional[Dict[str, Any]] = None
    timing_phrase: Optional[str] = None
    relevant_now: bool = False
    topic_relevant: bool = False
    cooldown_active: bool = False
    can_mention_city_history: bool = False
    can_mention_family: bool = False
    can_mention_visit: bool = False
    user_city: Optional[str] = None
    user_country: Optional[str] = None
    persona_home_city: Optional[str] = None
    persona_home_country: Optional[str] = None


class PersonaLoreService:
    """Resolves configured persona geographic connections, dynamic visits, and disclosure cooldowns."""

    @staticmethod
    def normalize_country(country: Optional[str]) -> str:
        if not country:
            return ""
        cleaned = re.sub(r"\s+", " ", country.strip().lower())
        return COUNTRY_ALIASES.get(cleaned, cleaned)

    @staticmethod
    def normalize_city(city: Optional[str]) -> str:
        if not city:
            return ""
        cleaned = re.sub(r"\s+", " ", city.strip().lower())
        return CITY_ALIASES.get(cleaned, cleaned)

    @classmethod
    def get_persona_city_connections(cls, persona: Any) -> List[Dict[str, Any]]:
        """Extracts explicitly configured city_connections from Persona without inventing facts."""
        if not persona:
            return []

        raw: Any = None
        if hasattr(persona, "get_city_connections") and callable(persona.get_city_connections):
            raw = persona.get_city_connections()
        elif getattr(persona, "city_connections", None) is not None:
            raw = getattr(persona, "city_connections")
        elif getattr(persona, "city_connections_data", None):
            raw = getattr(persona, "city_connections_data")
        elif getattr(persona, "location_extra", None):
            extra = getattr(persona, "location_extra")
            if isinstance(extra, str) and "city_connections" in extra:
                try:
                    parsed_extra = json.loads(extra)
                    if isinstance(parsed_extra, dict):
                        raw = parsed_extra.get("city_connections")
                except Exception:
                    raw = None

        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return []

        if isinstance(raw, dict):
            raw = raw.get("city_connections", [])

        if not isinstance(raw, list):
            return []

        valid: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            city = item.get("city")
            relative = (item.get("relative") or "").strip().lower()
            if not city or (relative and relative not in ALLOWED_RELATIVES):
                continue
            valid.append(item)
        return valid

    @staticmethod
    def _parse_date(val: Any) -> Optional[date]:
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            text = val.strip()
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(text[:10], fmt).date()
                except ValueError:
                    continue
        return None

    @classmethod
    def get_visit_timing_phrase(cls, days_until: int, status: str) -> Optional[str]:
        if status in ("completed", "cancelled"):
            return None
        if status == "arrived" or days_until == 0:
            return "я уже там"
        if days_until >= 7:
            return "на следующей неделе"
        if 4 <= days_until <= 6:
            return "через несколько дней"
        if days_until == 3:
            return "через пару-тройку дней"
        if days_until == 2:
            return "через пару дней"
        if days_until == 1:
            return "завтра"
        return None

    @classmethod
    def compute_travel_state(
        cls,
        connection: Dict[str, Any],
        today: Optional[date] = None,
    ) -> Optional[Dict[str, Any]]:
        """Dynamically computes travel status and days_until from an absolute visit_date."""
        planned = bool(connection.get("planned_visit"))
        raw_date = connection.get("visit_date") or connection.get("departure_date")
        if not planned and not raw_date:
            return None

        ref_today = today or datetime.now(timezone.utc).date()
        visit_dt = cls._parse_date(raw_date)

        # Fallback only if owner supplied visit_window_days without an absolute date
        if visit_dt is None:
            window_days = connection.get("visit_window_days")
            if window_days is None:
                return None
            days_until = int(window_days)
            departure_iso = None
        else:
            days_until = (visit_dt - ref_today).days
            departure_iso = visit_dt.isoformat()

        end_dt = cls._parse_date(connection.get("visit_end_date"))

        if connection.get("cancelled"):
            status = "cancelled"
        elif days_until > 3:
            status = "planned"
        elif 1 <= days_until <= 3:
            status = "soon"
        elif days_until == 0:
            status = "arrived"
        else:
            if end_dt is not None and ref_today <= end_dt:
                status = "arrived"
            else:
                status = "completed"

        return {
            "destination": connection.get("city"),
            "departure_date": departure_iso,
            "status": status,
            "days_until": days_until,
        }

    @classmethod
    def is_topic_relevant(
        cls,
        incoming_message: str,
        active_thread: Optional[str],
        target_city: Optional[str],
    ) -> bool:
        """Checks if the current message/topic naturally invites geographic connection."""
        text = (incoming_message or "").lower()
        if not text.strip():
            return False

        norm_city = cls.normalize_city(target_city)
        if norm_city:
            stems = CITY_STEMS.get(norm_city, [norm_city])
            if any(stem in text for stem in stems):
                return True

        if any(re.search(pat, text) for pat in RELEVANT_TOPIC_PATTERNS):
            return True

        if active_thread in ("relocation_immigrant", "travel_trips", "dating_meetup"):
            return True

        return False

    @classmethod
    def _is_on_cooldown(
        cls,
        last_iso: Optional[str],
        last_msg_count: Optional[int],
        current_msg_count: int,
        now: datetime,
    ) -> bool:
        if not last_iso:
            return False
        if last_msg_count is not None and (current_msg_count - last_msg_count) < DEFAULT_COOLDOWN_MESSAGES:
            return True
        try:
            last_dt = datetime.fromisoformat(last_iso)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now_aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            elapsed_hours = (now_aware - last_dt).total_seconds() / 3600.0
            return elapsed_hours < DEFAULT_COOLDOWN_HOURS
        except Exception:
            return False

    @classmethod
    def resolve_geographic_connection(
        cls,
        persona: Any,
        contact: Any = None,
        user_geo_profile: Any = None,
        incoming_message: str = "",
        active_thread: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> GeoConnectionResolution:
        """Evaluates all 8 context resolution checks before allowing persona lore mention."""
        res = GeoConnectionResolution()
        if not persona:
            return res

        ref_now = now or datetime.now(timezone.utc)
        ref_today = ref_now.date()

        persona_city = getattr(persona, "city", None)
        persona_country = getattr(persona, "country", None)
        res.persona_home_city = persona_city
        res.persona_home_country = persona_country

        # 1 & 2. Extract user's current city/country with confidence guard
        user_city = None
        user_country = None
        city_conf = 1.0
        if user_geo_profile is not None:
            if isinstance(user_geo_profile, dict):
                curr = user_geo_profile.get("current_location") or {}
                user_city = curr.get("city") or user_geo_profile.get("current_city")
                user_country = curr.get("country") or user_geo_profile.get("current_country")
                city_conf = float(curr.get("confidence", user_geo_profile.get("confidence", 1.0)) or 0.0)
            else:
                user_city = getattr(user_geo_profile, "current_city", None)
                user_country = getattr(user_geo_profile, "current_country", None)
                city_conf = float(getattr(user_geo_profile, "current_location_confidence", 1.0) or 1.0)

        if contact is not None:
            user_city = user_city or getattr(contact, "current_city", None) or getattr(contact, "detected_city", None)
            user_country = user_country or getattr(contact, "current_country", None)

        res.user_city = user_city
        res.user_country = user_country

        if not user_city or not user_country or city_conf < 0.5:
            return res

        norm_user_country = cls.normalize_country(user_country)
        norm_persona_country = cls.normalize_country(persona_country)
        norm_user_city = cls.normalize_city(user_city)
        norm_persona_city = cls.normalize_city(persona_city)

        same_country = bool(norm_user_country and norm_persona_country and norm_user_country == norm_persona_country)
        same_city = bool(norm_user_city and norm_persona_city and norm_user_city == norm_persona_city)

        res.geographic_relation["same_country"] = same_country
        res.geographic_relation["same_city"] = same_city

        # 2. Same-country & different-city condition
        if not same_country or same_city:
            return res

        # 3 & 4. Match against explicitly configured city_connections
        connections = cls.get_persona_city_connections(persona)
        matched_conn = None
        for conn in connections:
            conn_city = cls.normalize_city(conn.get("city"))
            conn_country = cls.normalize_country(conn.get("country")) if conn.get("country") else norm_persona_country
            if conn_city == norm_user_city and conn_country == norm_user_country:
                matched_conn = conn
                break

        if not matched_conn:
            return res

        res.matched = True
        res.connection = matched_conn
        res.geographic_relation["persona_has_family_connection"] = bool(matched_conn.get("relative"))

        # 5 & 6. Dynamic travel state
        travel = cls.compute_travel_state(matched_conn, today=ref_today)
        res.travel = travel
        if travel:
            res.timing_phrase = cls.get_visit_timing_phrase(travel["days_until"], travel["status"])
            res.geographic_relation["persona_upcoming_visit"] = travel["status"] in ("planned", "soon", "arrived")

        # 7. Cooldown check from separate persona_lore_state on Contact
        lore_state = {}
        msg_count = 0
        if contact is not None:
            if hasattr(contact, "get_persona_lore_state") and callable(contact.get_persona_lore_state):
                lore_state = contact.get_persona_lore_state() or {}
            msg_count = int(getattr(contact, "total_messages_in", 0) or 0)

        city_cd = cls._is_on_cooldown(
            lore_state.get("city_connection_last_mentioned"),
            lore_state.get("city_connection_msg_count"),
            msg_count,
            ref_now,
        )
        fam_cd = cls._is_on_cooldown(
            lore_state.get("family_connection_last_mentioned"),
            lore_state.get("family_connection_msg_count"),
            msg_count,
            ref_now,
        )
        visit_cd = cls._is_on_cooldown(
            lore_state.get("visit_last_mentioned"),
            lore_state.get("visit_msg_count"),
            msg_count,
            ref_now,
        )

        res.can_mention_city_history = bool(matched_conn.get("history")) and not city_cd
        res.can_mention_family = bool(matched_conn.get("relative")) and not fam_cd
        res.can_mention_visit = bool(res.geographic_relation["persona_upcoming_visit"] and res.timing_phrase) and not visit_cd

        any_available = res.can_mention_city_history or res.can_mention_family or res.can_mention_visit
        res.cooldown_active = not any_available

        # 8. Opportunistic topic relevance
        res.topic_relevant = cls.is_topic_relevant(incoming_message, active_thread, user_city)
        res.relevant_now = bool(res.matched and res.topic_relevant and any_available)
        return res

    @classmethod
    def format_persona_lore_prompt_block(
        cls,
        resolution: GeoConnectionResolution,
        persona: Any,
    ) -> str:
        """Formats the PERSONA LORE prompt block only when relevant and not on cooldown."""
        if not resolution.matched or not resolution.connection:
            return ""

        conn = resolution.connection
        city_name = conn.get("city") or resolution.user_city
        persona_home = getattr(persona, "city", "") or resolution.persona_home_city or ""

        if resolution.cooldown_active:
            return (
                f"[PERSONA LORE — ЗАПРЕТ ПОВТОРА]:\n"
                f"• Ты уже недавно упоминала свою связь с городом {city_name} (родственников / поездку). "
                f"НЕ повторяй это снова! Продолжай диалог по текущей теме.\n"
                f"• Город собеседника: {city_name}, твой город: {persona_home}. Не путай их и не говори «мы обе живём в {city_name}»."
            )

        if not resolution.relevant_now:
            return ""

        rel_raw = (conn.get("relative") or "").strip().lower()
        rel_ru = RELATIVE_TO_RU.get(rel_raw, rel_raw)
        lines: List[str] = [
            "[PERSONA LORE — ГЕОГРАФИЧЕСКАЯ СВЯЗЬ ПЕРСОНАЖА (НЕ ПУТАТЬ С ПАМЯТЬЮ О СОБЕСЕДНИКЕ!)]:",
            f"• Это ТВОЯ вымышленная биография ({getattr(persona, 'name', 'Персонаж')}), а НЕ факты о семье собеседника.",
            f"• Собеседник сейчас в городе {city_name} (та же страна: {resolution.user_country}, но другой город; ты живёшь в {persona_home}).",
        ]

        if resolution.can_mention_family or resolution.can_mention_city_history:
            history_str = conn.get("history") or f"раньше жила там ({rel_ru})"
            lines.append(f"• Настроенная связь с городом {city_name}: {rel_ru}; история: {history_str}.")

        if resolution.travel:
            t_status = resolution.travel["status"]
            if resolution.can_mention_visit and resolution.timing_phrase:
                lines.append(
                    f"• Запланированная поездка в {city_name}: статус={t_status}, ориентир по времени=«{resolution.timing_phrase}»."
                )
            elif t_status in ("completed", "cancelled"):
                lines.append(
                    f"• ВНИМАНИЕ: Поездка в {city_name} уже имеет статус '{t_status}'. НЕ говори, что скоро туда поедешь!"
                )

        lines.extend(
            [
                "ПРАВИЛА ЕСТЕСТВЕННОГО ИСПОЛЬЗОВАНИЯ:",
                "1. НЕ вываливай всю историю сразу! Упомяни только то, что органично подходит к текущей реплике (можно просто связь с городом/тётей или только поездку).",
                "2. НЕ придумывай лишних деталей (имя, возраст, профессию родственника, точный адрес, причины переезда или срок жизни там).",
                f"3. НЕ путай города: собеседник живёт в {city_name}, а ты живёшь в {persona_home} (не говори «мы обе живём в {city_name}»).",
                "4. Если текущая тема важнее (работа, личные переживания и т.д.) — не перебивай её ради этой детали.",
            ]
        )
        return "\n".join(lines)

    @classmethod
    def record_lore_disclosure_from_reply(
        cls,
        contact: Any,
        reply_text: str,
        resolution: GeoConnectionResolution,
        now: Optional[datetime] = None,
    ) -> bool:
        """Detects if the bot's reply revealed persona lore and updates separate cooldown state."""
        if not contact or not reply_text or not resolution.matched or not resolution.connection:
            return False

        text = reply_text.lower()
        conn = resolution.connection
        ref_now = now or datetime.now(timezone.utc)
        now_iso = ref_now.isoformat()
        msg_count = int(getattr(contact, "total_messages_in", 0) or 0)

        norm_city = cls.normalize_city(conn.get("city"))
        stems = CITY_STEMS.get(norm_city, [norm_city] if norm_city else [])
        mentioned_city = any(s in text for s in stems) or "там раньше" in text or "там жила" in text

        rel_raw = (conn.get("relative") or "").strip().lower()
        rel_stems = RELATIVE_KEYWORDS.get(rel_raw, [rel_raw] if rel_raw else [])
        mentioned_family = any(rs in text for rs in rel_stems)

        mentioned_visit = any(re.search(pat, text) for pat in VISIT_REPLY_PATTERNS)

        if not (mentioned_city or mentioned_family or mentioned_visit):
            return False

        state = {}
        if hasattr(contact, "get_persona_lore_state") and callable(contact.get_persona_lore_state):
            state = dict(contact.get_persona_lore_state() or {})

        if mentioned_city or mentioned_family:
            state["city_connection_last_mentioned"] = now_iso
            state["city_connection_msg_count"] = msg_count
        if mentioned_family:
            state["family_connection_last_mentioned"] = now_iso
            state["family_connection_msg_count"] = msg_count
        if mentioned_visit:
            state["visit_last_mentioned"] = now_iso
            state["visit_msg_count"] = msg_count

        if hasattr(contact, "set_persona_lore_state") and callable(contact.set_persona_lore_state):
            contact.set_persona_lore_state(state)
        return True
