import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

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
        Resolves the IANA timezone name for a given city or region.
        Never guesses timezone based on interface language.
        Accurately maps:
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

        # 3. Check country if provided
        if country:
            country_clean = cls.clean_geographic_string(country).strip().lower()
            geo_c = cls.get_geo_info(country_clean)
            if geo_c:
                return geo_c.timezone

        # 4. Direct IANA validation via pytz (e.g. if user passed valid IANA string)
        try:
            pytz.timezone(city)
            return city
        except Exception:
            pass

        return "Europe/Moscow"

    @classmethod
    def parse_and_clean_location(cls, raw_text: str) -> Dict[str, Any]:
        """
        Strictly separates geography and timezone from user/AI input.
        Never puts timezone or continent into city/district.
        Derives canonical city, country, region, and IANA timezone.
        Example:
          'Лос Анджелес, Уест-Голливуд (Europe/Moscow)'
          -> {
               'city': 'Лос-Анджелес',
               'district': 'Уэст-Голливуд',
               'country': 'США',
               'country_code': 'US',
               'region': 'Калифорния',
               'timezone_name': 'America/Los_Angeles',
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

        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        raw_city = parts[0] if parts else "Москва"
        raw_district = parts[1] if len(parts) > 1 else ""

        # Normalize city via Geo Registry
        geo = cls.get_geo_info(raw_city)
        if not geo and raw_district:
            # Maybe user entered 'Уэст-Голливуд, Лос-Анджелес'
            geo = cls.get_geo_info(raw_district)
            if geo:
                raw_city, raw_district = raw_district, raw_city

        if geo:
            canonical_city = geo.canonical_city
            # If user entered in English (e.g. 'London'), preserve English form if city matches
            if raw_city.lower() in ("london", "new york", "los angeles", "paris", "berlin", "warsaw"):
                canonical_city = raw_city.title()
            
            # Clean district: normalize if known typo e.g. "Уест-Голливуд" -> "Уэст-Голливуд"
            cleaned_district = raw_district
            if raw_district:
                d_low = raw_district.lower().replace("-", " ")
                if "уест" in d_low or "west" in d_low:
                    cleaned_district = "Уэст-Голливуд" if any(c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" for c in raw_district.lower()) else "West Hollywood"
                elif "манхеттен" in d_low:
                    cleaned_district = "Манхэттен"
                elif "васька" in d_low:
                    cleaned_district = "Васильевский остров"

            country = geo.country
            country_code = geo.country_code
            region = geo.region
            tz_name = geo.timezone
        else:
            canonical_city = raw_city
            cleaned_district = raw_district
            country = "Россия"
            country_code = "RU"
            region = None
            tz_name = cls.resolve_timezone_for_city(raw_city)

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

            geo = cls.get_geo_info(raw_city)

            # Check if geography or timezone or region needs update
            needs_geo_sync = False
            if geo:
                if (p.timezone_name != geo.timezone and getattr(p, "timezone_source", "AUTO") == "AUTO"):
                    needs_geo_sync = True
                if (geo.country != "Россия" and p.country == "Россия"):
                    needs_geo_sync = True
                if (not getattr(p, "region", None) and geo.region):
                    needs_geo_sync = True
                if (p.city != geo.canonical_city and raw_city.lower() not in ("london", "new york", "los angeles", "paris", "berlin", "warsaw")):
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
                if cleaned_data["district"] and p.district != cleaned_data["district"]:
                    p.district = cleaned_data["district"]
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
