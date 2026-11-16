"""
OutputSanitizer: Mandatory Application-Level Output Sanitation Layer.

Enforces ZERO HTML / XML / Markdown / Internal Markup / JSON Leakage
on every outgoing user-facing Telegram message.

Pipeline:
  LLM response
   -> JSON / structured output extraction
   -> Internal reasoning & bracket marker removal ([EVENT: ...], <thinking>...</thinking>)
   -> HTML/XML tag removal (preserving line breaks for block tags like <div>, <p>, <br>)
   -> Safe HTML entity decoding (without reconstructing tags)
   -> Accidental Markdown syntax removal (**, __, ~~, `, ```, [text](url))
   -> Whitespace normalization
   -> Final outgoing validation (assert non-empty, no tags, no JSON, no internal markers)
"""

import html
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class OutputSanitizer:
    """
    Deterministic sanitizer & final validator for all user-facing conversational Telegram text.
    Never trusts raw LLM output as directly sendable text.
    """

    # Internal reasoning/meta XML blocks whose ENTIRE content must be dropped
    INTERNAL_BLOCK_TAGS = (
        "thinking",
        "thought",
        "reasoning",
        "reflection",
        "internal",
        "system",
        "debug",
        "analysis",
        "scratchpad",
        "tool_call",
        "tool_result",
        "metadata",
    )

    # Internal bracket markers: [EVENT: ...], [DEBUG: ...], [SYSTEM: ...], etc.
    INTERNAL_BRACKET_RE = re.compile(
        r"\[(?:EVENT|DEBUG|SYSTEM|MEMORY|STATE|INTENT|REASON|TOOL|META|CONTEXT|INTERNAL|PROMPT|ANALYSIS|ACTION|TRIP_STATE|CITY_CONNECTION|PERSONA_LORE|MEETING_HANDOFF_REQUIRED|ACTIVITY_CATALOG|OWNER_HANDOFF)(?:\s*:[^\]]*)?\]",
        re.IGNORECASE,
    )

    # Block-level HTML tags that should introduce paragraph / line breaks
    PARAGRAPH_TAG_RE = re.compile(r"</?(?:p|blockquote|pre|h[1-6]|ul|ol)\b[^>]*>", re.IGNORECASE)
    LINEBREAK_TAG_RE = re.compile(r"(?:<br\s*/?>|</?(?:div|li|tr)\b[^>]*>)", re.IGNORECASE)

    # General HTML/XML tag matcher:
    # Matches <tag ...>, </tag>, <tag/> where tag starts with an ASCII letter.
    # Intentionally does NOT match emoticons like "<3", "<33", or math like "5 < 10".
    ANY_HTML_XML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9:_-]*(?:\s+[^<>]*)?\s*/?>")

    # Orphan closing or unclosed tag fragments at edges, e.g. "...</div>" or "<div>..."
    ORPHAN_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9:_-]*\s*/?>")

    # Markdown fenced code blocks ```lang\n...\n```
    MD_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)```", re.DOTALL)

    # Markdown inline code `code`
    MD_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

    # Markdown bold/underline/strikethrough
    MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
    MD_UNDERLINE_RE = re.compile(r"__(.+?)__", re.DOTALL)
    MD_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)

    # Markdown heading at line start: ### Heading -> Heading
    MD_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")

    # Markdown link [text](url) -> text
    MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")

    @classmethod
    def extract_text_from_structured(cls, raw: Any) -> str:
        """
        If the model accidentally returned a dict or serialized JSON object
        (e.g. {"text": "<div>привет</div>", "emotion": "playful"}),
        safely extract the natural-language text field instead of stringifying JSON.
        """
        if raw is None:
            return ""

        if isinstance(raw, dict):
            for key in ("text", "reply", "message", "response", "content", "answer"):
                val = raw.get(key)
                if isinstance(val, str) and val.strip():
                    return val
            return ""

        text = str(raw).strip()
        if not text:
            return ""

        # Strip surrounding ```json ... ``` fence before attempting JSON parse
        fence_match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
        candidate = fence_match.group(1).strip() if fence_match else text

        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    for key in ("text", "reply", "message", "response", "content", "answer"):
                        val = parsed.get(key)
                        if isinstance(val, str) and val.strip():
                            logger.warning("[OUTPUT_SANITIZER] Extracted '%s' from leaked JSON payload", key)
                            return val
                    # JSON object with no natural-language text field -> drop
                    logger.warning("[OUTPUT_SANITIZER] Dropped internal JSON object with no text field")
                    return ""
            except Exception:
                # Looks like broken serialized JSON with internal keys
                if re.search(r'"(?:emotion|intent|confidence|state|reasoning|tool)"\s*:', candidate):
                    m = re.search(r'"(?:text|reply|message)"\s*:\s*"((?:\\.|[^"\\])*)"', candidate)
                    if m:
                        try:
                            return json.loads(f'"{m.group(1)}"')
                        except Exception:
                            return m.group(1)
                    return ""

        return text

    @classmethod
    def strip_internal_blocks_and_markers(cls, text: str) -> str:
        """Removes internal reasoning XML blocks and [EVENT: ...] / [DEBUG: ...] markers."""
        if not text:
            return ""

        for tag in cls.INTERNAL_BLOCK_TAGS:
            text = re.sub(
                rf"<{tag}\b[^>]*>.*?</{tag}>",
                "",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            )

        text = cls.INTERNAL_BRACKET_RE.sub("", text)
        return text

    @classmethod
    def strip_html_xml_tags(cls, text: str) -> str:
        """
        Removes HTML/XML tags while preserving natural line/paragraph breaks
        and keeping normal human punctuation like ':)', ';)', '<3'.
        """
        if not text:
            return ""

        # 1. Replace closing+opening paragraph boundaries cleanly: </p><p> -> \n\n
        text = re.sub(r"</p>\s*<p\b[^>]*>", "\n\n", text, flags=re.IGNORECASE)
        # Replace closing+opening div boundaries: </div><div> -> \n
        text = re.sub(r"</div>\s*<div\b[^>]*>", "\n", text, flags=re.IGNORECASE)
        # Replace closing+opening li boundaries: </li><li> -> \n
        text = re.sub(r"</li>\s*<li\b[^>]*>", "\n", text, flags=re.IGNORECASE)

        # 2. Convert remaining paragraph & linebreak tags into newlines
        text = cls.PARAGRAPH_TAG_RE.sub("\n\n", text)
        text = cls.LINEBREAK_TAG_RE.sub("\n", text)

        # 3. Strip all remaining HTML/XML tags (inline <b>, <i>, <span>, <a>, <foo>, etc.)
        text = cls.ANY_HTML_XML_TAG_RE.sub("", text)
        text = cls.ORPHAN_TAG_RE.sub("", text)

        return text

    @classmethod
    def decode_safe_entities(cls, text: str) -> str:
        """
        Decodes HTML entities (&amp;, &quot;, &#39;, &nbsp;, etc.)
        and immediately strips any tags that might have been reconstructed by &lt;...&gt;.
        """
        if not text or "&" not in text:
            return text

        # Replace non-breaking space entity before unescape
        text = text.replace("&nbsp;", " ").replace("&#160;", " ")
        decoded = html.unescape(text)

        # Security check: if unescaping reconstructed an HTML/XML tag, strip it
        if cls.ANY_HTML_XML_TAG_RE.search(decoded):
            decoded = cls.strip_html_xml_tags(decoded)

        return decoded

    @classmethod
    def strip_markdown_leakage(cls, text: str) -> str:
        """
        Removes accidental Markdown formatting control syntax while preserving
        conversational text and normal human punctuation.
        """
        if not text:
            return ""

        # 1. Fenced code blocks -> keep inner text
        text = cls.MD_FENCE_RE.sub(lambda m: m.group(1).strip(), text)
        # 2. Inline code `text` -> text
        text = cls.MD_INLINE_CODE_RE.sub(r"\1", text)
        # 3. Bold **text**, underline __text__, strike ~~text~~
        text = cls.MD_BOLD_RE.sub(r"\1", text)
        text = cls.MD_UNDERLINE_RE.sub(r"\1", text)
        text = cls.MD_STRIKE_RE.sub(r"\1", text)
        # 4. Markdown headings ### Title -> Title
        text = cls.MD_HEADING_RE.sub("", text)
        # 5. Markdown links [label](url) -> label
        text = cls.MD_LINK_RE.sub(r"\1", text)

        return text

    @classmethod
    def normalize_whitespace(cls, text: str) -> str:
        """Normalizes spaces and excessive newlines while preserving intentional paragraphs."""
        if not text:
            return ""

        # Normalize CRLF
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Trim spaces around newlines
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        # Collapse 3+ consecutive newlines into at most 2 (paragraph break)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Collapse multiple spaces/tabs on the same line
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @classmethod
    def sanitize_outgoing_text(cls, raw_output: Any) -> str:
        """
        Full mandatory sanitization pipeline for every outgoing conversational message.
        """
        text = cls.extract_text_from_structured(raw_output)
        if not text:
            return ""

        from companion.services.url_lock_service import UrlLockService

        text = cls.strip_internal_blocks_and_markers(text)
        text = UrlLockService.sanitize_links_and_placeholders(text)
        text = cls.strip_html_xml_tags(text)
        text = cls.decode_safe_entities(text)
        text = cls.strip_markdown_leakage(text)
        text = UrlLockService.sanitize_links_and_placeholders(text)
        text = cls.normalize_whitespace(text)
        return text

    @classmethod
    def validate_final_outgoing(
        cls,
        text: Any,
        approved_urls: Optional[Any] = None,
        allowed_bot_username: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Final outgoing contract check (Section 16 & Section 22 URL Lock) before calling Telegram sendMessage.
        Returns: (is_valid, failure_reason)
        """
        if not isinstance(text, str):
            return False, "not_a_string"

        stripped = text.strip()
        if not stripped:
            return False, "empty_text"

        if cls.ANY_HTML_XML_TAG_RE.search(stripped):
            return False, "raw_html_xml_detected"

        if cls.INTERNAL_BRACKET_RE.search(stripped):
            return False, "internal_marker_detected"

        from companion.services.url_lock_service import UrlLockService
        url_ok, url_err = UrlLockService.run_presend_assertions(
            stripped,
            approved_urls=approved_urls,
            allowed_bot_username=allowed_bot_username,
        )
        if not url_ok:
            return False, url_err

        # Check for serialized JSON object leakage
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    return False, "serialized_json_detected"
            except Exception:
                pass

        return True, None
