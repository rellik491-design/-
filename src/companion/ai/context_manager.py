import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from companion.models.entities import Contact, ContactMessage, ContactMemory, ConversationSummary, Persona
from companion.ai.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class ContextManager:
    """
    7-Level Conversation Context Manager & Intelligent Historical Retriever (Phases 8 & 9).

    Explicitly forbids dumping the entire conversation history into Gemini on every turn.
    Constructs context across 7 structured levels:
      1. Current message
      2. Recent conversation window (bounded)
      3. Conversation summary (active topics, unresolved questions)
      4. Important facts about the interlocutor
      5. User / interlocutor preferences
      6. Relevant long-term memory & commitments
      7. Targeted historical messages retrieved ONLY when a context problem/reference is detected

    Retrieval pipeline:
      detect_context_problem()
      -> retrieve_relevant_history()
      -> analyze_history()
      -> update_context()
    """

    # Triggers indicating the user references an older conversation, claims a contradiction, or asks to verify an agreement
    PAST_REFERENCE_PATTERNS = [
        re.compile(r"я\s+(?:же\s+)?(?:уже\s+)?(?:говорил|писал|рассказывал|упоминал|отвечал)", re.IGNORECASE),
        re.compile(r"ты\s+(?:же\s+)?(?:уже\s+)?(?:спрашивала|забыла|писала|говорила|обещала)", re.IGNORECASE),
        re.compile(r"мы\s+(?:же\s+)?(?:уже\s+)?(?:договаривались|обсуждали|решили|говорили)", re.IGNORECASE),
        re.compile(r"(?:помнишь|вспомни|напомни|в\s+прошлый\s+раз|раньше\s+писал|вчера\s+писал|пару\s+дней\s+назад)", re.IGNORECASE),
        re.compile(r"(?:повторяешься|одно\s+и\s+то\s+же|опять\s+спрашиваешь|сколько\s+можно\s+спрашивать)", re.IGNORECASE),
        re.compile(r"(?:как\s+меня\s+зовут|где\s+я\s+живу|из\s+какого\s+я\s+города|кем\s+я\s+работаю|во\s+сколько\s+встречаемся|куда\s+мы\s+идем)", re.IGNORECASE),
    ]

    STOP_WORDS_RU = {
        "привет", "как", "дела", "что", "где", "когда", "почему", "зачем", "кто", "это", "все", "всё",
        "уже", "говорил", "писал", "тебе", "меня", "мне", "мой", "моя", "ты", "вы", "мы", "они", "она",
        "он", "просто", "очень", "давай", "ладно", "хорошо", "понял", "ясно", "помнишь", "забыла", "опять",
    }

    @classmethod
    def detect_context_problem(
        cls,
        new_message: str,
        recent_messages: List[Dict[str, Any]],
        memories: Optional[List[ContactMemory]] = None,
        contact: Optional[Contact] = None,
    ) -> Dict[str, Any]:
        """
        Step 1 of Intelligent History Retrieval:
        Determines whether the current message requires targeted retrieval of older history
        (e.g., 'I already told you...', unclear reference to an old message, contradiction,
        or verification of an earlier agreement).
        """
        if not new_message:
            return {"needs_retrieval": False, "reason": None, "keywords": []}

        text_clean = new_message.strip()
        matched_reasons: List[str] = []

        for pattern in cls.PAST_REFERENCE_PATTERNS:
            if pattern.search(text_clean):
                matched_reasons.append(f"past_reference:{pattern.pattern[:30]}")
                break

        # Check for potential city/fact contradiction if contact already has a detected_city
        if contact and contact.detected_city:
            from companion.services.timezone_service import TimezoneService
            new_city, _ = TimezoneService.detect_city_and_timezone(text_clean)
            if new_city and new_city.lower() != contact.detected_city.lower():
                matched_reasons.append(f"city_contradiction:{contact.detected_city}->{new_city}")

        # Extract meaningful search keywords (nouns/roots >= 4 chars)
        words = re.findall(r"[а-яёa-z0-9]{4,}", text_clean.lower())
        keywords = [w for w in words if w not in cls.STOP_WORDS_RU][:5]

        # Also check if user asks a short question referencing an agreement when funnel_stage != chatting
        if (
            not matched_reasons
            and contact
            and contact.funnel_stage in ("date_proposed", "quest_agreed")
            and any(k in text_clean.lower() for k in ("когда", "во сколько", "где", "куда", "квест", "встреч", "договор"))
        ):
            matched_reasons.append("agreement_verification")

        return {
            "needs_retrieval": len(matched_reasons) > 0,
            "reason": ", ".join(matched_reasons) if matched_reasons else None,
            "keywords": keywords,
        }

    @classmethod
    async def retrieve_relevant_history(
        cls,
        session: Optional[AsyncSession],
        contact_id: Optional[int],
        problem_info: Dict[str, Any],
        recent_message_ids: Optional[List[int]] = None,
        max_results: int = 6,
    ) -> List[Dict[str, Any]]:
        """
        Step 2 of Intelligent History Retrieval:
        Queries only relevant older historical messages outside the recent window
        rather than loading all messages.
        """
        if not problem_info.get("needs_retrieval") or session is None or not contact_id:
            return []

        exclude_ids = set(recent_message_ids or [])
        keywords: List[str] = problem_info.get("keywords", [])

        stmt = (
            select(ContactMessage)
            .where(
                ContactMessage.contact_id == contact_id,
                ContactMessage.is_deleted == False,
            )
            .order_by(desc(ContactMessage.id))
            .limit(80)  # Bounded scan of historical rows
        )
        rows = (await session.execute(stmt)).scalars().all()

        older_rows = [r for r in rows if r.id not in exclude_ids]
        if not older_rows:
            return []

        scored: List[Tuple[int, ContactMessage]] = []
        for r in older_rows:
            msg_lower = (r.text or "").lower()
            score = 0
            for kw in keywords:
                # Match prefix stem (first 4-5 chars) for Russian morphology
                stem = kw[:5] if len(kw) >= 5 else kw
                if stem in msg_lower:
                    score += 3
            # Boost user messages containing factual disclosures or agreements
            if r.sender_role == "user" and any(
                marker in msg_lower
                for marker in ("живу", "работаю", "зовут", "квест", "встрет", "давай", "выходн", "суббот", "воскресен", "кофе")
            ):
                score += 2
            if score > 0:
                scored.append((score, r))

        scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
        selected = [item[1] for item in scored[:max_results]]

        # If no keyword matched specifically (e.g. generic "я же тебе уже говорил!"), grab the earliest 3 user disclosures
        if not selected:
            user_disclosures = [r for r in reversed(older_rows) if r.sender_role == "user"][:3]
            selected = user_disclosures

        selected.sort(key=lambda r: r.id)
        return [
            {
                "id": r.id,
                "role": "user" if r.sender_role == "user" else "model",
                "text": r.text,
                "created_at": r.created_at,
            }
            for r in selected
        ]

    @classmethod
    def analyze_history(
        cls,
        retrieved_messages: List[Dict[str, Any]],
        problem_info: Dict[str, Any],
    ) -> Optional[str]:
        """
        Step 3 of Intelligent History Retrieval:
        Synthesizes the retrieved historical turns into a focused verification note for the AI.
        """
        if not retrieved_messages:
            return None

        reason = problem_info.get("reason") or "context_verification"
        lines: List[str] = [
            f"[ТАРГЕТИРОВАННЫЙ ПОИСК ПО АРХИВУ ДИАЛОГА (Причина: {reason})]:"
        ]
        for msg in retrieved_messages:
            speaker = "Парень ранее писал" if msg.get("role") == "user" else "Ты ранее отвечала"
            lines.append(f"- {speaker}: «{msg.get('text', '')}»")
        lines.append(
            "ВАЖНО: Обязательно учти эти более ранние сообщения, чтобы не повторять вопрос и не противоречить тому, что уже обсуждалось!"
        )
        return "\n".join(lines)

    @classmethod
    def update_context(
        cls,
        context_blocks: List[str],
        history_analysis: Optional[str],
    ) -> str:
        """
        Step 4 of Intelligent History Retrieval:
        Merges the multi-level context blocks with any targeted historical retrieval analysis.
        """
        all_blocks = list(context_blocks)
        if history_analysis:
            all_blocks.append(history_analysis)
        return "\n\n".join(b for b in all_blocks if b)

    @classmethod
    def detect_conversation_thread(
        cls,
        text: str,
        recent_window: List[Dict[str, Any]],
        contact: Optional[Contact] = None,
    ) -> str:
        """
        Detects active topic thread (Section 22):
        - relocation_immigrant: moving, visas, life abroad, living in another country
        - work_career: jobs, projects, office, freelance, studies
        - travel_trips: vacations, flights, weekend trips
        - dating_meetup: meetup, quest, coffee, dating app context
        - casual_lifestyle: daily life, coffee, music, leisure, banter
        """
        low = (text or "").lower()
        if any(w in low for w in ["переезд", "переехал", "переїхав", "лондон", "виза", "віза", "за границей", "за кордоном", "иммигрант", "эмиграц", "адаптац", "чужая страна"]):
            return "relocation_immigrant"
        if any(w in low for w in ["работ", "робот", "офис", "офіс", "проект", "фриланс", "начальник", "зарплат", "собеседован", "співбесід"]):
            return "work_career"
        if any(w in low for w in ["поездк", "поїздк", "билет", "квиток", "самолет", "літак", "отпуск", "відпустк", "отель", "готель"]):
            return "travel_trips"
        if any(w in low for w in ["встреч", "зустріч", "свидани", "побаченн", "кофе", "кава", "квест", "ужин", "вечеря"]):
            return "dating_meetup"
        return "casual_lifestyle"

    @classmethod
    async def build_seven_level_context(
        cls,
        persona: Persona,
        contact: Optional[Contact],
        new_incoming_message: str,
        session: Optional[AsyncSession] = None,
        explicit_recent_history: Optional[List[Dict[str, str]]] = None,
        web_search_context: Optional[str] = None,
        dynamic_language_profile: Optional[Any] = None,
        contact_deflection: Optional[Any] = None,
        user_geo_profile: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Builds the complete 7-level context package for Gemini:
          1. Current message
          2. Recent conversation (bounded window)
          3. Conversation summary
          4. Important facts
          5. User preferences
          6. Relevant long-term memory & commitments
          7. Targeted historical retrieval (only when needed)
        """
        contact_id = contact.id if contact else None

        # Level 2: Recent conversation window
        if explicit_recent_history is not None and len(explicit_recent_history) > 0:
            recent_window = explicit_recent_history[-12:]
        else:
            recent_window = await MemoryManager.get_recent_messages(session, contact_id, limit=10)

        # Load Long-Term Memories & Summary from DB when session is available
        memories: List[ContactMemory] = []
        summary_rec: Optional[ConversationSummary] = None
        if session is not None and contact_id:
            memories = await MemoryManager.get_long_term_memories(session, contact_id)
            stmt_sum = select(ConversationSummary).where(ConversationSummary.contact_id == contact_id)
            summary_rec = (await session.execute(stmt_sum)).scalars().first()

        # Separate memories into Level 4 (Facts), Level 5 (Preferences), Level 6 (Long-term memory / agreements / habits)
        facts_list: List[str] = []
        prefs_list: List[str] = []
        ltm_list: List[str] = []

        if contact:
            if contact.first_name:
                facts_list.append(f"Имя в профиле: {contact.first_name}")
            if contact.detected_city:
                facts_list.append(f"Город проживания: {contact.detected_city}")
            if contact.origin_city:
                facts_list.append(f"Родной город: {contact.origin_city}")
            if contact.client_free_time:
                facts_list.append(f"График / свободное время: {contact.client_free_time}")
            if contact.no_brackets:
                prefs_list.append("Строго просил писать БЕЗ скобочек ')' и '('")
            if contact.language:
                lang_name = "Украинский" if contact.language == "uk" else ("Смешанный (суржик)" if contact.language == "mixed" else ("Английский" if contact.language == "en" else "Русский"))
                prefs_list.append(f"Язык общения: {lang_name}")
            if contact.funnel_stage and contact.funnel_stage != "chatting":
                ltm_list.append(f"Текущий этап воронки свидания: {contact.funnel_stage}")

        for mem in memories:
            entry = f"{mem.key}: {mem.value}"
            if mem.category == "fact":
                facts_list.append(entry)
            elif mem.category == "preference":
                prefs_list.append(entry)
            else:
                ltm_list.append(f"[{mem.category}] {entry}")

        # Level 3: Conversation summary
        summary_lines: List[str] = []
        if summary_rec:
            if summary_rec.summary_text:
                summary_lines.append(f"Краткая суть прошлых тем: {summary_rec.summary_text}")
            if summary_rec.active_topics:
                summary_lines.append(f"Активные темы: {summary_rec.active_topics}")
            if summary_rec.unresolved_questions:
                summary_lines.append(f"Незакрытые вопросы: {summary_rec.unresolved_questions}")
            if summary_rec.commitments:
                ltm_list.append(f"Договоренности: {summary_rec.commitments}")
        elif contact and (contact.dialogue_summary or contact.dialogue_topics):
            if contact.dialogue_summary:
                summary_lines.append(f"Краткая суть диалога: {contact.dialogue_summary}")
            if contact.dialogue_topics:
                summary_lines.append(f"Обсуждавшиеся темы: {contact.dialogue_topics}")

        # Level 7: Intelligent Historical Retrieval (only when a context problem / past reference is detected)
        problem_info = cls.detect_context_problem(
            new_message=new_incoming_message,
            recent_messages=recent_window,
            memories=memories,
            contact=contact,
        )
        recent_ids = [m["id"] for m in recent_window if isinstance(m, dict) and "id" in m]
        retrieved_history: List[Dict[str, Any]] = []
        history_analysis: Optional[str] = None
        if problem_info["needs_retrieval"]:
            retrieved_history = await cls.retrieve_relevant_history(
                session=session,
                contact_id=contact_id,
                problem_info=problem_info,
                recent_message_ids=recent_ids,
            )
            history_analysis = cls.analyze_history(retrieved_history, problem_info)

        # Assemble structured context blocks
        context_blocks: List[str] = []

        # Level 0: Active Conversation Thread (Section 22)
        active_thread = cls.detect_conversation_thread(new_incoming_message, recent_window, contact)
        thread_descriptions = {
            "relocation_immigrant": "Переезд, жизнь за границей, адаптация, визы, сравнение стран и городов",
            "work_career": "Работа, карьера, проекты, офис, учеба, загрузка",
            "travel_trips": "Путешествия, поездки, отпуск, рейсы, отели",
            "dating_meetup": "Встречи, кофе, квесты, романтика, прогулки",
            "casual_lifestyle": "Повседневная жизнь, отдых, сериалы, музыка, юмор",
        }
        thread_desc = thread_descriptions.get(active_thread, "Общение и знакомство")
        thread_block = (
            f"[УРОВЕНЬ 0 — АКТИВНАЯ ТЕМА ДИАЛОГА (СТРОГАЯ СВЯЗНОСТЬ)]:\n"
            f"• Текущая тема: {active_thread} ({thread_desc}).\n"
            f"• ПРАВИЛО: Активная тема должна доминировать в ответе! Отвечай связно, не теряй нить разговора и не перескакивай на случайные фразы."
        )
        context_blocks.append(thread_block)

        # Immigrant Geography Block: Origin != Location != Timezone != Language (Sections 1-8, 31-34)
        from companion.services.user_geography_service import UserGeoProfile, UserGeographyService
        effective_geo = user_geo_profile
        if isinstance(effective_geo, dict):
            effective_geo = UserGeoProfile.from_dict(effective_geo)
        if not effective_geo and contact:
            raw_geo = contact.get_geo_profile()
            effective_geo = UserGeoProfile.from_dict(raw_geo) if isinstance(raw_geo, dict) else raw_geo
            if effective_geo:
                effective_geo.origin_country = effective_geo.origin_country or getattr(contact, "origin_country", None)
                effective_geo.origin_city = effective_geo.origin_city or getattr(contact, "origin_city", None)
                effective_geo.current_country = effective_geo.current_country or getattr(contact, "current_country", None)
                effective_geo.current_city = effective_geo.current_city or getattr(contact, "current_city", None) or getattr(contact, "detected_city", None)
                effective_geo.timezone = effective_geo.timezone or getattr(contact, "detected_timezone", None)
        if effective_geo and (
            effective_geo.origin_country
            or effective_geo.origin_city
            or effective_geo.current_country
            or effective_geo.current_city
        ):
            geo_block = UserGeographyService.format_geo_prompt_block(effective_geo, persona)
            if geo_block:
                context_blocks.append(geo_block)

        # Persona Lore & Geographic Connection (same country, different city, family tie, dynamic visit)
        from companion.services.persona_lore_service import PersonaLoreService
        lore_resolution = PersonaLoreService.resolve_geographic_connection(
            persona=persona,
            contact=contact,
            user_geo_profile=effective_geo,
            incoming_message=new_incoming_message,
            active_thread=active_thread,
        )
        lore_block = PersonaLoreService.format_persona_lore_prompt_block(lore_resolution, persona)
        if lore_block:
            context_blocks.append(lore_block)

        # Meeting Planner (Cinema / Quest preference, synchronized city catalog, owner handoff)
        from companion.services.meeting_planner_service import MeetingPlannerService
        meeting_resolution = MeetingPlannerService.evaluate_meeting_turn(
            persona=persona,
            contact=contact,
            incoming_message=new_incoming_message,
            user_geo_profile=effective_geo,
            lore_resolution=lore_resolution,
        )
        meeting_block = MeetingPlannerService.format_meeting_planner_prompt_block(meeting_resolution, persona)
        if meeting_block:
            context_blocks.append(meeting_block)

        # Negative / Correction Memory (Highest Priority: Sections 18.E, 25, 30)
        corrections_list: List[str] = []
        if contact:
            corrections_list.extend(contact.get_correction_events())
        for mem in memories:
            if mem.category == "correction":
                corrections_list.append(f"{mem.key}: {mem.value}")
        if corrections_list:
            corr_block = (
                "[ВЫСШИЙ ПРИОРИТЕТ — ИСПРАВЛЕНИЯ И НЕГАТИВНАЯ ПАМЯТЬ]:\n"
                "• Собеседник прямо исправил бота в прошлых сообщениях:\n"
                + "\n".join(f"  - {c}" for c in corrections_list[-4:])
                + "\n• СТРОГОЕ ПРАВИЛО: Эти исправления имеют наивысший приоритет! Категорически запрещено повторять старую ошибку!"
            )
            context_blocks.append(corr_block)

        if summary_lines:
            context_blocks.append("[УРОВЕНЬ 3 — СВОДКА ДИАЛОГА]:\n" + "\n".join(f"• {s}" for s in summary_lines))
        if facts_list:
            context_blocks.append("[УРОВЕНЬ 4 — ИЗВЕСТНЫЕ ФАКТЫ О СОБЕСЕДНИКЕ]:\n" + "\n".join(f"• {f}" for f in facts_list))
        if prefs_list:
            context_blocks.append("[УРОВЕНЬ 5 — ПРЕДПОЧТЕНИЯ СОБЕСЕДНИКА]:\n" + "\n".join(f"• {p}" for p in prefs_list))

        # Personal profile block (persistent communication style & learned preferences)
        if contact:
            from companion.services.user_profile_service import UserProfileService
            profile_prompt = UserProfileService.format_profile_prompt_block(contact)
            if profile_prompt:
                context_blocks.append(profile_prompt)

        if ltm_list:
            context_blocks.append("[УРОВЕНЬ 6 — ДОЛГОСРОЧНАЯ ПАМЯТЬ И ДОГОВОРЕННОСТИ]:\n" + "\n".join(f"• {m}" for m in ltm_list))

        # Level 8: External verified web data (CoinGecko rates, live weather, Wikipedia encyclopedia)
        if web_search_context:
            context_blocks.append(f"[УРОВЕНЬ 8 — ПРОВЕРЕННЫЕ ДАННЫЕ ИЗ ВНЕШНЕГО МИРА (ВЕБ-ПОИСК)]:\n{web_search_context}")

        # Dynamic Language & Style Mirroring Block
        if dynamic_language_profile:
            p_lang = getattr(dynamic_language_profile, "primary_language", "ru")
            s_level = getattr(dynamic_language_profile, "slang_level", 0.5)
            p_level = getattr(dynamic_language_profile, "profanity_level", 0.0)
            translit = getattr(dynamic_language_profile, "transliteration", False)
            script_val = getattr(dynamic_language_profile, "script", "cyrillic")
            lang_name = "украинском" if p_lang == "uk" else ("английском" if p_lang == "en" else ("смешанном (суржик)" if p_lang == "mixed" else "русском"))
            lang_block = (
                f"[ДИНАМИЧЕСКИЙ ЯЗЫКОВОЙ ПРОФИЛЬ И ЗЕРКАЛИРОВАНИЕ СТИЛЯ]:\n"
                f"• Язык ответа: строго на {lang_name} языке ({p_lang.upper()})! Не переводи речь собеседника на другой язык.\n"
                f"• Уровень сленга: {s_level:.1f} (отражай непринужденный молодежный стиль, понимай 'гс', 'хз', 'мб', 'спс', 'пж').\n"
                f"• Письменность: {'собеседник пишет латиницей (транслитом), но отвечай на чистой кириллице' if translit else ('латиница' if script_val == 'latin' else 'кириллица')}."
            )
            context_blocks.append(lang_block)

        # Anti-Instagram / Contact Deflection Block
        if contact_deflection and getattr(contact_deflection, "deflection_required", False):
            tone = getattr(contact_deflection, "suggested_deflection_tone", "playful_dodge")
            c_type = getattr(contact_deflection, "contact_type", "instagram")
            count = getattr(contact_deflection, "request_count", 1)
            deflection_instruction = (
                f"[ПОВЕДЕНЧЕСКИЙ БЛОК: УВОД ОТ ЗАПРОСА КОНТАКТОВ / ИНСТАГРАМА (УРОВЕНЬ {count})]:\n"
                f"• Запрос собеседника: {c_type} (попытка получить личные соцсети, ник или номер).\n"
                f"• СТРОЖАЙШЕЕ ПРАВИЛО: НИ В КОЕМ СЛУЧАЕ НЕ ДАВАЙ, НЕ ПРИДУМЫВАЙ, НЕ НАМЕКАЙ И НЕ ПОДТВЕРЖДАЙ СВОЙ INSTAGRAM, WHATSAPP, НОМЕР ИЛИ ТЕЛЕГРАМ-НИК!\n"
                f"• КАТЕГОРИЧЕСКИЙ ЗАПРЕТ РОБОТИЗИРОВАННЫХ ОТКАЗОВ: никаких «Я не даю инсту», «У меня нет инсты», «Политика запрещает»!\n"
                f"• КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ЛЖИВЫХ ОТМАЗОК: никаких «забыла пароль», «телефон сломался», «заблокировали»!\n"
                f"• НЕ ОТВЕЧАЙ, ЕСТЬ ЛИ ИНСТАГРАМ: если спрашивают «у тебя вообще есть инста?» — уходи от прямого ответа без подтверждения или отрицания!\n"
                f"• СТРАТЕГИЯ УВОДА (РЕЖИМ: {tone.upper()}):\n"
                f"  1. Отреагируй легко и естественно (с юмором, лёгким поддразниванием или флиртом: «ахах, а тебе зачем так срочно инста 😭», «куда ты меня уже тащишь ахах»).\n"
                f"  2. НЕ отвечай на сам запрос контакта.\n"
                f"  3. Перенаправь внимание на текущий разговор в Telegram («давай сначала тут нормально пообщаемся 😌», «не торопись, мы ещё тут не договорили»).\n"
                f"  4. Если запрос повторный (уровень {count} >= 3) — обозначь спокойную или твердую личную границу без роботоподобности."
            )
            context_blocks.append(deflection_instruction)

        # Format Level 2 (Recent Conversation) + Level 1 (Current Message)
        recent_lines: List[str] = []
        for turn in recent_window[-10:]:
            role_label = "Парень" if turn.get("role") == "user" or turn.get("sender_role") == "user" else persona.name
            recent_lines.append(f"{role_label}: {turn.get('text', '')}")

        # Avoid duplicating the current message if it was just saved to DB before calling generate_reply
        if not recent_lines or not recent_lines[-1].endswith(new_incoming_message.strip()):
            recent_lines.append(f"Парень (ТЕКУЩЕЕ СООБЩЕНИЕ): {new_incoming_message}")

        context_blocks.append(
            "[УРОВЕНЬ 2 и 1 — НЕДАВНИЙ КОНТЕКСТ И ТЕКУЩЕЕ СООБЩЕНИЕ]:\n"
            + "\n".join(recent_lines)
            + f"\n{persona.name}:"
        )

        final_prompt_contents = cls.update_context(context_blocks, history_analysis)

        # Build Final Internal Context Object (Sections 9, 35)
        internal_context = {
            "conversation": {
                "chat_id": contact.tg_id if contact else None,
                "active_thread": active_thread,
                "recent_messages_count": len(recent_window),
                "unresolved_questions": [summary_rec.unresolved_questions] if summary_rec and summary_rec.unresolved_questions else [],
            },
            "user": {
                "origin_country": (getattr(effective_geo, "origin_country", None) if effective_geo else None) or (contact.origin_country if contact else None),
                "origin_city": (getattr(effective_geo, "origin_city", None) if effective_geo else None) or (contact.origin_city if contact else None),
                "current_country": (getattr(effective_geo, "current_country", None) if effective_geo else None) or (contact.current_country if contact else None),
                "current_city": (getattr(effective_geo, "current_city", None) if effective_geo else None) or (contact.current_city or (contact.detected_city if contact else None)),
                "timezone": (getattr(effective_geo, "timezone", None) if effective_geo else None) or (contact.detected_timezone if contact else None) or "Europe/Kyiv",
                "languages": getattr(effective_geo, "languages", {}) if effective_geo else {},
            },
            "language": {
                "current": getattr(dynamic_language_profile, "primary_language", contact.language if contact else "ru"),
                "confidence": getattr(dynamic_language_profile, "language_confidence", 0.95),
                "style": "casual",
            },
            "temporal_context": {
                "timezone": getattr(effective_geo, "timezone", "Europe/Kyiv") if effective_geo else (contact.detected_timezone if contact else "Europe/Kyiv"),
            },
            "intent": {
                "active_thread": active_thread,
                "needs_retrieval": problem_info.get("needs_retrieval", False),
            },
            "memory": {
                "facts": facts_list,
                "preferences": prefs_list,
                "corrections": corrections_list,
            },
            "message_control": {
                "bot_must_not_answer_own_message": True,
                "target_author": "USER",
            },
            "persona_lore": {
                "matched": lore_resolution.matched,
                "geographic_relation": lore_resolution.geographic_relation,
                "natural_meeting_opportunity": lore_resolution.natural_meeting_opportunity,
                "travel": lore_resolution.travel,
                "relevant_now": lore_resolution.relevant_now,
                "cooldown_active": lore_resolution.cooldown_active,
            },
            "meeting_planner": {
                "meeting_control": meeting_resolution.meeting_control,
                "stage": meeting_resolution.stage,
                "meeting_city": meeting_resolution.meeting_city,
                "activity_type": meeting_resolution.activity_type,
                "selected_options": meeting_resolution.selected_options,
                "handoff_required": meeting_resolution.handoff_required,
                "handoff_payload": meeting_resolution.handoff_payload,
            },
        }

        return {
            "prompt_contents": final_prompt_contents,
            "recent_window": recent_window,
            "summary": summary_lines,
            "facts": facts_list,
            "preferences": prefs_list,
            "long_term_memory": ltm_list,
            "context_problem": problem_info,
            "retrieved_history": retrieved_history,
            "internal_context": internal_context,
            "active_thread": active_thread,
        }
