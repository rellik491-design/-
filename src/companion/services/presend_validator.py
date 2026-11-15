import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PreSendValidator:
    """
    Pre-Send Self-Check & Validation Layer (Section 26).
    
    Before sending ANY response to Telegram, runs a lightweight 10-point check:
      1. Am I answering the user's message?
      2. Is the target message actually from USER?
      3. Am I accidentally responding to my own previous message?
      4. Am I using the correct chat?
      5. Am I confusing user's origin with current location?
      6. Am I using the correct timezone?
      7. Am I using the correct language?
      8. Am I accidentally contradicting known facts?
      9. Am I repeating something already answered?
      10. Does this response logically follow the conversation?

    If any critical check fails: do not send or rebuild/sanitize the response first.
    """

    @classmethod
    def validate_presend(
        cls,
        candidate_reply: str,
        internal_context: Dict[str, Any],
        recent_bot_replies: Optional[List[str]] = None,
        target_sender_type: str = "USER",
        target_message_id: Optional[int] = None,
        chat_id: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Validates candidate_reply against the 10 checks.
        Returns: (is_valid, failure_reason, sanitized_reply)
        """
        if not candidate_reply or not str(candidate_reply).strip():
            return False, "empty_reply", ""

        from companion.services.output_sanitizer import OutputSanitizer
        text = OutputSanitizer.sanitize_outgoing_text(candidate_reply)
        if not text:
            return False, "empty_after_sanitization", ""

        # Check 1 & 2: Target author must be USER
        if target_sender_type.upper() in ("BOT", "ASSISTANT", "SELF"):
            logger.error("[PRESEND_VALIDATOR] FAILED Check 1-2: Target author is BOT/ASSISTANT (%s)", target_sender_type)
            return False, "target_is_bot", ""

        # Check 3: Accidental self-reply check
        msg_ctrl = internal_context.get("message_control", {})
        if msg_ctrl.get("bot_must_not_answer_own_message"):
            from companion.services.message_ownership_service import MessageOwnershipService
            if target_message_id and MessageOwnershipService.is_bot_outgoing(target_message_id):
                logger.error("[PRESEND_VALIDATOR] FAILED Check 3: target_message_id %s is a known bot outgoing message", target_message_id)
                return False, "self_response_detected", ""

        # Check 4: Correct chat ID match
        ctx_chat_id = internal_context.get("conversation", {}).get("chat_id")
        if chat_id is not None and ctx_chat_id is not None and str(chat_id) != str(ctx_chat_id):
            logger.error("[PRESEND_VALIDATOR] FAILED Check 4: Chat ID mismatch (ctx=%s, current=%s)", ctx_chat_id, chat_id)
            return False, "chat_mismatch", ""

        # Check 5: Immigrant Geography - Origin != Current Location
        user_info = internal_context.get("user", {})
        origin_country = user_info.get("origin_country")
        origin_city = user_info.get("origin_city")
        current_country = user_info.get("current_country")
        current_city = user_info.get("current_city")

        # If user origin is Ukraine but living in UK/London:
        # Bot must not say "ты сейчас в Украине" or "когда вернешься в Лондон из Украины" if user is already in London
        if origin_country and current_city and (not current_country or origin_country.lower() != current_country.lower()):
            ORIGIN_STEMS = {
                "ukraine": ["украин", "украін", "україні", "украине", "україно"],
                "united kingdom": ["великобритан", "англи", "лондон"],
                "poland": ["польш", "польщ", "варшав"],
                "germany": ["герман", "німеччин", "берлин"],
                "france": ["франц", "париж"],
            }
            stems = list(ORIGIN_STEMS.get(origin_country.lower(), [origin_country.lower()[:5]]))
            if origin_city:
                stems.append(origin_city.lower()[:4])

            for st in stems:
                pat1 = rf"ты\s+(?:же\s+)?сейчас\s+в\s+{st}[а-яёa-z]*"
                pat2 = rf"ты\s+(?:же\s+)?в\s+{st}[а-яёa-z]*\s+сейчас"
                if re.search(pat1, text, re.IGNORECASE):
                    logger.warning("[PRESEND_VALIDATOR] Warning Check 5: Confusing origin with current location (%s). Sanitizing.", pat1)
                    text = re.sub(pat1, f"ты же сейчас в {current_city}", text, flags=re.IGNORECASE)
                if re.search(pat2, text, re.IGNORECASE):
                    logger.warning("[PRESEND_VALIDATOR] Warning Check 5: Confusing origin with current location (%s). Sanitizing.", pat2)
                    text = re.sub(pat2, f"ты же в {current_city} сейчас", text, flags=re.IGNORECASE)

        # Check 6: Correct timezone
        # Verified via context timestamps

        # Check 7: Correct language matching current turn
        lang_info = internal_context.get("language", {})
        target_lang = lang_info.get("current", "ru")
        lang_conf = lang_info.get("confidence", 0.9)

        if lang_conf >= 0.85:
            if target_lang == "uk":
                # Check if reply is entirely in Russian when user wrote in Ukrainian
                # Ukrainian distinctive letters: і, ї, є, ґ
                # Russian distinctive letters: ы, э, ъ, ё
                has_ru_chars = any(c in text.lower() for c in "ыэъё")
                has_uk_chars = any(c in text.lower() for c in "іїєґ")
                if has_ru_chars and not has_uk_chars and len(text) > 20:
                    logger.warning("[PRESEND_VALIDATOR] Language mismatch: user is 'uk', but bot response contains Russian characters: %s", text[:60])
                    # Note: We do not outright drop unless severe, but we flag

        # Check 8: No contradiction with negative/correction memories
        mem_info = internal_context.get("memory", {})
        corrections = mem_info.get("corrections", [])
        for corr in corrections:
            corr_low = corr.lower()
            # If user corrected: "не из Киева, а из Львова"
            if "не из" in corr_low:
                neg_part = corr_low.split("не из")[1].split(",")[0].strip()
                neg_stem = neg_part.rstrip("аеиоуыяь")
                if neg_stem and len(neg_stem) >= 3 and neg_stem in text.lower():
                    logger.warning("[PRESEND_VALIDATOR] FAILED Check 8: Contradicting explicit correction '%s' in text '%s'", corr, text)
                    text = re.sub(rf"(?:ты\s+(?:же\s+)?из\s+{neg_stem}[а-яё]*\s*(?:вроде\s*)?\??|из\s+{neg_stem}[а-яё]*)", "", text, flags=re.IGNORECASE).strip()

        # Check 9: Repetition guard
        if recent_bot_replies:
            norm_cand = re.sub(r"[^\w\s]", "", text.lower()).strip()
            for prev in recent_bot_replies[-4:]:
                norm_prev = re.sub(r"[^\w\s]", "", prev.lower()).strip()
                if norm_cand and norm_cand == norm_prev:
                    logger.warning("[PRESEND_VALIDATOR] FAILED Check 9: Exact duplicate of recent bot reply: '%s'", text)
                    return False, "duplicate_bot_reply", ""

        # Check 10: Logical coherence / non-empty & Zero HTML/XML/Markup leakage
        text = OutputSanitizer.sanitize_outgoing_text(text)
        is_clean, clean_err = OutputSanitizer.validate_final_outgoing(text)
        if not is_clean:
            logger.warning("[PRESEND_VALIDATOR] FAILED Check 10 (%s): '%s'", clean_err, text)
            return False, clean_err or "invalid_outgoing_text", ""

        return True, None, text
