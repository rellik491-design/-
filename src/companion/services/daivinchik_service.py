"""
Daivinchik (Дайвинчик) Forwarded Profile Detector, Parser, & Bio Generator.
Extracts factual attributes, isolates them from synthetic non-sensitive assumptions,
and generates human-like, non-cliché dating bios with persistent persona consistency.
"""

import hashlib
import json
import logging
import random
import re
from typing import Dict, Any, Optional, Tuple, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.models import DaivinchikProfile

logger = logging.getLogger(__name__)


class DaivinchikService:
    """
    Core service for detecting, parsing, and generating authentic bios
    from forwarded Daivinchik dating profiles.
    """

    # Regex patterns for Daivinchik profile formats
    # Format 1: "Вика, 22, Москва" or "✨ Алина, 24, Санкт-Петербург (Россия)"
    PROFILE_HEADER_REGEX = re.compile(
        r'^(?:[✨🌸🔥☕️💎\s]*)([А-ЯЁA-Z][а-яёa-z]+)[,\s]+(\d{1,2})[,\s]+([А-ЯЁA-Zа-яёa-z\s\-]+?)(?:\s*\(([^)]+)\))?(?:\n|$)',
        re.MULTILINE
    )

    # Format 2: Labeled fields "Имя: Вика\nВозраст: 22\nГород: Москва"
    LABEL_NAME_REGEX = re.compile(r'(?:Имя|Name)[:\s]+([А-ЯЁA-Z][а-яёa-z]+)', re.IGNORECASE)
    LABEL_AGE_REGEX = re.compile(r'(?:Возраст|Age)[:\s]+(\d{1,2})', re.IGNORECASE)
    LABEL_CITY_REGEX = re.compile(r'(?:Город|City)[:\s]+([А-ЯЁA-Zа-яёa-z\s\-]+)', re.IGNORECASE)
    LABEL_COUNTRY_REGEX = re.compile(r'(?:Страна|Country)[:\s]+([А-ЯЁA-Zа-яёa-z\s\-]+)', re.IGNORECASE)

    # Height: "Рост: 168" or "168 см"
    HEIGHT_REGEX = re.compile(r'(?:Рост[:\s]+|рост[:\s]+)?(\d{3})\s*(?:см)?', re.IGNORECASE)

    # Forward signatures
    FORWARD_BOT_KEYWORDS = ("дайвинчик", "leomatchbot", "знакомства", "leo match", "dayvinchik", "bot", "бот")

    @classmethod
    def is_daivinchik_message(cls, message: Dict[str, Any]) -> bool:
        """
        Detects whether an incoming Telegram message is a forwarded or pasted Daivinchik profile.
        """
        text = (message.get("text") or message.get("caption") or "").strip()
        if not text:
            return False

        # 1. Check forward origin / forward headers
        fwd_origin = message.get("forward_origin") or {}
        fwd_user = fwd_origin.get("sender_user") or message.get("forward_from") or {}
        fwd_name = (
            fwd_user.get("first_name", "") + " " +
            fwd_user.get("username", "") + " " +
            str(fwd_origin.get("sender_user_name", "")) + " " +
            str(message.get("forward_sender_name", ""))
        ).lower()

        is_from_bot = any(kw in fwd_name for kw in cls.FORWARD_BOT_KEYWORDS)

        # 2. Check text signature
        has_header = bool(cls.PROFILE_HEADER_REGEX.search(text))
        has_labels = bool(cls.LABEL_NAME_REGEX.search(text) and cls.LABEL_AGE_REGEX.search(text))

        if is_from_bot and (has_header or has_labels or re.search(r'\b\d{2}\b', text)):
            return True

        if has_header or has_labels:
            # Further verification: text contains typical dating keywords or age
            if re.search(r'\b(1[6-9]|[2-5]\d)\b', text):
                return True

        return False

    @classmethod
    def parse_profile(cls, raw_text: str) -> Dict[str, Any]:
        """
        Parses raw text and strictly extracts verified FACTS.
        """
        clean_text = raw_text.strip()
        name: Optional[str] = None
        age: Optional[int] = None
        city: Optional[str] = None
        country: Optional[str] = None
        height: Optional[int] = None
        hobbies: List[str] = []
        occupation: Optional[str] = None
        goals: Optional[str] = None
        remaining_lines: List[str] = []

        # Try Header Regex
        header_match = cls.PROFILE_HEADER_REGEX.search(clean_text)
        lines = clean_text.splitlines()

        if header_match:
            name = header_match.group(1).strip()
            try:
                age = int(header_match.group(2).strip())
            except ValueError:
                age = None
            city = header_match.group(3).strip()
            if header_match.group(4):
                country = header_match.group(4).strip()
            start_line_idx = 0
            for idx, l in enumerate(lines):
                if header_match.group(1) in l:
                    start_line_idx = idx + 1
                    break
            remaining_lines = lines[start_line_idx:]
        else:
            # Try Labeled Regex
            n_m = cls.LABEL_NAME_REGEX.search(clean_text)
            if n_m:
                name = n_m.group(1).strip()
            a_m = cls.LABEL_AGE_REGEX.search(clean_text)
            if a_m:
                try:
                    age = int(a_m.group(1).strip())
                except ValueError:
                    pass
            c_m = cls.LABEL_CITY_REGEX.search(clean_text)
            if c_m:
                city = c_m.group(1).strip()
            ctry_m = cls.LABEL_COUNTRY_REGEX.search(clean_text)
            if ctry_m:
                country = ctry_m.group(1).strip()
            remaining_lines = lines

        # Infer country if city is recognized
        if city and not country:
            c_low = city.lower()
            if any(rc in c_low for rc in ("москва", "питер", "санкт-петербург", "казань", "самара", "екб", "екатеринбург", "новосибирск", "сочи")):
                country = "Россия"
            elif any(uc in c_low for uc in ("киев", "київ", "львов", "львів", "одесса", "харьков")):
                country = "Украина"
            elif any(bc in c_low for bc in ("минск", "гродно", "брест")):
                country = "Беларусь"
            elif any(kc in c_low for kc in ("алматы", "астана")):
                country = "Казахстан"
            elif any(qc in c_low for qc in ("квебек", "монреаль", "торонто")):
                country = "Канада"
            elif any(fc in c_low for fc in ("лион", "париж", "ницца")):
                country = "Франция"

        # Parse remaining description, height, hobbies, occupation
        desc_parts = []
        for line in remaining_lines:
            l = line.strip()
            if not l:
                continue

            # Always extract hobbies / interests from any line
            for kw in ("кофе", "выставки", "прогулки", "кино", "книги", "музыка", "квесты", "путешествия", "спорт", "зал", "сериалы", "игры"):
                if kw in l.lower() and kw not in hobbies:
                    hobbies.append(kw)

            # Height
            if "рост" in l.lower() or re.search(r'\b1[5-9]\d\s*см\b', l, re.IGNORECASE):
                h_m = re.search(r'\b(1[5-9]\d|20\d)\b', l)
                if h_m:
                    height = int(h_m.group(1))
                    continue

            # Occupation
            if any(w in l.lower() for w in ("учусь", "работаю", "дизайнер", "программист", "студент", "it", "маркетолог", "архитектор", "врач")):
                if not occupation:
                    occupation = l
                    continue

            # Goals
            if any(w in l.lower() for w in ("ищу", "цель", "отношения", "общение", "для души", "серьезные")):
                if not goals:
                    goals = l
                    continue

            desc_parts.append(l)

        description = " ".join(desc_parts)

        facts = {
            "name": name,
            "age": age,
            "city": city,
            "country": country,
            "height": height,
            "occupation": occupation,
            "hobbies": hobbies,
            "goals": goals,
            "description": description,
        }

        return facts

    @classmethod
    def generate_consistent_persona_details(cls, facts: Dict[str, Any], seed_hash: str) -> Dict[str, Any]:
        """
        Generates safe, non-sensitive synthetic assumptions to enrich the bio
        and keeps them strictly deterministic and consistent based on seed_hash.
        """
        rng = random.Random(seed_hash)

        lifestyle_options = [
            "люблю уютные кофейни и прогулки по вечернему городу",
            "обожаю спонтанные поездки и душевные разговоры за кофе",
            "ценю людей с хорошим чувством юмора и легким характером",
            "люблю открывать новые атмосферные места и вкусный кофе",
            "за любой кипиш, кроме скучных посиделок дома",
        ]
        humor_options = [
            "с легким сарказмом и самоиронией",
            "с улыбкой и открытым сердцем",
            "с ноткой легкого озорства",
        ]
        interest_options = [
            ["хорошая музыка", "вечерние прогулки", "квесты"],
            ["атмосферные выставки", "фильмы", "уютные кофейни"],
            ["путешествия", "спорт", "вкусная еда"],
            ["дизайн", "эстетика", "душевные разговоры"],
        ]

        synth = {
            "vibe": rng.choice(lifestyle_options),
            "humor_tone": rng.choice(humor_options),
            "interests": rng.choice(interest_options),
        }
        return synth

    @classmethod
    def generate_bio(
        cls,
        facts: Dict[str, Any],
        synth_details: Dict[str, Any],
        style_variant: str = "natural"
    ) -> str:
        """
        Generates a human-sounding, non-cliché dating bio.
        Styles: 'natural', 'short', 'playful', 'serious'.
        """
        name = facts.get("name") or "Алина"
        age = facts.get("age")
        city = facts.get("city") or "Москва"
        occupation = facts.get("occupation")
        hobbies = facts.get("hobbies") or synth_details.get("interests", ["кофе", "прогулки"])
        vibe = synth_details.get("vibe", "люблю уютные кофейни и прогулки")

        age_str = f", {age}" if age else ""
        occ_clean = ""
        if occupation:
            occ_clean = re.sub(r'^(я\s+|работаю\s+|учусь\s+)', '', occupation, flags=re.IGNORECASE).strip()

        # Build bio by style
        if style_variant == "short":
            if occ_clean:
                return f"{name}{age_str}, {city} ✨\n{occ_clean.capitalize()}. {vibe.capitalize()}) За любой движ, кроме душных разговоров."
            return f"{name}{age_str}, {city} ✨\n{vibe.capitalize()}. Напиши, если знаешь где варят лучший кофе)"

        elif style_variant == "playful":
            hob_str = ", ".join(hobbies[:3])
            return (
                f"{name}{age_str}, {city} 😉\n"
                f"Люблю {hob_str} и спорить по мелочам ахах)\n"
                f"Если ты душный — пролистывай, а если легкий на подъем — давай проверим наш вайб ✨"
            )

        elif style_variant == "serious":
            return (
                f"{name}{age_str}, {city}\n"
                f"Для меня важно искреннее общение, взаимное уважение и общие ценности.\n"
                f"{vibe.capitalize()}. Буду рада познакомиться с открытым и интересным человеком 🤍"
            )

        else: # "natural" (default)
            hob_str = f"В свободное время — {', '.join(hobbies[:3])}." if hobbies else ""
            occ_part = f"{occ_clean.capitalize()}. " if occ_clean else ""
            return (
                f"{name}{age_str}, {city} ✨\n"
                f"{occ_part}{vibe.capitalize()}. {hob_str}\n"
                f"Ценю простоту, искренность и хорошее чувство юмора) Напиши мне, расскажи о себе."
            )

    @classmethod
    async def process_daivinchik_forward(
        cls,
        session: AsyncSession,
        user_id: int,
        raw_text: str,
        style_variant: str = "natural"
    ) -> Tuple[DaivinchikProfile, bool]:
        """
        Parses profile, checks for persistent consistency, generates or refreshes bio,
        and saves into database.
        Returns: (profile_model, is_new)
        """
        facts = cls.parse_profile(raw_text)
        # Compute deterministic seed hash based on core facts
        seed_source = f"{facts.get('name')}_{facts.get('age')}_{facts.get('city')}_{facts.get('description', '')[:50]}"
        profile_hash = hashlib.md5(seed_source.encode("utf-8")).hexdigest()

        # Check existing profile in DB for consistency
        stmt = select(DaivinchikProfile).where(
            DaivinchikProfile.user_id == user_id,
            DaivinchikProfile.profile_hash == profile_hash
        )
        res = await session.execute(stmt)
        existing = res.scalars().first()

        is_new = False
        if existing:
            profile = existing
            synth_details = json.loads(profile.generated_details_json or "{}")
            if not synth_details:
                synth_details = cls.generate_consistent_persona_details(facts, profile_hash)
                profile.generated_details_json = json.dumps(synth_details, ensure_ascii=False)
        else:
            is_new = True
            synth_details = cls.generate_consistent_persona_details(facts, profile_hash)
            profile = DaivinchikProfile(
                user_id=user_id,
                profile_hash=profile_hash,
                raw_text=raw_text,
                name=facts.get("name"),
                age=facts.get("age"),
                city=facts.get("city"),
                country=facts.get("country"),
                facts_json=json.dumps(facts, ensure_ascii=False),
                generated_details_json=json.dumps(synth_details, ensure_ascii=False),
                style_variant=style_variant,
            )
            session.add(profile)
            await session.flush()

        # Generate bio
        bio_text = cls.generate_bio(facts, synth_details, style_variant=style_variant)
        profile.current_bio = bio_text
        profile.style_variant = style_variant

        # Update history
        history = json.loads(profile.bio_history_json or "[]")
        if bio_text not in history:
            history.append(bio_text)
            profile.bio_history_json = json.dumps(history[-10:], ensure_ascii=False)

        await session.commit()
        await session.refresh(profile)
        return profile, is_new

    @classmethod
    def format_profile_card(cls, profile: DaivinchikProfile) -> str:
        """
        Formats a structured Telegram presentation card separating FACTS from GENERATED BIO.
        """
        facts = json.loads(profile.facts_json or "{}")
        synth = json.loads(profile.generated_details_json or "{}")

        name = facts.get("name") or "Не указано"
        age = facts.get("age") or "Не указан"
        city = facts.get("city") or "Не указан"
        country = facts.get("country") or "Не указана"
        occ = facts.get("occupation") or "Не указана"
        hobbies = ", ".join(facts.get("hobbies") or []) or "Не указаны"

        style_labels = {
            "natural": "🌿 Естественный (Живой)",
            "short": "📏 Короткий (Ёмкий)",
            "playful": "😜 Игривый (Кокетка)",
            "serious": "💼 Серьезный (Искренний)",
        }
        curr_style = style_labels.get(profile.style_variant, profile.style_variant)

        card = (
            f"🎯 <b>Распознана анкета из Дайвинчика!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>ИЗВЛЕЧЕННЫЕ ФАКТЫ:</b>\n"
            f"• <b>Имя:</b> {name}\n"
            f"• <b>Возраст:</b> {age}\n"
            f"• <b>Город:</b> {city}\n"
            f"• <b>Страна:</b> {country}\n"
            f"• <b>Занятость:</b> {occ}\n"
            f"• <b>Интересы:</b> {hobbies}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✨ <b>СГЕНЕРИРОВАННАЯ АНКЕТА ({curr_style}):</b>\n\n"
            f"<code>{profile.current_bio}</code>\n\n"
            f"<i>💡 Нажмите на текст анкеты, чтобы скопировать. Вы можете настроить стиль кнопками ниже:</i>"
        )
        return card

    @classmethod
    def build_bio_actions_keyboard(cls, profile_id: int) -> Dict[str, Any]:
        """
        Constructs the interactive Telegram inline keyboard for bio customization.
        """
        return {
            "inline_keyboard": [
                [
                    {"text": "🔄 Сгенерировать заново", "callback_data": f"daiv_regen_{profile_id}"},
                ],
                [
                    {"text": "📏 Короче", "callback_data": f"daiv_style_{profile_id}_short"},
                    {"text": "🌿 Естественнее", "callback_data": f"daiv_style_{profile_id}_natural"},
                ],
                [
                    {"text": "😜 Игривее", "callback_data": f"daiv_style_{profile_id}_playful"},
                    {"text": "💼 Серьезнее", "callback_data": f"daiv_style_{profile_id}_serious"},
                ],
                [
                    {"text": "📋 Показать чистый текст", "callback_data": f"daiv_copy_{profile_id}"},
                    {"text": "🏠 Главное меню", "callback_data": "menu_main"},
                ],
            ]
        }
