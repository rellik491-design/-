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
    async def build_seven_level_context(
        cls,
        persona: Persona,
        contact: Optional[Contact],
        new_incoming_message: str,
        session: Optional[AsyncSession] = None,
        explicit_recent_history: Optional[List[Dict[str, str]]] = None,
        web_search_context: Optional[str] = None,
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
                prefs_list.append(f"Язык общения: {'Русский' if contact.language == 'ru' else 'Английский'}")
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

        return {
            "prompt_contents": final_prompt_contents,
            "recent_window": recent_window,
            "summary": summary_lines,
            "facts": facts_list,
            "preferences": prefs_list,
            "long_term_memory": ltm_list,
            "context_problem": problem_info,
            "retrieved_history": retrieved_history,
        }
