import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import pytz

logger = logging.getLogger(__name__)


@dataclass
class GeoLocationInfo:
    """Structured canonical geographic and timezone descriptor."""
    canonical_city: str
    country: str
    country_code: str
    region: str
    timezone: str
    aliases: List[str] = field(default_factory=list)


# Canonical country dictionary with country codes and primary timezones
KNOWN_COUNTRIES: Dict[str, Tuple[str, str, str]] = {
    # alias.lower(): (canonical_country_name, country_code, default_tz)
    "usa": ("США", "US", "America/New_York"),
    "сша": ("США", "US", "America/New_York"),
    "us": ("США", "US", "America/New_York"),
    "u.s.": ("США", "US", "America/New_York"),
    "u.s.a.": ("США", "US", "America/New_York"),
    "united states": ("США", "US", "America/New_York"),
    "united states of america": ("США", "US", "America/New_York"),
    "соединенные штаты": ("США", "US", "America/New_York"),
    "соединенные штаты америки": ("США", "US", "America/New_York"),
    "штаты": ("США", "US", "America/New_York"),
    "америка": ("США", "US", "America/New_York"),
    "uk": ("Великобритания", "GB", "Europe/London"),
    "u.k.": ("Великобритания", "GB", "Europe/London"),
    "great britain": ("Великобритания", "GB", "Europe/London"),
    "united kingdom": ("Великобритания", "GB", "Europe/London"),
    "великобритания": ("Великобритания", "GB", "Europe/London"),
    "британия": ("Великобритания", "GB", "Europe/London"),
    "англия": ("Великобритания", "GB", "Europe/London"),
    "england": ("Великобритания", "GB", "Europe/London"),
    "canada": ("Канада", "CA", "America/Toronto"),
    "канада": ("Канада", "CA", "America/Toronto"),
    "россия": ("Россия", "RU", "Europe/Moscow"),
    "рф": ("Россия", "RU", "Europe/Moscow"),
    "russia": ("Россия", "RU", "Europe/Moscow"),
    "russian federation": ("Россия", "RU", "Europe/Moscow"),
    "украина": ("Украина", "UA", "Europe/Kyiv"),
    "ukraine": ("Украина", "UA", "Europe/Kyiv"),
    "беларусь": ("Беларусь", "BY", "Europe/Minsk"),
    "belarus": ("Беларусь", "BY", "Europe/Minsk"),
    "белоруссия": ("Беларусь", "BY", "Europe/Minsk"),
    "казахстан": ("Казахстан", "KZ", "Asia/Almaty"),
    "kazakhstan": ("Казахстан", "KZ", "Asia/Almaty"),
    "германия": ("Германия", "DE", "Europe/Berlin"),
    "germany": ("Германия", "DE", "Europe/Berlin"),
    "deutschland": ("Германия", "DE", "Europe/Berlin"),
    "франция": ("Франция", "FR", "Europe/Paris"),
    "france": ("Франция", "FR", "Europe/Paris"),
    "испания": ("Испания", "ES", "Europe/Madrid"),
    "spain": ("Испания", "ES", "Europe/Madrid"),
    "италия": ("Италия", "IT", "Europe/Rome"),
    "italy": ("Италия", "IT", "Europe/Rome"),
    "польша": ("Польша", "PL", "Europe/Warsaw"),
    "poland": ("Польша", "PL", "Europe/Warsaw"),
    "оаэ": ("ОАЭ", "AE", "Asia/Dubai"),
    "uae": ("ОАЭ", "AE", "Asia/Dubai"),
    "эмираты": ("ОАЭ", "AE", "Asia/Dubai"),
    "united arab emirates": ("ОАЭ", "AE", "Asia/Dubai"),
    "турция": ("Турция", "TR", "Europe/Istanbul"),
    "turkey": ("Турция", "TR", "Europe/Istanbul"),
    "грузия": ("Грузия", "GE", "Asia/Tbilisi"),
    "georgia": ("Грузия", "GE", "Asia/Tbilisi"),
    "армения": ("Армения", "AM", "Asia/Yerevan"),
    "armenia": ("Армения", "AM", "Asia/Yerevan"),
    "япония": ("Япония", "JP", "Asia/Tokyo"),
    "japan": ("Япония", "JP", "Asia/Tokyo"),
    "таиланд": ("Таиланд", "TH", "Asia/Bangkok"),
    "thailand": ("Таиланд", "TH", "Asia/Bangkok"),
    "тайланд": ("Таиланд", "TH", "Asia/Bangkok"),
    "индонезия": ("Индонезия", "ID", "Asia/Makassar"),
    "indonesia": ("Индонезия", "ID", "Asia/Makassar"),
    "австралия": ("Австралия", "AU", "Australia/Sydney"),
    "australia": ("Австралия", "AU", "Australia/Sydney"),
    "чехия": ("Чехия", "CZ", "Europe/Prague"),
    "czech": ("Чехия", "CZ", "Europe/Prague"),
    "австрия": ("Австрия", "AT", "Europe/Vienna"),
    "austria": ("Австрия", "AT", "Europe/Vienna"),
    "нидерланды": ("Нидерланды", "NL", "Europe/Amsterdam"),
    "netherlands": ("Нидерланды", "NL", "Europe/Amsterdam"),
    "голландия": ("Нидерланды", "NL", "Europe/Amsterdam"),
    "швейцария": ("Швейцария", "CH", "Europe/Zurich"),
    "switzerland": ("Швейцария", "CH", "Europe/Zurich"),
    "швеция": ("Швеция", "SE", "Europe/Stockholm"),
    "sweden": ("Швеция", "SE", "Europe/Stockholm"),
    "норвегия": ("Норвегия", "NO", "Europe/Oslo"),
    "norway": ("Норвегия", "NO", "Europe/Oslo"),
    "финляндия": ("Финляндия", "FI", "Europe/Helsinki"),
    "finland": ("Финляндия", "FI", "Europe/Helsinki"),
    "дания": ("Дания", "DK", "Europe/Copenhagen"),
    "denmark": ("Дания", "DK", "Europe/Copenhagen"),
    "португалия": ("Португалия", "PT", "Europe/Lisbon"),
    "portugal": ("Португалия", "PT", "Europe/Lisbon"),
}

# Comprehensive US States with abbreviations, English names, Russian names, and default IANA timezones
US_STATES_DATA: Dict[str, Tuple[str, str, str]] = {
    # code: (english_name, russian_name, iana_tz)
    "AL": ("Alabama", "Алабама", "America/Chicago"),
    "AK": ("Alaska", "Аляска", "America/Anchorage"),
    "AZ": ("Arizona", "Аризона", "America/Phoenix"),
    "AR": ("Arkansas", "Арканзас", "America/Chicago"),
    "CA": ("California", "Калифорния", "America/Los_Angeles"),
    "CO": ("Colorado", "Колорадо", "America/Denver"),
    "CT": ("Connecticut", "Коннектикут", "America/New_York"),
    "DE": ("Delaware", "Делавэр", "America/New_York"),
    "FL": ("Florida", "Флорида", "America/New_York"),
    "GA": ("Georgia", "Джорджия", "America/New_York"),
    "HI": ("Hawaii", "Гавайи", "Pacific/Honolulu"),
    "ID": ("Idaho", "Айдахо", "America/Boise"),
    "IL": ("Illinois", "Иллинойс", "America/Chicago"),
    "IN": ("Indiana", "Индиана", "America/Indiana/Indianapolis"),
    "IA": ("Iowa", "Айова", "America/Chicago"),
    "KS": ("Kansas", "Канзас", "America/Chicago"),
    "KY": ("Kentucky", "Кентукки", "America/New_York"),
    "LA": ("Louisiana", "Луизиана", "America/Chicago"),
    "ME": ("Maine", "Мэн", "America/New_York"),
    "MD": ("Maryland", "Мэриленд", "America/New_York"),
    "MA": ("Massachusetts", "Массачусетс", "America/New_York"),
    "MI": ("Michigan", "Мичиган", "America/Detroit"),
    "MN": ("Minnesota", "Миннесота", "America/Chicago"),
    "MS": ("Mississippi", "Миссисипи", "America/Chicago"),
    "MO": ("Missouri", "Миссури", "America/Chicago"),
    "MT": ("Montana", "Монтана", "America/Denver"),
    "NE": ("Nebraska", "Небраска", "America/Chicago"),
    "NV": ("Nevada", "Невада", "America/Los_Angeles"),
    "NH": ("New Hampshire", "Нью-Гэмпшир", "America/New_York"),
    "NJ": ("New Jersey", "Нью-Джерси", "America/New_York"),
    "NM": ("New Mexico", "Нью-Мексико", "America/Denver"),
    "NY": ("New York", "Нью-Йорк", "America/New_York"),
    "NC": ("North Carolina", "Северная Каролина", "America/New_York"),
    "ND": ("North Dakota", "Северная Дакота", "America/Chicago"),
    "OH": ("Ohio", "Огайо", "America/New_York"),
    "OK": ("Oklahoma", "Оклахома", "America/Chicago"),
    "OR": ("Oregon", "Орегон", "America/Los_Angeles"),
    "PA": ("Pennsylvania", "Пенсильвания", "America/New_York"),
    "RI": ("Rhode Island", "Род-Айленд", "America/New_York"),
    "SC": ("South Carolina", "Южная Каролина", "America/New_York"),
    "SD": ("South Dakota", "Южная Дакота", "America/Chicago"),
    "TN": ("Tennessee", "Теннесси", "America/Chicago"),
    "TX": ("Texas", "Техас", "America/Chicago"),
    "UT": ("Utah", "Юта", "America/Denver"),
    "VT": ("Vermont", "Вермонт", "America/New_York"),
    "VA": ("Virginia", "Вирджиния", "America/New_York"),
    "WA": ("Washington", "Вашингтон", "America/Los_Angeles"),
    "WV": ("West Virginia", "Западная Вирджиния", "America/New_York"),
    "WI": ("Wisconsin", "Висконсин", "America/Chicago"),
    "WY": ("Wyoming", "Вайоминг", "America/Denver"),
    "DC": ("District of Columbia", "Округ Колумбия", "America/New_York"),
}

STATE_LOOKUP_MAP: Dict[str, Tuple[str, str, str]] = {}

def _build_state_index():
    for code, (eng, ru, tz) in US_STATES_DATA.items():
        val = (eng, ru, tz)
        STATE_LOOKUP_MAP[code.lower()] = val
        STATE_LOOKUP_MAP[eng.lower()] = val
        STATE_LOOKUP_MAP[eng.lower().replace("-", " ")] = val
        STATE_LOOKUP_MAP[ru.lower()] = val
        STATE_LOOKUP_MAP[ru.lower().replace("-", " ")] = val

_build_state_index()


# Comprehensive database of cities, countries, regions, and canonical IANA timezones.
# Covers CIS, Europe, North America, Middle East, Asia, and Oceania.
# Supports automatic Daylight Saving Time (DST) transitions via IANA/pytz.
CANONICAL_GEO_DATABASE: List[GeoLocationInfo] = [
    # --- United States & Canada ---
    GeoLocationInfo(
        canonical_city="Лос-Анджелес",
        country="США",
        country_code="US",
        region="Калифорния",
        timezone="America/Los_Angeles",
        aliases=[
            "лос-анджелес", "лос анджелес", "лосанджелес", "los angeles", "los-angeles", "losangeles",
            "la", "л.а.", "уэст-голливуд", "уэст голливуд", "уест-голливуд", "уест голливуд", "west hollywood",
            "голливуд", "hollywood", "беверли-хиллз", "beverly hills", "санта-моника", "santa monica",
        ],
    ),
    GeoLocationInfo(
        canonical_city="Сан-Франциско",
        country="США",
        country_code="US",
        region="Калифорния",
        timezone="America/Los_Angeles",
        aliases=["сан-франциско", "сан франциско", "санфранциско", "san francisco", "sf", "сф"],
    ),
    GeoLocationInfo(
        canonical_city="Нью-Йорк",
        country="США",
        country_code="US",
        region="Нью-Йорк",
        timezone="America/New_York",
        aliases=[
            "нью-йорк", "нью йорк", "ньюйорк", "new york", "new-york", "newyork", "nyc",
            "манхэттен", "манхеттен", "manhattan", "бруклин", "brooklyn", "сохо", "soho", "квиинс", "queens",
        ],
    ),
    GeoLocationInfo(
        canonical_city="Майами",
        country="США",
        country_code="US",
        region="Флорида",
        timezone="America/New_York",
        aliases=["майами", "miami", "майами бич", "майами-бич", "miami beach", "флорида", "florida"],
    ),
    GeoLocationInfo(
        canonical_city="Вашингтон",
        country="США",
        country_code="US",
        region="Округ Колумбия",
        timezone="America/New_York",
        aliases=["вашингтон", "washington", "washington dc", "вашингтон дс"],
    ),
    GeoLocationInfo(
        canonical_city="Бостон",
        country="США",
        country_code="US",
        region="Массачусетс",
        timezone="America/New_York",
        aliases=["бостон", "boston", "массачусетс"],
    ),
    GeoLocationInfo(
        canonical_city="Чикаго",
        country="США",
        country_code="US",
        region="Иллинойс",
        timezone="America/Chicago",
        aliases=["чикаго", "chicago", "иллинойс", "illinois"],
    ),
    GeoLocationInfo(
        canonical_city="Портленд",
        country="США",
        country_code="US",
        region="Орегон",
        timezone="America/Los_Angeles",
        aliases=["портленд", "portland", "portland oregon", "portland or", "портленд орегон", "портленд сша", "орегон", "oregon"],
    ),
    GeoLocationInfo(
        canonical_city="Шарлотт",
        country="США",
        country_code="US",
        region="Северная Каролина",
        timezone="America/New_York",
        aliases=["шарлотт", "шарлотта", "charlotte", "charlotte nc", "шарлотт сша", "северная каролина", "north carolina"],
    ),
    GeoLocationInfo(
        canonical_city="Сиэтл",
        country="США",
        country_code="US",
        region="Вашингтон",
        timezone="America/Los_Angeles",
        aliases=["сиэтл", "сиетл", "seattle", "seattle wa", "вашингтон штат", "сиэтл сша"],
    ),
    GeoLocationInfo(
        canonical_city="Остин",
        country="США",
        country_code="US",
        region="Техас",
        timezone="America/Chicago",
        aliases=["остин", "austin", "austin tx", "остин техас"],
    ),
    GeoLocationInfo(
        canonical_city="Хьюстон",
        country="США",
        country_code="US",
        region="Техас",
        timezone="America/Chicago",
        aliases=["хьюстон", "houston", "houston tx", "хьюстон техас"],
    ),
    GeoLocationInfo(
        canonical_city="Даллас",
        country="США",
        country_code="US",
        region="Техас",
        timezone="America/Chicago",
        aliases=["даллас", "dallas", "dallas tx", "даллас техас"],
    ),
    GeoLocationInfo(
        canonical_city="Сан-Антонио",
        country="США",
        country_code="US",
        region="Техас",
        timezone="America/Chicago",
        aliases=["сан-антонио", "сан антонио", "san antonio", "san antonio tx"],
    ),
    GeoLocationInfo(
        canonical_city="Сан-Диего",
        country="США",
        country_code="US",
        region="Калифорния",
        timezone="America/Los_Angeles",
        aliases=["сан-диего", "сан диего", "san diego", "san diego ca"],
    ),
    GeoLocationInfo(
        canonical_city="Сан-Хосе",
        country="США",
        country_code="US",
        region="Калифорния",
        timezone="America/Los_Angeles",
        aliases=["сан-хосе", "сан хосе", "san jose", "кремниевая долина", "silicon valley"],
    ),
    GeoLocationInfo(
        canonical_city="Филадельфия",
        country="США",
        country_code="US",
        region="Пенсильвания",
        timezone="America/New_York",
        aliases=["филадельфия", "philadelphia", "philly", "филли", "пенсильвания", "pennsylvania"],
    ),
    GeoLocationInfo(
        canonical_city="Атланта",
        country="США",
        country_code="US",
        region="Джорджия",
        timezone="America/New_York",
        aliases=["атланта", "atlanta", "atlanta ga", "джорджия", "georgia us"],
    ),
    GeoLocationInfo(
        canonical_city="Финикс",
        country="США",
        country_code="US",
        region="Аризона",
        timezone="America/Phoenix",
        aliases=["финикс", "феникс", "phoenix", "phoenix az", "аризона", "arizona"],
    ),
    GeoLocationInfo(
        canonical_city="Денвер",
        country="США",
        country_code="US",
        region="Колорадо",
        timezone="America/Denver",
        aliases=["денвер", "denver", "denver co", "колорадо", "colorado"],
    ),
    GeoLocationInfo(
        canonical_city="Лас-Вегас",
        country="США",
        country_code="US",
        region="Невада",
        timezone="America/Los_Angeles",
        aliases=["лас-вегас", "лас вегас", "вегас", "las vegas", "vegas", "невада", "nevada"],
    ),
    GeoLocationInfo(
        canonical_city="Детройт",
        country="США",
        country_code="US",
        region="Мичиган",
        timezone="America/Detroit",
        aliases=["детройт", "detroit", "мичиган", "michigan"],
    ),
    GeoLocationInfo(
        canonical_city="Нэшвилл",
        country="США",
        country_code="US",
        region="Теннесси",
        timezone="America/Chicago",
        aliases=["нэшвилл", "нешвилл", "nashville", "теннесси", "tennessee"],
    ),
    GeoLocationInfo(
        canonical_city="Орландо",
        country="США",
        country_code="US",
        region="Флорида",
        timezone="America/New_York",
        aliases=["орландо", "orlando", "диснеи"],
    ),
    GeoLocationInfo(
        canonical_city="Тампа",
        country="США",
        country_code="US",
        region="Флорида",
        timezone="America/New_York",
        aliases=["тампа", "tampa", "тампа-бэй", "tampa bay"],
    ),
    GeoLocationInfo(
        canonical_city="Миннеаполис",
        country="США",
        country_code="US",
        region="Миннесота",
        timezone="America/Chicago",
        aliases=["миннеаполис", "minneapolis", "миннесота", "minnesota"],
    ),
    GeoLocationInfo(
        canonical_city="Новый Орлеан",
        country="США",
        country_code="US",
        region="Луизиана",
        timezone="America/Chicago",
        aliases=["новый орлеан", "новый-орлеан", "new orleans", "луизиана", "louisiana"],
    ),
    GeoLocationInfo(
        canonical_city="Кливленд",
        country="США",
        country_code="US",
        region="Огайо",
        timezone="America/New_York",
        aliases=["кливленд", "cleveland", "огайо", "ohio"],
    ),
    GeoLocationInfo(
        canonical_city="Питтсбург",
        country="США",
        country_code="US",
        region="Пенсильвания",
        timezone="America/New_York",
        aliases=["питтсбург", "питсбург", "pittsburgh"],
    ),
    GeoLocationInfo(
        canonical_city="Сакраменто",
        country="США",
        country_code="US",
        region="Калифорния",
        timezone="America/Los_Angeles",
        aliases=["сакраменто", "sacramento"],
    ),
    GeoLocationInfo(
        canonical_city="Солт-Лейк-Сити",
        country="США",
        country_code="US",
        region="Юта",
        timezone="America/Denver",
        aliases=["солт-лейк-сити", "солт лейк сити", "salt lake city", "юта", "utah"],
    ),
    GeoLocationInfo(
        canonical_city="Гонолулу",
        country="США",
        country_code="US",
        region="Гавайи",
        timezone="Pacific/Honolulu",
        aliases=["гонолулу", "honolulu", "гавайи", "hawaii"],
    ),
    GeoLocationInfo(
        canonical_city="Канзас-Сити",
        country="США",
        country_code="US",
        region="Миссури",
        timezone="America/Chicago",
        aliases=["канзас-сити", "канзас сити", "kansas city", "миссури", "missouri"],
    ),
    GeoLocationInfo(
        canonical_city="Балтимор",
        country="США",
        country_code="US",
        region="Мэриленд",
        timezone="America/New_York",
        aliases=["балтимор", "baltimore", "мэриленд", "maryland"],
    ),
    GeoLocationInfo(
        canonical_city="Милуоки",
        country="США",
        country_code="US",
        region="Висконсин",
        timezone="America/Chicago",
        aliases=["милуоки", "milwaukee", "висконсин", "wisconsin"],
    ),
    GeoLocationInfo(
        canonical_city="Индианаполис",
        country="США",
        country_code="US",
        region="Индиана",
        timezone="America/Indiana/Indianapolis",
        aliases=["индианаполис", "indianapolis", "индиана", "indiana"],
    ),
    GeoLocationInfo(
        canonical_city="Колумбус",
        country="США",
        country_code="US",
        region="Огайо",
        timezone="America/New_York",
        aliases=["колумбус", "columbus"],
    ),
    GeoLocationInfo(
        canonical_city="Роли",
        country="США",
        country_code="US",
        region="Северная Каролина",
        timezone="America/New_York",
        aliases=["роли", "raleigh"],
    ),
    GeoLocationInfo(
        canonical_city="Торонто",
        country="Канада",
        country_code="CA",
        region="Онтарио",
        timezone="America/Toronto",
        aliases=["торонто", "toronto", "онтарио", "ontario"],
    ),
    GeoLocationInfo(
        canonical_city="Монреаль",
        country="Канада",
        country_code="CA",
        region="Квебек",
        timezone="America/Montreal",
        aliases=["монреаль", "montreal", "квебек", "quebec"],
    ),
    GeoLocationInfo(
        canonical_city="Ванкувер",
        country="Канада",
        country_code="CA",
        region="Британская Колумбия",
        timezone="America/Vancouver",
        aliases=["ванкувер", "vancouver"],
    ),

    # --- United Kingdom & Ireland ---
    GeoLocationInfo(
        canonical_city="Лондон",
        country="Великобритания",
        country_code="GB",
        region="Англия",
        timezone="Europe/London",
        aliases=[
            "лондон", "london", "hampstead", "хэмпстед", "кэмден", "camden", "челси", "chelsea",
            "майфэр", "mayfair", "сохо", "гринвич", "greenwich", "великобритания", "британия",
            "англия", "uk", "great britain", "united kingdom",
        ],
    ),
    GeoLocationInfo(
        canonical_city="Манчестер",
        country="Великобритания",
        country_code="GB",
        region="Англия",
        timezone="Europe/London",
        aliases=["манчестер", "manchester"],
    ),
    GeoLocationInfo(
        canonical_city="Дублин",
        country="Ирландия",
        country_code="IE",
        region="Ленстер",
        timezone="Europe/Dublin",
        aliases=["дублин", "dublin", "ирландия", "ireland"],
    ),

    # --- Western & Central Europe ---
    GeoLocationInfo(
        canonical_city="Париж",
        country="Франция",
        country_code="FR",
        region="Иль-де-Франс",
        timezone="Europe/Paris",
        aliases=["париж", "paris", "франция", "france", "ницца", "nice", "лион", "lyon"],
    ),
    GeoLocationInfo(
        canonical_city="Берлин",
        country="Германия",
        country_code="DE",
        region="Берлин",
        timezone="Europe/Berlin",
        aliases=["берлин", "berlin", "германия", "germany", "deutschland"],
    ),
    GeoLocationInfo(
        canonical_city="Мюнхен",
        country="Германия",
        country_code="DE",
        region="Бавария",
        timezone="Europe/Berlin",
        aliases=["мюнхен", "munich", "münchen", "бавария", "bavaria"],
    ),
    GeoLocationInfo(
        canonical_city="Франкфурт",
        country="Германия",
        country_code="DE",
        region="Гессен",
        timezone="Europe/Berlin",
        aliases=["франкфурт", "frankfurt", "франкфурт-на-майне"],
    ),
    GeoLocationInfo(
        canonical_city="Варшава",
        country="Польша",
        country_code="PL",
        region="Мазовецкое воеводство",
        timezone="Europe/Warsaw",
        aliases=["варшава", "warsaw", "warszawa", "польша", "poland", "polska"],
    ),
    GeoLocationInfo(
        canonical_city="Краков",
        country="Польша",
        country_code="PL",
        region="Малопольское воеводство",
        timezone="Europe/Warsaw",
        aliases=["краков", "krakow", "kraków"],
    ),
    GeoLocationInfo(
        canonical_city="Вроцлав",
        country="Польша",
        country_code="PL",
        region="Нижнесилезское воеводство",
        timezone="Europe/Warsaw",
        aliases=["вроцлав", "wroclaw", "wrocław"],
    ),
    GeoLocationInfo(
        canonical_city="Гданьск",
        country="Польша",
        country_code="PL",
        region="Поморское воеводство",
        timezone="Europe/Warsaw",
        aliases=["гданьск", "gdansk", "gdańsk"],
    ),
    GeoLocationInfo(
        canonical_city="Прага",
        country="Чехия",
        country_code="CZ",
        region="Прага",
        timezone="Europe/Prague",
        aliases=["прага", "prague", "praha", "чехия", "czech", "czech republic"],
    ),
    GeoLocationInfo(
        canonical_city="Вена",
        country="Австрия",
        country_code="AT",
        region="Вена",
        timezone="Europe/Vienna",
        aliases=["вена", "vienna", "wien", "австрия", "austria"],
    ),
    GeoLocationInfo(
        canonical_city="Рим",
        country="Италия",
        country_code="IT",
        region="Лацио",
        timezone="Europe/Rome",
        aliases=["рим", "rome", "roma", "италия", "italy", "italia"],
    ),
    GeoLocationInfo(
        canonical_city="Милан",
        country="Италия",
        country_code="IT",
        region="Ломбардия",
        timezone="Europe/Rome",
        aliases=["милан", "milan", "milano"],
    ),
    GeoLocationInfo(
        canonical_city="Мадрид",
        country="Испания",
        country_code="ES",
        region="Мадрид",
        timezone="Europe/Madrid",
        aliases=["мадрид", "madrid", "испания", "spain", "españa"],
    ),
    GeoLocationInfo(
        canonical_city="Барселона",
        country="Испания",
        country_code="ES",
        region="Каталония",
        timezone="Europe/Madrid",
        aliases=["барселона", "barcelona", "каталония", "catalonia"],
    ),
    GeoLocationInfo(
        canonical_city="Лиссабон",
        country="Португалия",
        country_code="PT",
        region="Лиссабон",
        timezone="Europe/Lisbon",
        aliases=["лиссабон", "lisbon", "lisboa", "португалия", "portugal", "порту", "porto"],
    ),
    GeoLocationInfo(
        canonical_city="Амстердам",
        country="Нидерланды",
        country_code="NL",
        region="Северная Голландия",
        timezone="Europe/Amsterdam",
        aliases=["амстердам", "amsterdam", "нидерланды", "netherlands", "голландия", "holland"],
    ),
    GeoLocationInfo(
        canonical_city="Брюссель",
        country="Бельгия",
        country_code="BE",
        region="Брюссель",
        timezone="Europe/Brussels",
        aliases=["брюссель", "brussels", "bruxelles", "бельгия", "belgium"],
    ),
    GeoLocationInfo(
        canonical_city="Цюрих",
        country="Швейцария",
        country_code="CH",
        region="Цюрих",
        timezone="Europe/Zurich",
        aliases=["цюрих", "zurich", "zürich", "швейцария", "switzerland", "женева", "geneva"],
    ),
    GeoLocationInfo(
        canonical_city="Будапешт",
        country="Венгрия",
        country_code="HU",
        region="Будапешт",
        timezone="Europe/Budapest",
        aliases=["будапешт", "budapest", "венгрия", "hungary"],
    ),

    # --- Northern & Baltic Europe ---
    GeoLocationInfo(
        canonical_city="Стокгольм",
        country="Швеция",
        country_code="SE",
        region="Стокгольм",
        timezone="Europe/Stockholm",
        aliases=["стокгольм", "stockholm", "швеция", "sweden"],
    ),
    GeoLocationInfo(
        canonical_city="Осло",
        country="Норвегия",
        country_code="NO",
        region="Осло",
        timezone="Europe/Oslo",
        aliases=["осло", "oslo", "норвегия", "norway"],
    ),
    GeoLocationInfo(
        canonical_city="Копенгаген",
        country="Дания",
        country_code="DK",
        region="Ховедстаден",
        timezone="Europe/Copenhagen",
        aliases=["копенгаген", "copenhagen", "дания", "denmark"],
    ),
    GeoLocationInfo(
        canonical_city="Хельсинки",
        country="Финляндия",
        country_code="FI",
        region="Уусимаа",
        timezone="Europe/Helsinki",
        aliases=["хельсинки", "helsinki", "финляндия", "finland"],
    ),
    GeoLocationInfo(
        canonical_city="Рига",
        country="Латвия",
        country_code="LV",
        region="Рига",
        timezone="Europe/Riga",
        aliases=["рига", "riga", "латвия", "latvia"],
    ),
    GeoLocationInfo(
        canonical_city="Вильнюс",
        country="Литва",
        country_code="LT",
        region="Вильнюс",
        timezone="Europe/Vilnius",
        aliases=["вильнюс", "vilnius", "литва", "lithuania"],
    ),
    GeoLocationInfo(
        canonical_city="Таллин",
        country="Эстония",
        country_code="EE",
        region="Харьюмаа",
        timezone="Europe/Tallinn",
        aliases=["таллин", "таллинн", "tallinn", "эстония", "estonia"],
    ),

    # --- Eastern Europe ---
    GeoLocationInfo(
        canonical_city="Киев",
        country="Украина",
        country_code="UA",
        region="Киевская область",
        timezone="Europe/Kyiv",
        aliases=["киев", "kyiv", "kiev", "украина", "ukraine", "печерск", "подол", "оболонь"],
    ),
    GeoLocationInfo(
        canonical_city="Харьков",
        country="Украина",
        country_code="UA",
        region="Харьковская область",
        timezone="Europe/Kyiv",
        aliases=["харьков", "kharkiv", "салтовка"],
    ),
    GeoLocationInfo(
        canonical_city="Одесса",
        country="Украина",
        country_code="UA",
        region="Одесская область",
        timezone="Europe/Kyiv",
        aliases=["одесса", "odesa", "odessa", "аркадия"],
    ),
    GeoLocationInfo(
        canonical_city="Львов",
        country="Украина",
        country_code="UA",
        region="Львовская область",
        timezone="Europe/Kyiv",
        aliases=["львов", "lviv"],
    ),
    GeoLocationInfo(
        canonical_city="Днепр",
        country="Украина",
        country_code="UA",
        region="Днепропетровская область",
        timezone="Europe/Kyiv",
        aliases=["днепр", "dnipro", "днепропетровск"],
    ),
    GeoLocationInfo(
        canonical_city="Кишинев",
        country="Молдова",
        country_code="MD",
        region="Кишинев",
        timezone="Europe/Chisinau",
        aliases=["кишинев", "кишинёв", "chisinau", "молдова", "moldova"],
    ),
    GeoLocationInfo(
        canonical_city="Бухарест",
        country="Румыния",
        country_code="RO",
        region="Бухарест",
        timezone="Europe/Bucharest",
        aliases=["бухарест", "bucharest", "румыния", "romania"],
    ),
    GeoLocationInfo(
        canonical_city="София",
        country="Болгария",
        country_code="BG",
        region="София",
        timezone="Europe/Sofia",
        aliases=["софия", "sofia", "болгария", "bulgaria"],
    ),
    GeoLocationInfo(
        canonical_city="Афины",
        country="Греция",
        country_code="GR",
        region="Аттика",
        timezone="Europe/Athens",
        aliases=["афины", "athens", "греция", "greece"],
    ),

    # --- Russia & Belarus ---
    GeoLocationInfo(
        canonical_city="Калининград",
        country="Россия",
        country_code="RU",
        region="Калининградская область",
        timezone="Europe/Kaliningrad",
        aliases=["калининград", "kaliningrad"],
    ),
    GeoLocationInfo(
        canonical_city="Москва",
        country="Россия",
        country_code="RU",
        region="Москва",
        timezone="Europe/Moscow",
        aliases=["москва", "moscow", "россия", "russia", "цао", "арбат", "тверская"],
    ),
    GeoLocationInfo(
        canonical_city="Санкт-Петербург",
        country="Россия",
        country_code="RU",
        region="Санкт-Петербург",
        timezone="Europe/Moscow",
        aliases=[
            "санкт-петербург", "санкт петербург", "питер", "петербург", "spb", "спб",
            "saint petersburg", "st petersburg", "васильевский остров", "васька", "петроградка",
        ],
    ),
    GeoLocationInfo(
        canonical_city="Казань",
        country="Россия",
        country_code="RU",
        region="Татарстан",
        timezone="Europe/Moscow",
        aliases=["казань", "kazan", "татарстан", "баумана"],
    ),
    GeoLocationInfo(
        canonical_city="Нижний Новгород",
        country="Россия",
        country_code="RU",
        region="Нижегородская область",
        timezone="Europe/Moscow",
        aliases=["нижний новгород", "нижний-новгород", "нижний", "nizhny novgorod"],
    ),
    GeoLocationInfo(
        canonical_city="Краснодар",
        country="Россия",
        country_code="RU",
        region="Краснодарский край",
        timezone="Europe/Moscow",
        aliases=["краснодар", "krasnodar", "кубань"],
    ),
    GeoLocationInfo(
        canonical_city="Сочи",
        country="Россия",
        country_code="RU",
        region="Краснодарский край",
        timezone="Europe/Moscow",
        aliases=["сочи", "sochi", "адлер", "красная поляна"],
    ),
    GeoLocationInfo(
        canonical_city="Ростов-на-Дону",
        country="Россия",
        country_code="RU",
        region="Ростовская область",
        timezone="Europe/Moscow",
        aliases=["ростов", "ростов-на-дону", "ростов на дону", "rostov"],
    ),
    GeoLocationInfo(
        canonical_city="Воронеж",
        country="Россия",
        country_code="RU",
        region="Воронежская область",
        timezone="Europe/Moscow",
        aliases=["воронеж", "voronezh"],
    ),
    GeoLocationInfo(
        canonical_city="Ярославль",
        country="Россия",
        country_code="RU",
        region="Ярославская область",
        timezone="Europe/Moscow",
        aliases=["ярославль", "yaroslavl"],
    ),
    GeoLocationInfo(
        canonical_city="Минск",
        country="Беларусь",
        country_code="BY",
        region="Минская область",
        timezone="Europe/Minsk",
        aliases=["минск", "minsk", "беларусь", "belarus", "белоруссия"],
    ),
    GeoLocationInfo(
        canonical_city="Самара",
        country="Россия",
        country_code="RU",
        region="Самарская область",
        timezone="Europe/Samara",
        aliases=["самара", "samara"],
    ),
    GeoLocationInfo(
        canonical_city="Саратов",
        country="Россия",
        country_code="RU",
        region="Саратовская область",
        timezone="Europe/Samara",
        aliases=["саратов", "saratov"],
    ),
    GeoLocationInfo(
        canonical_city="Тольятти",
        country="Россия",
        country_code="RU",
        region="Самарская область",
        timezone="Europe/Samara",
        aliases=["тольятти", "tolyatti"],
    ),
    GeoLocationInfo(
        canonical_city="Екатеринбург",
        country="Россия",
        country_code="RU",
        region="Свердловская область",
        timezone="Asia/Yekaterinburg",
        aliases=["екатеринбург", "yekaterinburg", "екб", "свердловск"],
    ),
    GeoLocationInfo(
        canonical_city="Челябинск",
        country="Россия",
        country_code="RU",
        region="Челябинская область",
        timezone="Asia/Yekaterinburg",
        aliases=["челябинск", "chelyabinsk"],
    ),
    GeoLocationInfo(
        canonical_city="Уфа",
        country="Россия",
        country_code="RU",
        region="Башкортостан",
        timezone="Asia/Yekaterinburg",
        aliases=["уфа", "ufa", "башкортостан"],
    ),
    GeoLocationInfo(
        canonical_city="Пермь",
        country="Россия",
        country_code="RU",
        region="Пермский край",
        timezone="Asia/Yekaterinburg",
        aliases=["пермь", "perm"],
    ),
    GeoLocationInfo(
        canonical_city="Тюмень",
        country="Россия",
        country_code="RU",
        region="Тюменская область",
        timezone="Asia/Yekaterinburg",
        aliases=["тюмень", "tyumen"],
    ),
    GeoLocationInfo(
        canonical_city="Омск",
        country="Россия",
        country_code="RU",
        region="Омская область",
        timezone="Asia/Omsk",
        aliases=["омск", "omsk"],
    ),
    GeoLocationInfo(
        canonical_city="Новосибирск",
        country="Россия",
        country_code="RU",
        region="Новосибирская область",
        timezone="Asia/Novosibirsk",
        aliases=["новосибирск", "novosibirsk", "нск", "академгородок"],
    ),
    GeoLocationInfo(
        canonical_city="Красноярск",
        country="Россия",
        country_code="RU",
        region="Красноярский край",
        timezone="Asia/Krasnoyarsk",
        aliases=["красноярск", "krasnoyarsk"],
    ),
    GeoLocationInfo(
        canonical_city="Томск",
        country="Россия",
        country_code="RU",
        region="Томская область",
        timezone="Asia/Novosibirsk",
        aliases=["томск", "tomsk"],
    ),
    GeoLocationInfo(
        canonical_city="Иркутск",
        country="Россия",
        country_code="RU",
        region="Иркутская область",
        timezone="Asia/Irkutsk",
        aliases=["иркутск", "irkutsk", "байкал"],
    ),
    GeoLocationInfo(
        canonical_city="Якутск",
        country="Россия",
        country_code="RU",
        region="Якутия",
        timezone="Asia/Yakutsk",
        aliases=["якутск", "yakutsk"],
    ),
    GeoLocationInfo(
        canonical_city="Владивосток",
        country="Россия",
        country_code="RU",
        region="Приморский край",
        timezone="Asia/Vladivostok",
        aliases=["владивосток", "vladivostok", "приморье"],
    ),
    GeoLocationInfo(
        canonical_city="Хабаровск",
        country="Россия",
        country_code="RU",
        region="Хабаровский край",
        timezone="Asia/Vladivostok",
        aliases=["хабаровск", "khabarovsk"],
    ),

    # --- Caucasus, Central Asia, Middle East & Asia ---
    GeoLocationInfo(
        canonical_city="Тбилиси",
        country="Грузия",
        country_code="GE",
        region="Тбилиси",
        timezone="Asia/Tbilisi",
        aliases=["тбилиси", "tbilisi", "грузия", "georgia", "батуми", "batumi"],
    ),
    GeoLocationInfo(
        canonical_city="Ереван",
        country="Армения",
        country_code="AM",
        region="Ереван",
        timezone="Asia/Yerevan",
        aliases=["ереван", "yerevan", "армения", "armenia"],
    ),
    GeoLocationInfo(
        canonical_city="Баку",
        country="Азербайджан",
        country_code="AZ",
        region="Баку",
        timezone="Asia/Baku",
        aliases=["баку", "baku", "азербайджан", "azerbaijan"],
    ),
    GeoLocationInfo(
        canonical_city="Алматы",
        country="Казахстан",
        country_code="KZ",
        region="Алматы",
        timezone="Asia/Almaty",
        aliases=["алматы", "almaty", "алма-ата", "алма ата", "казахстан", "kazakhstan"],
    ),
    GeoLocationInfo(
        canonical_city="Астана",
        country="Казахстан",
        country_code="KZ",
        region="Астана",
        timezone="Asia/Almaty",
        aliases=["астана", "astana", "нур-султан", "нур султан"],
    ),
    GeoLocationInfo(
        canonical_city="Ташкент",
        country="Узбекистан",
        country_code="UZ",
        region="Ташкент",
        timezone="Asia/Tashkent",
        aliases=["ташкент", "tashkent", "узбекистан", "uzbekistan", "самарканд", "samarkand"],
    ),
    GeoLocationInfo(
        canonical_city="Бишкек",
        country="Кыргызстан",
        country_code="KG",
        region="Бишкек",
        timezone="Asia/Bishkek",
        aliases=["бишкек", "bishkek", "кыргызстан", "kyrgyzstan", "киргизия"],
    ),
    GeoLocationInfo(
        canonical_city="Дубай",
        country="ОАЭ",
        country_code="AE",
        region="Дубай",
        timezone="Asia/Dubai",
        aliases=["дубай", "dubai", "оаэ", "uae", "эмираты", "emirates", "united arab emirates", "marina", "марина", "даунтаун дубай"],
    ),
    GeoLocationInfo(
        canonical_city="Абу-Даби",
        country="ОАЭ",
        country_code="AE",
        region="Абу-Даби",
        timezone="Asia/Dubai",
        aliases=["абу-даби", "абу даби", "абудаби", "abu dhabi", "abudhabi"],
    ),
    GeoLocationInfo(
        canonical_city="Стамбул",
        country="Турция",
        country_code="TR",
        region="Стамбул",
        timezone="Europe/Istanbul",
        aliases=["стамбул", "istanbul", "турция", "turkey", "анталья", "анталия", "antalya"],
    ),
    GeoLocationInfo(
        canonical_city="Токио",
        country="Япония",
        country_code="JP",
        region="Канто",
        timezone="Asia/Tokyo",
        aliases=["токио", "tokyo", "япония", "japan"],
    ),
    GeoLocationInfo(
        canonical_city="Сидней",
        country="Австралия",
        country_code="AU",
        region="Новый Южный Уэльс",
        timezone="Australia/Sydney",
        aliases=["сидней", "sydney", "австралия", "australia", "мельбурн", "melbourne"],
    ),
    GeoLocationInfo(
        canonical_city="Бали",
        country="Индонезия",
        country_code="ID",
        region="Бали",
        timezone="Asia/Makassar",
        aliases=["бали", "bali", "денпасар", "denpasar", "чангу", "canggu", "убуд", "ubud", "семиньяк", "индонезия", "indonesia"],
    ),
    GeoLocationInfo(
        canonical_city="Бангкок",
        country="Таиланд",
        country_code="TH",
        region="Бангкок",
        timezone="Asia/Bangkok",
        aliases=["бангкок", "bangkok", "таиланд", "thailand", "тайланд", "пхукет", "phuket", "самуи", "samui", "паттайя", "pattaya"],
    ),
]

# Fast lookup map indexed by normalized name
GEO_LOOKUP_MAP: Dict[str, GeoLocationInfo] = {}

def _build_geo_index():
    for info in CANONICAL_GEO_DATABASE:
        canonical_clean = info.canonical_city.lower()
        GEO_LOOKUP_MAP[canonical_clean] = info
        GEO_LOOKUP_MAP[canonical_clean.replace("-", " ")] = info
        GEO_LOOKUP_MAP[canonical_clean.replace("-", "")] = info
        for alias in info.aliases:
            a_clean = alias.lower()
            GEO_LOOKUP_MAP[a_clean] = info
            GEO_LOOKUP_MAP[a_clean.replace("-", " ")] = info
            GEO_LOOKUP_MAP[a_clean.replace("-", "")] = info

_build_geo_index()


# Flat mapping for legacy backward compatibility
CITY_TIMEZONE_MAP: Dict[str, str] = {
    alias: info.timezone for alias, info in GEO_LOOKUP_MAP.items()
}

OFFSET_ALIASES = {
    "gmt-8": "America/Los_Angeles",
    "utc-8": "America/Los_Angeles",
    "gmt-5": "America/New_York",
    "utc-5": "America/New_York",
    "gmt-4": "America/New_York",
    "utc-4": "America/New_York",
    "gmt+0": "Europe/London",
    "utc+0": "Europe/London",
    "gmt+1": "Europe/Berlin",
    "utc+1": "Europe/Berlin",
    "gmt+2": "Europe/Kyiv",
    "utc+2": "Europe/Kyiv",
    "gmt+3": "Europe/Moscow",
    "utc+3": "Europe/Moscow",
    "gmt+4": "Europe/Samara",
    "utc+4": "Europe/Samara",
    "gmt+5": "Asia/Yekaterinburg",
    "utc+5": "Asia/Yekaterinburg",
    "gmt+6": "Asia/Omsk",
    "utc+6": "Asia/Omsk",
    "gmt+7": "Asia/Novosibirsk",
    "utc+7": "Asia/Novosibirsk",
    "gmt+8": "Asia/Irkutsk",
    "utc+8": "Asia/Irkutsk",
}

# Regex matching any IANA timezone identifier (e.g. Europe/Moscow, America/Los_Angeles, Asia/Tokyo)
TIMEZONE_IDENTIFIER_PATTERN = re.compile(
    r"(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Za-z0-9_+\-]+",
    re.IGNORECASE,
)

# Regex matching timezone identifiers enclosed in parentheses: (Europe/Moscow), (America/Los_Angeles), (GMT+3), (UTC...)
PARENTHESIZED_TIMEZONE_PATTERN = re.compile(
    r"\s*\(\s*(?:(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Za-z0-9_+\-]+|(?:GMT|UTC)[+\-]?\d{0,2}(?::?\d{2})?)\s*\)",
    re.IGNORECASE,
)

# Standalone or parenthesized continents
CONTINENT_NAMES_PATTERN = re.compile(
    r"\b(?:Европа|Азия|Америка|Северная\s+Америка|Южная\s+Америка|Австралия|Африка|Антарктида|Europe|Asia|North\s+America|South\s+America|Australia|Africa)\b",
    re.IGNORECASE,
)


class TimezoneService:
    WEATHER_ICONS: Dict[int, Tuple[str, str]] = {
        0: ("☀️", "ясно"),
        1: ("🌤️", "в основном ясно"),
        2: ("⛅", "переменная облачность"),
        3: ("☁️", "пасмурно"),
        45: ("🌫️", "туман"),
        48: ("🌫️", "изморозь"),
        51: ("🌦️", "лёгкая морось"),
        53: ("🌦️", "морось"),
        55: ("🌧️", "густая морось"),
        61: ("🌧️", "небольшой дождь"),
        63: ("🌧️", "умеренный дождь"),
        65: ("🌧️", "сильный дождь"),
        71: ("🌨️", "небольшой снегопад"),
        73: ("🌨️", "снегопад"),
        75: ("❄️", "сильный снегопад"),
        77: ("🌨️", "снежные зёрна"),
        80: ("🌦️", "кратковременный ливень"),
        81: ("🌧️", "сильный ливень"),
        82: ("⛈️", "очень сильный ливень"),
        85: ("🌨️", "ливневый снегопад"),
        86: ("❄️", "сильный снегопад"),
        95: ("⛈️", "гроза"),
    }

    @classmethod
    async def fetch_live_city_weather(cls, city_name: str) -> Optional[Dict[str, Any]]:
        """
        Fetches real-time weather and forecast data for any city in the world using Open-Meteo.
        Supports fast offline coordinates for CIS/major cities and dynamic geocoding for US/world cities.
        """
        if not city_name:
            return None

        c_low = city_name.strip().lower()
        lat, lon, display_name = None, None, city_name

        CITY_COORDINATES = {
            "москва": (55.7558, 37.6173, "Москва"),
            "питер": (59.9343, 30.3351, "Санкт-Петербург"),
            "санкт-петербург": (59.9343, 30.3351, "Санкт-Петербург"),
            "самара": (53.1959, 50.1002, "Самара"),
            "казань": (55.7887, 49.1221, "Казань"),
            "сочи": (43.6028, 39.7342, "Сочи"),
            "екатеринбург": (56.8389, 60.6057, "Екатеринбург"),
            "новосибирск": (55.0084, 82.9357, "Новосибирск"),
            "краснодар": (45.0355, 38.9753, "Краснодар"),
            "дубай": (25.2048, 55.2708, "Дубай"),
            "лондон": (51.5074, -0.1278, "London"),
            "киев": (50.4501, 30.5234, "Киев"),
            "минск": (53.9045, 27.5615, "Минск"),
            "лос-анджелес": (34.0522, -118.2437, "Los Angeles"),
            "нью-йорк": (40.7128, -74.0060, "New York"),
            "портленд": (45.5234, -122.6762, "Portland"),
            "шарлотт": (35.2271, -80.8431, "Charlotte"),
        }

        if c_low in CITY_COORDINATES:
            lat, lon, display_name = CITY_COORDINATES[c_low]
        else:
            geo = cls.get_geo_info(c_low)
            search_query = city_name
            if geo:
                for a in geo.aliases:
                    if a.isascii() and len(a) > 2:
                        search_query = a
                        break

            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    g_resp = await client.get(
                        "https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": search_query, "count": 1, "format": "json"},
                    )
                    if g_resp.status_code == 200:
                        data = g_resp.json()
                        if data.get("results"):
                            res = data["results"][0]
                            lat, lon = res["latitude"], res["longitude"]
                            display_name = res.get("name", city_name)
            except Exception as e:
                logger.warning("Geocoding lookup error for %s: %s", city_name, e)

        if lat is None or lon is None:
            return None

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                w_resp = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current_weather": "true",
                        "hourly": "relativehumidity_2m,apparent_temperature",
                        "timezone": "auto",
                    },
                )
                if w_resp.status_code == 200:
                    data = w_resp.json()
                    cw = data.get("current_weather", {})
                    hourly = data.get("hourly", {})
                    temp = cw.get("temperature")
                    w_code = cw.get("weathercode", 0)
                    wind = cw.get("windspeed")
                    is_day = cw.get("is_day", 1)
                    humidity = hourly.get("relativehumidity_2m", [None])[0]
                    feels_like = hourly.get("apparent_temperature", [None])[0]

                    icon, desc = cls.WEATHER_ICONS.get(w_code, ("🌤️", "обычная погода"))
                    if not is_day and w_code == 0:
                        icon = "🌙"
                        desc = "ясно, звёздное небо"
                    elif not is_day and w_code in (1, 2):
                        icon = "☁️"

                    return {
                        "city": display_name,
                        "temperature": temp,
                        "feels_like": feels_like,
                        "weather_code": w_code,
                        "icon": icon,
                        "condition": desc,
                        "wind_speed": wind,
                        "humidity": humidity,
                        "is_day": bool(is_day),
                    }
        except Exception as e:
            logger.warning("Open-Meteo forecast error for %s: %s", city_name, e)

        return None

    @classmethod
    def format_city_detailed_report(
        cls, persona: Any, weather: Optional[Dict[str, Any]] = None, ref_utc_dt: Optional[datetime] = None
    ) -> str:
        """
        Builds a comprehensive real-time report for the persona's city:
        - Current local time, date, day of week
        - Timezone, UTC offset, difference with Moscow time
        - Sleep status (asleep / awake), active sleep window, time until wake-up / bed
        - Current live weather (temperature, feels like, condition with emoji, wind, humidity)
        - AI awareness reminder
        """
        city = persona.city or "Москва"
        district = getattr(persona, "district", "") or ""
        country = getattr(persona, "country", "") or ""
        region = getattr(persona, "region", "") or ""

        clean_city = cls.clean_geographic_string(city)
        clean_dist = cls.clean_geographic_string(district) if district else ""
        if clean_dist.lower() in ("usa", "сша", "us", "uk", "россия", "украина", "united states") or clean_dist.lower() == clean_city.lower():
            clean_dist = ""
        loc_str = f"{clean_city}, {clean_dist}" if clean_dist else clean_city

        country, region = cls.resolve_country_and_region(clean_city, country, region, persona.timezone_name)
        country_region_str = f"{country}, {region}" if region and region.lower() != country.lower() else country

        tz_name = getattr(persona, "timezone_name", "Europe/Moscow") or "Europe/Moscow"
        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        time_str = local_dt.strftime("%H:%M")

        ru_months = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
            7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
        }
        ru_weekdays = {
            0: "понедельник", 1: "вторник", 2: "среда", 3: "четверг",
            4: "пятница", 5: "суббота", 6: "воскресенье",
        }
        full_date_str = f"{local_dt.day} {ru_months.get(local_dt.month, '')} {local_dt.year} г. ({ru_weekdays.get(local_dt.weekday(), '')})"

        float_h = local_dt.hour + local_dt.minute / 60.0
        if 5.0 <= float_h < 11.0:
            period_ru = "🌅 Утро"
        elif 11.0 <= float_h < 17.0:
            period_ru = "☀️ День"
        elif 17.0 <= float_h < 22.0:
            period_ru = "🌆 Вечер"
        elif 22.0 <= float_h < 24.0 or 0.0 <= float_h < 1.0:
            period_ru = "🌃 Поздний вечер"
        else:
            period_ru = "🌌 Ночь"

        utc_off = local_dt.utcoffset()
        tot_sec = int(utc_off.total_seconds()) if utc_off else 0
        sign = "+" if tot_sec >= 0 else "-"
        hours = abs(tot_sec) // 3600
        mins = (abs(tot_sec) % 3600) // 60
        gmt_str = f"GMT{sign}{hours}" if mins == 0 else f"GMT{sign}{hours}:{mins:02d}"
        is_dst = bool(local_dt.dst())
        dst_label = "летнее время" if is_dst else "стандартное время"

        kyiv_dt = cls.get_local_datetime("Europe/Kyiv", ref_utc_dt)
        off_persona = local_dt.utcoffset().total_seconds() if local_dt.utcoffset() else 0
        off_kyiv = kyiv_dt.utcoffset().total_seconds() if kyiv_dt.utcoffset() else 0
        diff_hours = int((off_persona - off_kyiv) // 3600)
        kyiv_now_str = kyiv_dt.strftime("%H:%M")
        if diff_hours == 0:
            diff_str = f"совпадает с Киевом (в Киеве {kyiv_now_str})"
        elif diff_hours > 0:
            diff_str = f"+{diff_hours} ч. от Киева (в Киеве {kyiv_now_str})"
        else:
            diff_str = f"{diff_hours} ч. от Киева (в Киеве {kyiv_now_str})"

        sleep_start = getattr(persona, "sleep_start", "23:00") or "23:00"
        sleep_end = getattr(persona, "sleep_end", "08:30") or "08:30"
        is_sleeping = cls.is_persona_sleeping(tz_name, sleep_start, sleep_end, local_dt)

        start_t = cls.parse_time_str(sleep_start)
        end_t = cls.parse_time_str(sleep_end)
        now_t = local_dt.time()
        now_mins = now_t.hour * 60 + now_t.minute
        start_mins = start_t.hour * 60 + start_t.minute
        end_mins = end_t.hour * 60 + end_t.minute

        if is_sleeping:
            if end_mins >= now_mins:
                rem_m = end_mins - now_mins
            else:
                rem_m = (24 * 60 - now_mins) + end_mins
            rem_h = rem_m // 60
            rem_min_tail = rem_m % 60
            state_badge = f"😴 <b>Спит</b> <i>(до подъёма ~{rem_h} ч. {rem_min_tail:02d} мин.)</i>"
        else:
            if start_mins >= now_mins:
                rem_m = start_mins - now_mins
            else:
                rem_m = (24 * 60 - now_mins) + start_mins
            rem_h = rem_m // 60
            rem_min_tail = rem_m % 60
            state_badge = f"☀️ <b>Бодрствует</b> <i>(до отбоя ~{rem_h} ч. {rem_min_tail:02d} мин.)</i>"

        if weather and weather.get("temperature") is not None:
            temp = weather["temperature"]
            feels = weather.get("feels_like")
            icon = weather.get("icon", "🌤️")
            cond = weather.get("condition", "обычная погода")
            wind = weather.get("wind_speed", 0)
            hum = weather.get("humidity")
            feels_str = f" (ощущается как <b>{feels:+.1f}°C</b>)" if feels is not None else ""
            hum_str = f"\n• Влажность: <b>{hum}%</b>" if hum is not None else ""
            weather_text = (
                f"🌤️ <b>Погода прямо сейчас:</b>\n"
                f"• Температура: <b>{temp:+.1f}°C</b>{feels_str}\n"
                f"• Состояние: {icon} <b>{cond.capitalize()}</b>\n"
                f"• Ветер: <b>{wind} км/ч</b>{hum_str}"
            )
        else:
            weather_text = "🌤️ <b>Погода:</b> <i>(информация о погоде обновляется...)</i>"

        return (
            f"🌍 <b>ГОРОД И МЕСТНОЕ ВРЕМЯ МОДЕЛИ</b>\n\n"
            f"📍 <b>Локация:</b> {loc_str}\n"
            f"🌎 <b>Страна и регион:</b> {country_region_str}\n"
            f"🌐 <b>Часовой пояс:</b> <code>{tz_name}</code> ({gmt_str}, {dst_label})\n\n"
            f"⏱ <b>ТЕКУЩЕЕ ВРЕМЯ В ГОРОДЕ:</b>\n"
            f"• Точное время: <b>{time_str}</b>\n"
            f"• Дата: <b>{full_date_str}</b>\n"
            f"• Период суток: <b>{period_ru}</b>\n"
            f"• Разница с Киевом: <b>{diff_str}</b>\n\n"
            f"🌙 <b>РЕЖИМ СНА И АКТИВНОСТЬ:</b>\n"
            f"• Расписание сна: 🌙 <code>{sleep_start} – {sleep_end}</code>\n"
            f"• Статус модели: {state_badge}\n\n"
            f"{weather_text}\n\n"
            f"💡 <i>Нейросеть живет строго по этому местному времени: знает который час у неё за окном, какая погода и не отвечает во время сна.</i>"
        )
    @classmethod
    def clean_geographic_string(cls, text: Optional[str]) -> str:
        """
        Strips timezone identifiers and continent noise from a geographic string.
        Examples:
          'Лос Анджелес, Уест-Голливуд (Europe/Moscow)' -> 'Лос Анджелес, Уест-Голливуд'
          'London, Hampstead (Europe/London)' -> 'London, Hampstead'
          'Лос-Анджелес, Уэст-Голливуд, Северная Америка' -> 'Лос-Анджелес, Уэст-Голливуд'
        """
        if not text:
            return ""

        cleaned = str(text).strip()

        # 1. Remove parenthesized timezones like (Europe/Moscow) or (GMT+3)
        cleaned = PARENTHESIZED_TIMEZONE_PATTERN.sub("", cleaned)

        # 2. Remove naked timezone identifiers
        cleaned = TIMEZONE_IDENTIFIER_PATTERN.sub("", cleaned)

        # 3. Remove trailing/standalone continent references
        cleaned = re.sub(
            r"[,/\s]+(?:Европа|Азия|Америка|Северная\s+Америка|Южная\s+Америка|Австралия|Африка|Europe|Asia|North\s+America|South\s+America|Australia|Africa)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        # 4. Clean up trailing/leading punctuation and excess whitespace
        cleaned = re.sub(r"[\s,;/\-]+$", "", cleaned)
        cleaned = re.sub(r"^[\s,;/\-]+", "", cleaned)
        cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return cleaned

    @classmethod
    def get_geo_info(cls, city: Optional[str], country: Optional[str] = None) -> Optional[GeoLocationInfo]:
        """
        Looks up canonical GeoLocationInfo for a given city string.
        Performs space/hyphen agnostic, case-insensitive lookup.
        """
        if not city:
            return None

        cleaned = cls.clean_geographic_string(city).strip().lower()
        if not cleaned:
            return None

        # 1. Exact match
        if cleaned in GEO_LOOKUP_MAP:
            return GEO_LOOKUP_MAP[cleaned]

        # 2. Hyphen/space normalized
        clean_spaced = cleaned.replace("-", " ")
        if clean_spaced in GEO_LOOKUP_MAP:
            return GEO_LOOKUP_MAP[clean_spaced]

        clean_compact = cleaned.replace("-", "").replace(" ", "")
        if clean_compact in GEO_LOOKUP_MAP:
            return GEO_LOOKUP_MAP[clean_compact]

        # 3. Substring matching: check if any known city is inside cleaned string
        # Sort keys by descending length to match "Лос-Анджелес" before "Лос"
        for key in sorted(GEO_LOOKUP_MAP.keys(), key=len, reverse=True):
            if len(key) >= 3 and (key in cleaned or cleaned in key):
                return GEO_LOOKUP_MAP[key]

        return None

    @classmethod
    def resolve_timezone_for_city(cls, city: Optional[str], country: Optional[str] = None) -> str:
        """
        Resolves the IANA timezone name for a given city, state, or country.
        Never guesses timezone based on interface language.
        Accurately maps:
          Portland -> America/Los_Angeles
          Charlotte -> America/New_York
          Los Angeles / Лос-Анджелес / LA -> America/Los_Angeles
          London / Лондон -> Europe/London
          New York / Нью-Йорк -> America/New_York
          Warsaw / Варшава -> Europe/Warsaw
          Dubai / Дубай -> Asia/Dubai
          Moscow / Москва -> Europe/Moscow
        """
        if not city:
            return "Europe/Moscow"

        cleaned = cls.clean_geographic_string(city).strip().lower()
        if not cleaned:
            return "Europe/Moscow"

        # 1. Direct offset aliases (e.g. 'gmt-5', 'utc+2', 'gmt+0')
        raw_clean = str(city).strip().lower().replace(" ", "")
        if raw_clean in OFFSET_ALIASES:
            return OFFSET_ALIASES[raw_clean]
        for alias, tz in OFFSET_ALIASES.items():
            if alias in raw_clean:
                return tz

        # 2. Canonical Geo Registry lookup
        geo = cls.get_geo_info(cleaned, country)
        if geo:
            return geo.timezone

        # 3. Check US States
        if cleaned in STATE_LOOKUP_MAP:
            return STATE_LOOKUP_MAP[cleaned][2]
        for tok in re.split(r"[,;/\s]+", cleaned):
            t_low = tok.strip(".")
            if t_low in STATE_LOOKUP_MAP:
                return STATE_LOOKUP_MAP[t_low][2]

        # 4. Check known countries
        if cleaned in KNOWN_COUNTRIES:
            return KNOWN_COUNTRIES[cleaned][2]

        if country:
            country_clean = cls.clean_geographic_string(country).strip().lower()
            geo_c = cls.get_geo_info(country_clean)
            if geo_c:
                return geo_c.timezone
            if country_clean in KNOWN_COUNTRIES:
                return KNOWN_COUNTRIES[country_clean][2]

        # 5. Direct IANA validation via pytz (e.g. if user passed valid IANA string)
        try:
            pytz.timezone(city)
            return city
        except Exception:
            pass

        # 6. Fallback: if Latin letters -> America/New_York (US default), else Europe/Moscow
        is_latin = any(c.isascii() and c.isalpha() for c in city) and not any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in city)
        if is_latin:
            return "America/New_York"

        return "Europe/Moscow"

    @classmethod
    def resolve_country_and_region(
        cls,
        city: Optional[str],
        country: Optional[str] = None,
        region: Optional[str] = None,
        timezone_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Dynamically and smartly validates and resolves (country, region).
        Guarantees that US or international cities are NEVER represented as 'Россия'.
        """
        clean_city = cls.clean_geographic_string(city).strip()
        geo = cls.get_geo_info(clean_city)
        if geo:
            resolved_country = country if country and country != "Россия" else geo.country
            resolved_region = region or geo.region
            return resolved_country, resolved_region

        # Check if country passed is a valid non-Russian country
        c_clean = cls.clean_geographic_string(country).strip().lower()
        if c_clean in KNOWN_COUNTRIES and KNOWN_COUNTRIES[c_clean][0] != "Россия":
            return KNOWN_COUNTRIES[c_clean][0], region or ""

        # Check timezone prefix
        tz = timezone_name or ""
        if tz.startswith("America/"):
            if any(c in tz for c in ("Toronto", "Montreal", "Vancouver", "Edmonton", "Winnipeg")):
                return "Канада", region or ""
            return "США", region or ""
        elif tz.startswith("Europe/London"):
            return "Великобритания", region or "Англия"
        elif tz.startswith("Europe/Kyiv"):
            return "Украина", region or ""

        # Check if city is in Latin
        if any(c.isascii() and c.isalpha() for c in clean_city) and not any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in clean_city):
            if clean_city.lower() in ("moscow", "saint petersburg", "spb", "kazan", "sochi", "samara", "novosibirsk"):
                return "Россия", region or ""
            return "США", region or ""

        final_country = country or "Россия"
        final_region = region or ""
        return final_country, final_region

    @classmethod
    def is_foreign_city(cls, city: Optional[str]) -> bool:
        """Checks whether the city is a non-Russian foreign city."""
        if not city:
            return False
        c = city.strip().lower()
        if any(c.isascii() and c.isalpha() for c in c) and not any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in c):
            if c not in ("moscow", "saint petersburg", "spb", "kazan", "sochi", "samara", "novosibirsk"):
                return True
        geo = cls.get_geo_info(c)
        if geo and geo.country != "Россия":
            return True
        return False

    @classmethod
    def parse_and_clean_location(cls, raw_text: str) -> Dict[str, Any]:
        """
        Strictly separates geography and timezone from user/AI input.
        Never puts timezone, continent, or country name into city/district.
        Derives canonical city, country, region, and IANA timezone.
        Examples:
          'Portland, USA' -> {
               'city': 'Portland',
               'district': '',
               'country': 'США',
               'country_code': 'US',
               'region': 'Орегон',
               'timezone_name': 'America/Los_Angeles',
               'timezone_source': 'AUTO'
          }
          'Charlotte, USA' -> {
               'city': 'Charlotte',
               'district': '',
               'country': 'США',
               'country_code': 'US',
               'region': 'Северная Каролина',
               'timezone_name': 'America/New_York',
               'timezone_source': 'AUTO'
          }
        """
        if not raw_text:
            return {
                "city": "Москва",
                "district": "центр",
                "country": "Россия",
                "country_code": "RU",
                "region": "Москва",
                "timezone_name": "Europe/Moscow",
                "timezone_source": "AUTO",
            }

        cleaned = cls.clean_geographic_string(raw_text)

        # Split into tokens by comma or semicolon
        raw_tokens = [t.strip() for t in re.split(r"[,;]+", cleaned) if t.strip()]

        extracted_country: Optional[str] = None
        extracted_country_code: Optional[str] = None
        suggested_tz: Optional[str] = None

        # 1. Detect and extract country token
        tokens_without_country = []
        for token in raw_tokens:
            t_low = token.lower().strip(".")
            if t_low in KNOWN_COUNTRIES:
                c_name, c_code, c_tz = KNOWN_COUNTRIES[t_low]
                extracted_country = c_name
                extracted_country_code = c_code
                suggested_tz = c_tz
            else:
                tokens_without_country.append(token)

        # 2. Detect and extract US state / region token
        extracted_region: Optional[str] = None
        remaining_tokens = []
        for token in tokens_without_country:
            t_low = token.lower().strip(".")
            if t_low in STATE_LOOKUP_MAP:
                eng_name, ru_name, s_tz = STATE_LOOKUP_MAP[t_low]
                extracted_region = ru_name
                extracted_country = "США"
                extracted_country_code = "US"
                suggested_tz = s_tz
            else:
                remaining_tokens.append(token)

        raw_city = remaining_tokens[0] if remaining_tokens else ""
        raw_district = remaining_tokens[1] if len(remaining_tokens) > 1 else ""

        if not raw_city and extracted_region:
            raw_city = extracted_region
            extracted_region = None
        elif not raw_city:
            raw_city = "Москва"

        # Check Geo Registry for city
        geo = cls.get_geo_info(raw_city)
        if not geo and raw_district:
            # Maybe user entered 'Уэст-Голливуд, Лос-Анджелес'
            geo_d = cls.get_geo_info(raw_district)
            if geo_d:
                geo = geo_d
                raw_city, raw_district = raw_district, raw_city

        cleaned_district = raw_district
        if geo:
            # Preserve English casing if user entered in Latin
            is_city_latin = any(c.isascii() and c.isalpha() for c in raw_city) and not any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in raw_city)
            if is_city_latin:
                canonical_city = raw_city.title()
            else:
                canonical_city = geo.canonical_city

            # Normalize known district aliases
            if raw_district:
                d_low = raw_district.lower().replace("-", " ")
                if "уест" in d_low or "west" in d_low:
                    cleaned_district = "Уэст-Голливуд" if any(c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" for c in raw_district.lower()) else "West Hollywood"
                elif "манхеттен" in d_low:
                    cleaned_district = "Манхэттен"
                elif "васька" in d_low:
                    cleaned_district = "Васильевский остров"

            country = extracted_country or geo.country
            country_code = extracted_country_code or geo.country_code
            region = extracted_region or geo.region
            tz_name = geo.timezone
        else:
            canonical_city = raw_city
            # If user typed Latin city (e.g. 'Portland', 'Charlotte')
            is_city_latin = any(c.isascii() and c.isalpha() for c in raw_city) and not any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in raw_city)
            if is_city_latin:
                canonical_city = raw_city.title()

            if extracted_country:
                country = extracted_country
                country_code = extracted_country_code or "US"
                region = extracted_region
                tz_name = suggested_tz or cls.resolve_timezone_for_city(raw_city, country)
            elif extracted_region:
                country = "США"
                country_code = "US"
                region = extracted_region
                tz_name = suggested_tz or "America/New_York"
            elif is_city_latin:
                # Latin city not recognized in database: default to US, NOT Russia!
                country = "США"
                country_code = "US"
                region = None
                tz_name = cls.resolve_timezone_for_city(raw_city)
                if tz_name == "Europe/Moscow":
                    tz_name = "America/New_York"
            else:
                country = "Россия"
                country_code = "RU"
                region = None
                tz_name = cls.resolve_timezone_for_city(raw_city)

        # STRICT POST-PROCESSING: Guarantee district NEVER contains country or region name!
        if cleaned_district:
            cd_low = cleaned_district.lower().strip(".")
            if (
                cd_low in KNOWN_COUNTRIES
                or cd_low in STATE_LOOKUP_MAP
                or (region and cd_low == region.lower())
                or (country and cd_low == country.lower())
                or cd_low == canonical_city.lower()
                or cd_low in ("usa", "сша", "us", "uk", "россия", "украина", "united states")
            ):
                cleaned_district = ""

        return {
            "city": canonical_city,
            "district": cleaned_district,
            "country": country,
            "country_code": country_code,
            "region": region,
            "timezone_name": tz_name,
            "timezone_source": "AUTO",
        }

    @classmethod
    def validate_persona_location(
        cls,
        city: Optional[str] = None,
        district: Optional[str] = None,
        country: Optional[str] = None,
        region: Optional[str] = None,
        timezone_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validates persona location fields:
        - Confirms no timezone string is present in city, district, country, or region.
        - Confirms no continent is present in city or district.
        - Confirms timezone_name is a valid IANA timezone in pytz.
        Returns cleaned dictionary or raises ValueError if invalid.
        """
        for field_name, val in [("city", city), ("district", district), ("country", country), ("region", region)]:
            if val:
                # Check for timezone leak
                if TIMEZONE_IDENTIFIER_PATTERN.search(str(val)) or "GMT" in str(val) or "UTC" in str(val):
                    raise ValueError(f"Timezone identifier detected in {field_name}: {val}. Timezone must be stored separately.")
                # Check for parenthesized timezones
                if PARENTHESIZED_TIMEZONE_PATTERN.search(str(val)):
                    raise ValueError(f"Parenthesized timezone detected in {field_name}: {val}.")

        # Check for continent in city/district
        for field_name, val in [("city", city), ("district", district)]:
            if val and CONTINENT_NAMES_PATTERN.search(str(val)):
                raise ValueError(f"Continent name detected in {field_name}: {val}. Continent must not appear in city or district.")

        # Validate timezone name
        effective_tz = timezone_name or "Europe/Moscow"
        try:
            pytz.timezone(effective_tz)
        except Exception:
            raise ValueError(f"Invalid IANA timezone name: {timezone_name}")

        return {
            "city": cls.clean_geographic_string(city) if city else "",
            "district": cls.clean_geographic_string(district) if district else "",
            "country": cls.clean_geographic_string(country) if country else "",
            "region": cls.clean_geographic_string(region) if region else "",
            "timezone_name": effective_tz,
        }

    @staticmethod
    def parse_time_str(time_str: str) -> time:
        """Parses 'HH:MM' string to time object."""
        parts = time_str.strip().split(":")
        return time(hour=int(parts[0]), minute=int(parts[1]))

    @classmethod
    def get_local_datetime(cls, tz_name: str, ref_utc_dt: Optional[datetime] = None) -> datetime:
        """
        Gets current datetime in the specified timezone with automatic
        Daylight Saving Time (DST) adjustment for summer/winter transitions.
        """
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Europe/Moscow")

        if ref_utc_dt is None:
            return datetime.now(tz)
        if ref_utc_dt.tzinfo is None:
            ref_utc_dt = pytz.utc.localize(ref_utc_dt)
        return ref_utc_dt.astimezone(tz)

    @classmethod
    def get_timezone_display(cls, tz_name: str, ref_utc_dt: Optional[datetime] = None) -> str:
        """
        Returns human-readable timezone string with current GMT offset and DST status.
        E.g. 'America/New_York (GMT-5, зимнее время)' or 'Europe/London (GMT+1, летнее время BST)'.
        """
        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        utc_offset = local_dt.utcoffset()
        total_seconds = int(utc_offset.total_seconds()) if utc_offset else 0
        sign = "+" if total_seconds >= 0 else "-"
        hours = abs(total_seconds) // 3600
        mins = (abs(total_seconds) % 3600) // 60
        gmt_str = f"GMT{sign}{hours}" if mins == 0 else f"GMT{sign}{hours}:{mins:02d}"

        is_dst = bool(local_dt.dst())
        dst_label = "летнее время" if is_dst else "зимнее / стандартное время"
        return f"{tz_name} ({gmt_str}, {dst_label})"

    @classmethod
    def is_persona_sleeping(
        cls,
        timezone_name: str,
        sleep_start_str: str = "23:00",
        sleep_end_str: str = "08:30",
        ref_dt: Optional[datetime] = None,
    ) -> bool:
        """
        Evaluates whether the persona is currently sleeping strictly against
        the persona's LOCAL CITY TIME (in timezone_name).
        Supports:
          - Overnight sleep windows (e.g. 23:00 - 08:30):
            At 23:30 local -> True (sleeping)
            At 02:00 local -> True (sleeping)
            At 08:30 local -> False (waking up / sleep ended)
            At 15:00 local -> False (awake)
          - Same-day sleep windows (e.g. 13:00 - 15:00):
            13:00 <= current_time < 15:00 -> True
        """
        local_dt = cls.get_local_datetime(timezone_name, ref_dt)
        current_time = local_dt.time()

        start = cls.parse_time_str(sleep_start_str)
        end = cls.parse_time_str(sleep_end_str)

        if start > end:
            # Overnight interval crossing midnight: e.g. 23:00 to 08:30
            # She is asleep from start_time until end_time (exclusive of end_time)
            return current_time >= start or current_time < end
        else:
            # Same-day interval: e.g. 01:00 to 08:00
            return start <= current_time < end

    @classmethod
    def is_sleeping_hours(
        cls,
        tz_name: str,
        sleep_start_str: str = "23:00",
        sleep_end_str: str = "08:30",
        ref_utc_dt: Optional[datetime] = None,
    ) -> bool:
        """Backward-compatible alias for is_persona_sleeping."""
        return cls.is_persona_sleeping(tz_name, sleep_start_str, sleep_end_str, ref_utc_dt)

    @classmethod
    def get_persona_time_context(
        cls, persona: Any, ref_utc_dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Builds dynamic ephemeral time context for persona on each turn.
        Never stores 'current_time' or 'is_sleeping' as stale permanent DB state.
        """
        tz_name = getattr(persona, "timezone_name", "Europe/Moscow") or "Europe/Moscow"
        sleep_start = getattr(persona, "sleep_start", "23:00") or "23:00"
        sleep_end = getattr(persona, "sleep_end", "08:30") or "08:30"

        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        float_hour = local_dt.hour + local_dt.minute / 60.0
        time_str = local_dt.strftime("%H:%M")
        date_str = local_dt.strftime("%Y-%m-%d")

        # Conversational day period
        if 5.0 <= float_hour < 11.0:
            period = "morning"
        elif 11.0 <= float_hour < 17.0:
            period = "daytime"
        elif 17.0 <= float_hour < 22.0:
            period = "evening"
        elif 22.0 <= float_hour < 24.0 or 0.0 <= float_hour < 1.0:
            period = "late evening"
        else:
            period = "night"

        utc_offset = local_dt.utcoffset()
        total_seconds = int(utc_offset.total_seconds()) if utc_offset else 0
        sign = "+" if total_seconds >= 0 else "-"
        offset_str = f"{sign}{abs(total_seconds)//3600:02d}:{(abs(total_seconds)%3600)//60:02d}"

        is_sleeping = cls.is_persona_sleeping(tz_name, sleep_start, sleep_end, ref_utc_dt)

        return {
            "persona_local_time": time_str,
            "persona_local_date": date_str,
            "timezone": tz_name,
            "utc_offset": offset_str,
            "day_period": period,
            "is_sleeping": is_sleeping,
            "sleep_start": sleep_start,
            "sleep_end": sleep_end,
            "raw_local_dt": local_dt,
        }

    @classmethod
    def analyze_dialogue_sleep_state(
        cls,
        persona_tz: str,
        base_sleep_start: str = "23:00",
        base_sleep_end: str = "08:30",
        contact_last_outgoing_at: Optional[datetime] = None,
        contact_last_incoming_at: Optional[datetime] = None,
        incoming_message: Optional[str] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        ref_utc_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Dynamic per-dialogue sleep and bedtime analyzer.
        """
        local_dt = cls.get_local_datetime(persona_tz, ref_utc_dt)
        float_hour = local_dt.hour + local_dt.minute / 60.0
        time_str = local_dt.strftime("%H:%M")

        cleaned_msg = (incoming_message or "").lower().strip()
        is_goodnight_msg = any(
            phrase in cleaned_msg
            for phrase in ["спокойной ночи", "сладких снов", "я спать", "пойду спать", "ложусь спать", "до завтра"]
        )

        # 1. Daytime: 08:30 - 22:30 -> Awake
        if 8.5 <= float_hour < 22.5:
            return {
                "is_sleeping": False,
                "is_winding_down": False,
                "should_say_goodnight": is_goodnight_msg,
                "local_time_str": time_str,
                "phase": "daytime",
            }

        # 2. Deep Night: 01:30 - 08:30 -> Asleep
        if 1.5 <= float_hour < 8.5:
            return {
                "is_sleeping": True,
                "is_winding_down": False,
                "should_say_goodnight": False,
                "local_time_str": time_str,
                "phase": "deep_night",
            }

        # 3. Late Evening / Night transition window: 22:30 - 01:30
        now_dt = ref_utc_dt or datetime.now(pytz.utc)
        if now_dt.tzinfo is None:
            now_dt = pytz.utc.localize(now_dt)

        is_active_dialogue = False
        if contact_last_outgoing_at:
            last_out = contact_last_outgoing_at
            if last_out.tzinfo is None:
                last_out = pytz.utc.localize(last_out)
            gap_minutes = abs((now_dt - last_out).total_seconds()) / 60.0
            if gap_minutes <= 25.0:
                is_active_dialogue = True

        if is_goodnight_msg:
            return {
                "is_sleeping": False,
                "is_winding_down": True,
                "should_say_goodnight": True,
                "local_time_str": time_str,
                "phase": "evening_goodnight",
            }

        if is_active_dialogue:
            if float_hour < 23.75 or (float_hour >= 22.5 and float_hour < 24.0):
                return {
                    "is_sleeping": False,
                    "is_winding_down": float_hour >= 23.0,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "evening_active_chat",
                }
            elif 0.0 <= float_hour < 0.35:
                return {
                    "is_sleeping": False,
                    "is_winding_down": True,
                    "should_say_goodnight": True,
                    "local_time_str": time_str,
                    "phase": "evening_wrapping_up",
                }
            else:
                return {
                    "is_sleeping": True,
                    "is_winding_down": False,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "night_sleep",
                }
        else:
            if float_hour >= 23.0 or float_hour < 8.5:
                return {
                    "is_sleeping": True,
                    "is_winding_down": False,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "night_sleep_cold",
                }
            else:
                return {
                    "is_sleeping": False,
                    "is_winding_down": True,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "evening_wind_down",
                }

    @classmethod
    def calculate_next_morning_time(
        cls, tz_name: str, wake_hour: int = 9, ref_utc_dt: Optional[datetime] = None
    ) -> datetime:
        """Calculates next morning wake-up time in UTC."""
        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        wake_minute = random.randint(12, 38)
        target_local = local_dt.replace(hour=wake_hour, minute=wake_minute, second=0, microsecond=0)
        if target_local <= local_dt:
            target_local += timedelta(days=1)

        try:
            tz = pytz.timezone(tz_name)
            if hasattr(tz, "localize"):
                target_local = tz.normalize(target_local)
        except Exception:
            pass

        return target_local.astimezone(pytz.utc)

    @classmethod
    def is_safe_to_ping(
        cls,
        persona_tz: str,
        persona_sleep_start: str = "23:00",
        persona_sleep_end: str = "08:30",
        contact_tz: Optional[str] = None,
        contact_sleep_start: str = "23:00",
        contact_sleep_end: str = "08:30",
        ref_utc_dt: Optional[datetime] = None,
    ) -> bool:
        """Checks whether both persona and contact are outside their sleep windows."""
        if cls.is_persona_sleeping(persona_tz, persona_sleep_start, persona_sleep_end, ref_utc_dt):
            return False

        effective_contact_tz = contact_tz or persona_tz
        if cls.is_persona_sleeping(effective_contact_tz, contact_sleep_start, contact_sleep_end, ref_utc_dt):
            return False

        return True

    @classmethod
    def detect_city_and_timezone(cls, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Analyzes user message to detect mentions of cities or countries,
        accounting for Russian noun declensions (e.g., 'из Казани', 'в Киеве', 'в Лондоне', 'в Штатах').
        Returns (canonical_city_title, timezone_name) or (None, None).
        """
        cleaned = text.lower()
        for city_name, tz in CITY_TIMEZONE_MAP.items():
            if len(city_name) <= 2:
                pattern = r"\b" + re.escape(city_name) + r"\b"
            elif city_name.endswith(("а", "я", "ь", "е", "й")):
                stem = city_name[:-1]
                pattern = r"\b" + re.escape(stem) + r"[аяыиеюуойьех]*\b"
            else:
                stem = city_name
                pattern = r"\b" + re.escape(stem) + r"[аеуомах]*\b"

            if re.search(pattern, cleaned):
                canonical = city_name.capitalize()
                if city_name == "санкт-петербург":
                    canonical = "Санкт-Петербург"
                elif city_name == "питер":
                    canonical = "Питер"
                elif city_name == "нижний новгород":
                    canonical = "Нижний Новгород"
                elif city_name in ("нью-йорк", "new york", "ньюйорк"):
                    canonical = "Нью-Йорк"
                elif city_name in ("сша", "штаты", "америка", "usa"):
                    canonical = "США"
                elif city_name in ("великобритания", "британия", "англия", "uk"):
                    canonical = "Великобритания"
                elif city_name in ("украина", "киев", "kyiv", "kiev"):
                    canonical = "Киев"
                elif city_name in ("дубай", "dubai", "оаэ", "эмираты", "абу-даби"):
                    canonical = "Дубай"
                elif city_name in GEO_LOOKUP_MAP:
                    canonical = GEO_LOOKUP_MAP[city_name].canonical_city
                return canonical, tz
        return None, None

    @classmethod
    def format_time_context(
        cls, persona_tz: str, contact_tz: Optional[str] = None, ref_utc_dt: Optional[datetime] = None
    ) -> str:
        """
        Generates prompt context describing current time and timezone for persona and contact.
        """
        persona_dt = cls.get_local_datetime(persona_tz, ref_utc_dt)
        persona_time_str = persona_dt.strftime("%H:%M")
        persona_display = cls.get_timezone_display(persona_tz, ref_utc_dt)

        effective_contact_tz = contact_tz or persona_tz
        contact_dt = cls.get_local_datetime(effective_contact_tz, ref_utc_dt)
        contact_time_str = contact_dt.strftime("%H:%M")
        contact_display = cls.get_timezone_display(effective_contact_tz, ref_utc_dt)

        if effective_contact_tz == persona_tz:
            return f"Локальное время в твоем городе: {persona_time_str} ({persona_display})."

        return (
            f"Твое локальное время: {persona_time_str} ({persona_display}). "
            f"Локальное время собеседника: {contact_time_str} ({contact_display})."
        )

    @classmethod
    async def migrate_corrupted_personas(cls, session: Any) -> int:
        """
        Scans all personas in the database:
        - Cleans leaked timezone strings in city/district.
        - Resolves correct canonical city, country, region, and IANA timezone for all cities (America, Europe, CIS, etc.).
        - Normalizes and links sleep schedule (sleep_start, sleep_end) to the persona local timezone.
        Returns the number of updated records.
        """
        from sqlalchemy import select
        from companion.models.entities import Persona

        stmt = select(Persona)
        result = await session.execute(stmt)
        personas = result.scalars().all()
        updated_count = 0
        valid_time_pattern = re.compile(r"^[0-2]\d:[0-5]\d$")

        for p in personas:
            changed = False
            raw_city = p.city or "Москва"
            raw_district = p.district or ""

            # 1. Clean leaked timezones or continents
            has_leak = (
                PARENTHESIZED_TIMEZONE_PATTERN.search(raw_city)
                or TIMEZONE_IDENTIFIER_PATTERN.search(raw_city)
                or PARENTHESIZED_TIMEZONE_PATTERN.search(raw_district)
                or TIMEZONE_IDENTIFIER_PATTERN.search(raw_district)
            )

            # Check if district contains country name (e.g. district='USA' or 'США')
            district_is_country = raw_district.strip().lower() in ("usa", "сша", "us", "uk", "россия", "украина", "united states", "russia")

            geo = cls.get_geo_info(raw_city)

            # Check if foreign city is marked as Russia or Europe/Moscow
            is_foreign = (
                cls.is_foreign_city(raw_city)
                or (p.timezone_name and p.timezone_name.startswith("America/"))
                or district_is_country
                or raw_city.lower() in ("portland", "charlotte", "seattle", "austin", "houston", "dallas", "los angeles", "new york", "london", "paris", "berlin", "warsaw")
            )
            wrong_russia = is_foreign and (p.country == "Россия" or p.timezone_name == "Europe/Moscow" or not getattr(p, "region", None))

            needs_geo_sync = has_leak or district_is_country or wrong_russia
            if geo:
                if (p.timezone_name != geo.timezone and getattr(p, "timezone_source", "AUTO") == "AUTO"):
                    needs_geo_sync = True
                if (geo.country != "Россия" and p.country == "Россия"):
                    needs_geo_sync = True
                if (not getattr(p, "region", None) and geo.region):
                    needs_geo_sync = True
                if (p.city != geo.canonical_city and raw_city.lower() not in (
                    "london", "new york", "los angeles", "paris", "berlin", "warsaw", "portland", "charlotte", "seattle", "austin"
                )):
                    needs_geo_sync = True

            # 2. Normalize sleep schedule
            sleep_start = getattr(p, "sleep_start", None) or "23:00"
            sleep_end = getattr(p, "sleep_end", None) or "08:30"
            if not valid_time_pattern.match(str(sleep_start)):
                sleep_start = "23:00"
                changed = True
            if not valid_time_pattern.match(str(sleep_end)):
                sleep_end = "08:30"
                changed = True

            if p.sleep_start != sleep_start:
                p.sleep_start = sleep_start
                changed = True
            if p.sleep_end != sleep_end:
                p.sleep_end = sleep_end
                changed = True

            if has_leak or needs_geo_sync:
                combined = f"{raw_city}, {raw_district}" if raw_district else raw_city
                cleaned_data = cls.parse_and_clean_location(combined)

                if p.city != cleaned_data["city"]:
                    p.city = cleaned_data["city"]
                    changed = True
                new_district = cleaned_data.get("district") or ""
                if (p.district or "") != new_district:
                    p.district = new_district
                    changed = True
                if p.country != cleaned_data["country"]:
                    p.country = cleaned_data["country"]
                    changed = True
                if getattr(p, "country_code", None) != cleaned_data.get("country_code"):
                    p.country_code = cleaned_data.get("country_code", "RU")
                    changed = True
                if cleaned_data.get("region") and getattr(p, "region", None) != cleaned_data["region"]:
                    p.region = cleaned_data["region"]
                    changed = True
                if getattr(p, "timezone_source", "AUTO") == "AUTO" and cleaned_data.get("timezone_name"):
                    if p.timezone_name != cleaned_data["timezone_name"]:
                        p.timezone_name = cleaned_data["timezone_name"]
                        changed = True

            elif geo and getattr(p, "region", None) != geo.region:
                p.region = geo.region
                changed = True

            if changed:
                updated_count += 1
                logger.info(
                    "[MIGRATION] Updated persona #%d: city='%s', district='%s', country='%s', region='%s', tz='%s', sleep='%s-%s'",
                    p.id, p.city, p.district, p.country, getattr(p, "region", None), p.timezone_name, p.sleep_start, p.sleep_end
                )

        if updated_count > 0:
            await session.commit()
            logger.info("[MIGRATION] Successfully updated %d persona records.", updated_count)

        return updated_count
