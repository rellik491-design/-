import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.models.entities import AccountSession, Contact, Persona, Subscription, User
from companion.models.enums import AccountStatus, ContactStatus, PlanDuration
from companion.services.account_service import AccountService
from companion.services.stats_service import StatsService
from companion.services.subscription_service import SubscriptionService
from companion.services.timezone_service import TimezoneService

logger = logging.getLogger(__name__)


FLOW_LABELS: Dict[str, str] = {
    "WAIT_NAME_AGE": "имя и возраст девушки (например: Алина, 21)",
    "WAIT_CITY_DISTRICT": "город и район проживания (например: Москва, Хамовники)",
    "WAIT_BIRTH_CITY": "родной город девушки (например: Самара)",
    "WAIT_MOVE_REASON": "причину переезда",
    "WAIT_YEARS_IN_CITY": "сколько времени живёт в городе",
    "WAIT_DATING_APP": "контекст знакомства (Дайвинчик / Тиндер)",
    "WAIT_STUDY_HOBBIES": "учёбу, работу и хобби",
    "WAIT_EXTRA_FACTS": "детали и предпочтения",
    "WAIT_SLEEP_SCHEDULE": "режим сна (например: 23:00 - 08:30)",
    "WAIT_LEGEND_WIZARD": "свободное описание анкеты одним сообщением",
    "WAIT_CUSTOM_VOICE_TEXT": "текст для озвучки живым голосом",
    "WAIT_CONTACT_MANUAL_REPLY": "текст сообщения для отправки клиенту",
    "WAIT_CONTACT_SEARCH": "имя, @username или тему для поиска по чатам",
    "WAIT_ADMIN_GRANT": "@username или ID и количество дней подписки",
}

ROBOTIC_PATTERNS = [
    r"^(certainly|of course|sure thing|great question|absolutely)[!,\.\s]*",
    r"^i'?d be happy to help[!,\.\s]*",
    r"^as an ai[^,\.]*[,\.\s]*",
    r"^(безусловно|конечно же|с радостью помогу|с удовольствием помогу|отличный вопрос)[!,\.\s]*",
    r"^я с радостью[^,\.]*[,\.\s]*",
]


@dataclass
class ManagerState:
    """Per-user/per-chat conversational & UI workflow state."""

    user_id: int
    current_menu: str = "menu_main"
    previous_menu: str = "menu_main"
    active_flow: Optional[str] = None
    flow_target_id: Optional[int] = None
    interrupted_flow: Optional[str] = None
    interrupted_target_id: Optional[int] = None
    last_entity_type: Optional[str] = None  # "contact" | "account" | "persona" | "invoice" | "user"
    last_entity_id: Optional[int] = None
    last_edited_field: Optional[str] = None  # "name_age" | "age" | "city" | "sleep_schedule" | "grant_days" | "extra_facts"
    pending_confirmation: Optional[Dict[str, Any]] = None
    chat_filter: str = "all"  # "all" | "active" | "waiting" | "attention" | "paused"
    chat_page: int = 1
    chat_search_query: Optional[str] = None
    user_style: str = "balanced"  # "brief" | "detailed" | "casual" | "frustrated"
    variation_idx: int = 0
    recent_turns: List[Dict[str, str]] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def set_menu(self, menu_name: str) -> None:
        if self.current_menu != menu_name:
            self.previous_menu = self.current_menu
            self.current_menu = menu_name
        self.updated_at = datetime.now(timezone.utc)

    def start_flow(self, flow_name: str, target_id: Optional[int] = None) -> None:
        self.active_flow = flow_name
        self.flow_target_id = target_id
        self.updated_at = datetime.now(timezone.utc)

    def clear_flow(self) -> None:
        self.active_flow = None
        self.flow_target_id = None
        self.updated_at = datetime.now(timezone.utc)

    def pause_flow_for_interruption(self) -> None:
        if self.active_flow:
            self.interrupted_flow = self.active_flow
            self.interrupted_target_id = self.flow_target_id
            self.active_flow = None
            self.flow_target_id = None
            self.updated_at = datetime.now(timezone.utc)

    def resume_interrupted(self) -> Tuple[Optional[str], Optional[int]]:
        if self.interrupted_flow:
            self.active_flow = self.interrupted_flow
            self.flow_target_id = self.interrupted_target_id
            self.interrupted_flow = None
            self.interrupted_target_id = None
            self.updated_at = datetime.now(timezone.utc)
            return self.active_flow, self.flow_target_id
        return None, None

    def record_turn(self, role: str, text: str) -> None:
        self.recent_turns.append({"role": role, "text": text[:300]})
        if len(self.recent_turns) > 12:
            self.recent_turns = self.recent_turns[-12:]
        self.updated_at = datetime.now(timezone.utc)


class MessageDebouncer:
    """
    Combines rapid consecutive messages from the same user into a single coherent request,
    handling mid-stream corrections like:
      "привет" -> "подожди" -> "не, лучше покажи последнего клиента"
    """

    def __init__(self, window_seconds: float = 0.35):
        self.window_seconds = window_seconds
        self._buffers: Dict[int, List[str]] = {}
        self._tasks: Dict[int, asyncio.Task] = {}

    @staticmethod
    def combine_burst(messages: List[str]) -> str:
        """
        Intelligently merges a burst of rapid messages.
        If a later message in the burst is a self-correction ("actually", "wait no", "не, лучше", "отмена"),
        the correction overrides the earlier tentative fragments.
        """
        cleaned = [m.strip() for m in messages if m and m.strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]

        # Check if the user cancelled at the very end of the burst
        last_low = cleaned[-1].lower()
        cancel_finals = ("cancel", "stop", "nevermind", "actually don't", "отмена", "стой", "не надо", "отбой")
        if any(last_low == c or last_low.startswith(c + " ") for c in cancel_finals):
            return cleaned[-1]

        # Check if a later message starts with a correction marker ("actually ...", "не, давай ...", "лучше ...")
        correction_prefixes = (
            "actually",
            "wait,",
            "wait ",
            "no,",
            "no ",
            "instead",
            "не,",
            "не ",
            "нет,",
            "подожди,",
            "хотя ",
            "давай лучше",
            "лучше ",
            "а нет",
        )
        for idx in range(len(cleaned) - 1, 0, -1):
            low_item = cleaned[idx].lower()
            if any(low_item.startswith(pref) for pref in correction_prefixes):
                # Strip pure filler prefixes like "wait" / "hey" if there is substantive content after
                return cleaned[idx]

        # Drop pure attention-grabbers ("hey", "привет", "слушай", "wait") if followed by an actual instruction
        filler_only = {"hey", "hi", "wait", "actually", "привет", "слушай", "подожди", "стой", "короче", "кстати"}
        substantive = [m for m in cleaned if m.lower().rstrip("!.,?") not in filler_only]
        if substantive:
            return " ".join(substantive)
        return " ".join(cleaned)

    async def enqueue(
        self,
        user_id: int,
        text: str,
        callback: Callable[[str], Coroutine[Any, Any, None]],
    ) -> None:
        buf = self._buffers.setdefault(user_id, [])
        buf.append(text)
        existing_task = self._tasks.get(user_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        async def _flush():
            await asyncio.sleep(self.window_seconds)
            msgs = self._buffers.pop(user_id, [])
            self._tasks.pop(user_id, None)
            combined = self.combine_burst(msgs)
            if combined:
                await callback(combined)

        self._tasks[user_id] = asyncio.create_task(_flush())


class ConversationManagerEngine:
    """
    Human-like Manager Engine for the Telegram Bot.
    Acts as a sharp, concise, context-aware human operations manager for the user/owner.
    """

    ACK_VARIATIONS = [
        "Готово.",
        "Сделал.",
        "Обновил.",
        "Принято, сохранил.",
        "Ок, обновил.",
    ]

    THANKS_VARIATIONS = [
        "Без проблем 👍",
        "Обращайся.",
        "Всегда пожалуйста.",
        "Отлично, работаем дальше.",
        "👍",
    ]

    WAIT_VARIATIONS = [
        "Хорошо, не тороплю. Когда будешь готов — напиши или выбери действие ниже.",
        "Ок, жду. Все настройки на месте.",
        "Без проблем, пиши как будешь готов.",
    ]

    CANCEL_VARIATIONS = [
        "Ок, отменил. Ничего не менял.",
        "Принято, сбросил ввод.",
        "Отменил действие.",
    ]

    def __init__(self):
        self._states: Dict[int, ManagerState] = {}
        self.debouncer = MessageDebouncer(window_seconds=0.35)

    def get_state(self, user_id: int) -> ManagerState:
        state = self._states.get(user_id)
        if state is None:
            state = ManagerState(user_id=user_id)
            self._states[user_id] = state
        return state

    @staticmethod
    def strip_robotic_filler(text: str) -> str:
        """Removes stereotypical AI customer-support openings while keeping natural tone."""
        if not text:
            return text
        cleaned = text.strip()
        for pat in ROBOTIC_PATTERNS:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned and cleaned[0].islower() and text[0].isupper():
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned

    def pick_variation(self, user_id: int, pool: List[str]) -> str:
        state = self.get_state(user_id)
        idx = state.variation_idx % len(pool)
        state.variation_idx += 1
        return pool[idx]

    def detect_user_style(self, user_id: int, text: str) -> str:
        state = self.get_state(user_id)
        low = text.lower().strip()
        frustration_markers = (
            "почему опять",
            "не работает",
            "сломалось",
            "опять не",
            "че за",
            "что за фигня",
            "блин",
            "wtf",
            "doesn't work",
            "broken",
            "again?",
            "why isn't",
        )
        if any(m in low for m in frustration_markers):
            state.user_style = "frustrated"
        elif len(text) <= 15:
            state.user_style = "brief"
        elif len(text) > 80:
            state.user_style = "detailed"
        else:
            state.user_style = "balanced"
        return state.user_style

    @staticmethod
    def classify_contact_status(contact: Contact, now_utc: Optional[datetime] = None) -> Tuple[str, str, str]:
        """
        Classifies a contact into one of the real operational statuses:
        Returns (bucket_key, badge_emoji, label).
        bucket_key in: "attention" | "active" | "waiting" | "paused"
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        tags_str = (contact.tags or "").lower()
        has_quest_or_meetup = (
            contact.status == ContactStatus.LEAD_MEETING
            or "квест" in tags_str
            or "встреч" in tags_str
            or bool(getattr(contact, "lead_summary", None))
            or bool(getattr(contact, "alert_date_sent", False))
            or bool(getattr(contact, "alert_quest_sent", False))
        )
        has_violation = bool(contact.has_violation or (contact.violation_count and contact.violation_count > 0))

        if has_quest_or_meetup or has_violation:
            reason = "оскорбление" if has_violation and not has_quest_or_meetup else "горячий лид / встреча"
            return "attention", "🔴", f"требует внимания ({reason})"

        if contact.status == ContactStatus.BLOCKED or not contact.ai_active or contact.bot_paused:
            lbl = "заблокирован" if contact.status == ContactStatus.BLOCKED else "на паузе (ручной)"
            return "paused", "⚪", lbl

        if contact.last_incoming_at:
            last_in = contact.last_incoming_at
            if last_in.tzinfo is None:
                last_in = last_in.replace(tzinfo=timezone.utc)
            age_sec = (now_utc - last_in).total_seconds()
            if age_sec <= 3600:
                return "active", "🟢", "активный диалог"
            if age_sec <= 86400:
                return "waiting", "🟡", "ожидает ответа"

        return "waiting", "🟡", "ожидает"

    @staticmethod
    def is_cancel_intent(text: str) -> bool:
        low = text.lower().strip().rstrip("!.,?")
        exact_cancels = {
            "no",
            "nah",
            "nope",
            "cancel",
            "stop",
            "nevermind",
            "never mind",
            "actually don't",
            "dont",
            "don't",
            "нет",
            "не",
            "неа",
            "отмена",
            "отмени",
            "отменить",
            "стой",
            "стоп",
            "отбой",
            "не надо",
            "передумал",
            "передумала",
            "назад",
        }
        if low in exact_cancels:
            return True
        prefixes = ("actually don't", "never mind", "не надо", "отмени ", "давай отмен")
        return any(low.startswith(p) for p in prefixes)

    @staticmethod
    def is_confirm_intent(text: str) -> bool:
        low = text.lower().strip().rstrip("!.,?")
        exact_confirms = {
            "yes",
            "yep",
            "yeah",
            "sure",
            "ok",
            "okay",
            "confirm",
            "do it",
            "go ahead",
            "да",
            "ага",
            "угу",
            "давай",
            "ок",
            "окей",
            "подтверждаю",
            "продолжай",
            "продолжим",
            "погнали",
            "делай",
        }
        return low in exact_confirms

    @staticmethod
    def is_wait_intent(text: str) -> bool:
        low = text.lower().strip().rstrip("!.,?")
        exact_waits = {
            "wait",
            "hold on",
            "one sec",
            "give me a second",
            "подожди",
            "погоди",
            "секунду",
            "сек",
            "ща",
            "щас",
            "минутку",
        }
        return low in exact_waits

    @staticmethod
    def is_thanks_intent(text: str) -> bool:
        low = text.lower().strip().rstrip("!.,?")
        exact_thanks = {
            "thanks",
            "thank you",
            "thx",
            "ty",
            "cool",
            "nice",
            "awesome",
            "great",
            "спасибо",
            "спс",
            "благодарю",
            "круто",
            "супер",
            "отлично",
            "кайф",
            "четко",
            "чётко",
        }
        return low in exact_thanks

    @staticmethod
    def is_latest_contact_intent(text: str) -> bool:
        low = text.lower().strip()
        patterns = [
            "check the latest",
            "latest one",
            "show the latest",
            "last chat",
            "last contact",
            "last lead",
            "покажи последн",
            "кто последн",
            "последний чат",
            "последний диалог",
            "последний лид",
            "последнего клиента",
            "что там по последнему",
            "проверь последн",
        ]
        return any(p in low for p in patterns)

    @staticmethod
    def extract_correction_value(text: str) -> Optional[str]:
        """
        Detects corrections like:
          - "make it 24 instead"
          - "change to Казань"
          - "давай лучше 23"
          - "не, давай Самара"
          - "пусть будет 14"
          - "поменяй на 25"
        """
        low = text.lower().strip()
        patterns = [
            r"^make it\s+(.+?)(?:\s+instead)?$",
            r"^actually[,\s]+(?:make it\s+)?(.+?)(?:\s+instead)?$",
            r"^change (?:it )?to\s+(.+)$",
            r"^(?:не[,\s]+|нет[,\s]+|а нет[,\s]+)?(?:давай лучше|лучше|пусть будет|сделай|поменяй на|исправь на)\s+(.+?)(?:\s+вместо этого)?$",
        ]
        for pat in patterns:
            m = re.match(pat, text.strip(), flags=re.IGNORECASE)
            if m:
                val = m.group(1).strip().rstrip(".")
                if val and low not in ("давай", "лучше"):
                    return val
        return None

    @staticmethod
    def is_navigation_or_question_interruption(text: str) -> Optional[str]:
        """
        Checks if a message sent while in an active input flow is actually a side-question
        or navigation request rather than input for the flow.
        Returns an intent code: "stats", "sub", "accounts", "chats", "latest", "help", "frustrated" or None.
        """
        low = text.lower().strip()
        if ConversationManagerEngine.is_latest_contact_intent(low):
            return "latest"
        if any(w in low for w in ("статистик", "сколько лидов", "сколько сообщений", "конверси", "stats", "analytics")):
            return "stats"
        if any(w in low for w in ("подписк", "сколько осталось дней", "тариф", "subscription", "my plan")):
            return "sub"
        if any(w in low for w in ("мои аккаунт", "какие аккаунт", "подключенные аккаунты", "accounts")):
            return "accounts"
        if any(w in low for w in ("покажи чаты", "список чатов", "кто пишет", "горячие лиды", "active chats")):
            return "chats"
        if any(w in low for w in ("почему не работает", "не отвечает бот", "сломалось", "doesn't work", "not working")):
            return "frustrated"
        if low in ("меню", "главное меню", "main menu", "помощь", "help"):
            return "menu"
        return None

    async def apply_contextual_correction(
        self,
        session: AsyncSession,
        user_id: int,
        db_user_id: int,
        new_value: str,
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Applies 'make it X instead' / 'давай лучше X' to the last edited field or entity.
        """
        state = self.get_state(user_id)
        field_name = state.last_edited_field

        # Find target persona if editing persona fields
        stmt_p = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
        if state.last_entity_type == "persona" and state.last_entity_id:
            stmt_p = select(Persona).where(Persona.id == state.last_entity_id, Persona.user_id == db_user_id)
        persona = (await session.execute(stmt_p)).scalars().first()

        # If new_value is a valid age (18-45) and we were editing persona or name_age
        age_only = re.match(r"^(1[8-9]|[2-3][0-9]|4[0-5])(?:\s*(?:лет|года|год|yo))?$", new_value.strip(), re.I)
        if persona and (field_name in ("name_age", "age", None)) and age_only:
            persona.age = int(age_only.group(1))
            state.last_edited_field = "name_age"
            await session.commit()
            return (
                f"Поправил возраст: теперь <b>{persona.name}, {persona.age} лет</b>.",
                {
                    "inline_keyboard": [
                        [{"text": "👤 Открыть анкету", "callback_data": "menu_legend"}],
                        [{"text": "◀️ Главное меню", "callback_data": "menu_main"}],
                    ]
                },
            )

        if persona and field_name == "name_age":
            age_m = re.search(r"\b(1[7-9]|[2-3][0-9]|4[0-5])\b", new_value)
            if age_m:
                persona.age = int(age_m.group(1))
                c_name = re.sub(r"\b(1[7-9]|[2-3][0-9]|4[0-5])\b", "", new_value).replace(",", "").strip()
                if c_name:
                    persona.name = c_name.capitalize()
            else:
                persona.name = new_value.strip().capitalize()
            await session.commit()
            return (
                f"Обновил: теперь <b>{persona.name}, {persona.age} лет</b>.",
                {
                    "inline_keyboard": [
                        [{"text": "👤 Открыть анкету", "callback_data": "menu_legend"}],
                        [{"text": "◀️ Главное меню", "callback_data": "menu_main"}],
                    ]
                },
            )

        if persona and field_name == "city":
            parts = [p.strip() for p in new_value.split(",", 1)]
            persona.city = parts[0]
            if len(parts) > 1:
                persona.district = parts[1]
            persona.timezone_name = TimezoneService.resolve_timezone_for_city(persona.city)
            await session.commit()
            loc_str = f"{persona.city}, {persona.district}" if persona.district else persona.city
            return (
                f"Поправил город: теперь <b>{html.escape(loc_str)}</b> (часовой пояс: <code>{persona.timezone_name}</code>).",
                {
                    "inline_keyboard": [
                        [{"text": "👤 Открыть анкету", "callback_data": "menu_legend"}],
                        [{"text": "◀️ Главное меню", "callback_data": "menu_main"}],
                    ]
                },
            )

        if persona and field_name == "birth_city":
            persona.birth_city = new_value.strip()
            await session.commit()
            return (
                f"Поправил родной город: теперь <b>{html.escape(persona.birth_city)}</b>.",
                {
                    "inline_keyboard": [
                        [{"text": "👤 Открыть анкету", "callback_data": "menu_legend"}],
                        [{"text": "◀️ Главное меню", "callback_data": "menu_main"}],
                    ]
                },
            )

        if persona and field_name == "sleep_schedule":
            times = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", new_value)
            if len(times) >= 2:
                persona.sleep_start = times[0]
                persona.sleep_end = times[1]
                await session.commit()
                return (
                    f"Поправил режим сна: теперь <b>{persona.sleep_start} – {persona.sleep_end}</b>.",
                    {
                        "inline_keyboard": [
                            [{"text": "👤 Открыть анкету", "callback_data": "menu_legend"}],
                            [{"text": "◀️ Главное меню", "callback_data": "menu_main"}],
                        ]
                    },
                )

        if field_name == "grant_days" and state.last_entity_id:
            m_days = re.search(r"(\d+)", new_value)
            if m_days:
                days = max(1, int(m_days.group(1)))
                sub = await SubscriptionService.grant_custom_days(
                    session, user_id=state.last_entity_id, days=days, extend=False
                )
                return (
                    f"Изменил срок подписки: теперь начислено <b>{days} дн.</b> (до <code>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</code>).",
                    {
                        "inline_keyboard": [
                            [{"text": "👥 Подписчики", "callback_data": "admin_users_overview"}],
                            [{"text": "◀️ Главное меню", "callback_data": "menu_main"}],
                        ]
                    },
                )

        return None

    async def build_system_diagnostics_reply(
        self,
        session: AsyncSession,
        db_user_id: int,
        is_admin: bool,
        has_ai_key: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Calm, direct troubleshooting when the user is frustrated ('почему не работает' / 'бот молчит').
        Inspects actual DB state (subscription, connected accounts, paused chats, AI key) and gives exact status.
        """
        sub = await SubscriptionService.get_active_subscription(session, db_user_id)
        sub_info = SubscriptionService.get_detailed_status(sub)
        accounts = await AccountService.get_user_accounts(session, db_user_id)
        active_accs = [a for a in accounts if a.is_active and a.status == AccountStatus.CONNECTED]
        contacts = await StatsService.get_chat_leads(session, db_user_id)
        paused_contacts = [c for c in contacts if c.bot_paused or not c.ai_active]

        issues = []
        kb_rows = []

        if not sub_info["is_active"]:
            issues.append("• ❌ <b>Подписка не активна</b> — автоответы в чатах остановлены.")
            kb_rows.append([{"text": "💳 Активировать подписку", "callback_data": "menu_buy_sub"}])
        else:
            issues.append(f"• ✅ Подписка активна ({sub_info['plan_name']}, осталось {sub_info['remaining_str']}).")

        if not accounts:
            issues.append("• ❌ <b>Нет подключенных аккаунтов Telegram Business</b> — боту неоткуда получать чаты.")
            kb_rows.append([{"text": "➕ Подключить аккаунт", "callback_data": "menu_accounts"}])
        elif not active_accs:
            issues.append(f"• ⚠️ <b>Аккаунты на паузе ({len(accounts)} шт.)</b> — включите аккаунт в разделе «Мои аккаунты».")
            kb_rows.append([{"text": "📱 Включить аккаунт", "callback_data": "menu_accounts"}])
        else:
            issues.append(f"• ✅ Подключено активных аккаунтов: <b>{len(active_accs)}</b>.")

        if paused_contacts:
            issues.append(
                f"• ⏸ <b>Чатов на ручной паузе: {len(paused_contacts)}</b> "
                f"(если вы писали клиенту вручную, бот ждёт 15 минут или снятия паузы)."
            )
            kb_rows.append([{"text": "💬 Проверить чаты на паузе", "callback_data": "chats_filter_paused"}])

        if not has_ai_key:
            issues.append("• ⚠️ <b>Не задан GEMINI_API_KEY</b> — используется резервный движок.")

        kb_rows.append([{"text": "💬 Открыть чаты (CRM)", "callback_data": "menu_leads_chats"}])
        kb_rows.append([{"text": "◀️ Главное меню", "callback_data": "menu_main"}])

        text = (
            "Проверил систему прямо сейчас — вот текущая картина:\n\n"
            + "\n".join(issues)
            + "\n\nЕсли конкретный диалог молчит, открой его карточку в «Чатах» и проверь, не стоит ли на нём ручная пауза."
        )
        return text, {"inline_keyboard": kb_rows}
