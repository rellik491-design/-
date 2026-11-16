"""
Strict URL Output Lock & Zero Placeholder Links Service.

Application-level security and output-validation layer guaranteeing:
1. Default mode ALLOW_URLS = False for all LLM-generated conversational text.
2. Zero placeholder links ([ссылка], [link], [URL], <URL>, <LINK>, (ссылка), [тык], [забронировать], etc.).
3. Detection and removal of raw URLs, Markdown links, HTML <a href> links, obfuscated URLs,
   and unauthorized @social_handles / t.me links.
4. Never replaces a blocked URL with '[ссылка]' or any placeholder.
5. Context-validated ApprovedUrlObject pipeline (verifying city, venue, activity, purpose, expiration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from companion.services.persona_lore_service import PersonaLoreService


@dataclass
class ApprovedUrlObject:
    url: str
    purpose: str = "booking"
    source: str = "verified_catalog"
    city: Optional[str] = None
    venue: Optional[str] = None
    activity: Optional[str] = None
    verified: bool = False
    allowed: bool = False
    expires_at: Optional[str] = None

    def to_render_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "type": self.purpose,
            "source": self.source,
            "verified": self.verified,
            "allowed": self.allowed,
            "approved": bool(self.verified and self.allowed),
        }


class UrlLockService:
    """Enforces Strict URL Output Lock and Zero Placeholder Links on all outgoing messages."""

    DEFAULT_ALLOW_URLS: bool = False

    # 1. Bracketed / parenthesized / angle-bracketed URL placeholders (Sections 1 & 2)
    PLACEHOLDER_WORDS_CORE = (
        r"ссылк[а-яё]*|ссылочк[а-яё]*|link[s]?|url[s]?|тык|клик(?:ни(?:\s+сюда)?)?|"
        r"забронироват[а-яё]*|брон[ьяи][а-яё]*|booking(?:\s+link)?|сайт|билет[а-яё]*|смотреть"
    )

    FORBIDDEN_PLACEHOLDER_RE = re.compile(
        r"(?:"
        r"\[\s*(?:[^\]]*?\b(?:ссылк[а-яё]*|ссылочк[а-яё]*|link|url|тык|кликни(?:\s+сюда)?|забронировать|booking\s+link)\b[^\]]*?)\s*\]"
        r"|\(\s*(?:ссылк[а-яё]*|ссылочк[а-яё]*|link|url|тут\s+ссылка|здесь\s+ссылка|вот\s+ссылка|ссылка\s+на\s+бронирование)\s*\)"
        r"|<\s*(?:URL|LINK|HREF|ССЫЛКА)\s*>"
        r")",
        re.IGNORECASE,
    )

    # 2. Markdown link bypasses including malformed spacing: [text](url) or [text] (url)
    MARKDOWN_URL_RE = re.compile(
        r"\[([^\]]*)\]\s*\(\s*(?:https?://|hxxps?://|www\.|t\.me/|telegram\.me/|[a-zA-Z0-9_-]+\.(?:com|ru|uk|co\.uk|org|net|io|me|ua|de|fr)\b)[^\)]*\)",
        re.IGNORECASE,
    )

    # 3. HTML <a href="...">text</a> bypasses
    HTML_HREF_FULL_RE = re.compile(
        r"<a\b[^>]*\bhref\s*=\s*['\"][^'\"]*['\"][^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    HTML_HREF_TAG_RE = re.compile(
        r"<a\b[^>]*\bhref\s*=\s*['\"][^'\"]*['\"][^>]*>",
        re.IGNORECASE,
    )

    # 4. Obfuscated URLs: hxxp://, hxxps://, example[.]com, example dot com, example точка com
    OBFUSCATED_URL_RE = re.compile(
        r"(?:"
        r"\bhxxps?://[^\s<>\"')]+"
        r"|\b[a-zA-Z0-9_-]{2,}\s*(?:\[\.\]|\(\.\))\s*(?:com|ru|uk|co\.uk|org|net|io|me|ua|info|biz)(?:/[^\s<>\"')]*)?"
        r"|\b[a-zA-Z0-9_-]{2,}\s+(?:dot|точка)\s+(?:com|ru|uk|org|net|io|me|ua)\b(?:/[^\s<>\"')]*)?"
        r")",
        re.IGNORECASE,
    )

    # 5. Raw URLs: https://, http://, www., t.me/, telegram.me/, bare common domains
    RAW_URL_RE = re.compile(
        r"(?:"
        r"\b(?:https?://|www\.)[^\s<>\"')]+"
        r"|\b(?:t\.me|telegram\.me|instagram\.com|instagr\.am|vk\.com|wa\.me|tiktok\.com|snapchat\.com)/[a-zA-Z0-9_./?=&%-]+"
        r"|\b[a-zA-Z0-9_-]{2,}\.(?:com|co\.uk|ru|org|net|io|me|ua|uk|de|fr|es|it|pl)/(?:[a-zA-Z0-9_./?=&%-]*)"
        r"|\b(?:example|venue-name|cinema|quest)\.(?:com|co\.uk|ru|org|net|example)\b(?:/[^\s<>\"')]*)?"
        r")",
        re.IGNORECASE,
    )

    # 6. Social handles: @username
    SOCIAL_HANDLE_RE = re.compile(r"(?<![a-zA-Z0-9._%+-])@([a-zA-Z0-9_]{3,32})\b")

    # 7. Pretend-link lead-in phrases that become dangling when a URL/placeholder is removed
    DANGLING_LINK_PHRASE_RE = re.compile(
        r"(?:[,.;:—\-]?\s*\b(?:вот\s+ссылк[а-яё]*(?:\s+на\s+[а-яёa-z0-9_\s]{1,25})?|держи\s+ссылк[а-яё]*|скидываю\s+ссылк[а-яё]*|ловите?\s+ссылк[а-яё]*|тут\s+ссылк[а-яё]*|здесь\s+ссылк[а-яё]*|вот\s+сайт|ссылк[а-яё]*\s+на\s+брон[а-яё]*|тогда\s+бронируй\s+здесь|бронируй\s+тут|переходи\s+по\s+ссылке)\s*[:—\-]*\s*)",
        re.IGNORECASE,
    )

    LINK_LABEL_WORDS_RE = re.compile(
        r"^\s*(?:ссылк[а-яё]*|ссылочк[а-яё]*|link|url|тык|клик(?:ни)?|забронировать|бронь|смотреть|тут|здесь|сюда|сайт|билеты)\s*$",
        re.IGNORECASE,
    )

    @classmethod
    def no_forbidden_url_placeholder(cls, text: str) -> bool:
        return not bool(cls.FORBIDDEN_PLACEHOLDER_RE.search(text or ""))

    @classmethod
    def no_markdown_url(cls, text: str) -> bool:
        return not bool(cls.MARKDOWN_URL_RE.search(text or ""))

    @classmethod
    def no_html_href(cls, text: str) -> bool:
        return not bool(cls.HTML_HREF_TAG_RE.search(text or ""))

    @classmethod
    def no_obfuscated_url(cls, text: str) -> bool:
        return not bool(cls.OBFUSCATED_URL_RE.search(text or ""))

    @classmethod
    def no_unapproved_url(cls, text: str, approved_urls: Optional[Set[str]] = None) -> bool:
        if not text:
            return True
        allowed = approved_urls or set()
        for m in cls.RAW_URL_RE.finditer(text):
            found = m.group(0).rstrip(".,!?;:)")
            if found not in allowed:
                return False
        return True

    @classmethod
    def no_unapproved_social_handle(cls, text: str, allowed_bot_username: Optional[str] = None) -> bool:
        if not text:
            return True
        allowed_uname = (allowed_bot_username or "").lstrip("@").lower()
        for m in cls.SOCIAL_HANDLE_RE.finditer(text):
            uname = m.group(1).lower()
            if not (allowed_uname and uname == allowed_uname):
                return False
        return True

    @classmethod
    def contains_any_link_attempt(cls, text: str, allowed_bot_username: Optional[str] = None) -> bool:
        """Returns True if text contains any URL, placeholder, Markdown/HTML link, or obfuscated link."""
        if not text:
            return False
        return not (
            cls.no_forbidden_url_placeholder(text)
            and cls.no_markdown_url(text)
            and cls.no_html_href(text)
            and cls.no_obfuscated_url(text)
            and cls.no_unapproved_url(text)
            and cls.no_unapproved_social_handle(text, allowed_bot_username=allowed_bot_username)
        )

    @classmethod
    def validate_approved_url(
        cls,
        url_obj: ApprovedUrlObject,
        meeting_city: Optional[str] = None,
        venue: Optional[str] = None,
        activity: Optional[str] = None,
        purpose: str = "booking",
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Section 17 & 18: Context-aware validation for an ApprovedUrlObject.
        Even a valid URL is rejected if its city, venue, or purpose does not match current context.
        """
        if not url_obj or not isinstance(url_obj, ApprovedUrlObject):
            return False
        if not url_obj.verified or not url_obj.allowed:
            return False
        if not url_obj.url or not url_obj.url.startswith("https://"):
            return False
        if cls.FORBIDDEN_PLACEHOLDER_RE.search(url_obj.url):
            return False

        if purpose and url_obj.purpose and url_obj.purpose != purpose:
            return False

        if meeting_city and url_obj.city:
            if PersonaLoreService.normalize_city(meeting_city) != PersonaLoreService.normalize_city(url_obj.city):
                return False

        if venue and url_obj.venue:
            if venue.strip().lower() != url_obj.venue.strip().lower():
                return False

        if activity and url_obj.activity:
            if activity.strip().lower() != url_obj.activity.strip().lower():
                return False

        if url_obj.expires_at:
            ref_now = now or datetime.now(timezone.utc)
            try:
                exp_dt = datetime.fromisoformat(url_obj.expires_at)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if ref_now > exp_dt:
                    return False
            except Exception:
                return False

        return True

    @classmethod
    def sanitize_links_and_placeholders(
        cls,
        text: str,
        approved_urls: Optional[Set[str]] = None,
        allowed_bot_username: Optional[str] = None,
    ) -> str:
        """
        Removes all unapproved URLs, HTML/Markdown links, obfuscated URLs, and URL placeholders
        WITHOUT EVER replacing a blocked URL with '[ссылка]'.
        Also removes dangling 'вот ссылка:' lead-ins so the message never pretends a link was sent.
        """
        if not text:
            return ""

        allowed_set = approved_urls or set()
        had_link_artifact = False

        # 1. Strip HTML <a href="...">label</a>
        def _strip_html_a(m: re.Match) -> str:
            nonlocal had_link_artifact
            had_link_artifact = True
            inner = (m.group(1) or "").strip()
            if cls.FORBIDDEN_PLACEHOLDER_RE.search(inner):
                return ""
            return inner

        text = cls.HTML_HREF_FULL_RE.sub(_strip_html_a, text)
        if cls.HTML_HREF_TAG_RE.search(text):
            had_link_artifact = True
            text = cls.HTML_HREF_TAG_RE.sub("", text)

        # 2. Strip Markdown links [label](url)
        def _strip_md_link(m: re.Match) -> str:
            nonlocal had_link_artifact
            had_link_artifact = True
            label = (m.group(1) or "").strip()
            if cls.LINK_LABEL_WORDS_RE.match(label) or cls.FORBIDDEN_PLACEHOLDER_RE.search(label):
                return ""
            return label

        text = cls.MARKDOWN_URL_RE.sub(_strip_md_link, text)

        # 3. Strip forbidden placeholders ([ссылка], [link], <URL>, (ссылка), etc.)
        if cls.FORBIDDEN_PLACEHOLDER_RE.search(text):
            had_link_artifact = True
            text = cls.FORBIDDEN_PLACEHOLDER_RE.sub("", text)

        # 4. Strip obfuscated URLs (hxxps://, example[.]com, example dot com)
        if cls.OBFUSCATED_URL_RE.search(text):
            had_link_artifact = True
            text = cls.OBFUSCATED_URL_RE.sub("", text)

        # 5. Strip raw unapproved URLs
        def _strip_raw_url(m: re.Match) -> str:
            nonlocal had_link_artifact
            raw_u = m.group(0)
            clean_u = raw_u.rstrip(".,!?;:)")
            if clean_u in allowed_set:
                return raw_u
            had_link_artifact = True
            return ""

        text = cls.RAW_URL_RE.sub(_strip_raw_url, text)

        # 6. Strip dangling "вот ссылка на квест:" phrases if a link/placeholder was present or standalone
        if had_link_artifact or cls.DANGLING_LINK_PHRASE_RE.search(text):
            text = cls.DANGLING_LINK_PHRASE_RE.sub(" ", text)

        # Clean up orphan punctuation / double spaces left after removal
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"\[\s*\]", "", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\s+([,.!?])", r"\1", text)
        return text.strip(" :—\t")

    @classmethod
    def run_presend_assertions(
        cls,
        text: str,
        approved_urls: Optional[Set[str]] = None,
        allowed_bot_username: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Section 22: Final pre-send assertions before every Telegram send.
        """
        if not cls.no_forbidden_url_placeholder(text):
            return False, "forbidden_url_placeholder_detected"
        if not cls.no_markdown_url(text):
            return False, "markdown_url_detected"
        if not cls.no_html_href(text):
            return False, "html_href_detected"
        if not cls.no_obfuscated_url(text):
            return False, "obfuscated_url_detected"
        if not cls.no_unapproved_url(text, approved_urls=approved_urls):
            return False, "unapproved_url_detected"
        if not cls.no_unapproved_social_handle(text, allowed_bot_username=allowed_bot_username):
            return False, "unapproved_social_handle_detected"
        return True, None
