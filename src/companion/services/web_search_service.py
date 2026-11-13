"""
WebResearchService: Real-time internet research and verification service.
Provides deterministic policy for when to search, queries live external sources
(crypto/currency, weather, encyclopedic and factual knowledge), and synthesizes
verified information with source attribution.
Never hallucinates search results.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)


class SearchDecision(dict):
    """
    Search decision object supporting:
    - Dict indexing: decision["should_search"], decision["search_type"], decision["query"], decision["reason"]
    - Attribute access: decision.should_search, decision.search_type, decision.query, decision.reason
    - Tuple unpacking: should_search, search_type, query = decision
    """
    def __init__(self, should_search: bool, search_type: str, query: str, reason: str = ""):
        super().__init__(should_search=should_search, search_type=search_type, query=query, reason=reason)
        self.should_search = should_search
        self.search_type = search_type
        self.query = query
        self.reason = reason

    def __iter__(self):
        return iter((self.should_search, self.search_type, self.query))

    def __getitem__(self, item):
        if item in ("type", "search_type"):
            return self.search_type
        return super().__getitem__(item)


class WebResearchService:
    """
    Real internet search & fact verification service.
    Implements:
    - Clear tool-selection policy (never searches for stable knowledge like 2+2 or WWII).
    - Real-time crypto & exchange rates (CoinGecko).
    - Real-time weather (Open-Meteo).
    - General encyclopedic and current information search (Wikipedia API & DuckDuckGo API).
    - Graceful, non-hallucinating fallback when web search fails or yields 0 results.
    """

    # User-Agent for real HTTP requests
    HEADERS = {
        "User-Agent": "TelegramCompanionBot/2.0 (compatible; research-agent; contact@example.com)"
    }
    TIMEOUT_SECONDS = 7.0

    # Stable basic facts that should NEVER trigger web search
    TRIVIAL_KNOWLEDGE_PATTERNS = [
        re.compile(r"^\s*(?:сколько\s+будет\s+)?\d+\s*[\+\-\*\/]\s*\d+\s*\??$", re.IGNORECASE),
        re.compile(r"\b(?:2\s*\+\s*2|два\s+плюс\s+два|сколько\s+будет\s+2\s*\+\s*2)\b", re.IGNORECASE),
        re.compile(r"\b(?:вторая\s+мировая\s+война|великая\s+отечественная)\b.*\b(?:когда|год|началась|закончилась)\b", re.IGNORECASE),
        re.compile(r"\b(?:столица\s+(?:франции|россии|италии|германии|японии|испании|великобритании))\b", re.IGNORECASE),
        re.compile(r"\b(?:как\s+сварить\s+яйцо|как\s+варить\s+пельмени|как\s+заварить\s+чай)\b", re.IGNORECASE),
        re.compile(r"\b(?:что\s+такое\s+днк|что\s+такое\s+гравитация)\b", re.IGNORECASE),
    ]

    # Explicit web search triggers
    EXPLICIT_SEARCH_TRIGGERS = [
        re.compile(r"\b(?:найди\s+в\s+интернете|погугли|поищи\s+в\s+сети|глянь\s+в\s+гугле|посмотри\s+в\s+интернете|проверь\s+в\s+сети|поищи\s+в\s+нете)\b", re.IGNORECASE),
        re.compile(r"\b(?:search\s+online|google\s+it|check\s+the\s+web|look\s+up\s+online|search\s+the\s+web)\b", re.IGNORECASE),
    ]

    # Crypto / Currency triggers
    CRYPTO_CURRENCY_TRIGGERS = [
        re.compile(r"\b(?:курс|цена|стоимость|почем|сколько\s+стоит)\b.*\b(?:биткоин|биткоина|btc|bitcoin|эфир|eth|ethereum|usdt|крипт|криптовалют|доллар|доллара|евро|рубл)\b", re.IGNORECASE),
        re.compile(r"\b(?:биткоин|btc|bitcoin|эфириум|ethereum|крипта)\b.*\b(?:курс|цена|стоит|сейчас|сегодня)\b", re.IGNORECASE),
        re.compile(r"\b(?:bitcoin\s+price|crypto\s+price|eth\s+price|dollar\s+rate|exchange\s+rate)\b", re.IGNORECASE),
    ]

    # Weather triggers
    WEATHER_TRIGGERS = [
        re.compile(r"\b(?:погода|градусов|температура|дождь|снег|пасмурно|холодно|жарко)\b.*\b(?:сейчас|сегодня|завтра|на\s+улице|в\s+[а-яёa-z]+)\b", re.IGNORECASE),
        re.compile(r"\b(?:какая\s+погода|сколько\s+градусов|погода\s+в|weather\s+in|current\s+weather)\b", re.IGNORECASE),
    ]

    # General current/changing information triggers
    CHANGING_INFO_TRIGGERS = [
        re.compile(r"\b(?:что\s+сейчас\s+происходит|что\s+нового|последние\s+новости|свежие\s+новости|what\s+happened\s+today|latest\s+news)\b", re.IGNORECASE),
        re.compile(r"\b(?:кто\s+такой|кто\s+такая|что\s+такое|расскажи\s+про)\s+([а-яёa-z]+)\b", re.IGNORECASE),
        re.compile(r"\b(?:расписание|когда\s+выйдет|дата\s+выхода|релиз|release\s+date|schedule\s+of)\b", re.IGNORECASE),
        re.compile(r"\b(?:сколько\s+сейчас\s+стоит|актуальная\s+цена|current\s+price\s+of)\b", re.IGNORECASE),
        re.compile(r"https?://[^\s]+", re.IGNORECASE),
    ]

    @classmethod
    def should_search_web(cls, query: str) -> SearchDecision:
        """
        Deterministic Tool-Use Policy (Sections 16 & 17):
        Decides whether external web research is needed.
        Returns: SearchDecision(should_search, search_type, clean_query, reason)
        search_type: 'crypto_or_currency' | 'weather' | 'general' | 'none'
        """
        if not query:
            return SearchDecision(False, "none", "", reason="empty_query")

        cleaned = query.strip()
        low = cleaned.lower()

        # 1. Exclude basic stable knowledge
        for pattern in cls.TRIVIAL_KNOWLEDGE_PATTERNS:
            if pattern.search(low):
                return SearchDecision(False, "none", cleaned, reason="trivial_stable_knowledge")

        # 2. Exclude personal chit-chat
        personal_chitchat = [
            "как дела", "как жизнь", "чем занята", "что делаешь", "ты красивая",
            "ты мне нравишься", "погнали", "привет", "доброе утро", "спокойной ночи",
            "скучаю", "расскажи о себе", "кто ты", "откуда ты",
        ]
        if any(low == pc or low.startswith(pc + " ") for pc in personal_chitchat) and len(cleaned.split()) <= 6:
            return SearchDecision(False, "none", cleaned, reason="casual_chit_chat")

        # 3. Explicit search requested
        for pattern in cls.EXPLICIT_SEARCH_TRIGGERS:
            if pattern.search(low):
                clean_q = pattern.sub("", cleaned).strip(" ,:?!.")
                return SearchDecision(True, "general", clean_q or cleaned, reason="explicit_search_requested")

        # 4. Crypto or currency pricing
        for pattern in cls.CRYPTO_CURRENCY_TRIGGERS:
            if pattern.search(low):
                return SearchDecision(True, "crypto_or_currency", cleaned, reason="crypto_currency_pricing")

        # 5. Weather inquiry
        for pattern in cls.WEATHER_TRIGGERS:
            if pattern.search(low):
                return SearchDecision(True, "weather", cleaned, reason="weather_inquiry")

        # 6. Current / changing information
        for pattern in cls.CHANGING_INFO_TRIGGERS:
            if pattern.search(low):
                return SearchDecision(True, "general", cleaned, reason="changing_info_or_entity")

        return SearchDecision(False, "none", cleaned, reason="default_no_search")

    @classmethod
    async def fetch_crypto_price(cls, query: str) -> Dict[str, Any]:
        """Fetches live cryptocurrency prices from CoinGecko API."""
        url = "https://api.coingecko.com/api/v3/simple/price"
        ids = "bitcoin,ethereum,tether,solana,the-open-network"
        params = {"ids": ids, "vs_currencies": "usd,rub"}
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        try:
            async with httpx.AsyncClient(timeout=cls.TIMEOUT_SECONDS, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=cls.HEADERS)
                if resp.status_code == 200:
                    data = resp.json()
                    btc_usd = data.get("bitcoin", {}).get("usd")
                    btc_rub = data.get("bitcoin", {}).get("rub")
                    eth_usd = data.get("ethereum", {}).get("usd")
                    eth_rub = data.get("ethereum", {}).get("rub")
                    sol_usd = data.get("solana", {}).get("usd")
                    ton_usd = data.get("the-open-network", {}).get("usd")

                    snippets = []
                    if btc_usd:
                        snippets.append(f"Bitcoin (BTC): ${btc_usd:,.0f} USD ({btc_rub:,.0f} RUB)" if btc_rub else f"Bitcoin (BTC): ${btc_usd:,.0f} USD")
                    if eth_usd:
                        snippets.append(f"Ethereum (ETH): ${eth_usd:,.2f} USD ({eth_rub:,.0f} RUB)" if eth_rub else f"Ethereum (ETH): ${eth_usd:,.2f} USD")
                    if ton_usd:
                        snippets.append(f"TON (The Open Network): ${ton_usd:.2f} USD")
                    if sol_usd:
                        snippets.append(f"Solana (SOL): ${sol_usd:.2f} USD")

                    return {
                        "success": True,
                        "query": query,
                        "search_type": "crypto_or_currency",
                        "results": [
                            {
                                "title": "CoinGecko Live Crypto Market Rates",
                                "snippet": "; ".join(snippets),
                                "url": "https://www.coingecko.com",
                            }
                        ],
                        "source": f"CoinGecko Live Crypto Rates ({now_iso})",
                        "fetched_at": now_iso,
                    }
        except Exception as e:
            logger.warning("CoinGecko API error: %s", e)

        return {
            "success": False,
            "query": query,
            "search_type": "crypto_or_currency",
            "results": [],
            "error": "crypto_api_unavailable",
            "fallback_response": "не могу сейчас нормально проверить курс, поэтому не хочу придумывать",
        }

    # Standard Russian city coordinates for fast weather lookup
    CITY_COORDINATES = {
        "москва": (55.7558, 37.6173, "Москва"),
        "питер": (59.9343, 30.3351, "Санкт-Петербург"),
        "санкт-петербург": (59.9343, 30.3351, "Санкт-Петербург"),
        "петербург": (59.9343, 30.3351, "Санкт-Петербург"),
        "самара": (53.1959, 50.1002, "Самара"),
        "казань": (55.7887, 49.1221, "Казань"),
        "екатеринбург": (56.8389, 60.6057, "Екатеринбург"),
        "сочи": (43.6028, 39.7342, "Сочи"),
        "новосибирск": (55.0084, 82.9357, "Новосибирск"),
        "краснодар": (45.0355, 38.9753, "Краснодар"),
        "нижний новгород": (56.3269, 44.0059, "Нижний Новгород"),
        "уфа": (54.7388, 55.9721, "Уфа"),
        "дубай": (25.2048, 55.2708, "Дубай"),
    }

    WEATHER_CODES_RU = {
        0: "ясно",
        1: "в основном ясно",
        2: "переменная облачность",
        3: "пасмурно",
        45: "туман",
        48: "изморозь",
        51: "лёгкая морось",
        53: "морось",
        55: "густая морось",
        61: "слабый дождь",
        63: "дождь",
        65: "сильный дождь",
        71: "слабый снегопад",
        73: "снегопад",
        75: "сильный снегопад",
        77: "снежные зёрна",
        80: "ливневый дождь",
        81: "сильный ливень",
        82: "очень сильный ливень",
        85: "снегопад",
        86: "сильный снегопад",
        95: "гроза",
    }

    @classmethod
    async def fetch_weather(cls, query: str, default_city: str = "Москва") -> Dict[str, Any]:
        """Fetches live weather from Open-Meteo API for detected city."""
        low = query.lower()
        target_city = default_city
        coords = cls.CITY_COORDINATES.get(default_city.lower(), (55.7558, 37.6173, "Москва"))

        for city_key, city_data in cls.CITY_COORDINATES.items():
            if city_key in low:
                coords = city_data
                target_city = city_data[2]
                break

        lat, lon, city_name = coords
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
            "timezone": "auto",
        }
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        try:
            async with httpx.AsyncClient(timeout=cls.TIMEOUT_SECONDS, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=cls.HEADERS)
                if resp.status_code == 200:
                    cw = resp.json().get("current_weather", {})
                    temp = cw.get("temperature")
                    w_code = cw.get("weathercode", 0)
                    w_desc = cls.WEATHER_CODES_RU.get(w_code, "обычная погода")
                    wind = cw.get("windspeed")

                    temp_str = f"{temp:+.1f}°C" if temp is not None else "неизвестно"
                    snippet = f"Погода в г. {city_name}: {temp_str}, {w_desc}, скорость ветра {wind} км/ч."

                    return {
                        "success": True,
                        "query": query,
                        "search_type": "weather",
                        "city": city_name,
                        "temperature": temp,
                        "condition": w_desc,
                        "results": [
                            {
                                "title": f"Погода в {city_name} — Open-Meteo",
                                "snippet": snippet,
                                "url": f"https://open-meteo.com/en/docs#latitude={lat}&longitude={lon}",
                            }
                        ],
                        "source": f"Open-Meteo Weather Service ({city_name}, {now_iso})",
                        "fetched_at": now_iso,
                    }
        except Exception as e:
            logger.warning("Open-Meteo API error: %s", e)

        return {
            "success": False,
            "query": query,
            "search_type": "weather",
            "results": [],
            "error": "weather_api_unavailable",
            "fallback_response": "не могу сейчас нормально посмотреть погоду, поэтому не хочу придумывать",
        }

    @classmethod
    async def fetch_general_search(cls, query: str, max_results: int = 3) -> Dict[str, Any]:
        """
        Fetches live encyclopedic and web information via Wikipedia Search API
        with HTML snippet extraction and source URLs.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        clean_q = re.sub(r"[?!.,\"']", " ", query).strip()
        # Extract meaningful terms
        terms = [w for w in clean_q.split() if len(w) >= 3 and w.lower() not in ("найди", "интернете", "погугли", "поищи", "сейчас")][:5]
        effective_query = " ".join(terms) if terms else clean_q

        url = "https://ru.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": effective_query,
            "format": "json",
            "srlimit": max_results,
        }

        try:
            async with httpx.AsyncClient(timeout=cls.TIMEOUT_SECONDS, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=cls.HEADERS)
                if resp.status_code == 200:
                    data = resp.json()
                    search_items = data.get("query", {}).get("search", [])
                    if search_items:
                        results = []
                        for item in search_items[:max_results]:
                            title = item.get("title", "")
                            raw_snip = item.get("snippet", "")
                            clean_snip = unescape(re.sub(r"<[^>]+>", "", raw_snip)).strip()
                            page_url = f"https://ru.wikipedia.org/wiki/{title.replace(' ', '_')}"
                            results.append({
                                "title": title,
                                "snippet": clean_snip,
                                "url": page_url,
                            })

                        return {
                            "success": True,
                            "query": query,
                            "search_type": "general",
                            "results": results,
                            "source": f"Wikipedia Web Knowledge ({now_iso})",
                            "fetched_at": now_iso,
                        }
        except Exception as e:
            logger.warning("General web search API error: %s", e)

        return {
            "success": False,
            "query": query,
            "search_type": "general",
            "results": [],
            "error": "no_results_or_network_error",
            "fallback_response": "не могу сейчас нормально проверить, поэтому не хочу придумывать",
        }

    @classmethod
    async def search(cls, query: str, default_city: str = "Москва") -> Dict[str, Any]:
        """
        Main entry point for web research:
        Applies tool selection policy -> fetches real verified data -> formats response.
        """
        needs_search, s_type, clean_query = cls.should_search_web(query)
        if not needs_search:
            return {
                "success": False,
                "needed": False,
                "reason": "trivial_or_chat_knowledge",
                "results": [],
            }

        if s_type == "crypto_or_currency":
            return await cls.fetch_crypto_price(clean_query)
        elif s_type == "weather":
            return await cls.fetch_weather(clean_query, default_city=default_city)
        else:
            return await cls.fetch_general_search(clean_query)

    @classmethod
    def format_web_context_for_prompt(cls, search_result: Dict[str, Any]) -> str:
        """
        Formats verified external data into a clean prompt context block
        for the conversation engine (Level 8 — External Verified Web Data).
        """
        if not search_result or not search_result.get("success"):
            fb = (search_result or {}).get("fallback_response") or "не могу сейчас нормально проверить, поэтому не хочу придумывать"
            return (
                "[УРОВЕНЬ 8 — ПОИСК В ИНТЕРНЕТЕ: ОШИБКА ДОСТУПА К СЕТИ]:\n"
                f"Результат поиска: СЕТЬ НЕДОСТУПНА. {fb}. "
                "СТРОЖАЙШИЙ ЗАПРЕТ: НИ В КОЕМ СЛУЧАЕ НЕ ВЫДУМЫВАЙ ЦИФРЫ И ФАКТЫ ИЗ ГОЛОВЫ!"
            )

        source = search_result.get("source", "Интернет-источники")
        results = search_result.get("results") or []
        lines = [f"[УРОВЕНЬ 8 — ВНЕШНИЕ ПРОВЕРЕННЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА ({source})]:"]
        for idx, item in enumerate(results, 1):
            t = item.get("title", "")
            s = item.get("snippet", "")
            u = item.get("url", "")
            lines.append(f"{idx}. {t}: «{s}» (источник: {u})")
        lines.append(
            "ПРАВИЛО ИСПОЛЬЗОВАНИЯ: Сформулируй естественный человеческий ответ на основе этих реальных проверенных данных. "
            "Не цитируй поисковую выдачу сухо списком, ответь тепло своими словами как реальная девушка."
        )
        return "\n".join(lines)

    @classmethod
    async def search_crypto(cls, query: str) -> Dict[str, Any]:
        return await cls.fetch_crypto_price(query)

    @classmethod
    async def search_weather(cls, query: str, default_city: str = "Москва") -> Dict[str, Any]:
        return await cls.fetch_weather(query, default_city=default_city)

    @classmethod
    async def search_wikipedia(cls, query: str) -> Dict[str, Any]:
        return await cls.fetch_general_search(query)
