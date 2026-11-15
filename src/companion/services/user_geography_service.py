"""
UserGeographyService: Advanced Immigrant Geography, Multi-Location Distinction,
Spatial Reference Resolution, and Timezone Synchronization (Sections 1–8, 31–34).

Core Tenet:
origin ≠ current location ≠ nationality ≠ language ≠ timezone ≠ topic being discussed.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class UserGeoProfile:
    origin_country: Optional[str] = None
    origin_city: Optional[str] = None
    origin_confidence: float = 0.0
    origin_source: str = ""

    current_country: Optional[str] = None
    current_city: Optional[str] = None
    current_confidence: float = 0.0
    current_source: str = ""
    is_temporary: bool = False

    home_country: Optional[str] = None
    home_city: Optional[str] = None

    family_country: Optional[str] = None
    family_city: Optional[str] = None

    temporary_location: Optional[str] = None
    previous_locations: List[Dict[str, Any]] = field(default_factory=list)

    timezone: Optional[str] = None
    timezone_confidence: float = 0.0
    timezone_source: str = ""

    nationality: Optional[str] = None
    languages: Dict[str, float] = field(default_factory=dict)
    correction_events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "origin": {
                "country": self.origin_country,
                "city": self.origin_city,
                "confidence": round(self.origin_confidence, 2),
                "source": self.origin_source,
            },
            "current_location": {
                "country": self.current_country,
                "city": self.current_city,
                "confidence": round(self.current_confidence, 2),
                "source": self.current_source,
                "is_temporary": self.is_temporary,
            },
            "home_location": {
                "country": self.home_country,
                "city": self.home_city,
            },
            "family_location": {
                "country": self.family_country,
                "city": self.family_city,
            },
            "temporary_location": self.temporary_location,
            "previous_locations": self.previous_locations[-10:],
            "timezone": {
                "value": self.timezone,
                "confidence": round(self.timezone_confidence, 2),
                "source": self.timezone_source,
            },
            "nationality": self.nationality,
            "languages": self.languages,
            "correction_events": self.correction_events[-10:],
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "UserGeoProfile":
        if not data or not isinstance(data, dict):
            return cls()

        orig = data.get("origin") or {}
        curr = data.get("current_location") or {}
        home = data.get("home_location") or {}
        fam = data.get("family_location") or {}
        tz = data.get("timezone") or {}

        return cls(
            origin_country=orig.get("country"),
            origin_city=orig.get("city"),
            origin_confidence=float(orig.get("confidence", 0.0)),
            origin_source=orig.get("source", ""),
            current_country=curr.get("country"),
            current_city=curr.get("city"),
            current_confidence=float(curr.get("confidence", 0.0)),
            current_source=curr.get("source", ""),
            is_temporary=bool(curr.get("is_temporary", False)),
            home_country=home.get("country"),
            home_city=home.get("city"),
            family_country=fam.get("country"),
            family_city=fam.get("city"),
            temporary_location=data.get("temporary_location"),
            previous_locations=data.get("previous_locations", []),
            timezone=tz.get("value") if isinstance(tz, dict) else (tz if isinstance(tz, str) else None),
            timezone_confidence=float(tz.get("confidence", 0.0)) if isinstance(tz, dict) else 0.8,
            timezone_source=tz.get("source", "") if isinstance(tz, dict) else "profile",
            nationality=data.get("nationality"),
            languages=data.get("languages", {}),
            correction_events=data.get("correction_events", []),
        )


class UserGeographyService:
    """
    Service responsible for immigrant geography parsing, context resolution,
    and timezone alignment.
    """

    # Comprehensive city-to-country & timezone metadata
    KNOWN_CITIES = {
        # United Kingdom
        "лондон": {"country": "United Kingdom", "city": "London", "tz": "Europe/London"},
        "london": {"country": "United Kingdom", "city": "London", "tz": "Europe/London"},
        "манчестер": {"country": "United Kingdom", "city": "Manchester", "tz": "Europe/London"},
        "manchester": {"country": "United Kingdom", "city": "Manchester", "tz": "Europe/London"},
        "бирмингем": {"country": "United Kingdom", "city": "Birmingham", "tz": "Europe/London"},
        "эдинбург": {"country": "United Kingdom", "city": "Edinburgh", "tz": "Europe/London"},

        # Ukraine
        "киев": {"country": "Ukraine", "city": "Kyiv", "tz": "Europe/Kyiv"},
        "київ": {"country": "Ukraine", "city": "Kyiv", "tz": "Europe/Kyiv"},
        "kyiv": {"country": "Ukraine", "city": "Kyiv", "tz": "Europe/Kyiv"},
        "kiev": {"country": "Ukraine", "city": "Kyiv", "tz": "Europe/Kyiv"},
        "львов": {"country": "Ukraine", "city": "Lviv", "tz": "Europe/Kyiv"},
        "львів": {"country": "Ukraine", "city": "Lviv", "tz": "Europe/Kyiv"},
        "lviv": {"country": "Ukraine", "city": "Lviv", "tz": "Europe/Kyiv"},
        "одесса": {"country": "Ukraine", "city": "Odesa", "tz": "Europe/Kyiv"},
        "одеса": {"country": "Ukraine", "city": "Odesa", "tz": "Europe/Kyiv"},
        "odesa": {"country": "Ukraine", "city": "Odesa", "tz": "Europe/Kyiv"},
        "харьков": {"country": "Ukraine", "city": "Kharkiv", "tz": "Europe/Kyiv"},
        "харків": {"country": "Ukraine", "city": "Kharkiv", "tz": "Europe/Kyiv"},
        "днепр": {"country": "Ukraine", "city": "Dnipro", "tz": "Europe/Kyiv"},
        "дніпро": {"country": "Ukraine", "city": "Dnipro", "tz": "Europe/Kyiv"},
        "запорожье": {"country": "Ukraine", "city": "Zaporizhzhia", "tz": "Europe/Kyiv"},
        "запоріжжя": {"country": "Ukraine", "city": "Zaporizhzhia", "tz": "Europe/Kyiv"},
        "винница": {"country": "Ukraine", "city": "Vinnytsia", "tz": "Europe/Kyiv"},
        "вінниця": {"country": "Ukraine", "city": "Vinnytsia", "tz": "Europe/Kyiv"},
        "ивано-франковск": {"country": "Ukraine", "city": "Ivano-Frankivsk", "tz": "Europe/Kyiv"},
        "івано-франківськ": {"country": "Ukraine", "city": "Ivano-Frankivsk", "tz": "Europe/Kyiv"},
        "полтава": {"country": "Ukraine", "city": "Poltava", "tz": "Europe/Kyiv"},
        "черновцы": {"country": "Ukraine", "city": "Chernivtsi", "tz": "Europe/Kyiv"},
        "чернівці": {"country": "Ukraine", "city": "Chernivtsi", "tz": "Europe/Kyiv"},

        # Poland
        "варшава": {"country": "Poland", "city": "Warsaw", "tz": "Europe/Warsaw"},
        "warsaw": {"country": "Poland", "city": "Warsaw", "tz": "Europe/Warsaw"},
        "краков": {"country": "Poland", "city": "Krakow", "tz": "Europe/Warsaw"},
        "krakow": {"country": "Poland", "city": "Krakow", "tz": "Europe/Warsaw"},
        "вроцлав": {"country": "Poland", "city": "Wroclaw", "tz": "Europe/Warsaw"},
        "гданьск": {"country": "Poland", "city": "Gdansk", "tz": "Europe/Warsaw"},

        # Germany
        "берлин": {"country": "Germany", "city": "Berlin", "tz": "Europe/Berlin"},
        "berlin": {"country": "Germany", "city": "Berlin", "tz": "Europe/Berlin"},
        "мюнхен": {"country": "Germany", "city": "Munich", "tz": "Europe/Berlin"},
        "франкфурт": {"country": "Germany", "city": "Frankfurt", "tz": "Europe/Berlin"},
        "гамбург": {"country": "Germany", "city": "Hamburg", "tz": "Europe/Berlin"},

        # France
        "париж": {"country": "France", "city": "Paris", "tz": "Europe/Paris"},
        "paris": {"country": "France", "city": "Paris", "tz": "Europe/Paris"},
        "ницца": {"country": "France", "city": "Nice", "tz": "Europe/Paris"},
        "лион": {"country": "France", "city": "Lyon", "tz": "Europe/Paris"},

        # Spain
        "мадрид": {"country": "Spain", "city": "Madrid", "tz": "Europe/Madrid"},
        "барселона": {"country": "Spain", "city": "Barcelona", "tz": "Europe/Madrid"},
        "валенсия": {"country": "Spain", "city": "Valencia", "tz": "Europe/Madrid"},

        # Italy
        "рим": {"country": "Italy", "city": "Rome", "tz": "Europe/Rome"},
        "милан": {"country": "Italy", "city": "Milan", "tz": "Europe/Rome"},

        # Czechia & Austria
        "прага": {"country": "Czech Republic", "city": "Prague", "tz": "Europe/Prague"},
        "вена": {"country": "Austria", "city": "Vienna", "tz": "Europe/Vienna"},

        # USA & Canada
        "нью-йорк": {"country": "United States", "city": "New York", "tz": "America/New_York"},
        "new york": {"country": "United States", "city": "New York", "tz": "America/New_York"},
        "лос-анджелес": {"country": "United States", "city": "Los Angeles", "tz": "America/Los_Angeles"},
        "los angeles": {"country": "United States", "city": "Los Angeles", "tz": "America/Los_Angeles"},
        "майами": {"country": "United States", "city": "Miami", "tz": "America/New_York"},
        "чикаго": {"country": "United States", "city": "Chicago", "tz": "America/Chicago"},
        "торонто": {"country": "Canada", "city": "Toronto", "tz": "America/Toronto"},
        "ванкувер": {"country": "Canada", "city": "Vancouver", "tz": "America/Vancouver"},

        # UAE
        "дубай": {"country": "United Arab Emirates", "city": "Dubai", "tz": "Asia/Dubai"},
        "dubai": {"country": "United Arab Emirates", "city": "Dubai", "tz": "Asia/Dubai"},

        # Russia
        "москва": {"country": "Russia", "city": "Москва", "tz": "Europe/Moscow"},
        "санкт-петербург": {"country": "Russia", "city": "Санкт-Петербург", "tz": "Europe/Moscow"},
        "питер": {"country": "Russia", "city": "Санкт-Петербург", "tz": "Europe/Moscow"},
        "петербург": {"country": "Russia", "city": "Санкт-Петербург", "tz": "Europe/Moscow"},
        "самара": {"country": "Russia", "city": "Самара", "tz": "Europe/Samara"},
        "казань": {"country": "Russia", "city": "Казань", "tz": "Europe/Moscow"},
        "новосибирск": {"country": "Russia", "city": "Новосибирск", "tz": "Asia/Novosibirsk"},
        "novosibirsk": {"country": "Russia", "city": "Новосибирск", "tz": "Asia/Novosibirsk"},
        "екатеринбург": {"country": "Russia", "city": "Екатеринбург", "tz": "Asia/Yekaterinburg"},
        "нижний новгород": {"country": "Russia", "city": "Нижний Новгород", "tz": "Europe/Moscow"},
        "челябинск": {"country": "Russia", "city": "Челябинск", "tz": "Asia/Yekaterinburg"},
        "красноярск": {"country": "Russia", "city": "Красноярск", "tz": "Asia/Krasnoyarsk"},
        "омск": {"country": "Russia", "city": "Омск", "tz": "Asia/Omsk"},
        "уфа": {"country": "Russia", "city": "Уфа", "tz": "Asia/Yekaterinburg"},
        "ростов": {"country": "Russia", "city": "Ростов", "tz": "Europe/Moscow"},
        "краснодар": {"country": "Russia", "city": "Краснодар", "tz": "Europe/Moscow"},
        "сочи": {"country": "Russia", "city": "Сочи", "tz": "Europe/Moscow"},
        "пермь": {"country": "Russia", "city": "Пермь", "tz": "Asia/Yekaterinburg"},
        "воронеж": {"country": "Russia", "city": "Воронеж", "tz": "Europe/Moscow"},
        "саратов": {"country": "Russia", "city": "Саратов", "tz": "Europe/Samara"},
        "тюмень": {"country": "Russia", "city": "Тюмень", "tz": "Asia/Yekaterinburg"},
        "тольятти": {"country": "Russia", "city": "Тольятти", "tz": "Europe/Samara"},
        "ижевск": {"country": "Russia", "city": "Ижевск", "tz": "Europe/Samara"},
        "барнаул": {"country": "Russia", "city": "Барнаул", "tz": "Asia/Novosibirsk"},
        "ульяновск": {"country": "Russia", "city": "Ульяновск", "tz": "Europe/Samara"},
        "иркутск": {"country": "Russia", "city": "Иркутск", "tz": "Asia/Irkutsk"},
        "хабаровск": {"country": "Russia", "city": "Хабаровск", "tz": "Asia/Vladivostok"},
        "ярославль": {"country": "Russia", "city": "Ярославль", "tz": "Europe/Moscow"},
        "владивосток": {"country": "Russia", "city": "Владивосток", "tz": "Asia/Vladivostok"},
        "томск": {"country": "Russia", "city": "Томск", "tz": "Asia/Novosibirsk"},
        "калининград": {"country": "Russia", "city": "Калининград", "tz": "Europe/Kaliningrad"},
    }

    # Known Countries & Default Timezones
    KNOWN_COUNTRIES = {
        "украина": {"country": "Ukraine", "tz": "Europe/Kyiv"},
        "україна": {"country": "Ukraine", "tz": "Europe/Kyiv"},
        "ukraine": {"country": "Ukraine", "tz": "Europe/Kyiv"},

        "великобритания": {"country": "United Kingdom", "tz": "Europe/London"},
        "британия": {"country": "United Kingdom", "tz": "Europe/London"},
        "англия": {"country": "United Kingdom", "tz": "Europe/London"},
        "велика британія": {"country": "United Kingdom", "tz": "Europe/London"},
        "британія": {"country": "United Kingdom", "tz": "Europe/London"},
        "англія": {"country": "United Kingdom", "tz": "Europe/London"},
        "uk": {"country": "United Kingdom", "tz": "Europe/London"},
        "united kingdom": {"country": "United Kingdom", "tz": "Europe/London"},

        "германия": {"country": "Germany", "tz": "Europe/Berlin"},
        "німеччина": {"country": "Germany", "tz": "Europe/Berlin"},
        "germany": {"country": "Germany", "tz": "Europe/Berlin"},

        "польша": {"country": "Poland", "tz": "Europe/Warsaw"},
        "польща": {"country": "Poland", "tz": "Europe/Warsaw"},
        "poland": {"country": "Poland", "tz": "Europe/Warsaw"},

        "сша": {"country": "United States", "tz": "America/New_York"},
        "штаты": {"country": "United States", "tz": "America/New_York"},
        "америка": {"country": "United States", "tz": "America/New_York"},
        "usa": {"country": "United States", "tz": "America/New_York"},

        "франция": {"country": "France", "tz": "Europe/Paris"},
        "франція": {"country": "France", "tz": "Europe/Paris"},
        "france": {"country": "France", "tz": "Europe/Paris"},

        "испания": {"country": "Spain", "tz": "Europe/Madrid"},
        "іспанія": {"country": "Spain", "tz": "Europe/Madrid"},
        "spain": {"country": "Spain", "tz": "Europe/Madrid"},

        "италия": {"country": "Italy", "tz": "Europe/Rome"},
        "італія": {"country": "Italy", "tz": "Europe/Rome"},
        "italy": {"country": "Italy", "tz": "Europe/Rome"},

        "канада": {"country": "Canada", "tz": "America/Toronto"},
        "canada": {"country": "Canada", "tz": "America/Toronto"},

        "чехия": {"country": "Czech Republic", "tz": "Europe/Prague"},
        "чехія": {"country": "Czech Republic", "tz": "Europe/Prague"},

        "австрия": {"country": "Austria", "tz": "Europe/Vienna"},
        "австрія": {"country": "Austria", "tz": "Europe/Vienna"},

        "оаэ": {"country": "United Arab Emirates", "tz": "Asia/Dubai"},
        "эмираты": {"country": "United Arab Emirates", "tz": "Asia/Dubai"},
        "uae": {"country": "United Arab Emirates", "tz": "Asia/Dubai"},

        "россия": {"country": "Russia", "tz": "Europe/Moscow"},
        "росія": {"country": "Russia", "tz": "Europe/Moscow"},
    }

    # Combined regex patterns for origin vs residence
    COMBINED_IMMIGRANT_PATTERNS = [
        # "родом из Украины, но сейчас живу в Лондоне"
        re.compile(
            r"(?i)\b(?:я\s+)?(?:родом\s+из|родом\s+з|родился\s+в|народився\s+в|народився\s+у)\s+([а-яёіїєґa-z\s\-]+?)[,\s]+(?:но|але|but)\s+(?:сейчас|зараз|currently)\s+(?:живу|мешкаю|переехал\s+в|переїхав\s+до|living\s+in)\s+([а-яёіїєґa-z\s\-]+)",
        ),
        # "из Украины, живу в Лондоне"
        re.compile(
            r"(?i)\b(?:я\s+)?(?:из|з)\s+([а-яёіїєґa-z\s\-]+?)[,\s]+(?:но|але)?\s*(?:сейчас|зараз)?\s*(?:живу|мешкаю)\s+(?:в|у|до|in)\s+([а-яёіїєґa-z\s\-]+)",
        ),
        # "originally from Ukraine, but now living in London"
        re.compile(
            r"(?i)\boriginally\s+from\s+([a-z\s\-]+?)[,\s]+(?:but\s+)?(?:now\s+|currently\s+)?(?:living\s+in|based\s+in)\s+([a-z\s\-]+)",
        ),
    ]

    # Explicit origin expressions
    ORIGIN_PATTERNS = [
        re.compile(r"(?i)\b(?:я\s+)?(?:родом\s+из|родом\s+з)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я\s+)?(?:родился\s+в|родился\s+в\s+городе|народився\s+у|народився\s+в)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я\s+)?(?:вырос\s+в|виріс\s+у|виріс\s+в)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я\s+)?(?:сам|сама)\s+(?:из|з|с)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я\s+)?(?:из|з|с)\s+([а-яёіїєґa-z\s\-]+?)(?:[,\.\?!]|\s+как|\s+а\s+ты|\s+и|$)"),
        re.compile(r"(?i)\boriginally\s+from\s+([a-z\s\-]+)"),
    ]

    # Explicit residence / relocation expressions
    RESIDENCE_PATTERNS = [
        re.compile(r"(?i)\b(?:сейчас|зараз|теперь|пока|currently)\s+(?:живу|мешкаю|осел|оселився|нахожусь|сижу|обитаю|снимаю|проживаю)\s+(?:в|у|во|in)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я\s+)?(?:живу|мешкаю|нахожусь|проживаю)\s+(?:в|у|во|in)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:переехал\s+в|переїхав\s+до|перебрался\s+в|moved\s+to|relocated\s+to)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:уже|вже)\s+\d+\s+(?:лет|года|років|роки|years)\s+(?:живу\s+в|мешкаю\s+в|в|у)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я|мы|сам|сама)\s+(?:сейчас|зараз|тут)?\s*(?:в|у)\s+([а-яёіїєґa-z\s\-]+?)(?:[,\.\?!]|\s+живу|\s+работаю|\s+учусь|\s+а\s+ты|$)"),
        re.compile(r"(?i)\b(?:live|living|based|staying)\s+in\s+([a-z\s\-]+)"),
    ]

    # Temporary travel expressions
    TRAVEL_PATTERNS = [
        re.compile(r"(?i)\b(?:сейчас|зараз|currently)\s+(?:в|у|in)\s+([а-яёіїєґa-z\s\-]+?)\s+(?:на\s+пару\s+дней|на\s+выходных|на\s+тиждень|в\s+командировке|в\s+отпуске|проездом|в\s+гостях|на\s+відпочинку|for\s+a\s+few\s+days)"),
        re.compile(r"(?i)\b(?:приехал\s+в|приїхав\s+до|visiting)\s+([а-яёіїєґa-z\s\-]+?)\s+(?:на\s+пару\s+дней|по\s+делам|по\s+роботі)"),
    ]

    # Return home expressions
    RETURN_PATTERNS = [
        re.compile(r"(?i)\b(?:вернулся|повернувся|уже\s+вернулась|вернулась|приехал|приїхав)\s+(?:домой\s+|додому\s+)?(?:в|у|до)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:уже\s+дома|вже\s+вдома)\s+(?:в|у)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\bback\s+(?:home\s+in|in)\s+([a-z\s\-]+)"),
    ]

    # Explicit correction expressions (Section 30)
    CORRECTION_PATTERNS = [
        re.compile(r"(?i)\b(?:нет|ні|не|no)[,\s]+(?:я\s+не\s+из|я\s+не\s+з)[^,]*?[,\s]+(?:а\s+(?:я\s+)?(?:из|з)\s+|а\s+|я\s+из\s+|я\s+з\s+|из\s+|з\s+)\s*([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:я\s+вообще-то\s+сейчас\s+в|я\s+взагалі-то\s+зараз\s+у)\s+([а-яёіїєґa-z\s\-]+)"),
        re.compile(r"(?i)\b(?:нет|ні)[,\s]+(?:я\s+сейчас\s+в|я\s+зараз\s+у|я\s+в)\s+([а-яёіїєґa-z\s\-]+)"),
    ]

    # Nationality self-identification
    NATIONALITY_PATTERNS = [
        (re.compile(r"(?i)\b(?:я\s+)?(?:украинец|украинка|українець|українка)\b"), "Ukrainian"),
        (re.compile(r"(?i)\b(?:я\s+)?(?:британец|британка|англієць|англієць)\b"), "British"),
        (re.compile(r"(?i)\b(?:я\s+)?(?:поляк|полька)\b"), "Polish"),
        (re.compile(r"(?i)\b(?:я\s+)?(?:немец|немка|німець|німкеня)\b"), "German"),
    ]

    @classmethod
    def match_location(cls, raw_name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Resolves raw city/country snippet into (canonical_city, canonical_country, timezone).
        """
        if not raw_name:
            return None, None, None

        cleaned = raw_name.strip().lower()
        cleaned = re.sub(r"[\.,!\?;]+", "", cleaned).strip()

        # Check known cities
        for city_key, info in cls.KNOWN_CITIES.items():
            if len(city_key) <= 3:
                pattern = r"\b" + re.escape(city_key) + r"\b"
            elif city_key.endswith(("а", "я", "ь", "е", "й")):
                stem = city_key[:-1]
                pattern = r"\b" + re.escape(stem) + r"[аяыиеюуойьехіїєґ]*\b"
            else:
                stem = city_key
                pattern = r"\b" + re.escape(stem) + r"[аеуомахіиєюьґ]*\b"

            if re.search(pattern, cleaned):
                return info["city"], info["country"], info["tz"]

        # Check known countries
        for country_key, info in cls.KNOWN_COUNTRIES.items():
            if len(country_key) <= 3:
                pattern = r"\b" + re.escape(country_key) + r"\b"
            elif country_key.endswith(("а", "я", "ь", "е", "й")):
                stem = country_key[:-1]
                pattern = r"\b" + re.escape(stem) + r"[аяыиеюуойьехіїєґ]*\b"
            else:
                stem = country_key
                pattern = r"\b" + re.escape(stem) + r"[аеуомахіиєюьґ]*\b"

            if re.search(pattern, cleaned):
                return None, info["country"], info["tz"]

        # Fallback to TimezoneService if not matched in KNOWN_CITIES/KNOWN_COUNTRIES
        from companion.services.timezone_service import TimezoneService
        det_city, det_tz = TimezoneService.detect_city_and_timezone(cleaned)
        if det_city:
            return det_city, None, det_tz

        return None, None, None

    @classmethod
    def _parse_geography(
        cls,
        text: str,
        current_profile_data: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> UserGeoProfile:
        """
        Parses incoming text for immigrant geography, distinguishing origin,
        current residence, temporary trips, corrections, and timezone.
        Maintains independent fields without merging origin with location.
        """
        profile = UserGeoProfile.from_dict(current_profile_data)
        if not text or not str(text).strip():
            return profile

        low = text.strip().lower()
        now_iso = datetime.now(timezone.utc).isoformat()

        # 0. Nationality check
        for pat, nat in cls.NATIONALITY_PATTERNS:
            if pat.search(low):
                profile.nationality = nat

        # 1. Combined immigrant statements ("родом из Украины, но сейчас живу в Лондоне")
        for pat in cls.COMBINED_IMMIGRANT_PATTERNS:
            m = pat.search(low)
            if m:
                raw_orig = m.group(1).strip()
                raw_curr = m.group(2).strip()

                orig_city, orig_country, _ = cls.match_location(raw_orig)
                curr_city, curr_country, curr_tz = cls.match_location(raw_curr)

                if orig_city or orig_country:
                    profile.origin_city = orig_city
                    profile.origin_country = orig_country or profile.origin_country
                    profile.origin_confidence = 0.95
                    profile.origin_source = "explicit_user_statement"

                if curr_city or curr_country:
                    profile.current_city = curr_city or profile.current_city
                    profile.current_country = curr_country or profile.current_country
                    profile.current_confidence = 0.96
                    profile.current_source = "explicit_user_statement"
                    profile.is_temporary = False
                    profile.home_city = profile.current_city
                    profile.home_country = profile.current_country

                    if curr_tz:
                        profile.timezone = curr_tz
                        profile.timezone_confidence = 0.96
                        profile.timezone_source = "current_location"

                return profile

        # 2. Explicit correction check (Section 30: "нет, я не из Киева, я из Львова")
        for pat in cls.CORRECTION_PATTERNS:
            m = pat.search(low)
            if m:
                raw_loc = m.group(1).strip()
                city, country, tz = cls.match_location(raw_loc)
                if city or country:
                    correction_entry = {
                        "timestamp": now_iso,
                        "raw_statement": m.group(0),
                        "corrected_city": city,
                        "corrected_country": country,
                    }
                    profile.correction_events.append(correction_entry)

                    # If user is correcting origin ("я не из Киева, я из Львова")
                    if "из" in m.group(0) or "з" in m.group(0):
                        profile.origin_city = city
                        profile.origin_country = country or profile.origin_country
                        profile.origin_confidence = 0.98
                        profile.origin_source = "explicit_correction"
                    else:
                        # Correcting current location
                        profile.current_city = city or profile.current_city
                        profile.current_country = country or profile.current_country
                        profile.current_confidence = 0.98
                        profile.current_source = "explicit_correction"
                        profile.is_temporary = False
                        if tz:
                            profile.timezone = tz
                            profile.timezone_confidence = 0.98
                            profile.timezone_source = "current_location"
                    return profile

        # 3. Temporary travel / vacation check ("сейчас в Париже на пару дней")
        for pat in cls.TRAVEL_PATTERNS:
            m = pat.search(low)
            if m:
                raw_dest = m.group(1).strip()
                city, country, tz = cls.match_location(raw_dest)
                if city or country:
                    if profile.current_city and profile.current_city != (city or country):
                        profile.previous_locations.append({
                            "city": profile.current_city,
                            "country": profile.current_country,
                            "left_at": now_iso,
                        })
                    profile.temporary_location = city or country
                    profile.is_temporary = True
                    profile.current_city = city or profile.current_city
                    profile.current_country = country or profile.current_country
                    profile.current_confidence = 0.90
                    profile.current_source = "temporary_travel"
                    if tz:
                        profile.timezone = tz
                        profile.timezone_confidence = 0.90
                        profile.timezone_source = "temporary_location"
                    return profile

        # 4. Return home check ("вернулась домой в Лондон")
        for pat in cls.RETURN_PATTERNS:
            m = pat.search(low)
            if m:
                raw_dest = m.group(1).strip()
                city, country, tz = cls.match_location(raw_dest)
                if city or country:
                    profile.temporary_location = None
                    profile.is_temporary = False
                    profile.current_city = city or profile.current_city
                    profile.current_country = country or profile.current_country
                    profile.home_city = profile.current_city
                    profile.home_country = profile.current_country
                    profile.current_confidence = 0.96
                    profile.current_source = "return_home"
                    if tz:
                        profile.timezone = tz
                        profile.timezone_confidence = 0.96
                        profile.timezone_source = "current_location"
                    return profile

        # 5. Standalone Origin parsing ("родом из Львова", "я из Киева родом")
        for pat in cls.ORIGIN_PATTERNS:
            m = pat.search(low)
            if m:
                raw_orig = m.group(1).strip()
                city, country, _ = cls.match_location(raw_orig)
                if city or country:
                    profile.origin_city = city
                    profile.origin_country = country or profile.origin_country
                    profile.origin_confidence = 0.95
                    profile.origin_source = "explicit_user_statement"
                    # DO NOT change current_city or timezone based on origin!
                    return profile

        # 6. Standalone Residence parsing ("живу в Лондоне", "переехал в Берлин")
        for pat in cls.RESIDENCE_PATTERNS:
            m = pat.search(low)
            if m:
                raw_res = m.group(1).strip()
                city, country, tz = cls.match_location(raw_res)
                if city or country:
                    profile.current_city = city or profile.current_city
                    profile.current_country = country or profile.current_country
                    profile.current_confidence = 0.95
                    profile.current_source = "explicit_user_statement"
                    profile.is_temporary = False
                    profile.home_city = profile.current_city
                    profile.home_country = profile.current_country
                    if tz:
                        profile.timezone = tz
                        profile.timezone_confidence = 0.95
                        profile.timezone_source = "current_location"
                    return profile

        return profile

    @classmethod
    def analyze_and_update_geography(
        cls,
        first_arg: Any,
        second_arg: Optional[Any] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """
        Overloaded entrypoint:
          analyze_and_update_geography(contact: Contact, text: str) -> (UserGeoProfile, bool)
          analyze_and_update_geography(text: str, current_profile_data: Optional[Dict]) -> UserGeoProfile
        """
        if hasattr(first_arg, "get_geo_profile") or hasattr(first_arg, "geo_profile_data"):
            contact = first_arg
            text = str(second_arg or "")
            curr_prof = contact.get_geo_profile() if hasattr(contact, "get_geo_profile") else {}
            old_dict = curr_prof if isinstance(curr_prof, dict) else (curr_prof.to_dict() if curr_prof else {})
            new_prof = cls._parse_geography(text, old_dict, conversation_history)

            is_updated = (
                new_prof.to_dict() != old_dict
                or getattr(contact, "temporary_location", None) != new_prof.temporary_location
                or getattr(contact, "current_city", None) != new_prof.current_city
            )
            if new_prof.origin_country:
                contact.origin_country = new_prof.origin_country
            if new_prof.origin_city:
                contact.origin_city = new_prof.origin_city
            if new_prof.current_country:
                contact.current_country = new_prof.current_country
            if new_prof.current_city:
                contact.current_city = new_prof.current_city
                contact.detected_city = new_prof.current_city
            elif new_prof.origin_city and not getattr(contact, "current_city", None):
                contact.detected_city = new_prof.origin_city

            if new_prof.home_country:
                contact.home_country = new_prof.home_country
            if new_prof.home_city:
                contact.home_city = new_prof.home_city
            if new_prof.timezone:
                contact.detected_timezone = new_prof.timezone
            elif contact.detected_city and not getattr(contact, "detected_timezone", None):
                from companion.services.timezone_service import TimezoneService
                contact.detected_timezone = TimezoneService.resolve_timezone_for_city(contact.detected_city)

            if not contact.detected_city:
                from companion.services.timezone_service import TimezoneService
                det_c, det_tz = TimezoneService.detect_city_and_timezone(text)
                if det_c:
                    contact.detected_city = det_c
                    if det_tz and not getattr(contact, "detected_timezone", None):
                        contact.detected_timezone = det_tz
                    is_updated = True
            contact.temporary_location = new_prof.temporary_location
            if hasattr(contact, "set_geo_profile"):
                contact.set_geo_profile(new_prof)
            for corr in new_prof.correction_events:
                raw_stmt = corr.get("raw_statement", "") if isinstance(corr, dict) else str(corr)
                if raw_stmt and hasattr(contact, "add_correction_event"):
                    existing_evts = contact.get_correction_events()
                    if not any(raw_stmt in str(e) for e in existing_evts):
                        contact.add_correction_event(corr)
            return new_prof, is_updated

        text = str(first_arg or "")
        current_profile_data = second_arg if isinstance(second_arg, dict) else None
        return cls._parse_geography(text, current_profile_data, conversation_history)

    @classmethod
    def resolve_spatial_reference(
        cls,
        phrase: str,
        profile: UserGeoProfile,
        conversation_text: str = "",
    ) -> Dict[str, Any]:
        """
        Resolves ambiguous spatial references such as "дома", "у нас", "там", "домой" (Section 32).
        Never blindly jumps to origin or current city without checking context.
        """
        low = (phrase or "").lower()
        conv_low = (conversation_text or "").lower()

        # Phrases like "у нас там", "как там у вас", "погода там"
        if "там" in low or "у нас там" in low:
            # If the user lives in London but originates from Ukraine, "там" typically refers to origin
            if profile.origin_country and profile.current_country and profile.origin_country != profile.current_country:
                target_loc = profile.origin_city or profile.origin_country
                return {
                    "referent": "origin_or_family_home",
                    "location": target_loc,
                    "resolved_target": target_loc,
                    "confidence": 0.85,
                    "explanation": f"Refers to origin/family home in {target_loc}, not current residence",
                }

        # Phrases like "уже дома", "я дома", "пришел домой"
        if "дома" in low or "домой" in low:
            if profile.is_temporary and profile.home_city:
                # If currently on a trip, could mean returning to home_city
                if "еду" in low or "вернулся" in low:
                    return {
                        "referent": "home_city",
                        "location": profile.home_city,
                        "resolved_target": profile.home_city,
                        "confidence": 0.80,
                    }
            target_loc = profile.current_city or profile.current_country
            return {
                "referent": "current_residence",
                "location": target_loc,
                "resolved_target": target_loc,
                "confidence": 0.90,
            }

        target_loc = profile.current_city or profile.origin_city
        return {
            "referent": "unknown",
            "location": target_loc,
            "resolved_target": target_loc,
            "confidence": 0.50,
        }

    @classmethod
    def format_geo_prompt_block(cls, profile: Any, persona: Any) -> str:
        """
        Constructs the strict anti-confusion geographic prompt block for Gemini (Section 31 & 35).
        """
        if isinstance(profile, dict):
            profile = UserGeoProfile.from_dict(profile)
        elif profile is None:
            profile = UserGeoProfile()
        origin_str = f"{profile.origin_city}, {profile.origin_country}" if profile.origin_city and profile.origin_country else (profile.origin_country or profile.origin_city or "не указано")
        current_str = f"{profile.current_city}, {profile.current_country}" if profile.current_city and profile.current_country else (profile.current_country or profile.current_city or "не подтвержден")
        home_str = f"{profile.home_city}, {profile.home_country}" if profile.home_city and profile.home_country else (profile.home_city or profile.home_country or current_str)
        tz_str = profile.timezone or getattr(persona, "timezone_name", "Europe/Moscow")

        temp_str = f" (ВРЕМЕННАЯ ПОЕЗДКА: {profile.temporary_location})" if profile.is_temporary and profile.temporary_location else ""

        corrections_note = ""
        if profile.correction_events:
            last_c = profile.correction_events[-1]
            corrections_note = f"\n  * ВАЖНО: Собеседник прямо исправлял насчет локации: «{last_c.get('raw_statement')}». Строго учитывай это исправление!"

        return f"""[ГЕОГРАФИЧЕСКИЙ И ИММИГРАНТСКИЙ ПРОФИЛЬ СОБЕСЕДНИКА]:
• Происхождение (Откуда родом): {origin_str}
• Текущее фактическое местонахождение: {current_str}{temp_str}
• Постоянный дом / город проживания: {home_str}
• Часовой пояс собеседника: {tz_str} (синхронизирован строго по текущему месту жительства: {current_str}, а НЕ по происхождению!)
• Национальность: {profile.nationality or "не указана"}
СТРОЖАЙШИЙ ЗАПРЕТ ПУТАНИЦЫ И СТЕРЕОТИПОВ:
1. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО путать происхождение и текущее место жительства:
   - Если собеседник родом из Украины, но сейчас живет в Лондоне — он находится В ЛОНДОНЕ!
   - Запрещено говорить ему «ты в Украине», «у вас в Киеве», не рассчитывай время по Киеву, когда он в Великобритании!
2. Язык общения собеседника НЕ определяет его физическую геолокацию (собеседник в Лондоне может писать по-украински или по-русски, это нормально для экспатов).
3. Ссылки «у нас там» при жизни за границей обычно относятся к родной стране / семье ({origin_str}), а «дома» — к его квартире/дому в текущем городе ({current_str}).
4. Никаких стереотипов: никогда не приписывай собеседнику политические взгляды, религию или статус на основе национальности или языка.{corrections_note}"""
