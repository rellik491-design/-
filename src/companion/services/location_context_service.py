"""
LocationContextService: Persistent Location, Dynamic Time & Geographic Context System.

Implements Sections 1–22 of the persistent location, time, and geographic context architecture:
1. Persona Location from bot profile (never invented, never inferred from user location).
2. Timezone derived from persona location using IANA names (Europe/Kyiv, Europe/Warsaw, Europe/Paris, etc.).
3. Never stores "current time" as persistent memory; computes dynamic local time at runtime.
4. Ephemeral TimeContext object with day periods, weekend detection, UTC offset.
5. Natural conversational day periods (morning 05-10:59, daytime 11-16:59, evening 17-21:59, late evening 22-00:59, night 01-04:59).
6. Persona time vs User time separation with strict user timezone detection priority (explicit > confirmed city > unknown; never guess from language).
7. Persona routine / schedule support (wake_time, sleep_time, work_start, work_end).
8. Lightweight persistent city & country knowledge catalogs.
9. Static knowledge vs dynamic web enrichment separation.
10. Temporary trip support (temporary current location with fallback to home location).
11. Section 17 prompt block injection.
12. Temporal phrase resolution ("завтра в 10", "через два часа") into absolute ISO timestamps.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import pytz

from companion.services.timezone_service import TimezoneService

logger = logging.getLogger(__name__)


ENGLISH_WEEKDAYS = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}

ENGLISH_MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

RUSSIAN_WEEKDAYS = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}


@dataclass
class PersonaLocationProfile:
    """Persistent structured location profile of the persona (Section 1 & 15)."""
    city: str
    country: str
    country_code: str
    timezone: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    temporary: bool = False
    home_location: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "persona_location": {
                "city": self.city,
                "country": self.country,
                "country_code": self.country_code,
                "timezone": self.timezone,
                "latitude": self.latitude,
                "longitude": self.longitude,
            }
        }
        if self.temporary:
            data["temporary"] = True
            if self.home_location:
                data["home_location"] = self.home_location
        return data


@dataclass
class PersonaRoutine:
    """Optional routine/schedule for the persona (Section 10)."""
    wake_time: str = "09:00"
    sleep_time: str = "23:00"
    work_start: Optional[str] = "10:00"
    work_end: Optional[str] = "18:00"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "routine": {
                "wake_time": self.wake_time,
                "sleep_time": self.sleep_time,
                "work_start": self.work_start,
                "work_end": self.work_end,
            }
        }


@dataclass
class CityProfile:
    """Lightweight persistent factual profile for a city (Section 11)."""
    name: str
    country: str
    region: str
    language: str
    currency: str
    currency_code: str
    timezone: str
    known_for: List[str] = field(default_factory=list)
    districts: List[str] = field(default_factory=list)
    landmarks: List[str] = field(default_factory=list)
    common_places: List[str] = field(default_factory=list)
    transport: List[str] = field(default_factory=list)
    climate: str = "умеренный"
    nearby_cities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "city_profile": {
                "name": self.name,
                "country": self.country,
                "region": self.region,
                "language": self.language,
                "currency": self.currency_code,
                "timezone": self.timezone,
                "known_for": self.known_for,
            }
        }


@dataclass
class CountryProfile:
    """Lightweight persistent country profile (Section 12)."""
    name: str
    capital: str
    currency: str
    currency_code: str
    language_context: List[str]
    timezone: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "country_profile": {
                "name": self.name,
                "capital": self.capital,
                "currency": self.currency_code,
                "language_context": self.language_context,
                "timezone": self.timezone,
            }
        }


@dataclass
class TimeContext:
    """Ephemeral context object built dynamically before generating a response (Sections 4, 5, 7)."""
    persona_local_time: str       # "HH:MM" e.g. "21:37"
    persona_local_date: str       # "YYYY-MM-DD" e.g. "2026-11-14"
    day_of_week: str              # "Saturday"
    timezone: str                 # "Europe/Kyiv"
    utc_offset: str               # "+02:00"
    day_period: str               # "morning", "daytime", "evening", "late evening", "night"
    is_weekend: bool              # True/False
    formatted_date_eng: str       # "Saturday, November 14, 2026"
    raw_local_dt: datetime
    user_timezone: str = "unknown"
    user_local_time: Optional[str] = None
    user_utc_offset: Optional[str] = None
    time_diff_hours: Optional[float] = None
    routine_phase: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "persona_local_time": self.persona_local_time,
            "persona_local_date": self.persona_local_date,
            "day_of_week": self.day_of_week,
            "timezone": self.timezone,
            "utc_offset": self.utc_offset,
            "day_period": self.day_period,
            "is_weekend": self.is_weekend,
        }


class CountryKnowledgeCatalog:
    """Lightweight catalog of country facts (Section 12)."""

    COUNTRIES: Dict[str, CountryProfile] = {
        "ukraine": CountryProfile(
            name="Ukraine",
            capital="Kyiv",
            currency="гривна",
            currency_code="UAH",
            language_context=["Ukrainian", "Russian"],
            timezone="Europe/Kyiv",
        ),
        "украина": CountryProfile(
            name="Ukraine",
            capital="Kyiv",
            currency="гривна",
            currency_code="UAH",
            language_context=["Ukrainian", "Russian"],
            timezone="Europe/Kyiv",
        ),
        "russia": CountryProfile(
            name="Россия",
            capital="Москва",
            currency="рубль",
            currency_code="RUB",
            language_context=["Russian"],
            timezone="Europe/Moscow",
        ),
        "россия": CountryProfile(
            name="Россия",
            capital="Москва",
            currency="рубль",
            currency_code="RUB",
            language_context=["Russian"],
            timezone="Europe/Moscow",
        ),
        "poland": CountryProfile(
            name="Poland",
            capital="Warsaw",
            currency="злотый",
            currency_code="PLN",
            language_context=["Polish", "Ukrainian", "English"],
            timezone="Europe/Warsaw",
        ),
        "польша": CountryProfile(
            name="Poland",
            capital="Warsaw",
            currency="злотый",
            currency_code="PLN",
            language_context=["Polish", "Ukrainian", "English"],
            timezone="Europe/Warsaw",
        ),
        "france": CountryProfile(
            name="France",
            capital="Paris",
            currency="евро",
            currency_code="EUR",
            language_context=["French", "English"],
            timezone="Europe/Paris",
        ),
        "франция": CountryProfile(
            name="France",
            capital="Paris",
            currency="евро",
            currency_code="EUR",
            language_context=["French", "English"],
            timezone="Europe/Paris",
        ),
        "united kingdom": CountryProfile(
            name="United Kingdom",
            capital="London",
            currency="фунт стерлингов",
            currency_code="GBP",
            language_context=["English"],
            timezone="Europe/London",
        ),
        "великобритания": CountryProfile(
            name="United Kingdom",
            capital="London",
            currency="фунт стерлингов",
            currency_code="GBP",
            language_context=["English"],
            timezone="Europe/London",
        ),
        "germany": CountryProfile(
            name="Germany",
            capital="Berlin",
            currency="евро",
            currency_code="EUR",
            language_context=["German", "English"],
            timezone="Europe/Berlin",
        ),
        "германия": CountryProfile(
            name="Germany",
            capital="Berlin",
            currency="евро",
            currency_code="EUR",
            language_context=["German", "English"],
            timezone="Europe/Berlin",
        ),
        "united states": CountryProfile(
            name="USA",
            capital="Washington, D.C.",
            currency="доллар США",
            currency_code="USD",
            language_context=["English"],
            timezone="America/New_York",
        ),
        "usa": CountryProfile(
            name="USA",
            capital="Washington, D.C.",
            currency="доллар США",
            currency_code="USD",
            language_context=["English"],
            timezone="America/New_York",
        ),
        "сша": CountryProfile(
            name="USA",
            capital="Washington, D.C.",
            currency="доллар США",
            currency_code="USD",
            language_context=["English"],
            timezone="America/New_York",
        ),
        "uae": CountryProfile(
            name="UAE",
            capital="Abu Dhabi",
            currency="дирхам",
            currency_code="AED",
            language_context=["Arabic", "English"],
            timezone="Asia/Dubai",
        ),
        "оаэ": CountryProfile(
            name="UAE",
            capital="Abu Dhabi",
            currency="дирхам",
            currency_code="AED",
            language_context=["Arabic", "English"],
            timezone="Asia/Dubai",
        ),
    }

    CITY_TO_COUNTRY: Dict[str, Tuple[str, str]] = {
        "киев": ("Ukraine", "UA"),
        "kyiv": ("Ukraine", "UA"),
        "kiev": ("Ukraine", "UA"),
        "одесса": ("Ukraine", "UA"),
        "львов": ("Ukraine", "UA"),
        "харьков": ("Ukraine", "UA"),
        "днепр": ("Ukraine", "UA"),
        "москва": ("Россия", "RU"),
        "питер": ("Россия", "RU"),
        "петербург": ("Россия", "RU"),
        "санкт-петербург": ("Россия", "RU"),
        "самара": ("Россия", "RU"),
        "казань": ("Россия", "RU"),
        "екатеринбург": ("Россия", "RU"),
        "новосибирск": ("Россия", "RU"),
        "нижний новгород": ("Россия", "RU"),
        "сочи": ("Россия", "RU"),
        "краснодар": ("Россия", "RU"),
        "ростов": ("Россия", "RU"),
        "варшава": ("Poland", "PL"),
        "warsaw": ("Poland", "PL"),
        "краков": ("Poland", "PL"),
        "лондон": ("United Kingdom", "GB"),
        "london": ("United Kingdom", "GB"),
        "париж": ("France", "FR"),
        "paris": ("France", "FR"),
        "берлин": ("Germany", "DE"),
        "berlin": ("Germany", "DE"),
        "дубай": ("UAE", "AE"),
        "dubai": ("UAE", "AE"),
        "лос-анджелес": ("USA", "US"),
        "лос анджелес": ("USA", "US"),
        "los angeles": ("USA", "US"),
        "la": ("USA", "US"),
        "сан-франциско": ("USA", "US"),
        "san francisco": ("USA", "US"),
        "майами": ("USA", "US"),
        "miami": ("USA", "US"),
        "чикаго": ("USA", "US"),
        "chicago": ("USA", "US"),
        "нью-йорк": ("USA", "US"),
        "нью йорк": ("USA", "US"),
        "new york": ("USA", "US"),
        "минск": ("Беларусь", "BY"),
        "тбилиси": ("Georgia", "GE"),
        "ереван": ("Armenia", "AM"),
        "баку": ("Azerbaijan", "AZ"),
        "алматы": ("Kazakhstan", "KZ"),
        "астана": ("Kazakhstan", "KZ"),
        "ташкент": ("Uzbekistan", "UZ"),
        "прага": ("Czech Republic", "CZ"),
        "вена": ("Austria", "AT"),
        "рим": ("Italy", "IT"),
        "мадрид": ("Spain", "ES"),
    }

    @classmethod
    def get_country_profile(cls, name_or_city: str) -> CountryProfile:
        cleaned = (name_or_city or "").strip().lower()
        if cleaned in cls.COUNTRIES:
            return cls.COUNTRIES[cleaned]

        if cleaned in cls.CITY_TO_COUNTRY:
            c_name, _ = cls.CITY_TO_COUNTRY[cleaned]
            if c_name.lower() in cls.COUNTRIES:
                return cls.COUNTRIES[c_name.lower()]

        # Fallback generic profile
        tz = TimezoneService.resolve_timezone_for_city(cleaned)
        return CountryProfile(
            name=name_or_city.capitalize() if name_or_city else "Unknown",
            capital=name_or_city.capitalize() if name_or_city else "Unknown",
            currency="местная валюта",
            currency_code="LOC",
            language_context=["местный язык"],
            timezone=tz,
        )

    @classmethod
    def infer_country_and_code(cls, city: str, explicit_country: Optional[str] = None) -> Tuple[str, str]:
        if explicit_country and explicit_country.strip():
            c_clean = explicit_country.strip().lower()
            if c_clean in cls.COUNTRIES:
                p = cls.COUNTRIES[c_clean]
                return p.name, ("UA" if p.name == "Ukraine" else ("RU" if p.name == "Россия" else "INT"))
            return explicit_country.strip(), "INT"

        city_clean = (city or "").strip().lower()
        if city_clean in cls.CITY_TO_COUNTRY:
            return cls.CITY_TO_COUNTRY[city_clean]

        geo = TimezoneService.get_geo_info(city)
        if geo:
            c_name = "USA" if geo.country == "США" else geo.country
            return c_name, geo.country_code

        return "Россия", "RU"


class CityKnowledgeCatalog:
    """Lightweight persistent catalog of basic city facts (Section 11)."""

    CITIES: Dict[str, CityProfile] = {
        "киев": CityProfile(
            name="Kyiv",
            country="Ukraine",
            region="Kyiv City",
            language="Ukrainian",
            currency="гривна",
            currency_code="UAH",
            timezone="Europe/Kyiv",
            known_for=["historic center", "Dnieper", "Podil", "Khreshchatyk"],
            districts=["Печерск", "Подол", "Шевченковский", "Голосеево", "Оболонь", "Позняки", "Дарница"],
            landmarks=["Крещатик", "Майдан Незалежности", "Андреевский спуск", "Софийский собор", "Золотые Ворота", "Днепр"],
            common_places=["ВДНГ", "Пейзажная аллея", "Труханов остров", "парк Шевченко", "Почтовая площадь"],
            transport=["метро (красная, синяя, зеленая ветки)", "фуникулер", "скоростной трамвай", "маршрутки"],
            climate="умеренно-континентальный, мягкая зима, теплое лето",
            nearby_cities=["Бровары", "Борисполь", "Ирпень", "Буча", "Вышгород"],
        ),
        "kyiv": CityProfile(
            name="Kyiv",
            country="Ukraine",
            region="Kyiv City",
            language="Ukrainian",
            currency="гривна",
            currency_code="UAH",
            timezone="Europe/Kyiv",
            known_for=["historic center", "Dnieper", "Podil", "Khreshchatyk"],
            districts=["Печерск", "Подол", "Шевченковский", "Голосеево", "Оболонь", "Позняки", "Дарница"],
            landmarks=["Крещатик", "Майдан Незалежности", "Андреевский спуск", "Софийский собор", "Золотые Ворота", "Днепр"],
            common_places=["ВДНГ", "Пейзажная аллея", "Труханов остров", "парк Шевченко", "Почтовая площадь"],
            transport=["метро (красная, синяя, зеленая ветки)", "фуникулер", "скоростной трамвай", "маршрутки"],
            climate="умеренно-континентальный, мягкая зима, теплое лето",
            nearby_cities=["Бровары", "Борисполь", "Ирпень", "Буча", "Вышгород"],
        ),
        "москва": CityProfile(
            name="Москва",
            country="Россия",
            region="Москва",
            language="русский",
            currency="рубль",
            currency_code="RUB",
            timezone="Europe/Moscow",
            known_for=["Красная площадь", "метро", "Патриаршие пруды", "Москва-Сити"],
            districts=["Центр (ЦАО)", "Тверской", "Арбат", "Хамовники", "Замоскворечье", "Пресня", "Басманный"],
            landmarks=["Красная площадь", "Патриаршие пруды", "Парк Горького", "Зарядье", "Чистые пруды", "Москва-Сити", "Арбат"],
            common_places=["Депо", "Винзавод", "Artplay", "Flacon", "ГЭС-2", "ЦУМ"],
            transport=["метро", "МЦК", "МЦД", "каршеринг", "электробусы"],
            climate="умеренно-континентальный",
            nearby_cities=["Химки", "Мытищи", "Красногорск", "Одинцово"],
        ),
        "санкт-петербург": CityProfile(
            name="Санкт-Петербург",
            country="Россия",
            region="Санкт-Петербург",
            language="русский",
            currency="рубль",
            currency_code="RUB",
            timezone="Europe/Moscow",
            known_for=["Эрмитаж", "каналы и мосты", "Невский проспект", "белые ночи"],
            districts=["Центральный", "Петроградский", "Василеостровский", "Адмиралтейский"],
            landmarks=["Невский проспект", "Эрмитаж", "Фонтанка", "Мойка", "Исаакиевский собор", "Спас на Крови"],
            common_places=["Севкабель Порт", "Новая Голландия", "улица Рубинштейна", "Лофт Проект Этажи"],
            transport=["метро", "разводные мосты", "трамваи"],
            climate="влажный морской, ветреный, частые дожди, белые ночи летом",
            nearby_cities=["Петергоф", "Пушкин", "Кронштадт"],
        ),
        "самара": CityProfile(
            name="Самара",
            country="Россия",
            region="Самарская область",
            language="русский",
            currency="рубль",
            currency_code="RUB",
            timezone="Europe/Samara",
            known_for=["Набережная Волги", "улица Ленинградская", "Жигулевское пиво", "ракетостроение"],
            districts=["Ленинский", "Самарский", "Октябрьский", "Промышленный"],
            landmarks=["Набережная Волги", "улица Ленинградская", "площадь Славы", "Дно", "площадь Куйбышева"],
            common_places=["Струковский сад", "Вертолетка", "парк Гагарина"],
            transport=["трамваи", "автобусы", "метро"],
            climate="умеренно-континентальный, жаркое лето, холодная зима",
            nearby_cities=["Тольятти", "Новокуйбышевск", "Сызрань"],
        ),
        "варшава": CityProfile(
            name="Warsaw",
            country="Poland",
            region="Mazowieckie",
            language="Polish",
            currency="злотый",
            currency_code="PLN",
            timezone="Europe/Warsaw",
            known_for=["Stare Miasto", "Vistula boulevards", "Pałac Kultury i Nauki"],
            districts=["Śródmieście", "Mokotów", "Wola", "Praga", "Ursynów"],
            landmarks=["Stare Miasto", "Wisła", "Pałac Kultury i Nauki", "Łazienki Królewskie", "Nowy Świat"],
            common_places=["Hala Koszyki", "Elektrownia Powiśle", "Bulwary Wiślane"],
            transport=["metro (M1, M2)", "tramwaje", "autobusy", "Veturilo"],
            climate="умеренно-континентальный",
            nearby_cities=["Pruszków", "Piaseczno", "Legionowo"],
        ),
        "лондон": CityProfile(
            name="London",
            country="United Kingdom",
            region="Greater London",
            language="English",
            currency="фунт",
            currency_code="GBP",
            timezone="Europe/London",
            known_for=["Big Ben", "Thames", "Tower Bridge", "Soho"],
            districts=["Westminster", "Soho", "Kensington", "Camden", "City of London"],
            landmarks=["Big Ben", "Tower Bridge", "Hyde Park", "Buckingham Palace"],
            common_places=["Covent Garden", "Borough Market", "Soho cafes"],
            transport=["Underground (Tube)", "red buses", "Overground"],
            climate="умеренный морской, мягкий",
            nearby_cities=["Reading", "Oxford", "Cambridge"],
        ),
        "париж": CityProfile(
            name="Paris",
            country="France",
            region="Île-de-France",
            language="French",
            currency="евро",
            currency_code="EUR",
            timezone="Europe/Paris",
            known_for=["Tour Eiffel", "Louvre", "Montmartre", "Seine"],
            districts=["1er Arrondissement", "Le Marais", "Montmartre", "Saint-Germain"],
            landmarks=["Tour Eiffel", "Louvre", "Montmartre", "Seine", "Champs-Élysées"],
            common_places=["Jardin des Tuileries", "Le Marais cafes", "Canal Saint-Martin"],
            transport=["Métro", "RER", "Vélib"],
            climate="умеренный океанический",
            nearby_cities=["Versailles", "Boulogne-Billancourt"],
        ),
        "дубай": CityProfile(
            name="Dubai",
            country="UAE",
            region="Emirate of Dubai",
            language="Arabic, English",
            currency="дирхам",
            currency_code="AED",
            timezone="Asia/Dubai",
            known_for=["Burj Khalifa", "Dubai Mall", "Dubai Marina", "Palm Jumeirah"],
            districts=["Downtown", "Dubai Marina", "Business Bay", "JBR", "Palm Jumeirah"],
            landmarks=["Burj Khalifa", "Dubai Mall", "Dubai Marina", "Palm Jumeirah", "Ain Dubai"],
            common_places=["La Mer", "City Walk", "Marina Walk"],
            transport=["Dubai Metro", "такси", "трамвай"],
            climate="аридный пустынный, очень жаркое лето, теплая комфортная зима",
            nearby_cities=["Sharjah", "Abu Dhabi", "Ajman"],
        ),
        "нью-йорк": CityProfile(
            name="New York",
            country="USA",
            region="New York State",
            language="English",
            currency="доллар",
            currency_code="USD",
            timezone="America/New_York",
            known_for=["Manhattan", "Central Park", "Times Square", "Brooklyn Bridge"],
            districts=["Manhattan", "Brooklyn", "Queens", "The Bronx", "Staten Island"],
            landmarks=["Empire State Building", "Central Park", "Brooklyn Bridge", "Times Square", "SoHo"],
            common_places=["High Line", "Washington Square Park", "Chelsea Market"],
            transport=["Subway", "yellow cabs", "Citi Bike"],
            climate="влажный континентальный",
            nearby_cities=["Jersey City", "Hoboken"],
        ),
        "берлин": CityProfile(
            name="Berlin",
            country="Germany",
            region="Berlin",
            language="German",
            currency="евро",
            currency_code="EUR",
            timezone="Europe/Berlin",
            known_for=["Brandenburger Tor", "Alexanderplatz", "Kreuzberg", "Spree"],
            districts=["Mitte", "Kreuzberg", "Friedrichshain", "Charlottenburg", "Prenzlauer Berg"],
            landmarks=["Brandenburger Tor", "Reichstag", "Alexanderplatz", "East Side Gallery"],
            common_places=["Tiergarten", "Mauerpark", "Museumsinsel"],
            transport=["U-Bahn", "S-Bahn", "tram"],
            climate="умеренно-континентальный",
            nearby_cities=["Potsdam"],
        ),
    }

    # Aliases
    CITIES["питер"] = CITIES["санкт-петербург"]
    CITIES["warsaw"] = CITIES["варшава"]
    CITIES["london"] = CITIES["лондон"]
    CITIES["paris"] = CITIES["париж"]
    CITIES["dubai"] = CITIES["дубай"]
    CITIES["new york"] = CITIES["нью-йорк"]
    CITIES["berlin"] = CITIES["берлин"]

    @classmethod
    def get_city_profile(cls, city_name: str) -> CityProfile:
        cleaned = (city_name or "").strip().lower()
        if cleaned in cls.CITIES:
            return cls.CITIES[cleaned]

        for k, prof in cls.CITIES.items():
            if k in cleaned or cleaned in k:
                return prof

        # Dynamically build fallback
        tz = TimezoneService.resolve_timezone_for_city(city_name)
        country_name, _ = CountryKnowledgeCatalog.infer_country_and_code(city_name)
        country_prof = CountryKnowledgeCatalog.get_country_profile(country_name)
        return CityProfile(
            name=city_name.capitalize() if city_name else "Unknown",
            country=country_prof.name,
            region=city_name.capitalize() if city_name else "Region",
            language=country_prof.language_context[0] if country_prof.language_context else "русский",
            currency=country_prof.currency,
            currency_code=country_prof.currency_code,
            timezone=tz,
            known_for=[f"город {city_name}"],
            districts=["центр", "спальные районы"],
            landmarks=["главная площадь", "парк", "набережная"],
            common_places=["центр города", "уютные кофейни"],
            transport=["общественный транспорт", "такси"],
            climate="умеренный",
            nearby_cities=[],
        )


class LocationContextService:
    """
    Dedicated Service for Location, Time, and Geographic Context (Section 20).
    Architecture:
    PersonaProfile -> LocationContext -> TimezoneResolver -> CurrentLocalTime -> City/Country Knowledge -> Conversation Context -> LLM
    """

    @classmethod
    def get_persona_location(cls, persona: Any) -> PersonaLocationProfile:
        """
        Builds structured persistent location profile strictly from persona (Section 1).
        Supports temporary trips (Section 15).
        """
        base_city = getattr(persona, "city", None) or "Москва"
        base_country = getattr(persona, "country", None)
        base_country_code = getattr(persona, "country_code", None)

        if not base_country or not base_country_code:
            inferred_country, inferred_code = CountryKnowledgeCatalog.infer_country_and_code(base_city, base_country)
            base_country = base_country or inferred_country
            base_country_code = base_country_code or inferred_code

        base_tz = getattr(persona, "timezone_name", None) or TimezoneService.resolve_timezone_for_city(base_city)

        # Check temporary trip (Section 15)
        trip_city = getattr(persona, "trip_city", None)
        if trip_city and trip_city.strip():
            trip_city = trip_city.strip()
            trip_country = getattr(persona, "trip_country", None)
            trip_code = getattr(persona, "trip_country_code", None)
            if not trip_country or not trip_code:
                inferred_tc, inferred_tcode = CountryKnowledgeCatalog.infer_country_and_code(trip_city, trip_country)
                trip_country = trip_country or inferred_tc
                trip_code = trip_code or inferred_tcode

            trip_tz = getattr(persona, "trip_timezone", None) or TimezoneService.resolve_timezone_for_city(trip_city)
            return PersonaLocationProfile(
                city=trip_city,
                country=trip_country,
                country_code=trip_code,
                timezone=trip_tz,
                temporary=True,
                home_location={
                    "city": base_city,
                    "country": base_country,
                    "country_code": base_country_code,
                    "timezone": base_tz,
                },
            )

        return PersonaLocationProfile(
            city=base_city,
            country=base_country,
            country_code=base_country_code,
            timezone=base_tz,
            temporary=False,
            home_location=None,
        )

    @classmethod
    def set_temporary_trip(
        cls,
        persona: Any,
        trip_city: str,
        trip_country: Optional[str] = None,
        trip_timezone: Optional[str] = None,
    ) -> PersonaLocationProfile:
        """Sets a temporary trip location for the persona (Section 15)."""
        persona.trip_city = trip_city.strip()
        if trip_country:
            persona.trip_country = trip_country.strip()
            _, code = CountryKnowledgeCatalog.infer_country_and_code(trip_city, trip_country)
            persona.trip_country_code = code
        else:
            c, code = CountryKnowledgeCatalog.infer_country_and_code(trip_city)
            persona.trip_country = c
            persona.trip_country_code = code

        persona.trip_timezone = trip_timezone or TimezoneService.resolve_timezone_for_city(trip_city)
        return cls.get_persona_location(persona)

    @classmethod
    def clear_temporary_trip(cls, persona: Any) -> PersonaLocationProfile:
        """Clears temporary trip and returns persona to permanent home location (Section 15)."""
        persona.trip_city = None
        persona.trip_country = None
        persona.trip_country_code = None
        persona.trip_timezone = None
        return cls.get_persona_location(persona)

    @classmethod
    def get_persona_routine(cls, persona: Any) -> PersonaRoutine:
        """Extracts optional routine/schedule for the persona (Section 10)."""
        wake = getattr(persona, "wake_time", None) or getattr(persona, "sleep_end", "09:00")
        sleep = getattr(persona, "sleep_time", None) or getattr(persona, "sleep_start", "23:00")
        work_s = getattr(persona, "work_start", "10:00")
        work_e = getattr(persona, "work_end", "18:00")
        return PersonaRoutine(
            wake_time=wake or "09:00",
            sleep_time=sleep or "23:00",
            work_start=work_s or "10:00",
            work_end=work_e or "18:00",
        )

    @classmethod
    def calculate_day_period(cls, local_time_or_dt: Union[datetime, time, int, float]) -> str:
        """
        Calculates conversational day period (Section 5):
        05:00–10:59 → morning
        11:00–16:59 → daytime
        17:00–21:59 → evening
        22:00–00:59 → late evening
        01:00–04:59 → night
        """
        if isinstance(local_time_or_dt, datetime):
            hour = local_time_or_dt.hour
        elif isinstance(local_time_or_dt, time):
            hour = local_time_or_dt.hour
        elif isinstance(local_time_or_dt, (int, float)):
            hour = int(local_time_or_dt)
        else:
            return "daytime"

        if 5 <= hour < 11:
            return "morning"
        elif 11 <= hour < 17:
            return "daytime"
        elif 17 <= hour < 22:
            return "evening"
        elif hour >= 22 or hour == 0:
            return "late evening"
        else:  # 1 <= hour < 5
            return "night"

    @classmethod
    def get_user_timezone_priority(cls, contact: Optional[Any]) -> Tuple[str, Optional[str]]:
        """
        Determines user timezone in strict priority order (Section 8):
        1. explicitly configured user timezone (contact.detected_timezone);
        2. reliable profile/location information (contact.detected_city);
        3. conversation context;
        4. otherwise 'unknown'.
        NEVER guess from language (Russian ≠ Russia, Ukrainian ≠ Ukraine) or IP.
        Returns: (timezone_name_or_unknown, detected_city_or_none)
        """
        if not contact:
            return "unknown", None

        # Priority 1: explicitly configured user timezone
        explicit_tz = getattr(contact, "detected_timezone", None)
        city = getattr(contact, "detected_city", None)
        if explicit_tz and explicit_tz.strip():
            try:
                pytz.timezone(explicit_tz.strip())
                return explicit_tz.strip(), city
            except Exception:
                pass

        # Priority 2: reliable city/location information
        if city and city.strip():
            resolved = TimezoneService.resolve_timezone_for_city(city.strip())
            if resolved:
                return resolved, city

        # Fallback: strictly unknown (never infer from language)
        return "unknown", None

    @classmethod
    def build_time_context(
        cls,
        persona: Any,
        contact: Optional[Any] = None,
        ref_utc_dt: Optional[datetime] = None,
    ) -> TimeContext:
        """
        Builds temporary ephemeral TimeContext object before generating a response (Sections 4, 5, 7).
        Calculates dynamic local time in persona's active timezone without storing it as memory.
        """
        loc = cls.get_persona_location(persona)
        tz_name = loc.timezone

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Europe/Moscow")
            tz_name = "Europe/Moscow"

        ref = ref_utc_dt or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = pytz.utc.localize(ref)

        local_dt = ref.astimezone(tz)
        local_time_str = local_dt.strftime("%H:%M")
        local_date_str = local_dt.strftime("%Y-%m-%d")

        wday_idx = local_dt.weekday()
        day_of_week_eng = ENGLISH_WEEKDAYS.get(wday_idx, "Saturday")
        month_eng = ENGLISH_MONTHS.get(local_dt.month, "November")
        formatted_date_eng = f"{day_of_week_eng}, {month_eng} {local_dt.day:02d}, {local_dt.year}"
        # Format matching "Saturday, November 14, 2026" (unpadded day if single digit, standard strftime)
        formatted_date_std = f"{day_of_week_eng}, {month_eng} {local_dt.day}, {local_dt.year}"

        # UTC offset formatted as "+02:00" or "-05:00"
        utc_off = local_dt.utcoffset()
        tot_sec = int(utc_off.total_seconds()) if utc_off else 0
        sign = "+" if tot_sec >= 0 else "-"
        hours = abs(tot_sec) // 3600
        mins = (abs(tot_sec) % 3600) // 60
        utc_offset_str = f"{sign}{hours:02d}:{mins:02d}"

        day_period = cls.calculate_day_period(local_dt)
        is_weekend = wday_idx in (5, 6)

        # User Timezone Resolution (Sections 7 & 8)
        user_tz, user_city = cls.get_user_timezone_priority(contact)
        user_local_time: Optional[str] = None
        user_utc_offset_str: Optional[str] = None
        time_diff_hours: Optional[float] = None

        if user_tz != "unknown":
            try:
                u_tz = pytz.timezone(user_tz)
                u_local_dt = ref.astimezone(u_tz)
                user_local_time = u_local_dt.strftime("%H:%M")

                u_utc_off = u_local_dt.utcoffset()
                u_tot_sec = int(u_utc_off.total_seconds()) if u_utc_off else 0
                u_sign = "+" if u_tot_sec >= 0 else "-"
                u_hours = abs(u_tot_sec) // 3600
                u_mins = (abs(u_tot_sec) % 3600) // 60
                user_utc_offset_str = f"{u_sign}{u_hours:02d}:{u_mins:02d}"
                time_diff_hours = (u_tot_sec - tot_sec) / 3600.0
            except Exception:
                user_tz = "unknown"

        # Routine context
        routine = cls.get_persona_routine(persona)
        routine_phase = None
        hour_float = local_dt.hour + local_dt.minute / 60.0
        try:
            sleep_h = int(routine.sleep_time.split(":")[0])
            wake_h = int(routine.wake_time.split(":")[0])
            if wake_h <= hour_float < wake_h + 1.0:
                routine_phase = "just_woke_up"
            elif routine.work_start and routine.work_end:
                ws = int(routine.work_start.split(":")[0])
                we = int(routine.work_end.split(":")[0])
                if ws <= hour_float < we:
                    routine_phase = "working"
        except Exception:
            pass

        return TimeContext(
            persona_local_time=local_time_str,
            persona_local_date=local_date_str,
            day_of_week=day_of_week_eng,
            timezone=tz_name,
            utc_offset=utc_offset_str,
            day_period=day_period,
            is_weekend=is_weekend,
            formatted_date_eng=formatted_date_std,
            raw_local_dt=local_dt,
            user_timezone=user_tz,
            user_local_time=user_local_time,
            user_utc_offset=user_utc_offset_str,
            time_diff_hours=time_diff_hours,
            routine_phase=routine_phase,
        )

    @classmethod
    def build_response_context_block(
        cls,
        persona: Any,
        contact: Optional[Any] = None,
        ref_utc_dt: Optional[datetime] = None,
    ) -> str:
        """
        Builds the compact response context block required by Section 17:
        PERSONA LOCATION: ...
        PERSONA TIMEZONE: ...
        PERSONA LOCAL TIME: ...
        DATE: ...
        DAY PERIOD: ...
        WEEKEND: ...
        USER TIMEZONE: ...
        CITY CONTEXT: ...
        """
        loc = cls.get_persona_location(persona)
        time_ctx = cls.build_time_context(persona, contact, ref_utc_dt)
        city_prof = CityKnowledgeCatalog.get_city_profile(loc.city)
        routine = cls.get_persona_routine(persona)

        weekend_str = "yes" if time_ctx.is_weekend else "no"

        # Base technical block (Section 17 exact format)
        block = (
            f"PERSONA LOCATION:\n"
            f"{loc.city}, {loc.country}\n\n"
            f"PERSONA TIMEZONE:\n"
            f"{loc.timezone}\n\n"
            f"PERSONA LOCAL TIME:\n"
            f"{time_ctx.persona_local_time}\n\n"
            f"DATE:\n"
            f"{time_ctx.formatted_date_eng}\n\n"
            f"DAY PERIOD:\n"
            f"{time_ctx.day_period}\n\n"
            f"WEEKEND:\n"
            f"{weekend_str}\n\n"
            f"USER TIMEZONE:\n"
            f"{time_ctx.user_timezone}\n\n"
            f"CITY CONTEXT:\n"
            f"{loc.city}"
        )

        # Add optional city background context (landmarks, districts, transport, routine)
        landmarks_preview = ", ".join(city_prof.landmarks[:4]) if city_prof.landmarks else "центр"
        districts_preview = ", ".join(city_prof.districts[:4]) if city_prof.districts else "центр"

        user_clock_note = ""
        if time_ctx.user_timezone != "unknown" and time_ctx.user_local_time:
            diff_note = (
                f" (разница {time_ctx.time_diff_hours:+.0f}ч)"
                if time_ctx.time_diff_hours is not None and time_ctx.time_diff_hours != 0
                else " (один часовой пояс с тобой)"
            )
            user_clock_note = (
                f"\n• ЧАСЫ СОБЕСЕДНИКА: часовой пояс {time_ctx.user_timezone}, "
                f"время собеседника: {time_ctx.user_local_time}{diff_note}."
            )

        trip_note = ""
        if loc.temporary and loc.home_location:
            trip_note = (
                f"\n• ПОЕЗДКА: Сейчас временно находишься в городе {loc.city} ({loc.country})! "
                f"Твой постоянный дом — {loc.home_location['city']}."
            )

        routine_note = (
            f"\n• РАСПИСАНИЕ И РЕЖИМ ДНЯ: подъем около {routine.wake_time}, сон около {routine.sleep_time}."
        )

        extra_info = (
            f"\n\n[РЕАЛЬНЫЙ ГОРОДСКОЙ И ВРЕМЕННОЙ КОНТЕКСТ]:\n"
            f"• Достопримечательности и ориентиры твоего города: {landmarks_preview}\n"
            f"• Основные районы: {districts_preview}\n"
            f"• Валюта: {city_prof.currency} ({city_prof.currency_code}){trip_note}{routine_note}{user_clock_note}\n"
            f"• КАТЕГОРИЧЕСКОЕ ПРАВИЛО: Используй факты о времени и городе естественно, "
            f"без навязчивых энциклопедических справок. Не путай свое время со временем собеседника!"
        )

        return block + extra_info

    @classmethod
    def resolve_temporal_phrase(
        cls,
        phrase: str,
        tz_name: str = "Europe/Moscow",
        ref_utc_dt: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Resolves relative temporal phrases into absolute ISO timestamps with timezone offsets (Section 18).
        Examples:
        - 'завтра в 10' -> '2026-11-15T10:00:00+02:00'
        - 'завтра вечером' -> '2026-11-15T19:00:00+02:00'
        - 'через два часа' -> '2026-11-14T19:17:17+02:00'
        - 'в субботу в 18:00' -> '2026-11-21T18:00:00+02:00'
        - 'послезавтра' -> '2026-11-16T12:00:00+02:00'
        """
        if not phrase or not phrase.strip():
            return None

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Europe/Moscow")
            tz_name = "Europe/Moscow"

        ref = ref_utc_dt or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = pytz.utc.localize(ref)

        local_now = ref.astimezone(tz)
        cleaned = phrase.strip().lower()

        # Word numbers mapping
        word_numbers = {
            "один": 1, "одну": 1, "час": 1,
            "два": 2, "две": 2,
            "три": 3,
            "четыре": 4,
            "пять": 5,
            "полчаса": 30,
        }

        # 1. Check relative days: "завтра", "послезавтра", "вчера", "сегодня"
        day_offset = None
        matched_day_word = None
        if re.search(r"\b(?:послезавтра|day\s+after\s+tomorrow)\b", cleaned):
            day_offset = 2
            matched_day_word = "послезавтра"
        elif re.search(r"\b(?:завтра|tomorrow)\b", cleaned):
            day_offset = 1
            matched_day_word = "завтра"
        elif re.search(r"\b(?:вчера|yesterday)\b", cleaned):
            day_offset = -1
            matched_day_word = "вчера"
        elif re.search(r"\b(?:сегодня|today)\b", cleaned):
            day_offset = 0
            matched_day_word = "сегодня"

        # 2. Check next weekday: "в субботу", "в воскресенье", "в следующий понедельник"
        weekday_map = {
            "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
            "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5, "воскресенье": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
        }
        m_wday = re.search(r"\b(?:в|во|next|следующ\w*)\s+([а-яёa-z]+)\b", cleaned)
        if m_wday and m_wday.group(1) in weekday_map and day_offset is None:
            target_w = weekday_map[m_wday.group(1)]
            curr_w = local_now.weekday()
            days_ahead = (target_w - curr_w) % 7
            if days_ahead == 0:
                days_ahead = 7
            day_offset = days_ahead
            matched_day_word = f"в {m_wday.group(1)}"

        # 3. Check "через N часов" / "через два часа" / "через полчаса"
        m_rel_time = re.search(r"\b(?:через|in)\s+(\d+|полчаса|два|две|три|четыре|пять|час|одну|один)\s*(?:час\w*|минут\w*|hours?|mins?)?\b", cleaned)
        if m_rel_time and day_offset is None:
            raw_val = m_rel_time.group(1)
            num_val = int(raw_val) if raw_val.isdigit() else word_numbers.get(raw_val, 1)
            if "минут" in cleaned or "мин" in cleaned or "полчаса" in cleaned:
                target_dt = local_now + timedelta(minutes=num_val)
            else:
                target_dt = local_now + timedelta(hours=num_val)

            iso_res = cls._format_iso(target_dt)
            return {
                "original_phrase": phrase,
                "matched_expression": m_rel_time.group(0),
                "resolved_timestamp": iso_res,
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_time": target_dt.strftime("%H:%M"),
                "day_of_week": RUSSIAN_WEEKDAYS.get(target_dt.weekday(), ""),
                "timezone": tz_name,
            }

        # If a day offset was found (e.g. завтра, в субботу, послезавтра)
        if day_offset is not None:
            target_date = local_now.date() + timedelta(days=day_offset)

            # Check explicit hour/minute: e.g. "в 10", "в 10:00", "в 18:30"
            m_time = re.search(r"\b(?:в|at)\s+(\d{1,2})(?::(\d{2}))?\b", cleaned)
            hour = 12
            minute = 0
            if m_time:
                h_val = int(m_time.group(1))
                m_val = int(m_time.group(2)) if m_time.group(2) else 0
                if 0 <= h_val <= 23 and 0 <= m_val <= 59:
                    hour = h_val
                    minute = m_val
                    if "вечера" in cleaned and hour < 12:
                        hour += 12
                    elif "утра" in cleaned and hour == 12:
                        hour = 0
            else:
                # Qualitative time of day
                if "вечером" in cleaned or "вечер" in cleaned:
                    hour = 19
                elif "утром" in cleaned or "утро" in cleaned:
                    hour = 10
                elif "днем" in cleaned or "день" in cleaned:
                    hour = 14
                elif "ночью" in cleaned:
                    hour = 23

            target_naive = datetime(target_date.year, target_date.month, target_date.day, hour, minute, 0)
            target_dt = tz.localize(target_naive)
            iso_res = cls._format_iso(target_dt)

            return {
                "original_phrase": phrase,
                "matched_expression": matched_day_word or phrase,
                "resolved_timestamp": iso_res,
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_time": target_dt.strftime("%H:%M"),
                "day_of_week": RUSSIAN_WEEKDAYS.get(target_dt.weekday(), ""),
                "timezone": tz_name,
                "offset_days": day_offset,
            }

        return None

    @classmethod
    def _format_iso(cls, dt: datetime) -> str:
        """Formats datetime into ISO 8601 with colon timezone offset (e.g. 2026-11-15T10:00:00+02:00)."""
        utc_off = dt.utcoffset()
        tot_sec = int(utc_off.total_seconds()) if utc_off else 0
        sign = "+" if tot_sec >= 0 else "-"
        hours = abs(tot_sec) // 3600
        mins = (abs(tot_sec) % 3600) // 60
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + f"{sign}{hours:02d}:{mins:02d}"
