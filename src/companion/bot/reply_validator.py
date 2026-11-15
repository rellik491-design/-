"""
ReplyValidator: Deterministic validation layer for Telegram native replies.
Enforces Section 6 & Section 49 rules:
- reply_to_message_id exists and is a positive integer
- target message belongs to the current chat
- message ID is recognized from active batch or recent chat history
- sender account has permission to reply
- graceful fallback to sending normally on validation or Telegram API failure
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ReplyValidator:
    """
    Deterministic validation of Telegram message IDs and native reply permissions.
    The LLM may suggest or request an ID, but application code validates it before
    making any Telegram API call.
    """

    @classmethod
    def validate_reply_target(
        cls,
        reply_to_message_id: Optional[int],
        valid_message_ids: Optional[Any] = None,
        chat_id: Optional[int] = None,
        can_reply_permission: bool = True,
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Validates target message ID:
        Returns: (is_valid, sanitized_id, error_reason)
        """
        if reply_to_message_id is None:
            return False, None, "no_reply_target_requested"

        if not can_reply_permission:
            logger.warning("[REPLY_VALIDATION] Sender lacks permission to reply in chat %s", chat_id)
            return False, None, "permission_denied"

        try:
            target_id = int(reply_to_message_id)
        except (ValueError, TypeError):
            logger.warning("[REPLY_VALIDATION] Invalid non-integer reply_to_message_id: %r", reply_to_message_id)
            return False, None, "invalid_id_type"

        if target_id <= 0:
            return False, None, "non_positive_id"

        # If a known set/list of valid IDs or batch dicts is provided, verify containment
        if valid_message_ids is not None:
            ids_set: Set[int] = set()
            if isinstance(valid_message_ids, (set, list, tuple)):
                for item in valid_message_ids:
                    if isinstance(item, dict):
                        mid = item.get("telegram_message_id") if item.get("telegram_message_id") is not None else item.get("message_id")
                        if mid is not None and str(mid).isdigit():
                            ids_set.add(int(mid))
                    elif isinstance(item, int):
                        ids_set.add(item)
                    elif str(item).isdigit():
                        ids_set.add(int(item))

            if ids_set and target_id not in ids_set:
                logger.warning(
                    "[REPLY_VALIDATION] Target ID %d not found in recognized messages for chat %s",
                    target_id,
                    chat_id,
                )
                return False, None, "id_not_found_in_chat"

        return True, target_id, None

    @classmethod
    def build_reply_parameters(
        cls,
        validated_reply_id: Optional[int] = None,
        is_business_connection: bool = False,
        chat_id: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Builds the standard Telegram Bot API 7.0+ `reply_parameters` object.
        CRITICAL BOT API SPECIFICATION:
        For messages sent on behalf of a business account (business_connection_id),
        `chat_id` inside reply_parameters is NOT supported by Telegram.
        """
        target_id = validated_reply_id if validated_reply_id is not None else reply_to_message_id
        if target_id is None:
            return None
        try:
            target_id = int(target_id)
        except (ValueError, TypeError):
            return None
        if target_id <= 0:
            return None

        params: Dict[str, Any] = {
            "message_id": target_id,
            "allow_sending_without_reply": True,
        }
        if not is_business_connection and chat_id is not None:
            params["chat_id"] = chat_id

        return params

    @classmethod
    def sanitize_contact_deflection(
        cls, text: str, allowed_bot_username: Optional[str] = None
    ) -> str:
        """
        Critical Negative Rule Guard (Section 12):
        Under NO circumstances allow the model to produce personal @usernames,
        Instagram URLs, or phone numbers in replies.
        If a leak is detected, replaces or strips it with natural conversational deflection.
        """
        if not text:
            return text

        allowed = (allowed_bot_username or "").lstrip("@").lower()

        # 1. Instagram URLs
        if re.search(r"(?i)\b(?:https?://)?(?:www\.)?instagram\.com/[a-zA-Z0-9_\.]+", text):
            logger.warning("[SAFETY_GUARD] Intercepted Instagram URL leak in generated text: %s", text)
            text = re.sub(
                r"(?i)\b(?:https?://)?(?:www\.)?instagram\.com/[a-zA-Z0-9_\.]+",
                "",
                text,
            ).strip()
            if not text:
                return "ахах, ну куда ты меня так торопишься утащить из телеги 😭"

        # 2. @username mentions outside allowed bot
        def _replace_username(match):
            uname = match.group(1).lower()
            if allowed and uname == allowed:
                return match.group(0)
            logger.warning("[SAFETY_GUARD] Intercepted @username leak: @%s", uname)
            return ""

        text = re.sub(r"@([a-zA-Z0-9_]{3,32})", _replace_username, text)

        # 3. Phone numbers (e.g. +7..., 89..., etc.)
        text = re.sub(r"(?:\+?[78])[\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}", "", text)

        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "ахах, давай здесь пообщаемся, мне тут намного комфортнее 😌"

        return text
