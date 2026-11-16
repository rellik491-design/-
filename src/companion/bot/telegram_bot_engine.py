import os
import random
import asyncio
import logging
import html
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union
import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models import (
    async_session_maker,
    init_db,
    User,
    Persona,
    PlanDuration,
    Contact,
    Subscription,
    PaymentInvoice,
    AccountSession,
    ContactBehavioralPreference,
    ContactMemory,
)
from companion.models.enums import ContactStatus, AccountStatus
from companion.services.styles_service import DialogueStyleEngine, StyleType
from companion.services.invite_service import InviteService
from companion.services.account_service import AccountService
from companion.services.stats_service import StatsService
from companion.services.handover_service import HandoverService
from companion.services.voice_service import VoiceService
from companion.services.stt_service import STTService
from companion.services.subscription_service import SubscriptionService
from companion.services.crypto_pay_service import CryptoPayService
from companion.services.reengagement_service import ReengagementService
from companion.services.ai_service import AIService
from companion.services.timezone_service import TimezoneService
from companion.services.intent_service import IntentService
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.business_bot import BusinessChatBot
from companion.bot.admin_notifier import AdminNotifier
from companion.bot.manager_engine import ConversationManagerEngine, FLOW_LABELS
from companion.ai.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class TelegramBotEngine:
    """
    Comprehensive Asynchronous Telegram Bot runner implementing:
    - Interactive Admin Control Panel & User Subscription Center
    - Direct Subscription Gifting (Admin grants 7, 14, 30, 31 days with instant user notification)
    - Full Subscription Lifecycle (Live countdown, upgrade, cancellation)
    - CryptoBot (@CryptoBot / Crypto Pay) automated payments & invoice verification
    - Ultra-realistic Voice Notes (OGG Opus with genuine jumping waveforms & human conversational cadence)
    - Telegram Business Chat Automation (business_connection & business_message)
    """

    VOICE_SAMPLES = {
        "shy": "...эмм... привет... я если честно немного смущаюсь так вот голосом записывать... ну... как твои дела? не отвлекаю тебя?",
        "playful": "...слушай, ну и чем ты меня сегодня удивлять собираешься, м? я сегодня прям в хорошем настроении...",
        "sarcastic": "...хм... ну привет... давай, рассказывай, только чур без банальных подкатов, ладно?",
        "caring": "...привет... слушай, как твой день вообще проходит? пообедал хоть нормально? ты там не перегружай себя на работе, отдыхай...",
        "adaptive_all": "...слушай, ну я только что домой зашла, вообще без сил... давай я сейчас в душ схожу быстренько, и минут через двадцать напишу тебе, хорошо?",
    }

    def __init__(self, token: str, admin_id: int):
        self.token = token
        self.admin_id = admin_id
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.client: Optional[httpx.AsyncClient] = None
        self.poll_client: Optional[httpx.AsyncClient] = None
        self._running = False
        self.offset = 0
        self._user_input_states: Dict[int, str] = {}
        self._cached_voice_files: Dict[str, str] = {}
        self.manager = ConversationManagerEngine()

    def is_admin_user(self, user_tg_id: Optional[int]) -> bool:
        if not user_tg_id:
            return False
        admin_ids = {self.admin_id, getattr(settings, "ADMIN_TELEGRAM_ID", 0), 8541203642, 8501711204} - {0, None}
        return user_tg_id in admin_ids

    async def start(self):
        self._running = True
        self.client = httpx.AsyncClient(timeout=25.0)
        self.poll_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=45.0, write=15.0, pool=45.0))
        logger.info("Telegram Bot Engine started for admin ID: %d", self.admin_id)

        # Register slash commands in Telegram API so typing '/' brings up the suggestions list
        await self.setup_bot_commands()

        # Notify admin of startup
        async with async_session_maker() as session:
            admin_user = await self.get_or_create_user(session, {"id": self.admin_id, "username": "admin"})
            sub = await SubscriptionService.get_active_subscription(session, admin_user.id)
            sub_info = SubscriptionService.get_detailed_status(sub)

        await self.send_message(
            chat_id=self.admin_id,
            text=self.format_main_menu_text(is_admin=True, sub_info=sub_info),
            reply_markup=self.build_main_menu_keyboard(is_admin=True),
            parse_mode="HTML",
        )

        poll_counter = 0
        while self._running:
            try:
                updates = await self.get_updates(offset=self.offset, timeout=20)
                if not self._running:
                    break
                for update in updates:
                    self.offset = update["update_id"] + 1
                    asyncio.create_task(self.handle_update(update))

                # Background checks: payments and contextual re-engagements every ~20s
                poll_counter += 1
                if poll_counter >= 5:
                    poll_counter = 0
                    asyncio.create_task(self.check_crypto_payments_background())
                    asyncio.create_task(self.check_scheduled_reengagements_background())

            except Exception as e:
                if self._running:
                    logger.error("Error in bot polling loop: %s (%s)", type(e).__name__, e)
                    await asyncio.sleep(2)

    async def stop(self):
        self._running = False
        if self.poll_client:
            await self.poll_client.aclose()
        if self.client:
            await self.client.aclose()

    async def check_crypto_payments_background(self):
        """Periodically polls unpaid invoices and delivers confirmed subscriptions."""
        try:
            async with async_session_maker() as session:
                paid_list = await CryptoPayService.process_pending_invoices(session)
                for inv, sub in paid_list:
                    stmt = select(User).where(User.id == inv.user_id)
                    u = (await session.execute(stmt)).scalars().first()
                    if u and u.telegram_id:
                        await self.send_message(
                            chat_id=u.telegram_id,
                            text=(
                                f"🎉 <b>Оплата через CryptoBot успешно подтверждена!</b>\n\n"
                                f"Вам начислена подписка на <b>{inv.plan.value}</b>!\n"
                                f"⏳ Срок действия продлен до: <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
                                f"Все функции бота и автоматизация чатов активны!"
                            ),
                            reply_markup=self.build_main_menu_keyboard(is_admin=self.is_admin_user(u.telegram_id)),
                            parse_mode="HTML",
                        )
                    # Alert admin
                    await AdminNotifier.send_telegram_alert(
                        f"💰 <b>Оплата через CryptoBot!</b>\nПользователь ID <code>{u.telegram_id if u else inv.user_id}</code> оплатил {inv.amount} USDT ({inv.plan.value}).",
                        self.admin_id,
                    )
        except Exception as e:
            logger.error("Background payment check error: %s", e)

    async def check_scheduled_reengagements_background(self):
        """Periodically checks and sends due contextual re-engagements and morning wake-up messages."""
        try:
            async with async_session_maker() as session:
                due_contacts = await ReengagementService.find_due_scheduled_reengagements(session)
                for contact in due_contacts:
                    user_stmt = select(User).where(User.id == contact.user_id)
                    user_obj = (await session.execute(user_stmt)).scalars().first()
                    if not user_obj:
                        continue

                    persona_stmt = select(Persona).where(Persona.user_id == user_obj.id, Persona.is_active == True)
                    persona = (await session.execute(persona_stmt)).scalars().first()
                    if not persona:
                        continue

                    # Check if girl is awake in her local city timezone (between 09:00 and 22:30)
                    now_utc = datetime.now(timezone.utc)
                    if TimezoneService.is_sleeping_hours(persona.timezone_name, persona.sleep_start, persona.sleep_end, ref_utc_dt=now_utc):
                        continue

                    prompt = ReengagementService.build_reengagement_prompt(persona, contact)
                    reply_text = await AIService.generate_reply(
                        persona=persona,
                        contact=contact,
                        chat_history=AIService.get_chat_history(contact.id),
                        new_incoming_message=f"[{prompt}]",
                    )
                    if not reply_text:
                        continue

                    # Clear scheduled state
                    contact.scheduled_reengage_at = None
                    contact.reengage_context = None
                    contact.pending_overnight_message = None
                    contact.last_outgoing_at = now_utc
                    contact.total_messages_out += 1
                    await session.commit()

                    # Find business connection if available
                    acc_stmt = select(AccountSession).where(AccountSession.user_id == user_obj.id, AccountSession.is_active == True)
                    acc = (await session.execute(acc_stmt)).scalars().first()
                    if acc and acc.business_connection_id:
                        bubbles = [b.strip() for b in reply_text.split("||") if b.strip()]
                        for b in bubbles:
                            await BusinessChatBot.send_business_message(
                                bot_token=self.token,
                                business_connection_id=acc.business_connection_id,
                                chat_id=contact.tg_id,
                                text=b,
                            )
                    elif contact.tg_id:
                        bubbles = [b.strip() for b in reply_text.split("||") if b.strip()]
                        for b in bubbles:
                            await self.send_message(chat_id=contact.tg_id, text=b)
        except Exception as e:
            logger.error("Background reengagement check error: %s", e)

    async def setup_bot_commands(self) -> bool:
        """
        Registers slash commands with Telegram Bot API via setMyCommands
        so they instantly pop up as suggestions when the user types '/' in Telegram,
        and sets the Menu button.
        """
        user_commands = [
            {"command": "start", "description": "🚀 Главное меню и статус"},
            {"command": "app", "description": "📱 Открыть Mini App панель управления"},
            {"command": "menu", "description": "📋 Открыть главное меню"},
            {"command": "sub", "description": "⭐ Моя подписка и продление"},
            {"command": "accounts", "description": "📱 Подключенные Telegram-аккаунты"},
            {"command": "legend", "description": "⚙️ Настройка анкеты девушки"},
            {"command": "styles", "description": "🎭 Режимы и стили общения"},
            {"command": "voice", "description": "🎙 Тест голосовых сообщений"},
            {"command": "leads", "description": "🔥 Горячие лиды на свидание/квест"},
            {"command": "stats", "description": "📊 Статистика переписок"},
            {"command": "help", "description": "❓ Справка и помощь"},
        ]

        admin_commands = user_commands + [
            {"command": "admin", "description": "👑 Панель управления (Админ)"},
            {"command": "users", "description": "👥 База пользователей и аккаунтов"},
            {"command": "grant", "description": "🎁 Выдать подписку пользователю"},
            {"command": "key", "description": "🧠 Подключить AI-ключ"},
            {"command": "clone", "description": "🎙 Настройка голоса"},
        ]

        if not self.client or not self.token or self.token in ("mock_token", "test_token"):
            return True

        try:
            # 1. Default commands for all users in private chats
            res_user = await self.client.post(
                f"{self.base_url}/setMyCommands",
                json={
                    "commands": user_commands,
                    "scope": {"type": "default"},
                },
            )
            if res_user.status_code != 200:
                logger.warning("Telegram setMyCommands (default) failed: %s %s", res_user.status_code, res_user.text)

            # 2. Admin commands for the admin chat
            if self.admin_id:
                res_adm = await self.client.post(
                    f"{self.base_url}/setMyCommands",
                    json={
                        "commands": admin_commands,
                        "scope": {"type": "chat", "chat_id": self.admin_id},
                    },
                )
                if res_adm.status_code != 200:
                    logger.warning("Telegram setMyCommands (admin) failed: %s %s", res_adm.status_code, res_adm.text)

            # 3. Configure Chat Menu button (Frozen Mini App: primary interface is the Telegram Bot itself)
            menu_btn_payload = {"menu_button": {"type": "commands"}}

            await self.client.post(
                f"{self.base_url}/setChatMenuButton",
                json=menu_btn_payload,
            )
            logger.info("Bot commands and Menu Button successfully registered in Telegram API.")
            return True
        except Exception as e:
            logger.warning("Exception in setup_bot_commands: %s", e)
            return False

    async def get_updates(self, offset: int = 0, timeout: int = 20) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/getUpdates"
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": [
                "message",
                "edited_message",
                "callback_query",
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
        }
        try:
            client = self.poll_client or self.client
            if not client:
                return []
            res = await client.post(url, json=payload)
            data = res.json()
            return data.get("result", []) if data.get("ok") else []
        except (httpx.ReadTimeout, httpx.TimeoutException):
            return []
        except Exception as e:
            if self._running:
                logger.error("Error in get_updates: %s (%s)", type(e).__name__, e)
            return []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
        reply_to_message_id: Optional[int] = None,
    ) -> bool:
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>[Текст сокращен из-за лимита длины Telegram]</i>"
        url = f"{self.base_url}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            from companion.bot.reply_validator import ReplyValidator
            reply_params = ReplyValidator.build_reply_parameters(reply_to_message_id, is_business_connection=False, chat_id=chat_id)
            if reply_params:
                payload["reply_parameters"] = reply_params
                payload["reply_to_message_id"] = int(reply_to_message_id)
                payload["allow_sending_without_reply"] = True
        try:
            if not self.client:
                return False
            res = await self.client.post(url, json=payload)
            if res.status_code == 200:
                try:
                    resp_json = res.json()
                    sent_id = resp_json.get("result", {}).get("message_id")
                    if sent_id:
                        from companion.services.message_ownership_service import MessageOwnershipService
                        MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                except Exception:
                    pass
                return True
            if res.status_code == 400 and "reply_parameters" in payload:
                logger.warning("Telegram rejected reply_parameters for direct chat %s (%s). Retrying without reply target.", chat_id, res.text)
                payload.pop("reply_parameters", None)
                payload.pop("reply_to_message_id", None)
                payload.pop("allow_sending_without_reply", None)
                retry_res = await self.client.post(url, json=payload)
                if retry_res.status_code == 200:
                    try:
                        resp_json = retry_res.json()
                        sent_id = resp_json.get("result", {}).get("message_id")
                        if sent_id:
                            from companion.services.message_ownership_service import MessageOwnershipService
                            MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                    except Exception:
                        pass
                    return True
                return False
            if res.status_code != 200:
                logger.error("Failed to send message: %s - %s", res.status_code, res.text)
            return res.status_code == 200
        except Exception as e:
            logger.error("Exception in send_message: %s (%s)", type(e).__name__, e)
            return False

    async def send_photo(
        self,
        chat_id: int,
        photo_path_or_id: str,
        caption: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Sends a photo to chat_id. Supports local storage file path or telegram_file_id.
        Returns Telegram message_id on success, or None on failure.
        """
        url = f"{self.base_url}/sendPhoto"
        data: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "parse_mode": parse_mode,
        }
        if caption:
            data["caption"] = caption
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = str(reply_to_message_id)

        try:
            if not self.client:
                return None
            import os
            if os.path.exists(photo_path_or_id):
                with open(photo_path_or_id, "rb") as f:
                    files = {"photo": (os.path.basename(photo_path_or_id), f, "image/jpeg")}
                    res = await self.client.post(url, data=data, files=files)
            else:
                data["photo"] = photo_path_or_id
                res = await self.client.post(url, data=data)

            if res.status_code == 200:
                resp_json = res.json()
                sent_id = resp_json.get("result", {}).get("message_id") or 1
                try:
                    from companion.services.message_ownership_service import MessageOwnershipService
                    MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                except Exception:
                    pass
                return sent_id
            else:
                logger.error("Failed to send photo: %s - %s", res.status_code, res.text)
                return None
        except Exception as e:
            logger.error("Exception in send_photo: %s (%s)", type(e).__name__, e)
            return None

    async def send_sticker(
        self,
        chat_id: int,
        sticker_file_id: str,
        reply_to_message_id: Optional[int] = None,
    ) -> bool:
        if not sticker_file_id or not self.client:
            return False
        url = f"{self.base_url}/sendSticker"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "sticker": sticker_file_id,
        }
        if reply_to_message_id is not None:
            from companion.bot.reply_validator import ReplyValidator
            reply_params = ReplyValidator.build_reply_parameters(reply_to_message_id, is_business_connection=False, chat_id=chat_id)
            if reply_params:
                payload["reply_parameters"] = reply_params
                payload["reply_to_message_id"] = int(reply_to_message_id)
                payload["allow_sending_without_reply"] = True
        try:
            res = await self.client.post(url, json=payload)
            if res.status_code == 200:
                try:
                    resp_json = res.json()
                    sent_id = resp_json.get("result", {}).get("message_id")
                    if sent_id:
                        from companion.services.message_ownership_service import MessageOwnershipService
                        MessageOwnershipService.register_outgoing_message_memory(chat_id, sent_id)
                except Exception:
                    pass
                return True
            if res.status_code == 400 and "reply_parameters" in payload:
                payload.pop("reply_parameters", None)
                payload.pop("reply_to_message_id", None)
                payload.pop("allow_sending_without_reply", None)
                retry_res = await self.client.post(url, json=payload)
                return retry_res.status_code == 200
            return res.status_code == 200
        except Exception as e:
            logger.error("Exception in send_sticker: %s (%s)", type(e).__name__, e)
            return False

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>[Текст сокращен из-за лимита длины Telegram]</i>"
        url = f"{self.base_url}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            if not self.client:
                return False
            res = await self.client.post(url, json=payload)
            if res.status_code != 200:
                logger.error("Failed to edit message: %s - %s", res.status_code, res.text)
            return res.status_code == 200
        except Exception as e:
            logger.error("Exception in edit_message: %s (%s)", type(e).__name__, e)
            return False

    async def answer_callback_query(
        self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
    ):
        url = f"{self.base_url}/answerCallbackQuery"
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        try:
            if not self.client:
                return
            await self.client.post(url, json=payload)
        except Exception as e:
            logger.error("Exception in answer_callback_query: %s (%s)", type(e).__name__, e)

    async def send_document(
        self,
        chat_id: int,
        document_bytes: bytes,
        filename: str,
        caption: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Sends document file (e.g. CSV statistics) via Telegram Bot API sendDocument."""
        if not self.token or self.token in ("mock_token", "test_token"):
            return True
        url = f"{self.base_url}/sendDocument"
        files = {"document": (filename, document_bytes, "text/csv")}
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            res = await self.client.post(url, data=data, files=files)
            return res.status_code == 200
        except Exception as e:
            logger.error("Error in send_document: %s", e)
            return False

    async def send_voice_by_file_id(
        self,
        chat_id: int,
        voice_file_id: str,
        caption: Optional[str] = None,
    ) -> bool:
        """Sends an existing Telegram voice note by file_id via Bot API sendVoice."""
        if not self.token or self.token in ("mock_token", "test_token"):
            return True
        url = f"{self.base_url}/sendVoice"
        data = {"chat_id": str(chat_id), "voice": voice_file_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        try:
            res = await self.client.post(url, data=data)
            return res.status_code == 200
        except Exception as e:
            logger.error("Error in send_voice_by_file_id: %s", e)
            return False

    async def download_file_bytes(self, file_id: str) -> Optional[bytes]:
        """Downloads a file (e.g. voice note) from Telegram servers by file_id."""
        url = f"{self.base_url}/getFile?file_id={file_id}"
        try:
            res = await self.client.get(url)
            data = res.json()
            if data.get("ok"):
                file_path = data["result"]["file_path"]
                download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
                d_res = await self.client.get(download_url)
                if d_res.status_code == 200:
                    return d_res.content
        except Exception as e:
            logger.error("Error downloading file from Telegram: %s", e)
        return None

    async def get_chat(self, chat_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Resolves chat or username information via Telegram Bot API getChat."""
        url = f"{self.base_url}/getChat"
        try:
            res = await self.client.get(url, params={"chat_id": chat_id})
            data = res.json()
            if data.get("ok"):
                return data.get("result")
        except Exception as e:
            logger.error("Error in get_chat for %s: %s", chat_id, e)
        return None

    # ==========================================
    # User / Persona DB Helper
    # ==========================================
    async def get_or_create_user(self, session: AsyncSession, tg_user: Dict[str, Any]) -> User:
        """Guarantees user exists in SQLite and has a persona record."""
        tg_id = tg_user.get("id")
        raw_username = tg_user.get("username")
        stmt = select(User).where(User.telegram_id == tg_id)
        res = await session.execute(stmt)
        user = res.scalars().first()
        if not user:
            user = User(
                telegram_id=tg_id,
                username=raw_username,
            )
            session.add(user)
            try:
                await session.commit()
                await session.refresh(user)
            except Exception:
                await session.rollback()
                res = await session.execute(stmt)
                user = res.scalars().first()
                if not user:
                    raise
        elif raw_username and user.username != raw_username:
            user.username = raw_username
            await session.commit()

        # Ensure Persona exists for this user
        stmt_p = select(Persona).where(Persona.user_id == user.id)
        res_p = await session.execute(stmt_p)
        persona = res_p.scalars().first()
        if not persona:
            persona = Persona(
                user_id=user.id,
                name="Алина",
                age=22,
                city="Москва",
                timezone_name="Europe/Moscow",
                sleep_start="23:00",
                sleep_end="08:30",
                legend="Ты общительная, милая и немного саркастичная девушка. Учишься на дизайне и любишь кофе.",
                current_style="adaptive_all",
            )
            session.add(persona)
            await session.commit()

        return user

    # ==========================================
    # Keyboards & Formatting
    # ==========================================
    def format_main_menu_text(self, is_admin: bool, sub_info: dict) -> str:
        role_badge = "👑 <b>Панель Главного Администратора</b>" if is_admin else "👋 <b>Личный кабинет AI-Компаньона</b>"

        has_ai_key = bool(settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY"))
        ai_badge = (
            "🧠 <b>Интеллект:</b> Нейросеть активна (Онлайн ✅)"
            if has_ai_key
            else "🧠 <b>Интеллект:</b> Умный диалоговый движок (Офлайн ℹ️ — отправьте <code>/key &lt;ключ&gt;</code> для подключения нейросети)"
        )

        if sub_info.get("is_active"):
            status_text = (
                f"✅ <b>Активна ({sub_info['plan_name']})</b>\n"
                f"⏳ <b>Действует до:</b> <code>{sub_info['expires_at_str']}</code>\n"
                f"⏱ <b>Осталось:</b> <b>{sub_info['remaining_str']}</b>\n"
                f"📱 <b>Лимит аккаунтов:</b> до {sub_info['max_accounts']} акк."
            )
        else:
            status_text = (
                "❌ <b>Подписка не активна</b>\n"
                "👉 <i>Оформите подписку в меню ниже для включения авто-ответов и генерации войсов.</i>"
            )

        return (
            f"{role_badge}\n\n"
            f"🌸 <b>AI-Компаньон</b> — ультра-реалистичное ведение диалогов от лица девушки "
            f"с естественными голосовыми и перехватом предложений встреч.\n\n"
            f"⭐ <b>СТАТУС ПОДПИСКИ:</b>\n{status_text}\n\n"
            f"{ai_badge}\n\n"
            f"Выберите нужный раздел в меню:"
        )

    def build_main_menu_keyboard(self, is_admin: bool = False) -> Dict[str, Any]:
        buttons = [
            [
                {"text": "💬 Лиды (Общение)", "callback_data": "menu_leads_chats"},
                {"text": "🔴 Лиды (Оскорбления)", "callback_data": "menu_leads_insults"},
            ],
            [
                {"text": "📊 Статистика общения с лидами", "callback_data": "menu_leads_stats_today"},
                {"text": "🔥 Лиды (Встречи)", "callback_data": "menu_leads"},
            ],
            [
                {"text": "⭐ Моя подписка", "callback_data": "menu_my_sub"},
                {"text": "💳 Оплата (@CryptoBot)", "callback_data": "menu_buy_sub"},
            ],
            [
                {"text": "🎭 Стиль: Адаптивный", "callback_data": "menu_styles"},
                {"text": "👤 Анкета и Легенда", "callback_data": "menu_legend"},
            ],
            [
                {"text": "🔗 Создать ссылку", "callback_data": "menu_invites"},
                {"text": "📱 Мои аккаунты", "callback_data": "menu_accounts"},
            ],
            [
                {"text": "🎙 Тест войса", "callback_data": "menu_voice_test"},
                {"text": "📈 Общая статистика", "callback_data": "menu_stats"},
            ],
        ]

        if is_admin:
            buttons.append([
                {"text": "👥 Подписчики и Аккаунты", "callback_data": "admin_users_overview"},
                {"text": "🎁 Выдать / Подарить подписку", "callback_data": "admin_grant_menu"},
            ])
            buttons.append([
                {"text": "📸 Photo Pool (Пул фото)", "callback_data": "photos_stats"},
            ])

        return {"inline_keyboard": buttons}

    def build_my_sub_keyboard(self, has_active_sub: bool) -> Dict[str, Any]:
        kb = [
            [{"text": "➕ Добавить время / Продлить", "callback_data": "menu_buy_sub"}],
        ]
        if has_active_sub:
            kb.append([{"text": "❌ Отменить подписку", "callback_data": "confirm_cancel_sub"}])
        kb.append([{"text": "◀️ Назад в меню", "callback_data": "menu_main"}])
        return {"inline_keyboard": kb}

    def build_buy_sub_keyboard(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "⚡ 3 дня — 3 USDT", "callback_data": "buy_plan_3d"},
                    {"text": "⚡ 7 дней — 7 USDT", "callback_data": "buy_plan_7d"},
                ],
                [
                    {"text": "💎 14 дней — 12 USDT", "callback_data": "buy_plan_14d"},
                    {"text": "👑 31 день — 25 USDT", "callback_data": "buy_plan_31d"},
                ],
                [{"text": "🧾 Активация чеком @CryptoBot", "callback_data": "cheque_info"}],
                [{"text": "◀️ Назад к подписке", "callback_data": "menu_my_sub"}],
            ]
        }

    @staticmethod
    def parse_plan_and_days(arg: str) -> tuple[int, PlanDuration, str]:
        arg_low = arg.lower().strip()
        if arg_low in ("3", "3d", "3_days", "3дня", "3д"):
            return 3, PlanDuration.DAYS_3, "3 дня"
        if arg_low in ("7", "7d", "7_days", "week", "7дней", "7д", "неделя"):
            return 7, PlanDuration.DAYS_7, "7 дней"
        if arg_low in ("14", "14d", "14_days", "2weeks", "14дней", "14д", "2недели"):
            return 14, PlanDuration.DAYS_14, "14 дней"
        if arg_low in ("30", "30d", "30дней", "30д"):
            return 30, PlanDuration.DAYS_31, "30 дней"
        if arg_low in ("31", "31d", "31_days", "1m", "month", "месяц", "31день", "31д"):
            return 31, PlanDuration.DAYS_31, "31 день (1 месяц)"
        if arg_low in ("90", "90d", "3m", "3месяца", "90дней"):
            return 90, PlanDuration.DAYS_31, "90 дней (3 месяца)"
        if arg_low in ("180", "180d", "6m", "полгода", "180дней"):
            return 180, PlanDuration.DAYS_31, "180 дней (полгода)"
        if arg_low in ("365", "365d", "1y", "year", "год", "365дней"):
            return 365, PlanDuration.DAYS_31, "365 дней (1 год)"

        clean_num = arg_low.rstrip("d").rstrip("д").rstrip("дней").rstrip("дня").strip()
        try:
            days = max(1, int(clean_num))
            if days >= 25:
                plan = PlanDuration.DAYS_31
            elif days >= 10:
                plan = PlanDuration.DAYS_14
            elif days >= 5:
                plan = PlanDuration.DAYS_7
            else:
                plan = PlanDuration.DAYS_3
            return days, plan, f"{days} дн."
        except ValueError:
            return 7, PlanDuration.DAYS_7, "7 дней"

    def build_admin_grant_keyboard(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "👥 Список подписчиков и их аккаунты", "callback_data": "admin_users_overview"}],
                [
                    {"text": "⚡ Выдать себе 3 дня", "callback_data": "grant_self_3d"},
                    {"text": "⚡ Выдать себе 7 дней", "callback_data": "grant_self_7d"},
                ],
                [
                    {"text": "💎 Выдать себе 14 дней", "callback_data": "grant_self_14d"},
                    {"text": "👑 Выдать себе 31 день", "callback_data": "grant_self_31d"},
                ],
                [
                    {"text": "🚀 Выдать себе 90 дней", "callback_data": "grant_self_90d"},
                    {"text": "🌟 Выдать себе 365 дней", "callback_data": "grant_self_365d"},
                ],
                [
                    {"text": "⚡ Шаблон: 3 дня", "callback_data": "grant_prompt_3d"},
                    {"text": "⚡ Шаблон: 7 дней", "callback_data": "grant_prompt_7d"},
                ],
                [
                    {"text": "💎 Шаблон: 14 дней", "callback_data": "grant_prompt_14d"},
                    {"text": "👑 Шаблон: 31 день", "callback_data": "grant_prompt_31d"},
                ],
                [
                    {"text": "🚀 Шаблон: 90 дней", "callback_data": "grant_prompt_90d"},
                    {"text": "🌟 Шаблон: 365 дней", "callback_data": "grant_prompt_365d"},
                ],
                [{"text": "◀️ Назад в главное меню", "callback_data": "menu_main"}],
            ]
        }

    def format_subscribers_overview_text(self, users_data: List[Dict[str, Any]]) -> str:
        total_users = len(users_data)
        active_subs = sum(1 for u in users_data if u["sub_info"]["is_active"])
        total_accounts = sum(u["total_accounts"] for u in users_data)
        active_accounts = sum(u["active_accounts"] for u in users_data)

        header = (
            "👥 <b>ПАНЕЛЬ АДМИНИСТРАТОРА: ПОЛЬЗОВАТЕЛИ И АККАУНТЫ</b>\n\n"
            f"📊 <b>СВОДНАЯ СТАТИСТИКА:</b>\n"
            f"• Всего пользователей в боте: <b>{total_users} чел.</b>\n"
            f"• С активной подпиской: <b>{active_subs} чел.</b>\n"
            f"• Подключено аккаунтов: <b>{total_accounts} акк.</b> (🟢 {active_accounts} активны)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        if not users_data:
            return header + "<i>В базе пока нет зарегистрированных пользователей.</i>"

        cards = []
        shown_count = 0
        current_len = len(header)
        for idx, u in enumerate(users_data, 1):
            sub_info = u["sub_info"]
            user_title = f"@{u['username']}" if u["username"] else f"ID {u['telegram_id']}"
            is_adm = self.is_admin_user(u["telegram_id"])
            adm_badge = " 👑 (Админ)" if is_adm else ""

            if sub_info["is_active"]:
                sub_badge = (
                    f"🟢 <b>{sub_info['plan_name']}</b> "
                    f"(осталось <b>{sub_info['remaining_str']}</b>)"
                )
                limit_badge = f"{u['total_accounts']} из {sub_info['max_accounts']} макс."
            else:
                sub_badge = "🔴 <b>Подписка не активна</b>"
                limit_badge = f"{u['total_accounts']} акк."

            acc_lines = []
            if u["accounts"]:
                for a_idx, a in enumerate(u["accounts"][:3], 1):
                    status_emoji = "🟢" if a["is_active"] and a["status"] == AccountStatus.CONNECTED.value else "⏸"
                    acc_tag = f"@{a['username']}" if a["username"] else f"ID {a['tg_user_id'] or a['id']}"
                    acc_lines.append(
                        f"     {status_emoji} <b>#{a_idx}</b> {acc_tag} "
                        f"(<b>{a['persona_name']}</b>, {a['persona_city']} | {a['style_title']})"
                    )
                if len(u["accounts"]) > 3:
                    acc_lines.append(f"     <i>...и еще {len(u['accounts']) - 3} акк.</i>")
                accs_str = "\n".join(acc_lines)
            else:
                accs_str = "     <i>Аккаунты не подключены</i>"

            card = (
                f"<b>{idx}. {user_title}</b>{adm_badge} [ID: <code>{u['telegram_id']}</code>]\n"
                f"   ⭐ <b>Подписка:</b> {sub_badge}\n"
                f"   📱 <b>Аккаунтов:</b> <b>{limit_badge}</b> (🟢 {u['active_accounts']} в сети)\n"
                f"   🔥 <b>Лидов:</b> {u['leads_count']}\n"
                f"   <b>Профили:</b>\n"
                f"{accs_str}"
            )
            if current_len + len(card) + 100 > 3800:
                remaining = len(users_data) - shown_count
                cards.append(f"<i>...и еще {remaining} пользователей (выберите пользователя на кнопках ниже)</i>")
                break
            cards.append(card)
            current_len += len(card) + 2
            shown_count += 1

        return header + "\n\n".join(cards)

    def build_subscribers_overview_keyboard(self, users_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        rows = [
            [{"text": "🔄 Обновить данные", "callback_data": "admin_users_overview"}],
        ]
        # Quick user buttons (up to 8 users)
        for u in users_data[:8]:
            u_name = f"@{u['username']}" if u['username'] else f"ID {u['telegram_id']}"
            acc_count = u['total_accounts']
            sub_state = "🟢" if u['sub_info']['is_active'] else "🔴"
            rows.append([
                {"text": f"{sub_state} {u_name} ({acc_count} акк.)", "callback_data": f"admin_user_detail_{u['user_id']}"}
            ])

        rows.append([
            {"text": "🎁 Выдать подписку вручную", "callback_data": "admin_grant_menu"},
            {"text": "◀️ В главное меню", "callback_data": "menu_main"},
        ])
        return {"inline_keyboard": rows}

    def format_single_user_detail_text(self, u: Dict[str, Any]) -> str:
        sub_info = u["sub_info"]
        user_title = f"@{u['username']}" if u["username"] else f"ID {u['telegram_id']}"
        is_adm = self.is_admin_user(u["telegram_id"])
        adm_badge = " 👑 (Администратор)" if is_adm else ""

        if sub_info["is_active"]:
            sub_status = (
                f"🟢 <b>АКТИВНА ({sub_info['plan_name']})</b>\n"
                f"   • Истекает: <code>{sub_info['expires_at_str']}</code> (МСК)\n"
                f"   • Осталось точно: <b>{sub_info['remaining_str']}</b>\n"
                f"   • Допустимый лимит аккаунтов: <b>до {sub_info['max_accounts']} акк.</b>\n"
                f"   • Войсов сегодня: {sub_info['voice_used_today']} / {sub_info['max_daily_voice']}"
            )
        else:
            sub_status = "🔴 <b>НЕ АКТИВНА / ИСТЕКЛА</b>"

        reg_date = u["created_at"].strftime("%d.%m.%Y %H:%M") if u.get("created_at") else "неизвестно"

        acc_lines = []
        if u["accounts"]:
            for idx, a in enumerate(u["accounts"], 1):
                status_emoji = "🟢" if a["is_active"] and a["status"] == AccountStatus.CONNECTED.value else "⏸"
                acc_tag = f"@{a['username']}" if a["username"] else f"ID {a['tg_user_id'] or a['id']}"
                acc_lines.append(
                    f"<b>{idx}. {acc_tag}</b> ({status_emoji} {a['status']})\n"
                    f"   • Имя в переписках: <b>{a['persona_name']}</b>, {a['persona_age']} лет, г. {a['persona_city']}\n"
                    f"   • Стиль: <b>{a['style_title']}</b>\n"
                    f"   • Connection ID: <code>{a['business_connection_id'] or 'нет'}</code>"
                )
            accounts_block = "\n\n".join(acc_lines)
        else:
            accounts_block = "<i>У этого пользователя пока нет подключенных Telegram-аккаунтов.</i>"

        return (
            f"👤 <b>КАРТОЧКА ПОЛЬЗОВАТЕЛЯ: {user_title}</b>{adm_badge}\n\n"
            f"🆔 <b>Telegram ID:</b> <code>{u['telegram_id']}</code>\n"
            f"📅 <b>Дата регистрации:</b> {reg_date}\n"
            f"🔥 <b>Закрыто лидов на свидания:</b> <b>{u['leads_count']}</b>\n\n"
            f"⭐ <b>СТАТУС ПОДПИСКИ:</b>\n{sub_status}\n\n"
            f"📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ ({u['total_accounts']} из {sub_info.get('max_accounts', 1)}):</b>\n"
            f"{accounts_block}\n\n"
            f"💡 <i>Используйте кнопки ниже для быстрого продления или начисления дней:</i>"
        )

    def build_single_user_detail_keyboard(self, user_id: int) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "⚡ +3 дня", "callback_data": f"grant_user_{user_id}_3"},
                    {"text": "⚡ +7 дней", "callback_data": f"grant_user_{user_id}_7"},
                ],
                [
                    {"text": "💎 +14 дней", "callback_data": f"grant_user_{user_id}_14"},
                    {"text": "👑 +31 день", "callback_data": f"grant_user_{user_id}_31"},
                ],
                [
                    {"text": "❌ Аннулировать подписку", "callback_data": f"cancel_user_sub_{user_id}"},
                ],
                [
                    {"text": "◀️ Назад к списку", "callback_data": "admin_users_overview"},
                    {"text": "🏠 В главное меню", "callback_data": "menu_main"},
                ],
            ]
        }

    def build_styles_keyboard(self, active_style: str = "adaptive_all", account_id: Optional[int] = None) -> Dict[str, Any]:
        suffix = f"_{account_id}" if account_id is not None else ""
        back_cb = f"legend_acc_{account_id}" if account_id is not None else "menu_main"
        back_text = "◀️ Назад к анкете аккаунта" if account_id is not None else "◀️ Назад в меню"

        return {
            "inline_keyboard": [
                [{"text": "✅ 🎭 Адаптивный режим (Активен для всех)", "callback_data": f"set_style_adaptive_all{suffix}"}],
                [{"text": back_text, "callback_data": back_cb}],
            ]
        }

    def format_legend_card(
        self,
        persona: Persona,
        account: Optional[AccountSession] = None,
        photo_count: Optional[int] = None,
    ) -> str:
        name = persona.name or "Алина"
        age = persona.age or 22
        city = persona.city or "Москва"
        district = getattr(persona, "district", "") or "центр / ЦАО"
        birth_city = getattr(persona, "birth_city", "") or "Самара"
        move_reason = getattr(persona, "move_reason", "") or "Поступила в университет на дизайн и осталась жить"
        years_in_city = getattr(persona, "years_in_city", "") or "Живу тут около 4 лет"
        dating_app = getattr(persona, "dating_app_context", "") or "совпали в Дайвинчике пару дней назад, вчера удалила анкету, перешла в тг"
        study_hobbies = getattr(persona, "study_work_hobbies", "") or "учусь на 4 курсе графического дизайна, выставки, кофе и прогулки"
        extra = getattr(persona, "extra_facts", "") or "не люблю банальные свидания в душных кафе, обожаю атмосферные квесты"
        sleep_start = getattr(persona, "sleep_start", "23:00") or "23:00"
        sleep_end = getattr(persona, "sleep_end", "08:30") or "08:30"
        tz_name = getattr(persona, "timezone_name", "Europe/Moscow") or "Europe/Moscow"
        style_val = getattr(persona, "current_style", None) or "adaptive_all"
        style_title = StyleType(style_val).title

        if account:
            acc_name = account.first_name or "Без имени"
            acc_user = f"@{account.username}" if account.username else f"ID: {account.tg_user_id or account.id}"
            acc_badge = f"📱 <b>Привязанный аккаунт:</b> {acc_name} ({acc_user})\n\n"
        else:
            acc_badge = "⭐ <b>Привязанный аккаунт:</b> Общий шаблон по умолчанию\n\n"

        photo_line = (
            f"• <b>Фотографии:</b> 📸 {photo_count} шт. (привязаны к модели)\n"
            if photo_count is not None
            else ""
        )

        return (
            f"👤 <b>АНКЕТА И ЛЕГЕНДА ДЕВУШКИ</b>\n\n"
            f"{acc_badge}"
            f"Эти данные нейросеть использует в каждом сообщении, чтобы общаться "
            f"строго по вашей индивидуальной легенде:\n\n"
            f"• <b>Имя и возраст:</b> {name}, {age} лет\n"
            f"• <b>Город и район:</b> {city}, {district} (<code>{tz_name}</code>)\n"
            f"• <b>Откуда родом:</b> {birth_city}\n"
            f"• <b>Почему переехала:</b> <i>{move_reason}</i>\n"
            f"• <b>Сколько уже тут:</b> <i>{years_in_city}</i>\n"
            f"• <b>Контекст дейтинга:</b> <i>{dating_app}</i>\n"
            f"• <b>Учеба / Работа и хобби:</b> <i>{study_hobbies}</i>\n"
            f"• <b>Детали и предпочтения:</b> <i>{extra}</i>\n"
            f"• <b>Режим сна:</b> 🌙 {sleep_start} – {sleep_end}\n"
            f"• <b>Стиль общения:</b> {style_title}\n"
            f"{photo_line}\n"
            f"<i>Вы можете отредактировать любой пункт отдельно или нажать кнопку ниже, чтобы нейросеть заполнила всю анкету из одного вашего сообщения!</i>"
        )

    def build_legend_keyboard(
        self,
        account_id: Optional[int] = None,
        has_multiple_accounts: bool = False,
        photo_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        suffix = f"_{account_id}" if account_id is not None else ""
        style_cb = f"style_acc_{account_id}" if account_id is not None else "menu_styles"
        acc_code = str(account_id) if account_id is not None else "default"

        photo_btn_label = (
            f"📸 Фотографии модели ({photo_count} шт.)"
            if photo_count is not None
            else "📸 Фотографии модели"
        )

        rows = [
            [
                {"text": "✏️ Имя и возраст", "callback_data": f"legend_edit_name_age{suffix}"},
                {"text": "📍 Город и район", "callback_data": f"legend_edit_city_district{suffix}"},
            ],
            [
                {"text": "🏠 Откуда родом", "callback_data": f"legend_edit_birth_city{suffix}"},
                {"text": "✈️ Почему переехала", "callback_data": f"legend_edit_move_reason{suffix}"},
            ],
            [
                {"text": "⏳ Сколько уже тут", "callback_data": f"legend_edit_years_in_city{suffix}"},
                {"text": "💬 Контекст дейтинга", "callback_data": f"legend_edit_dating_app{suffix}"},
            ],
            [
                {"text": "🎨 Учеба/Работа и Хобби", "callback_data": f"legend_edit_study_hobbies{suffix}"},
                {"text": "✨ Детали и предпочтения", "callback_data": f"legend_edit_extra_facts{suffix}"},
            ],
            [
                {"text": "🌙 Режим сна (Часы сна)", "callback_data": f"legend_edit_sleep{suffix}"},
                {"text": "🎭 Стиль общения", "callback_data": style_cb},
            ],
            [
                {"text": "⚡️ Заполнить легенду одним сообщением (AI)", "callback_data": f"legend_wizard_auto{suffix}"},
            ],
            [
                {"text": photo_btn_label, "callback_data": f"photos_acc_{acc_code}"},
            ],
        ]

        if has_multiple_accounts:
            rows.append([
                {"text": "◀️ К выбору аккаунтов", "callback_data": "menu_legend"},
                {"text": "🏠 Главное меню", "callback_data": "menu_main"},
            ])
        else:
            rows.append([{"text": "◀️ Назад в главное меню", "callback_data": "menu_main"}])

        return {"inline_keyboard": rows}

    def build_legend_accounts_keyboard(
        self, accounts: List[AccountSession], personas: Dict[int, Persona]
    ) -> Dict[str, Any]:
        buttons = []
        for acc in accounts:
            p = personas.get(acc.id)
            p_name = p.name if p else (acc.first_name or "Девушка")
            p_city = p.city if p else "Москва"
            acc_label = acc.first_name or (f"@{acc.username}" if acc.username else f"Аккаунт #{acc.id}")
            buttons.append([
                {
                    "text": f"📱 {acc_label} ({p_name}, {p_city})",
                    "callback_data": f"legend_acc_{acc.id}",
                }
            ])

        buttons.append([{"text": "⭐ Общий шаблон по умолчанию", "callback_data": "legend_acc_default"}])
        buttons.append([{"text": "◀️ Назад в главное меню", "callback_data": "menu_main"}])
        return {"inline_keyboard": buttons}

    def build_accounts_keyboard(
        self, accounts: List[AccountSession], personas: Dict[int, Persona]
    ) -> Dict[str, Any]:
        rows = []
        for acc in accounts:
            acc_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")
            rows.append([
                {"text": f"⚙️ Легенда {acc_title}", "callback_data": f"legend_acc_{acc.id}"},
                {"text": f"🎭 Стиль {acc_title}", "callback_data": f"style_acc_{acc.id}"},
            ])
            rows.append([
                {"text": f"📸 Фото {acc_title}", "callback_data": f"photos_acc_{acc.id}"},
            ])
            if acc.is_active and acc.status == AccountStatus.CONNECTED:
                rows.append([
                    {"text": f"⏸ Отключить {acc_title}", "callback_data": f"disconnect_acc_{acc.id}"}
                ])
            else:
                rows.append([
                    {"text": f"▶️ Включить {acc_title}", "callback_data": f"reconnect_acc_{acc.id}"},
                    {"text": f"🗑 Удалить {acc_title}", "callback_data": f"delete_acc_{acc.id}"},
                ])
        rows.append([{"text": "ℹ️ Инструкция по настройке Telegram Business", "callback_data": "menu_business_guide"}])
        rows.append([{"text": "➕ Подключить ещё аккаунт (Создать ссылку)", "callback_data": "menu_invites"}])
        rows.append([{"text": "◀️ В главное меню", "callback_data": "menu_main"}])
        return {"inline_keyboard": rows}

    def build_invites_keyboard(
        self,
        active_plan: Optional[PlanDuration] = None,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        if is_admin:
            return {
                "inline_keyboard": [
                    [
                        {"text": "⚡ Тариф 3 дня", "callback_data": "gen_link_3d"},
                        {"text": "⚡ Тариф 7 дней", "callback_data": "gen_link_7d"},
                    ],
                    [
                        {"text": "💎 Тариф 14 дней", "callback_data": "gen_link_14d"},
                        {"text": "👑 Тариф 31 день", "callback_data": "gen_link_31d"},
                    ],
                    [{"text": "◀️ Назад в меню", "callback_data": "menu_main"}],
                ]
            }

        if not active_plan:
            return {
                "inline_keyboard": [
                    [{"text": "💳 Оформить подписку", "callback_data": "menu_buy_sub"}],
                    [{"text": "◀️ Назад в меню", "callback_data": "menu_main"}],
                ]
            }

        allowed_days = active_plan.days_count

        btn_3d = (
            {"text": "⚡ Тариф 3 дня", "callback_data": "gen_link_3d"}
            if allowed_days >= 3
            else {"text": "🔒 3 дня", "callback_data": "link_locked_3d"}
        )
        btn_7d = (
            {"text": "⚡ Тариф 7 дней", "callback_data": "gen_link_7d"}
            if allowed_days >= 7
            else {"text": "🔒 7 дней (нужен тариф 7д)", "callback_data": "link_locked_7d"}
        )
        btn_14d = (
            {"text": "💎 Тариф 14 дней", "callback_data": "gen_link_14d"}
            if allowed_days >= 14
            else {"text": "🔒 14 дней (нужен тариф 14д)", "callback_data": "link_locked_14d"}
        )
        btn_31d = (
            {"text": "👑 Тариф 31 день", "callback_data": "gen_link_31d"}
            if allowed_days >= 31
            else {"text": "🔒 31 день (нужен тариф 31д)", "callback_data": "link_locked_31d"}
        )

        rows = [
            [btn_3d, btn_7d],
            [btn_14d, btn_31d],
        ]
        if allowed_days < 31:
            rows.append([{"text": "➕ Повысить тариф / Добавить время", "callback_data": "menu_buy_sub"}])
        rows.append([{"text": "◀️ Назад в меню", "callback_data": "menu_main"}])

        return {"inline_keyboard": rows}

    def build_voice_test_keyboard(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "🎧 Быстрый тест голоса", "callback_data": "voice_benchmark_test"},
                    {"text": "✍️ Озвучить свой текст", "callback_data": "voice_custom_text"},
                ],
                [
                    {"text": "🌸 Скромная (Нежная)", "callback_data": "voice_sample_shy"},
                    {"text": "🔥 Игривая (Кокетка)", "callback_data": "voice_sample_playful"},
                ],
                [
                    {"text": "⚡ Дерзкая (Сарказм)", "callback_data": "voice_sample_sarcastic"},
                    {"text": "☕ Заботливая (Уют)", "callback_data": "voice_sample_caring"},
                ],
                [
                    {"text": "🎭 Адаптивная (Универсал)", "callback_data": "voice_sample_adaptive_all"},
                    {"text": "🎲 Случайная реплика", "callback_data": "voice_sample_random"},
                ],
                [
                    {"text": "🗣 Распознавание речи (STT Инфо)", "callback_data": "voice_stt_info"},
                ],
                [{"text": "◀️ Назад в главное меню", "callback_data": "menu_main"}],
            ]
        }

    @staticmethod
    def build_cancel_flow_kb(back_callback: str = "menu_main") -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "❌ Отмена ввода", "callback_data": "cancel_input_flow"},
                    {"text": "◀️ Назад", "callback_data": back_callback},
                ]
            ]
        }

    @staticmethod
    def build_leads_view(leads: List[Dict[str, Any]], filter_mode: str = "all") -> tuple[str, Dict[str, Any]]:
        pending = [l for l in leads if not l.get("is_accepted")]
        accepted = [l for l in leads if l.get("is_accepted")]

        total_cnt = len(leads)
        pending_cnt = len(pending)
        accepted_cnt = len(accepted)

        if not leads:
            text = (
                "📭 <b>Пока нет горячих лидов на встречу.</b>\n\n"
                "Когда собеседник согласится пойти на квест или предложит встретиться — "
                "он мгновенно появится в этом разделе со ссылкой на его профиль в Telegram!"
            )
            kb = {"inline_keyboard": [[{"text": "◀️ В главное меню", "callback_data": "menu_main"}]]}
            return text, kb

        kb_rows = []

        if filter_mode == "pending":
            text = (
                f"🔔 <b>ЛИДЫ: НА КОМ НАПОМИНАНИЕ / ОЖИДАЮТ ({pending_cnt})</b>\n"
                f"<i>Собеседники, согласившиеся на квест или встречу, где ИИ еще ведет диалог или звенит будильник.</i>\n\n"
            )
            if not pending:
                text += "<i>В этой категории сейчас пусто. Все лиды уже приняты владельцем.</i>\n"
            else:
                for idx, l in enumerate(pending[:8], 1):
                    name = l["name"]
                    link = l["tg_link"]
                    username = f" (@{l['username']})" if l.get("username") else ""
                    city = f" • 📍 {l['city']}" if l.get("city") else ""
                    summary = l.get("proposal_summary") or "Согласился на квест 🎯"
                    time_str = l.get("detected_at") or "недавно"

                    if l.get("is_alarm_active"):
                        alarm_badge = "🚨 <b>ЗВОНИТ БУДИЛЬНИК</b>"
                    elif l.get("quest_alarm_muted"):
                        alarm_badge = "🔕 <i>Напоминание выкл.</i>"
                    else:
                        alarm_badge = "⏳ <i>Ожидает решения</i>"

                    text += (
                        f"<b>{idx}. <a href=\"{link}\">{name}</a></b>{username}{city}\n"
                        f"   💬 <i>«{summary}»</i>\n"
                        f"   ⏰ {time_str} | {alarm_badge} | 🤖 ИИ отвечает\n\n"
                    )
                    c_id = l["id"]
                    row = []
                    if l.get("is_alarm_active"):
                        row.append({"text": "🔕 Стоп звонок", "callback_data": f"lead_mute_{c_id}"})
                    row.append({"text": f"✅ Принять ({name[:12]})", "callback_data": f"lead_accept_{c_id}"})
                    kb_rows.append(row)

                if len(pending) > 8:
                    text += f"<i>...и еще {len(pending) - 8} ожидающих лидов.</i>\n"

        elif filter_mode == "accepted":
            text = (
                f"✅ <b>ЛИДЫ: КОГО ЗАБРАЛИ / ПРИНЯТЫЕ ВЛАДЕЛЬЦЕМ ({accepted_cnt})</b>\n"
                f"<i>Лиды, по которым диалог перехвачен вручную. ИИ в этих чатах остановлен.</i>\n\n"
            )
            if not accepted:
                text += "<i>Пока ни один лид не был перехвачен владельцем.</i>\n"
            else:
                for idx, l in enumerate(accepted[:8], 1):
                    name = l["name"]
                    link = l["tg_link"]
                    username = f" (@{l['username']})" if l.get("username") else ""
                    city = f" • 📍 {l['city']}" if l.get("city") else ""
                    summary = l.get("proposal_summary") or "Принят владельцем"
                    time_str = l.get("detected_at") or "ранее"

                    text += (
                        f"<b>{idx}. <a href=\"{link}\">{name}</a></b>{username}{city}\n"
                        f"   💬 <i>«{summary}»</i>\n"
                        f"   ⏰ {time_str} | 👤 <b>Взят владельцем</b> (ИИ выключен)\n\n"
                    )
                    c_id = l["id"]
                    kb_rows.append([{"text": f"🤖 Вернуть ИИ ({name[:14]})", "callback_data": f"lead_resume_{c_id}"}])

                if len(accepted) > 8:
                    text += f"<i>...и еще {len(accepted) - 8} принятых лидов.</i>\n"

        else:  # filter_mode == "all"
            text = (
                f"🔥 <b>ЛИДЫ (ВСТРЕЧИ И КВЕСТЫ)</b>\n"
                f"<i>Список горячих лидов: собеседники, согласившиеся пойти на квест или предложившие встретиться лично.</i>\n\n"
                f"📊 <b>Сводка:</b> Всего: <b>{total_cnt}</b> | 🔔 Напоминание/Ожидают: <b>{pending_cnt}</b> | ✅ Забрали: <b>{accepted_cnt}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
            )

            # Block 1: Pending
            text += f"🔔 <b>НА КОМ НАПОМИНАНИЕ / ОЖИДАЮТ ({pending_cnt}):</b>\n"
            if not pending:
                text += "<i>Нет активных напоминаний — все лиды приняты.</i>\n\n"
            else:
                for idx, l in enumerate(pending[:4], 1):
                    name = l["name"]
                    link = l["tg_link"]
                    username = f" (@{l['username']})" if l.get("username") else ""
                    city = f" • 📍 {l['city']}" if l.get("city") else ""
                    summary = l.get("proposal_summary") or "Согласился на квест 🎯"
                    time_str = l.get("detected_at") or "недавно"

                    if l.get("is_alarm_active"):
                        alarm_badge = "🚨 <b>ЗВОНИТ БУДИЛЬНИК</b>"
                    elif l.get("quest_alarm_muted"):
                        alarm_badge = "🔕 <i>Напоминание выкл.</i>"
                    else:
                        alarm_badge = "⏳ <i>Ожидает решения</i>"

                    text += (
                        f"<b>{idx}. <a href=\"{link}\">{name}</a></b>{username}{city}\n"
                        f"   💬 <i>«{summary}»</i>\n"
                        f"   ⏰ {time_str} | {alarm_badge} | 🤖 ИИ отвечает\n\n"
                    )
                    c_id = l["id"]
                    row = []
                    if l.get("is_alarm_active"):
                        row.append({"text": "🔕 Стоп звонок", "callback_data": f"lead_mute_{c_id}"})
                    row.append({"text": f"✅ Принять ({name[:12]})", "callback_data": f"lead_accept_{c_id}"})
                    kb_rows.append(row)

                if len(pending) > 4:
                    text += f"<i>...и еще {len(pending) - 4} в категории «Ожидают» (см. вкладку ниже).</i>\n\n"

            # Block 2: Accepted
            text += f"✅ <b>КОГО ЗАБРАЛИ / ПРИНЯТЫЕ ВЛАДЕЛЬЦЕМ ({accepted_cnt}):</b>\n"
            if not accepted:
                text += "<i>Пока ни один лид не был перехвачен владельцем.</i>\n\n"
            else:
                for idx, l in enumerate(accepted[:3], 1):
                    name = l["name"]
                    link = l["tg_link"]
                    username = f" (@{l['username']})" if l.get("username") else ""
                    city = f" • 📍 {l['city']}" if l.get("city") else ""
                    summary = l.get("proposal_summary") or "Принят владельцем"
                    time_str = l.get("detected_at") or "ранее"

                    text += (
                        f"<b>{idx}. <a href=\"{link}\">{name}</a></b>{username}{city}\n"
                        f"   💬 <i>«{summary}»</i>\n"
                        f"   ⏰ {time_str} | 👤 <b>Взят владельцем</b> (ИИ на паузе)\n\n"
                    )
                    c_id = l["id"]
                    kb_rows.append([{"text": f"🤖 Включить ИИ ({name[:14]})", "callback_data": f"lead_resume_{c_id}"}])

                if len(accepted) > 3:
                    text += f"<i>...и еще {len(accepted) - 3} в категории «Забрали» (см. вкладку ниже).</i>\n\n"

        # Tab navigation row
        tab_row = []
        if filter_mode != "pending":
            tab_row.append({"text": f"🔔 Ожидают ({pending_cnt})", "callback_data": "menu_leads_pending"})
        if filter_mode != "accepted":
            tab_row.append({"text": f"✅ Забрали ({accepted_cnt})", "callback_data": "menu_leads_accepted"})
        if filter_mode != "all":
            tab_row.append({"text": f"📋 Все лиды ({total_cnt})", "callback_data": "menu_leads"})
        if tab_row:
            kb_rows.append(tab_row)

        # Control row
        cur_cb = f"menu_leads_{filter_mode}" if filter_mode != "all" else "menu_leads"
        kb_rows.append([
            {"text": "🔄 Обновить", "callback_data": cur_cb},
            {"text": "◀️ В главное меню", "callback_data": "menu_main"},
        ])

        return text, {"inline_keyboard": kb_rows}

    async def build_chats_crm_payload(
        self,
        session: AsyncSession,
        db_user_id: int,
        is_admin: bool,
        filter_key: str = "all",
        page: int = 1,
        search_query: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Builds the Telegram-native Connected Chat Management (CRM) dashboard
        with status indicators (🟢 Active, 🟡 Waiting, 🔴 Needs attention, ⚪ Paused),
        filtering, search, and pagination.
        """
        contacts = await StatsService.get_chat_leads(session, db_user_id)
        if not contacts and is_admin:
            stmt_all = select(Contact).order_by(Contact.last_incoming_at.desc(), Contact.id.desc())
            res_all = await session.execute(stmt_all)
            contacts = list(res_all.scalars().all())

        now_utc = datetime.now(timezone.utc)
        counts = {"all": len(contacts), "active": 0, "waiting": 0, "attention": 0, "paused": 0}
        classified: List[tuple[Contact, str, str, str]] = []
        for c in contacts:
            bucket, icon, lbl = ConversationManagerEngine.classify_contact_status(c, now_utc=now_utc)
            counts[bucket] = counts.get(bucket, 0) + 1
            classified.append((c, bucket, icon, lbl))

        # Apply search query if set
        filtered = classified
        if search_query:
            q_low = search_query.lower().strip()
            filtered = [
                (c, b, ic, lb)
                for (c, b, ic, lb) in filtered
                if q_low in (c.first_name or "").lower()
                or q_low in (c.username or "").lower()
                or q_low in str(c.tg_id or "")
                or q_low in (c.dialogue_topics or "").lower()
                or q_low in (c.dialogue_summary or "").lower()
                or q_low in (c.tags or "").lower()
            ]

        # Apply filter tab
        if filter_key in ("active", "waiting", "attention", "paused"):
            filtered = [(c, b, ic, lb) for (c, b, ic, lb) in filtered if b == filter_key]
        else:
            filter_key = "all"

        page_size = 8
        total_items = len(filtered)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * page_size
        page_items = filtered[start_idx : start_idx + page_size]

        filter_titles = {
            "all": "Все диалоги",
            "active": "🟢 Активные",
            "waiting": "🟡 Ожидают ответа",
            "attention": "🔴 Требуют внимания",
            "paused": "⚪ На паузе / Ручной режим",
        }

        header = (
            f"💬 <b>ЛИДЫ (ОБЩЕНИЕ И ДИАЛОГИ) | УПРАВЛЕНИЕ ЧАТАМИ (CRM) — {filter_titles.get(filter_key, 'Все')}</b>\n\n"
            f"📊 <b>Статусы сейчас:</b> "
            f"🟢 Активных: <b>{counts['active']}</b> | "
            f"🟡 Ожидают: <b>{counts['waiting']}</b> | "
            f"🔴 Внимание: <b>{counts['attention']}</b> | "
            f"⚪ Пауза: <b>{counts['paused']}</b>\n"
        )
        if search_query:
            header += f"🔍 <b>Поиск:</b> «<code>{html.escape(search_query)}</code>» (найдено: {total_items})\n"

        if not page_items:
            text = (
                header
                + "\n📭 <b>В этой категории сейчас нет диалогов.</b>\n"
                "Выберите другой фильтр ниже или вернитесь ко всем чатам."
            )
        else:
            text = (
                header
                + "\n<i>Нажмите на чат для просмотра переписки, памяти ИИ, отправки сообщения или смены режима:</i>"
            )

        def _tab(key: str, label: str) -> Dict[str, str]:
            prefix = "• " if filter_key == key else ""
            suffix = " •" if filter_key == key else ""
            return {"text": f"{prefix}{label} ({counts.get(key, 0)}){suffix}", "callback_data": f"chats_filter_{key}"}

        kb_rows: List[List[Dict[str, Any]]] = [
            [_tab("all", "Все"), _tab("attention", "🔴 Внимание")],
            [_tab("active", "🟢 Актив"), _tab("waiting", "🟡 Ждут"), _tab("paused", "⚪ Пауза")],
        ]

        search_row = [{"text": "🔍 Поиск по чатам", "callback_data": "chats_search"}]
        if search_query:
            search_row.append({"text": "✖️ Сбросить поиск", "callback_data": "chats_clear_search"})
        kb_rows.append(search_row)

        for c, bucket, icon, state_lbl in page_items:
            c_name = html.escape(c.first_name or f"User_{c.tg_id}")
            user_str = f" (@{c.username})" if c.username else ""
            btn_txt = f"{icon} {c_name}{user_str} — {state_lbl}"
            kb_rows.append([{"text": btn_txt, "callback_data": f"chat_card_{c.id}"}])

        if total_pages > 1:
            nav_row = []
            if page > 1:
                nav_row.append({"text": "◀️ Пред.", "callback_data": f"chats_page_{filter_key}_{page - 1}"})
            nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": f"chats_filter_{filter_key}"})
            if page < total_pages:
                nav_row.append({"text": "След. ▶️", "callback_data": f"chats_page_{filter_key}_{page + 1}"})
            kb_rows.append(nav_row)

        kb_rows.append([{"text": "◀️ В главное меню", "callback_data": "menu_main"}])
        return text, {"inline_keyboard": kb_rows}

    def build_contact_card_payload(self, contact: Contact) -> tuple[str, Dict[str, Any]]:
        c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
        user_str = f"@{contact.username}" if contact.username else "нет"
        first_dt = contact.created_at.strftime("%d.%m.%Y %H:%M") if contact.created_at else "-"
        last_dt = contact.last_incoming_at.strftime("%d.%m.%Y %H:%M") if contact.last_incoming_at else "-"

        bucket, icon, state_lbl = ConversationManagerEngine.classify_contact_status(contact)
        if contact.status == ContactStatus.BLOCKED or not contact.ai_active:
            mode_badge = "⛔️ Заблокирован / ИИ выключен"
        elif contact.bot_paused:
            mode_badge = "👤 Ручной режим (ИИ на паузе)"
        else:
            mode_badge = "🤖 Авто-режим (ИИ общается)"

        diag_summary = html.escape(contact.dialogue_summary or "Обычная переписка")
        diag_topics = html.escape(contact.dialogue_topics or "знакомство")

        card_text = (
            f"💬 <b>КАРТОЧКА КЛИЕНТА (ОБЩЕНИЕ) | КАРТОЧКА КЛИЕНТА (CRM)</b>\n\n"
            f"👤 <b>Имя:</b> {c_name}\n"
            f"🔗 <b>Username:</b> {user_str}\n"
            f"🆔 <b>Telegram ID:</b> <code>{contact.tg_id}</code>\n"
            f"📅 <b>Первый контакт:</b> {first_dt}\n"
            f"⏰ <b>Последнее сообщение:</b> {last_dt}\n"
            f"🚦 <b>Операционный статус:</b> {icon} <b>{state_lbl}</b>\n"
            f"🎛 <b>Режим управления:</b> {mode_badge}\n"
            f"🏷 <b>Теги:</b> {contact.tags or '🟢 Активный'}\n\n"
            f"📈 <b>Активность:</b>\n"
            f"• Входящих от клиента: <b>{contact.total_messages_in}</b>\n"
            f"• Ответов нейросети: <b>{contact.total_messages_out}</b>\n"
            f"• Оскорблений/нарушений: <b>{contact.violation_count or 0}</b>\n\n"
            f"💡 <b>Темы разговора:</b> {diag_topics}\n"
            f"📝 <b>Краткое содержание диалога:</b>\n"
            f"{diag_summary}\n\n"
            f"<i>Выберите действие:</i>"
        )

        is_blocked = contact.status == ContactStatus.BLOCKED or not contact.ai_active
        kb = [
            [
                {"text": "💬 Вся переписка", "callback_data": f"chat_history_{contact.id}"},
                {"text": "🧠 Память ИИ (7 уровней)", "callback_data": f"chat_context_{contact.id}"},
            ],
            [
                {"text": "✉️ Написать от себя", "callback_data": f"chat_manual_reply_{contact.id}"},
                {"text": "🎙 Послушать войсы", "callback_data": f"chat_voice_list_{contact.id}"},
            ],
            [
                {"text": "🏷 Управление тегами", "callback_data": f"chat_tags_{contact.id}"},
            ],
            [
                {
                    "text": "🔓 Разблокировать / ИИ" if is_blocked else "⛔️ Заблокировать",
                    "callback_data": f"chat_toggle_block_{contact.id}",
                },
                {
                    "text": "🤖 Включить Авто-ИИ" if contact.bot_paused else "👤 Перехватить (Пауза ИИ)",
                    "callback_data": f"chat_toggle_pause_{contact.id}",
                },
            ],
            [
                {"text": "🗑 Удалить диалог", "callback_data": f"lead_action_delete_{contact.id}"},
                {"text": "↩️ Назад к списку", "callback_data": "menu_leads_chats"},
            ],
        ]
        return card_text, {"inline_keyboard": kb}

    async def build_contact_context_payload(
        self, session: AsyncSession, contact: Contact
    ) -> tuple[str, Dict[str, Any]]:
        """
        Renders the 7-Level AI Context & Long-Term Memory inspector inside Telegram,
        matching the Mini App's /api/v1/contacts/{id}/context view.
        """
        conv_sum = await MemoryManager.get_or_create_summary(session, contact.id)
        memories = await MemoryManager.get_memories(session, contact.id)

        c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
        sum_text = html.escape(conv_sum.summary_text or contact.dialogue_summary or "Пока накапливается...")
        topics_text = html.escape(conv_sum.active_topics or contact.dialogue_topics or "знакомство")
        unresolved_text = html.escape(conv_sum.unresolved_questions or "нет открытых вопросов")
        commitments_text = html.escape(conv_sum.commitments or "пока нет конкретных договоренностей")

        mem_lines = []
        for m in memories[:12]:
            cat = getattr(m, "category", None) or getattr(m, "fact_type", "fact")
            k = getattr(m, "key", None) or getattr(m, "fact_key", "info")
            v = getattr(m, "value", None) or getattr(m, "fact_value", "")
            mem_lines.append(
                f"• <b>{html.escape(str(cat))}/{html.escape(str(k))}:</b> {html.escape(str(v))}"
            )
        facts_block = "\n".join(mem_lines) if mem_lines else "<i>Долгосрочные факты ещё извлекаются по ходу беседы.</i>"
        covered_cnt = getattr(conv_sum, "messages_covered_count", 0) or getattr(conv_sum, "messages_summarized_count", 0)

        text = (
            f"🧠 <b>7-УРОВНЕВАЯ ПАМЯТЬ ИИ: {c_name}</b>\n\n"
            f"📌 <b>1. Сводка диалога (Rolling Summary):</b>\n{sum_text}\n\n"
            f"💬 <b>2. Активные темы:</b>\n{topics_text}\n\n"
            f"❓ <b>3. Незакрытые вопросы собеседника:</b>\n{unresolved_text}\n\n"
            f"🤝 <b>4. Обещания и договоренности:</b>\n{commitments_text}\n\n"
            f"🗂 <b>5. Долгосрочные факты о клиенте ({len(memories)}):</b>\n{facts_block}\n\n"
            f"📈 <b>Обработано сообщений в сводке:</b> {covered_cnt}"
        )

        kb = [
            [
                {"text": "🔄 Обновить сводку", "callback_data": f"chat_context_{contact.id}"},
                {"text": "🗑 Очистить факты", "callback_data": f"chat_context_clear_{contact.id}"},
            ],
            [
                {"text": "💬 История сообщений", "callback_data": f"chat_history_{contact.id}"},
                {"text": "↩️ Назад к карточке", "callback_data": f"chat_card_{contact.id}"},
            ],
        ]
        return text, {"inline_keyboard": kb}

    # ==========================================
    # Updates Dispatcher
    # ==========================================
    async def handle_update(self, update: Dict[str, Any]):
        try:
            # 1. Telegram Business Chat Automation updates
            if "business_message" in update:
                msg_data = update["business_message"]
                async with async_session_maker() as session:
                    await BusinessChatBot.process_business_message(
                        session=session,
                        message_data=msg_data,
                        bot_token=self.token,
                    )
                return

            if "edited_business_message" in update:
                edit_data = update["edited_business_message"]
                async with async_session_maker() as session:
                    await BusinessChatBot.process_edited_business_message(
                        session=session,
                        message_data=edit_data,
                    )
                return

            if "deleted_business_messages" in update:
                del_data = update["deleted_business_messages"]
                async with async_session_maker() as session:
                    await BusinessChatBot.process_deleted_business_messages(
                        session=session,
                        delete_data=del_data,
                    )
                return

            if "business_connection" in update:
                async with async_session_maker() as session:
                    await BusinessChatBot.process_business_connection(
                        session=session,
                        connection_data=update["business_connection"],
                        bot_token=self.token,
                    )
                return

            # 2. Callback query from Inline buttons
            if "callback_query" in update:
                await self.handle_callback(update["callback_query"])
                return

            # 3. Direct Message to the Bot
            if "message" in update:
                await self.handle_message(update["message"])
        except Exception as e:
            logger.exception("Error processing update %s: %s", update.get("update_id"), e)

    async def _handle_admin_photo_upload(
        self, message: Dict[str, Any], user_id: int, chat_id: int, caption: str
    ):
        """Processes photo upload into persistent photo pool by bot admin."""
        photos = message.get("photo", [])
        if not photos:
            await self.send_message(chat_id, "⚠️ Фотография не обнаружена в сообщении.")
            return

        best_photo = photos[-1]
        file_id = best_photo.get("file_id")
        file_bytes = await self.download_file_bytes(file_id)
        if not file_bytes:
            await self.send_message(chat_id, "❌ Не удалось скачать файл фотографии из Telegram.")
            return

        clean_cap = caption
        for prefix in ["#photo", "/addphoto", "/photo"]:
            if clean_cap.startswith(prefix):
                clean_cap = clean_cap[len(prefix):].strip()

        parts = clean_cap.split(maxsplit=2)
        category = "casual"
        tags = []
        description = ""

        from companion.services.photo_pool_service import PhotoPoolService
        if len(parts) >= 1 and parts[0]:
            cand_cat = parts[0].lstrip("#").lower()
            if cand_cat in PhotoPoolService.CATEGORIES or len(parts) > 1:
                category = cand_cat
                if len(parts) >= 2:
                    raw_tags = parts[1].split(",")
                    tags = [t.strip().lstrip("#").lower() for t in raw_tags if t.strip()]
                if len(parts) >= 3:
                    description = parts[2].strip()
            else:
                description = clean_cap
        elif clean_cap:
            description = clean_cap

        upload_state = self._user_input_states.get(user_id, "")
        target_acc_code: Optional[str] = None
        target_persona_id: Optional[int] = None
        model_title: Optional[str] = None

        async with async_session_maker() as session:
            db_user = await self.get_or_create_user(session, message.get("from", {}))
            accounts = await AccountService.get_user_accounts(session, db_user.id)

            if upload_state.startswith("photo_upload_acc:"):
                acc_code = upload_state.split(":", 1)[1]
                target_acc_code = acc_code
                if acc_code in ("default", "0"):
                    stmt = select(Persona).where(Persona.user_id == db_user.id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    p = res.scalars().first()
                    if not p:
                        p = Persona(user_id=db_user.id)
                        session.add(p)
                        await session.commit()
                        await session.refresh(p)
                    target_persona_id = p.id
                    model_title = p.name or "Общий шаблон"
                elif acc_code.isdigit():
                    acc_id = int(acc_code)
                    acc = next((a for a in accounts if a.id == acc_id), None)
                    if acc:
                        p = await AccountService.ensure_account_persona(session, acc)
                        target_persona_id = p.id
                        model_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")
            elif len(accounts) == 1:
                acc = accounts[0]
                p = await AccountService.ensure_account_persona(session, acc)
                target_persona_id = p.id
                target_acc_code = str(acc.id)
                model_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")

            try:
                photo, is_new = await PhotoPoolService.add_photo(
                    session=session,
                    file_bytes=file_bytes,
                    filename=f"{best_photo.get('file_unique_id', 'photo')}.jpg",
                    category=category,
                    tags=tags,
                    description=description,
                    user_id=db_user.id,
                    persona_id=target_persona_id,
                    telegram_file_id=file_id,
                )
            except Exception as e:
                await self.send_message(chat_id, f"❌ Ошибка добавления фото: {e}")
                return

            if target_persona_id is not None and target_acc_code is not None:
                stats = await PhotoPoolService.get_persona_photo_stats(session, target_persona_id)
                total_photos = stats.get("total_photos", 0)
                if is_new:
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Загрузить ещё фото", "callback_data": f"photo_up_acc_{target_acc_code}"}],
                            [{"text": f"📋 Фотографии модели ({total_photos} шт.)", "callback_data": f"photo_list_acc_{target_acc_code}_0"}],
                            [{"text": "◀️ К анкете модели", "callback_data": f"legend_acc_{target_acc_code}"}],
                        ]
                    }
                    msg = (
                        f"✅ <b>Фото успешно добавлено для модели {model_title}!</b>\n\n"
                        f"🆔 <b>ID фото:</b> <code>{photo.photo_id}</code>\n"
                        f"📁 <b>Категория:</b> <code>{photo.category}</code>\n"
                        f"🏷 <b>Теги:</b> {', '.join(photo.get_tags()) or '—'}\n"
                        f"📝 <b>Описание:</b> {photo.description or '—'}\n"
                        f"📊 <b>Всего фото у модели:</b> {total_photos} шт.\n"
                        f"🔒 <b>SHA-256:</b> <code>{photo.file_hash[:16]}...</code>\n\n"
                        f"<i>Вы можете отправить следующее фото прямо сейчас (или сразу пачкой) — бот свяжет его с этой же анкетой.</i>"
                    )
                    await self.send_message(chat_id, msg, reply_markup=kb, parse_mode="HTML")
                else:
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Загрузить другое фото", "callback_data": f"photo_up_acc_{target_acc_code}"}],
                            [{"text": f"📋 Фотографии модели ({total_photos} шт.)", "callback_data": f"photo_list_acc_{target_acc_code}_0"}],
                            [{"text": "◀️ К анкете модели", "callback_data": f"legend_acc_{target_acc_code}"}],
                        ]
                    }
                    msg = (
                        f"⚠️ <b>Это фото уже есть у модели {model_title}!</b>\n\n"
                        f"🆔 <b>Существующий ID:</b> <code>{photo.photo_id}</code>\n"
                        f"📁 <b>Категория:</b> <code>{photo.category}</code>\n"
                        f"🕒 <b>Загружено ранее:</b> {photo.created_at.strftime('%Y-%m-%d %H:%M') if photo.created_at else '—'}\n\n"
                        f"<i>Дубликаты исключены, чтобы не отправлять собеседникам одинаковые фото.</i>"
                    )
                    await self.send_message(chat_id, msg, reply_markup=kb, parse_mode="HTML")
            else:
                if is_new:
                    kb = {
                        "inline_keyboard": [
                            [{"text": "📋 Все фото пула", "callback_data": "photos_list_0"}],
                            [{"text": "◀️ В меню", "callback_data": "menu_main"}],
                        ]
                    }
                    msg = (
                        "✅ <b>Фото успешно добавлено в общий пул!</b>\n\n"
                        f"🆔 <b>ID:</b> <code>{photo.photo_id}</code>\n"
                        f"📁 <b>Категория:</b> <code>{photo.category}</code>\n"
                        f"🏷 <b>Теги:</b> {', '.join(photo.get_tags()) or '—'}\n"
                        f"📝 <b>Описание:</b> {photo.description or '—'}\n"
                        f"💾 <b>Размер:</b> {photo.file_size // 1024} КБ\n"
                        f"📊 <b>dHash:</b> <code>{photo.perceptual_hash or '—'}</code>\n"
                        f"🔒 <b>SHA-256:</b> <code>{photo.file_hash[:16]}...</code>\n\n"
                        "<i>Фото готово к контекстной отправке и защищено правилом No-Repeat.</i>"
                    )
                    await self.send_message(chat_id, msg, reply_markup=kb, parse_mode="HTML")
                else:
                    msg = (
                        "⚠️ <b>Дубликат обнаружен!</b>\n\n"
                        f"Это фото уже есть в пуле (совпадение по хэшу).\n"
                        f"🆔 <b>Существующий ID:</b> <code>{photo.photo_id}</code>\n"
                        f"📁 <b>Категория:</b> <code>{photo.category}</code>\n"
                        f"🕒 <b>Загружено:</b> {photo.created_at.strftime('%Y-%m-%d %H:%M') if photo.created_at else '—'}"
                    )
                    await self.send_message(chat_id, msg, parse_mode="HTML")

    async def handle_message(self, message: Dict[str, Any]):
        user_tg = message.get("from", {})
        user_id = user_tg.get("id")
        text = message.get("text", "").strip()
        chat_id = message.get("chat", {}).get("id")
        is_admin = self.is_admin_user(user_id)

        # 0. Intercept incoming voice note, audio or video note message (Speech-to-Text)
        if "voice" in message or "audio" in message or "video_note" in message:
            voice_obj = message.get("voice") or message.get("audio") or message.get("video_note")
            file_id = voice_obj.get("file_id")
            voice_duration = voice_obj.get("duration", 0)
            is_video_note = "video_note" in message
            default_mime = "video/mp4" if is_video_note else "audio/ogg"
            mime_type = voice_obj.get("mime_type") or default_mime
            media_title = "видео-сообщение (кружок)" if is_video_note else "голосовое сообщение"

            # Send immediate feedback that audio was received
            await self.send_message(
                chat_id,
                f"🎧 <i>Слушаю {media_title} ({voice_duration} сек)...</i>",
                parse_mode="HTML",
            )
            transcript, raw_audio = await STTService.transcribe_telegram_voice(
                self.token, file_id, mime_type
            )
            if not transcript and not raw_audio:
                raw_audio = await self.download_file_bytes(file_id)
                if raw_audio:
                    transcript = await STTService.transcribe_audio_bytes(
                        audio_bytes=raw_audio,
                        mime_type=mime_type,
                    )

            # Check if user is currently entering data (e.g. legend wizard by voice!)
            if user_id in self._user_input_states:
                if transcript:
                    await self.send_message(
                        chat_id,
                        f"🗣 <b>Распознано:</b>\n<i>«{transcript}»</i>\n\n<i>Применяю к анкете...</i>",
                        parse_mode="HTML",
                    )
                    text = transcript  # Hand over to state machine below!
                else:
                    await self.send_message(
                        chat_id,
                        "⚠️ <i>Не удалось четко распознать слова в голосовом сообщении. Напишите, пожалуйста, текстом или запишите громче.</i>",
                        parse_mode="HTML",
                    )
                    return
            else:
                if transcript:
                    reply_kb = None
                    admin_hint = ""
                    if is_admin:
                        short_id = f"v{int(datetime.now().timestamp()) % 100000}{random.randint(10, 99)}"
                        self._cached_voice_files[short_id] = file_id
                        reply_kb = {
                            "inline_keyboard": [
                                [
                                    {"text": "💬 В приветствия", "callback_data": f"sv_greet_{short_id}"},
                                    {"text": "☕ На свидание", "callback_data": f"sv_meet_{short_id}"},
                                ],
                                [
                                    {"text": "⚡ В сарказм", "callback_data": f"sv_sarc_{short_id}"},
                                    {"text": "🔥 Во флирт", "callback_data": f"sv_flirt_{short_id}"},
                                ],
                                [
                                    {"text": "🌸 В скромную", "callback_data": f"sv_shy_{short_id}"},
                                    {"text": "☕ В заботу", "callback_data": f"sv_care_{short_id}"},
                                ],
                                [{"text": "◀️ Назад к войсам", "callback_data": "menu_voice_test"}],
                            ]
                        }
                        admin_hint = "\n\n💡 <i>Вы можете привязать эту запись к Voice Pack кнопками ниже:</i>"

                    await self.send_message(
                        chat_id,
                        f"🗣 <b>Распознано {media_title} ({voice_duration} сек):</b>\n\n"
                        f"<i>«{transcript}»</i>\n\n"
                        f"✅ <b>Система Speech-to-Text работает!</b> Входящие войсы от мужчин в подключенных Telegram-аккаунтах распознаются и понимаются точно так же.{admin_hint}",
                        reply_markup=reply_kb,
                        parse_mode="HTML",
                    )
                    return
                else:
                    await self.send_message(
                        chat_id,
                        f"⚠️ <i>Не удалось четко разобрать слова в {media_title}. Попробуйте записать громче или ближе к микрофону.</i>",
                        parse_mode="HTML",
                    )
                    return

        # Admin Photo Pool Upload Interceptor (Section 2 & 23: Owner-only photo upload)
        caption = (message.get("caption") or "").strip()
        if "photo" in message:
            from companion.services.daivinchik_service import DaivinchikService
            is_dv = DaivinchikService.is_daivinchik_message(message)
            if is_admin and not is_dv:
                await self._handle_admin_photo_upload(message, user_id, chat_id, caption)
                return
            elif not is_admin and (caption.startswith("#photo") or caption.startswith("/addphoto") or caption.startswith("/photo")):
                # Normal users must never execute photo-management uploads or see internal details
                return

        # Multimodal Vision: process stickers, GIFs (animations), photos and reply contexts
        if not text or "photo" in message or "sticker" in message or "animation" in message or "reply_to_message" in message:
            from companion.services.vision_service import VisionService
            media_ctx = await VisionService.extract_media_context(
                bot_token=self.token,
                message_data=message,
            )
            augmented = media_ctx.get("text_augmented")
            if augmented:
                text = augmented

        if not text:
            return

        # Check if incoming message is a forwarded or pasted Daivinchik dating profile (Sections 16-20)
        from companion.services.daivinchik_service import DaivinchikService
        if DaivinchikService.is_daivinchik_message(message):
            raw_prof_text = (message.get("text") or message.get("caption") or "").strip()
            async with async_session_maker() as session:
                db_user = await self.get_or_create_user(session, user_tg)
                profile, is_new = await DaivinchikService.process_daivinchik_forward(
                    session=session,
                    user_id=db_user.id,
                    raw_text=raw_prof_text,
                    style_variant="natural"
                )
                card_text = DaivinchikService.format_profile_card(profile)
                kb = DaivinchikService.build_bio_actions_keyboard(profile.id)
                await self.send_message(chat_id, card_text, reply_markup=kb, parse_mode="HTML")
                return

        async with async_session_maker() as session:
            db_user = await self.get_or_create_user(session, user_tg)
            mgr_state = self.manager.get_state(user_id)
            self.manager.detect_user_style(user_id, text)
            mgr_state.record_turn("user", text)

            # Check user input state machine for editing legend / custom voice / manual reply / search
            if user_id in self._user_input_states:
                raw_peek = self._user_input_states[user_id]
                peek_state = raw_peek.split(":", 1)[0] if ":" in raw_peek else raw_peek
                peek_target = int(raw_peek.split(":", 1)[1]) if (":" in raw_peek and raw_peek.split(":", 1)[1].isdigit()) else None

                # 1) Did the user ask to wait ("подожди", "wait", "секунду")? Keep flow intact!
                if ConversationManagerEngine.is_wait_intent(text):
                    wait_msg = self.manager.pick_variation(user_id, ConversationManagerEngine.WAIT_VARIATIONS)
                    await self.send_message(
                        chat_id,
                        wait_msg,
                        reply_markup=self.build_cancel_flow_kb(mgr_state.current_menu or "menu_main"),
                        parse_mode="HTML",
                    )
                    return

                # 2) Did the user cancel ("отмена", "нет", "стой", "не надо", "nah", "cancel")?
                if ConversationManagerEngine.is_cancel_intent(text):
                    self._user_input_states.pop(user_id, None)
                    mgr_state.clear_flow()
                    cancel_msg = self.manager.pick_variation(user_id, ConversationManagerEngine.CANCEL_VARIATIONS)
                    back_cb = f"chat_card_{peek_target}" if peek_state == "WAIT_CONTACT_MANUAL_REPLY" and peek_target else (mgr_state.current_menu or "menu_main")
                    await self.send_message(
                        chat_id,
                        cancel_msg,
                        reply_markup={"inline_keyboard": [[{"text": "◀️ Вернуться назад", "callback_data": back_cb}], [{"text": "🏠 Главное меню", "callback_data": "menu_main"}]]},
                        parse_mode="HTML",
                    )
                    return

                # 3) Did the user interrupt the input flow with a slash command or a side-question?
                interruption_type = ConversationManagerEngine.is_navigation_or_question_interruption(text)
                if text.startswith("/") or interruption_type is not None:
                    self._user_input_states.pop(user_id, None)
                    mgr_state.active_flow = raw_peek
                    mgr_state.flow_target_id = peek_target
                    mgr_state.pause_flow_for_interruption()
                    # Fall through to handle the slash command or side-question below,
                    # and we'll offer a 1-tap button to resume the interrupted flow!
                else:
                    state_raw = self._user_input_states.pop(user_id)
                    mgr_state.clear_flow()
                    target_acc_id = None
                    acc_part = ""
                    if ":" in state_raw:
                        state, acc_part = state_raw.split(":", 1)
                        if acc_part.isdigit():
                            target_acc_id = int(acc_part)
                    else:
                        state = state_raw

                    if state == "photo_upload_acc":
                        self._user_input_states[user_id] = state_raw
                        acc_target = acc_part if acc_part else "default"
                        await self.send_message(
                            chat_id,
                            "📸 <b>Ожидается фотография девушки!</b>\n\n"
                            "Пожалуйста, отправьте фото (изображение) в чат.\n"
                            "Вы можете добавить к фото подпись или описание при отправке.\n\n"
                            "Для возврата к меню фото нажмите кнопку ниже:",
                            reply_markup={"inline_keyboard": [
                                [{"text": "◀️ Отмена / Назад", "callback_data": f"photos_acc_{acc_target}"}]
                            ]},
                            parse_mode="HTML",
                        )
                        return

                    # Handle non-persona input states first
                    if state == "WAIT_CUSTOM_VOICE_TEXT":
                        prepared_phonetic = VoiceService.prepare_text_for_tts(text)
                        await self.send_message(
                            chat_id,
                            f"🎙 <b>Синтезирую голосовое по вашему тексту...</b>\n"
                            f"📝 <b>Исходный текст:</b> «{html.escape(text)}»\n"
                            f"🗣 <b>Фонетическая разметка:</b> <i>«{html.escape(prepared_phonetic)}»</i>",
                            parse_mode="HTML",
                        )
                        sent = await VoiceService.send_telegram_voice_note(
                            bot_token=self.token,
                            chat_id=chat_id,
                            text=text,
                            caption=f"🎙 <b>Озвучка вашего текста:</b>\n<i>«{html.escape(text[:180])}»</i>",
                        )
                        kb_after = self.build_voice_test_keyboard()
                        if not sent:
                            await self.send_message(
                                chat_id,
                                "⚠️ Не удалось отправить аудиофайл в Telegram (проверьте настройки голосового движка).",
                                reply_markup=kb_after,
                                parse_mode="HTML",
                            )
                        return

                    if state == "WAIT_CONTACT_SEARCH":
                        mgr_state.chat_search_query = text.strip()
                        mgr_state.chat_page = 1
                        crm_text, crm_kb = await self.build_chats_crm_payload(
                            session=session,
                            db_user_id=db_user.id,
                            is_admin=is_admin,
                            filter_key=mgr_state.chat_filter,
                            page=1,
                            search_query=mgr_state.chat_search_query,
                        )
                        await self.send_message(chat_id, crm_text, reply_markup=crm_kb, parse_mode="HTML")
                        return

                    if state == "WAIT_CONTACT_MANUAL_REPLY":
                        contact_id = target_acc_id
                        stmt_c = select(Contact).where(Contact.id == contact_id)
                        if not is_admin:
                            stmt_c = stmt_c.where(Contact.user_id == db_user.id)
                        contact = (await session.execute(stmt_c)).scalars().first()
                        if not contact:
                            await self.send_message(
                                chat_id,
                                "🔄 Этот контакт больше не существует в базе. Обновляю список чатов.",
                                reply_markup={"inline_keyboard": [[{"text": "💬 Открыть чаты", "callback_data": "menu_leads_chats"}]]},
                                parse_mode="HTML",
                            )
                            return

                        await HandoverService.handle_manual_owner_message(
                            session=session,
                            contact=contact,
                            text=text,
                        )
                        delivered = False
                        stmt_a = select(AccountSession).where(
                            AccountSession.user_id == contact.user_id,
                            AccountSession.is_active == True,
                        )
                        acc = (await session.execute(stmt_a)).scalars().first()
                        if acc and acc.business_connection_id and contact.tg_id:
                            delivered = await BusinessChatBot.send_business_message(
                                bot_token=self.token,
                                business_connection_id=acc.business_connection_id,
                                chat_id=contact.tg_id,
                                text=text,
                            )
                        elif contact.tg_id:
                            delivered = await self.send_message(chat_id=contact.tg_id, text=text)

                        mgr_state.last_entity_type = "contact"
                        mgr_state.last_entity_id = contact.id
                        c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                        delivery_badge = (
                            "✅ <b>Сообщение доставлено клиенту в Telegram!</b>"
                            if delivered
                            else "💾 <b>Сообщение сохранено в историю диалога</b> (ожидает активного подключения Telegram Business)."
                        )
                        await self.send_message(
                            chat_id,
                            f"{delivery_badge}\n\n"
                            f"👤 <b>Кому:</b> {c_name}\n"
                            f"💬 <b>Текст:</b> «{html.escape(text)}»\n"
                            f"⏸ <b>Режим:</b> Автоответ ИИ поставлен на паузу на 15 минут (ручной перехват).",
                            reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "✉️ Написать ещё", "callback_data": f"chat_manual_reply_{contact.id}"},
                                        {"text": "🤖 Включить ИИ сразу", "callback_data": f"unpause_contact_{contact.id}"},
                                    ],
                                    [{"text": "💬 Карточка клиента", "callback_data": f"chat_card_{contact.id}"}],
                                ]
                            },
                            parse_mode="HTML",
                        )
                        return

                    target_account = None
                    persona = None
                    if target_acc_id:
                        stmt_acc = select(AccountSession).where(
                            AccountSession.id == target_acc_id, AccountSession.user_id == db_user.id
                        )
                        res_acc = await session.execute(stmt_acc)
                        target_account = res_acc.scalars().first()
                        if target_account:
                            persona = await AccountService.ensure_account_persona(session, target_account)

                    if not persona:
                        stmt_p = select(Persona).where(Persona.user_id == db_user.id).order_by(Persona.id.asc())
                        res_p = await session.execute(stmt_p)
                        persona = res_p.scalars().first()
                        if not persona:
                            persona = Persona(user_id=db_user.id)
                            session.add(persona)
                            await session.commit()
                            await session.refresh(persona)

                    mgr_state.last_entity_type = "persona"
                    mgr_state.last_entity_id = persona.id

                    user_accounts = await AccountService.get_user_accounts(session, db_user.id)
                    has_mult = len(user_accounts) > 1
                    acc_id_for_kb = target_account.id if target_account else None

                    if state == "WAIT_NAME_AGE":
                        age_match = re.search(r"\b(1[7-9]|[2-3][0-9]|4[0-5])\b", text)
                        if age_match:
                            persona.age = int(age_match.group(1))
                            clean_name = re.sub(r"\b(1[7-9]|[2-3][0-9]|4[0-5])\b", "", text).replace(",", "").strip()
                            if clean_name:
                                persona.name = clean_name.capitalize()
                        else:
                            persona.name = text.strip().capitalize()
                        mgr_state.last_edited_field = "name_age"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            f"✅ <b>Имя и возраст сохранены:</b> {persona.name}, {persona.age} лет!\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_CITY_DISTRICT":
                        from companion.services.timezone_service import TimezoneService

                        parts = [p.strip() for p in text.split(",", 1)]
                        persona.city = parts[0]
                        if len(parts) > 1:
                            persona.district = parts[1]
                        if persona.city:
                            persona.timezone_name = TimezoneService.resolve_timezone_for_city(persona.city)
                        mgr_state.last_edited_field = "city"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            f"✅ <b>Город и район сохранены:</b> {persona.city}"
                            + (f", {persona.district}" if persona.district else "")
                            + "!\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_BIRTH_CITY":
                        persona.birth_city = text.strip()
                        mgr_state.last_edited_field = "birth_city"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            f"✅ <b>Город рождения сохранен:</b> {persona.birth_city}!\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_MOVE_REASON":
                        persona.move_reason = text.strip()
                        mgr_state.last_edited_field = "move_reason"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            f"✅ <b>Причина переезда сохранена:</b> {persona.move_reason}!\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_YEARS_IN_CITY":
                        persona.years_in_city = text.strip()
                        mgr_state.last_edited_field = "years_in_city"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            f"✅ <b>Срок проживания сохранен:</b> {persona.years_in_city}!\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_DATING_APP":
                        persona.dating_app_context = text.strip()
                        mgr_state.last_edited_field = "dating_app"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            "✅ <b>Контекст дейтинга сохранен!</b>\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_STUDY_HOBBIES":
                        persona.study_work_hobbies = text.strip()
                        mgr_state.last_edited_field = "study_hobbies"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            "✅ <b>Учеба, работа и хобби сохранены!</b>\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_EXTRA_FACTS":
                        persona.extra_facts = text.strip()
                        mgr_state.last_edited_field = "extra_facts"
                        await session.commit()
                        await self.send_message(
                            chat_id,
                            "✅ <b>Детали и предпочтения сохранены!</b>\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

                    elif state == "WAIT_SLEEP_SCHEDULE":
                        times = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", text)
                        if len(times) >= 2:
                            persona.sleep_start = times[0].zfill(5)
                            persona.sleep_end = times[1].zfill(5)
                            mgr_state.last_edited_field = "sleep_schedule"
                            await session.commit()
                            await self.send_message(
                                chat_id,
                                f"✅ <b>Режим сна сохранен:</b> 🌙 {persona.sleep_start} – {persona.sleep_end}!\n\n"
                                + self.format_legend_card(persona, account=target_account),
                                reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                                parse_mode="HTML",
                            )
                        else:
                            # Keep user in WAIT_SLEEP_SCHEDULE and ask concisely for HH:MM - HH:MM
                            self._user_input_states[user_id] = state_raw
                            await self.send_message(
                                chat_id,
                                "Укажи время отбоя и подъема в формате <code>23:00 - 08:30</code> (два времени через тире или пробел).",
                                reply_markup=self.build_cancel_flow_kb("menu_legend"),
                                parse_mode="HTML",
                            )
                        return

                    elif state == "WAIT_LEGEND_WIZARD":
                        from companion.services.ai_service import AIService
                        from companion.services.timezone_service import TimezoneService

                        parsed = await AIService.parse_legend_wizard(text)
                        persona.name = parsed.get("name") or persona.name
                        persona.age = parsed.get("age") or persona.age
                        persona.city = parsed.get("city") or persona.city
                        persona.district = parsed.get("district") or persona.district
                        persona.birth_city = parsed.get("birth_city") or getattr(persona, "birth_city", None)
                        persona.move_reason = parsed.get("move_reason") or getattr(persona, "move_reason", None)
                        persona.years_in_city = parsed.get("years_in_city") or getattr(persona, "years_in_city", None)
                        persona.dating_app_context = parsed.get("dating_app_context") or persona.dating_app_context
                        persona.study_work_hobbies = parsed.get("study_work_hobbies") or persona.study_work_hobbies
                        persona.extra_facts = parsed.get("extra_facts") or persona.extra_facts
                        if parsed.get("legend"):
                            persona.legend = parsed["legend"]
                        if persona.city:
                            persona.timezone_name = TimezoneService.resolve_timezone_for_city(persona.city)
                        mgr_state.last_edited_field = "name_age"
                        await session.commit()

                        await self.send_message(
                            chat_id,
                            "🎉 <b>Анкета девушки успешно заполнена нейросетью!</b>\n\n"
                            + self.format_legend_card(persona, account=target_account),
                            reply_markup=self.build_legend_keyboard(account_id=acc_id_for_kb, has_multiple_accounts=has_mult),
                            parse_mode="HTML",
                        )
                        return

            # 0. Benchmark Voice Test (/test_voice or phrase 'тест гс')
            low_text = text.lower().strip()
            if text.startswith("/test_voice") or "тест гс" in low_text:
                benchmark_text = "Слушай, ну я только что домой зашла, вообще без сил... Давай я сейчас быстренько в душ схожу, и минут через двадцать тебе сама наберу, хорошо?"
                await self.send_message(
                    chat_id,
                    "⏳ <b>Записываю голосовое сообщение...</b>\n"
                    "<i>Живая интонация | Чистый русский язык без акцента | 48kHz Opus</i>",
                    parse_mode="HTML",
                )
                sent = await VoiceService.send_telegram_voice_note(
                    bot_token=self.token,
                    chat_id=chat_id,
                    text=benchmark_text,
                    caption="🎙 <b>Голосовое сообщение (Тест)</b>\n<i>«Слушай, ну я только что домой зашла, вообще без сил...»</i>",
                )
                if not sent:
                    await self.send_message(chat_id, "❌ Не удалось сгенерировать голосовое сообщение.")
                return

            # 0a. Admin Voice Cloning Command (/clone <api_key>)
            if is_admin and (text.startswith("/clone") or text.startswith("/set_voice_key")):
                parts = text.split()
                if len(parts) >= 2:
                    api_key = parts[1].strip()
                    await self.send_message(
                        chat_id,
                        "⏳ <b>Создаю цифровую модель голоса...</b>\n<i>Это займет пару секунд...</i>",
                        parse_mode="HTML",
                    )
                    ok, msg, voice_id = await VoiceService.clone_voice_from_sample(api_key)
                    if ok:
                        await self.send_message(
                            chat_id,
                            f"🎉 <b>Цифровая модель голоса успешно активирована!</b>\n\n"
                            f"🆔 Voice ID: <code>{voice_id}</code>\n"
                            f"💾 Настройки сохранены в конфигурации бота.\n\n"
                            f"🎙 Генерирую и отправляю проверочное голосовое сообщение...",
                            parse_mode="HTML",
                        )
                        test_text = "Ой, ну приветик) Я теперь говорю своим настоящим живым голосом! Как тебе результат?))"
                        await VoiceService.send_telegram_voice_note(
                            self.token,
                            chat_id,
                            text=test_text,
                            caption="🎙 <b>Проверка живого голоса</b>\n<i>«Ой, ну приветик) Я теперь говорю своим настоящим живым голосом!...»</i>",
                        )
                    else:
                        await self.send_message(chat_id, f"❌ <b>Ошибка настройки голоса:</b>\n{msg}", parse_mode="HTML")
                    return
                else:
                    await self.send_message(
                        chat_id,
                        "ℹ️ <b>Формат команды:</b>\n"
                        "<code>/clone &lt;ВАШ_VOICE_API_KEY&gt;</code>",
                        parse_mode="HTML",
                    )
                    return

            # 0b. Admin AI Key Command (/key <api_key>, /gemini <api_key> or raw key)
            is_raw_ai_key = is_admin and (text.startswith("AIzaSy") or text.startswith("AQ.")) and len(text) >= 35
            if is_admin and (text.startswith("/key") or text.startswith("/ai_key") or text.startswith("/set_key") or text.startswith("/gemini") or text.startswith("/set_gemini") or is_raw_ai_key):
                if is_raw_ai_key:
                    api_key = text.strip()
                else:
                    parts = text.split()
                    api_key = parts[1].strip() if len(parts) >= 2 else ""

                if api_key:
                    env_path = Path(".env")
                    if env_path.exists():
                        content = env_path.read_text(encoding="utf-8")
                        if "GEMINI_API_KEY=" in content:
                            content = re.sub(r"GEMINI_API_KEY=.*", f"GEMINI_API_KEY={api_key}", content)
                        else:
                            content += f"\nGEMINI_API_KEY={api_key}"
                        env_path.write_text(content, encoding="utf-8")
                    settings.GEMINI_API_KEY = api_key
                    os.environ["GEMINI_API_KEY"] = api_key

                    # Test AI model call
                    test_ok = False
                    err_msg = ""
                    try:
                        from google import genai
                        client = genai.Client(api_key=api_key)
                        resp = client.models.generate_content(
                            model=settings.GEMINI_MODEL,
                            contents="Напиши одно короткое слово: привет",
                        )
                        if resp and resp.text:
                            test_ok = True
                    except Exception as ge:
                        err_msg = str(ge)

                    if test_ok:
                        await self.send_message(
                            chat_id,
                            f"🎉 <b>AI-ключ успешно подключен и проверен!</b>\n\n"
                            f"✨ <b>Статус:</b> Нейросеть активна (Онлайн ✅)\n"
                            f"💾 Ключ сохранён в конфигурации.\n\n"
                            f"Теперь бот общается с памятью контекста, живыми девичьими интонациями и полным соответствием легенде!",
                            parse_mode="HTML",
                        )
                    else:
                        await self.send_message(
                            chat_id,
                            f"⚠️ <b>Ключ сохранён, но тестовый запрос вернул предупреждение:</b>\n"
                            f"<code>{html.escape(err_msg[:250]) if err_msg else 'нет ответа'}</code>\n\n"
                            f"<i>Если ключ валиден, бот автоматически начнет использовать его при входящих сообщениях.</i>",
                            parse_mode="HTML",
                        )
                    return
                else:
                    await self.send_message(
                        chat_id,
                        "ℹ️ <b>Формат команды:</b>\n<code>/key &lt;ВАШ_AI_API_KEY&gt;</code>\n\n"
                        "<i>(Или просто отправьте ключ прямо в чат)</i>",
                        parse_mode="HTML",
                    )
                    return

            # 1. Admin Grant Command (/grant <target> <duration/plan>)
            if is_admin and (text.startswith("/grant") or text.startswith("/gift")):
                parts = text.split()
                if len(parts) >= 3:
                    target_raw = parts[1].strip()
                    duration_raw = parts[2].strip()
                    days, plan, plan_label = self.parse_plan_and_days(duration_raw)

                    target_user = None
                    target_tg_id = None

                    if target_raw.isdigit():
                        target_tg_id = int(target_raw)
                        stmt_t = select(User).where(User.telegram_id == target_tg_id)
                        res_t = await session.execute(stmt_t)
                        target_user = res_t.scalars().first()
                        if not target_user:
                            target_user = User(telegram_id=target_tg_id)
                            session.add(target_user)
                            await session.commit()
                            await session.refresh(target_user)
                    else:
                        clean_name = target_raw.lstrip("@").strip()
                        # 1. Search in User table by username
                        stmt_u = select(User).where(func.lower(User.username) == clean_name.lower())
                        res_u = await session.execute(stmt_u)
                        target_user = res_u.scalars().first()

                        if target_user:
                            target_tg_id = target_user.telegram_id
                        else:
                            # 2. Search in Contact table by username
                            stmt_c = select(Contact).where(func.lower(Contact.username) == clean_name.lower())
                            res_c = await session.execute(stmt_c)
                            contact = res_c.scalars().first()
                            if contact and contact.telegram_id:
                                target_tg_id = contact.telegram_id
                                stmt_t = select(User).where(User.telegram_id == target_tg_id)
                                res_t = await session.execute(stmt_t)
                                target_user = res_t.scalars().first()
                                if not target_user:
                                    target_user = User(telegram_id=target_tg_id, username=clean_name)
                                    session.add(target_user)
                                    await session.commit()
                                    await session.refresh(target_user)
                            else:
                                # 3. Resolve via Telegram Bot API getChat
                                chat_info = await self.get_chat(f"@{clean_name}")
                                if chat_info and "id" in chat_info:
                                    target_tg_id = chat_info["id"]
                                    stmt_t = select(User).where(User.telegram_id == target_tg_id)
                                    res_t = await session.execute(stmt_t)
                                    target_user = res_t.scalars().first()
                                    if not target_user:
                                        target_user = User(
                                            telegram_id=target_tg_id,
                                            username=chat_info.get("username", clean_name),
                                        )
                                        session.add(target_user)
                                        await session.commit()
                                        await session.refresh(target_user)

                    if not target_user:
                        await self.send_message(
                            chat_id=chat_id,
                            text=(
                                f"❌ <b>Пользователь {target_raw} не найден в базе бота.</b>\n\n"
                                f"Чтобы выдать подписку по юзернейму, пользователь должен хотя бы раз нажать <code>/start</code> в боте, "
                                f"либо вы можете указать его числовой Telegram ID:\n"
                                f"<code>/grant &lt;ID пользователя&gt; &lt;тариф или дни&gt;</code>"
                            ),
                            parse_mode="HTML",
                        )
                        return

                    sub = await SubscriptionService.activate_plan(session, target_user.id, plan, custom_days=days, extend=False)

                    # Notify target user
                    if target_tg_id:
                        await self.send_message(
                            chat_id=target_tg_id,
                            text=(
                                f"🎁 <b>Вам подарок от Администратора!</b>\n\n"
                                f"Вам была начислена подписка на <b>{days} дней</b> ({plan_label})!\n"
                                f"⏳ Срок действия: до <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b> (МСК)\n"
                                f"📱 Лимит подключений: <b>до {sub.max_accounts} аккаунтов</b>\n\n"
                                f"Подписка уже активна! Нажмите /start для вызова главного меню."
                            ),
                            parse_mode="HTML",
                        )

                    user_display = f"@{target_user.username} (ID <code>{target_tg_id}</code>)" if target_user.username else f"ID <code>{target_tg_id}</code>"
                    await self.send_message(
                        chat_id=chat_id,
                        text=(
                            f"✅ <b>Подписка успешно выдана!</b>\n\n"
                            f"👤 Пользователь: <b>{user_display}</b>\n"
                            f"📅 Тариф: <b>{days} дней</b> ({plan_label})\n"
                            f"⏳ Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b> (МСК)\n"
                            f"📱 Лимит аккаунтов: <b>до {sub.max_accounts}</b>\n\n"
                            f"Пользователь получил подарочное уведомление в боте!"
                        ),
                        parse_mode="HTML",
                    )
                    return
                else:
                    await self.send_message(
                        chat_id=chat_id,
                        text=(
                            "ℹ️ <b>Формат команды выдачи подписки:</b>\n"
                            "<code>/grant &lt;@username или ID&gt; &lt;тариф или дни&gt;</code>\n\n"
                            "<b>Примеры:</b>\n"
                            "• <code>/grant @username 3</code> (на 3 дня)\n"
                            "• <code>/grant @username 7</code> (на 7 дней)\n"
                            "• <code>/grant @username 14</code> (на 14 дней)\n"
                            "• <code>/grant @username 31</code> (на 31 день / месяц)\n"
                            "• <code>/grant @username 90</code> (на 3 месяца)\n"
                            "• <code>/grant @username 365</code> (на 1 год)\n"
                            "• <code>/grant 8501711204 30</code> (по числовому ID)"
                        ),
                        parse_mode="HTML",
                    )
                    return

            # 2. CryptoBot Cheque submission (/check CQ... or link)
            if "t.me/CryptoBot?start=CQ" in text or text.startswith("/check") or text.startswith("CQ"):
                cheque_code = text.replace("/check", "").strip()
                # Activate 7 days bonus for cheque
                sub = await SubscriptionService.activate_plan(session, db_user.id, PlanDuration.DAYS_7)
                await self.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🎉 <b>Чек @CryptoBot принят к активации!</b>\n\n"
                        f"Вам начислено <b>7 дней подписки</b>!\n"
                        f"⏳ Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
                        f"Спасибо за поддержку проекта!"
                    ),
                    reply_markup=self.build_main_menu_keyboard(is_admin),
                    parse_mode="HTML",
                )
                await AdminNotifier.send_telegram_alert(
                    f"🧾 <b>Активация чеком @CryptoBot!</b>\nПользователь ID <code>{user_id}</code> активировал чек: <code>{cheque_code}</code>.",
                    self.admin_id,
                )
                return

            # 3. Handle deep-link activation (/start sub_...)
            if text.startswith("/start sub_"):
                token_str = text.split()[1].strip()
                invite = await InviteService.verify_invite(session, token_str)
                if invite:
                    # Activate subscription for this user with exact plan duration
                    sub = await SubscriptionService.activate_plan(session, db_user.id, invite.plan, extend=False)
                    reply_text = (
                        f"🎉 <b>Подписка успешно активирована!</b>\n\n"
                        f"📅 Ваш тариф: <b>{invite.plan.value}</b> (активен ровно {invite.plan.days_count} дн., до {sub.expires_at.strftime('%d.%m.%Y %H:%M')})\n"
                        f"📱 Лимит подключений: до <b>{invite.max_accounts}</b> акк.\n\n"
                        f"👉 <b>Как подключить бота к ответам в личных чатах:</b>\n"
                        f"1. Откройте в Telegram: <i>Настройки</i> ➡️ <i>Telegram Business</i> ➡️ <i>Чат-боты (Автоматизация чатов)</i>.\n"
                        f"2. В строке поиска укажите имя бота: <code>@{settings.BOT_USERNAME}</code> <i>(вводить нужно юзернейм, а не ссылку!)</i>\n"
                        f"3. Включите галочку «Отвечать на сообщения» и выберите нужные чаты.\n\n"
                        f"Всё готово — бот начнет автоматически вести диалоги за вас!"
                    )
                else:
                    reply_text = "❌ Ссылка недействительна или срок действия подписки истек."
                await self.send_message(chat_id, reply_text, parse_mode="HTML")
                return

            # 4. Slash Commands Dispatcher
            if text in ("/start", "/menu"):
                sub = await SubscriptionService.get_active_subscription(session, db_user.id)
                sub_info = SubscriptionService.get_detailed_status(sub)
                text_reply = self.format_main_menu_text(is_admin, sub_info)
                await self.send_message(
                    chat_id, text_reply, reply_markup=self.build_main_menu_keyboard(is_admin), parse_mode="HTML"
                )
                return

            if text in ("/app", "/miniapp", "/webapp"):
                if settings.WEBAPP_URL and settings.WEBAPP_URL.startswith("https://"):
                    kb = {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "🚀 Открыть Панель управления (Mini App)",
                                    "web_app": {"url": settings.WEBAPP_URL},
                                }
                            ],
                            [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                        ]
                    }
                    await self.send_message(
                        chat_id,
                        "📱 <b>TELEGRAM MINI APP — ПАНЕЛЬ УПРАВЛЕНИЯ</b>\n\n"
                        "Нажмите кнопку ниже, чтобы открыть полноценное приложение управления:\n"
                        "• Живая статистика и воронка свиданий\n"
                        "• CRM диалогов и 7-уровневая память ИИ\n"
                        "• Конструктор легенды и AI-визард\n"
                        "• Управление аккаунтами, войсами и подпиской",
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                else:
                    await self.send_message(
                        chat_id,
                        "⚠️ Для открытия Mini App внутри Telegram требуется HTTPS-адрес в <code>WEBAPP_URL</code>.",
                        parse_mode="HTML",
                    )
                return

            if text in ("/sub", "/status", "/subscription"):
                sub = await SubscriptionService.get_active_subscription(session, db_user.id)
                sub_info = SubscriptionService.get_detailed_status(sub)
                if sub_info["is_active"]:
                    text_reply = (
                        f"⭐ <b>УПРАВЛЕНИЕ ВАШЕЙ ПОДПИСКОЙ</b>\n\n"
                        f"📋 <b>Тарифный план:</b> {sub_info['plan_name']}\n"
                        f"⏳ <b>Действует до:</b> <code>{sub_info['expires_at_str']}</code> (МСК)\n"
                        f"⏱ <b>Осталось точно:</b> <b>{sub_info['remaining_str']}</b>\n"
                        f"📱 <b>Лимит аккаунтов:</b> до {sub_info['max_accounts']} акк.\n"
                        f"🎙 <b>Голосовых сегодня:</b> {sub_info['voice_used_today']} / {sub_info['max_daily_voice']}\n\n"
                        f"Вы можете в любой момент добавить время или отменить подписку:"
                    )
                else:
                    text_reply = (
                        "⭐ <b>УПРАВЛЕНИЕ ВАШЕЙ ПОДПИСКОЙ</b>\n\n"
                        "❌ <b>У вас нет активной подписки.</b>\n\n"
                        "Для доступа к автоматизации чатов, выбору стилей и реалистичным войсам "
                        "выберите подходящий тариф в разделе «Добавить время / Оплата»."
                    )
                await self.send_message(
                    chat_id, text_reply, reply_markup=self.build_my_sub_keyboard(sub_info["is_active"]), parse_mode="HTML"
                )
                return

            if text == "/accounts":
                accounts = await AccountService.get_user_accounts(session, db_user.id)
                if accounts:
                    personas = {}
                    lines = []
                    for idx, a in enumerate(accounts, 1):
                        p = await AccountService.ensure_account_persona(session, a)
                        personas[a.id] = p
                        if a.is_active and a.status == AccountStatus.CONNECTED:
                            status_badge = "🟢 Активен (Бот отвечает)"
                        elif a.status == AccountStatus.DISCONNECTED or not a.is_active:
                            status_badge = "⏸ Отключен (Пауза)"
                        else:
                            status_badge = f"⚠️ {a.status.value if hasattr(a.status, 'value') else a.status}"
                        acc_title = a.first_name or (f"@{a.username}" if a.username else f"ID {a.tg_user_id or a.id}")
                        cur_style = p.current_style or "adaptive_all"
                        style_title = StyleType(cur_style).title if cur_style in StyleType._value2member_map_ else cur_style
                        lines.append(
                            f"<b>{idx}. {acc_title}</b> ({status_badge})\n"
                            f"   • Имя в чатах: <b>{p.name}</b>, {p.age} лет, г. {p.city}\n"
                            f"   • Стиль общения: <b>{style_title}</b>"
                        )
                    text_reply = (
                        "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                        f"Всего подключено: <b>{len(accounts)} акк.</b>\n\n"
                        + "\n\n".join(lines) + "\n\n"
                        "💡 <i>Нажмите <b>«⚙️ Легенда»</b> или <b>«🎭 Стиль»</b> для индивидуальной настройки:</i>"
                    )
                    kb = self.build_accounts_keyboard(accounts, personas)
                else:
                    text_reply = (
                        "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                        "У вас пока не подключено ни одного аккаунта.\n\n"
                        "<b>Как подключить аккаунт за 1 минуту:</b>\n"
                        "1. Нажмите <b>«➕ Подключить аккаунт»</b> ниже.\n"
                        "2. Откройте Telegram на рабочем аккаунте: <i>Настройки ➡️ Telegram Business ➡️ Чат-боты</i>.\n"
                        f"3. Выберите бота <code>@{settings.BOT_USERNAME}</code> и сохраните."
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Подключить аккаунт (Создать ссылку)", "callback_data": "menu_invites"}],
                            [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                        ]
                    }
                await self.send_message(chat_id, text_reply, reply_markup=kb, parse_mode="HTML")
                return

            if text == "/legend":
                accounts = await AccountService.get_user_accounts(session, db_user.id)
                if len(accounts) > 1:
                    buttons = []
                    for acc in accounts:
                        p = await AccountService.ensure_account_persona(session, acc)
                        acc_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")
                        buttons.append([
                            {"text": f"📱 {acc_title} ({p.name}, {p.age})", "callback_data": f"legend_acc_{acc.id}"}
                        ])
                    buttons.append([{"text": "⭐ Общий шаблон по умолчанию", "callback_data": "legend_acc_default"}])
                    buttons.append([{"text": "◀️ Назад в главное меню", "callback_data": "menu_main"}])
                    text_reply = (
                        "⚙️ <b>НАСТРОЙКА ЛЕГЕНДЫ ДЛЯ АККАУНТОВ</b>\n\n"
                        f"У вас подключено: <b>{len(accounts)} акк.</b>\n\n"
                        "Вы можете настроить <b>разные имена, города и истории</b> для каждого аккаунта.\n\n"
                        "Выберите аккаунт для настройки:"
                    )
                    await self.send_message(chat_id, text_reply, reply_markup={"inline_keyboard": buttons}, parse_mode="HTML")
                    return
                elif len(accounts) == 1:
                    acc = accounts[0]
                    persona = await AccountService.ensure_account_persona(session, acc)
                    from companion.services.photo_pool_service import PhotoPoolService
                    p_stats = await PhotoPoolService.get_persona_photo_stats(session, persona.id)
                    p_count = p_stats.get("total_photos", 0)
                    await self.send_message(
                        chat_id,
                        self.format_legend_card(persona, account=acc, photo_count=p_count),
                        reply_markup=self.build_legend_keyboard(account_id=acc.id, has_multiple_accounts=False, photo_count=p_count),
                        parse_mode="HTML",
                    )
                    return
                else:
                    stmt = select(Persona).where(Persona.user_id == db_user.id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    if not persona:
                        persona = Persona(user_id=db_user.id)
                        session.add(persona)
                        await session.commit()
                        await session.refresh(persona)
                    from companion.services.photo_pool_service import PhotoPoolService
                    p_stats = await PhotoPoolService.get_persona_photo_stats(session, persona.id)
                    p_count = p_stats.get("total_photos", 0)
                    await self.send_message(
                        chat_id,
                        self.format_legend_card(persona, account=None, photo_count=p_count),
                        reply_markup=self.build_legend_keyboard(account_id=None, has_multiple_accounts=False, photo_count=p_count),
                        parse_mode="HTML",
                    )
                    return

            if text == "/styles":
                accounts = await AccountService.get_user_accounts(session, db_user.id)
                acc_info = f" (подключено аккаунтов: <b>{len(accounts)}</b>)" if accounts else ""
                text_reply = (
                    "🎭 <b>НАСТРОЙКА СТИЛЕЙ ОБЩЕНИЯ: АДАПТИВНЫЙ РЕЖИМ</b>\n\n"
                    f"В боте активирован единый <b>интеллектуальный адаптивный стиль</b>{acc_info}.\n\n"
                    "Ручной выбор отдельных шаблонов отключен: модель самостоятельно анализирует манеру общения каждого мужчины и общается со всеми по-разному, максимально реалистично!\n\n"
                    "💡 <b>Как нейросеть общается с разными клиентами:</b>\n"
                    "• <b>Анализирует характер и вайб:</b> сдержанным отвечает спокойно и лаконично, веселым — с юмором и иронией, романтичным — с девичьим смущением и теплом, уставшим — с искренней заботой.\n"
                    "• <b>Удерживает границы:</b> при наглости или напоре осаживает с достоинством и легким сарказмом, не прогибаясь.\n"
                    "• <b>Зеркалит ритм:</b> подстраивает длину реплик, темп и эмоциональный градус.\n"
                    "• <b>Уникальность:</b> каждый диалог строится по-своему, без шаблонных повторов."
                )
                await self.send_message(chat_id, text_reply, reply_markup=self.build_styles_keyboard("adaptive_all", account_id=None), parse_mode="HTML")
                return

            if text in ("/voice", "/voices"):
                text_reply = (
                    "🎙 <b>ТЕСТИРОВАНИЕ ГОЛОСОВЫХ СООБЩЕНИЙ</b>\n\n"
                    "Голосовые сообщения создаются с реалистичным мастерингом мобильного микрофона:\n"
                    "• Нативный чистый русский язык без иностранного акцента\n"
                    "• Естественные паузы, вздохи и живые телефонные интонации\n"
                    "• Формат <b>48kHz Telegram Voice OGG Opus</b> с прыгающей анимированной звуковой дорожкой\n\n"
                    "👇 Выберите стиль войса для мгновенной генерации и прослушивания:"
                )
                await self.send_message(chat_id, text_reply, reply_markup=self.build_voice_test_keyboard(), parse_mode="HTML")
                return

            if text == "/leads":
                leads = await StatsService.get_leads(session, db_user.id)
                text_reply, kb = self.build_leads_view(leads, filter_mode="all")
                await self.send_message(chat_id, text_reply, reply_markup=kb, parse_mode="HTML")
                return

            if text == "/stats":
                stats = await StatsService.get_overview(session, db_user.id)
                report = AdminNotifier.format_stats_report(stats)
                kb = {
                    "inline_keyboard": [
                        [{"text": "🔥 Список горячих лидов", "callback_data": "menu_leads"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                    ]
                }
                await self.send_message(chat_id, report, reply_markup=kb, parse_mode="HTML")
                return

            if text in ("/photos", "/photo_pool", "/photopool") or text.startswith("/photos ") or text.startswith("/photo_"):
                if not is_admin:
                    # Section 22 & 29: Normal users must never execute photo-management commands or see internal behavior
                    return

                from companion.services.photo_pool_service import PhotoAdminService, PhotoPoolService
                parts = text.split(maxsplit=2)
                cmd = parts[0].lower()
                subcmd = parts[1].lower() if len(parts) >= 2 else "stats"
                arg = parts[2].strip() if len(parts) >= 3 else (parts[1].strip() if cmd != "/photos" and len(parts) >= 2 else "")

                # /photos list
                if cmd == "/photos" and subcmd == "list":
                    photos = await PhotoPoolService.list_photos(session, limit=20, offset=0)
                    if not photos:
                        await self.send_message(
                            chat_id,
                            "📸 <b>В пуле пока нет загруженных фото.</b>\n\nПросто отправьте фотографию в этот чат для добавления.",
                            parse_mode="HTML",
                        )
                        return
                    lines = ["📸 <b>СПИСОК ФОТО В ПУЛЕ:</b>\n"]
                    for p in photos:
                        st = "🟢" if p.enabled else "⚪"
                        tags_str = ", ".join(p.get_tags()) or "—"
                        lines.append(
                            f"{st} <code>{p.photo_id}</code> [{p.category}] ({p.width}x{p.height})\n"
                            f"   🏷 Теги: {tags_str} | Отправок: {p.times_used}"
                        )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "📋 Управление списком", "callback_data": "photos_list_0"}],
                            [{"text": "📊 Статистика пула", "callback_data": "photos_stats"}],
                        ]
                    }
                    await self.send_message(chat_id, "\n".join(lines), reply_markup=kb, parse_mode="HTML")
                    return

                # /photos enable <photo_id>
                if cmd == "/photos" and subcmd == "enable" and arg:
                    updated = await PhotoPoolService.toggle_photo_status(session, arg, True)
                    if not updated:
                        await self.send_message(chat_id, f"❌ Фото <code>{arg}</code> не найдено.", parse_mode="HTML")
                    else:
                        await self.send_message(chat_id, f"✅ Фото <code>{arg}</code> включено (🟢 Активно).", parse_mode="HTML")
                    return

                # /photos disable <photo_id>
                if cmd == "/photos" and subcmd == "disable" and arg:
                    updated = await PhotoPoolService.toggle_photo_status(session, arg, False)
                    if not updated:
                        await self.send_message(chat_id, f"❌ Фото <code>{arg}</code> не найдено.", parse_mode="HTML")
                    else:
                        await self.send_message(chat_id, f"⏸ Фото <code>{arg}</code> отключено (⚪ Неактивно).", parse_mode="HTML")
                    return

                # /photos delete <photo_id> or /photo_del <photo_id>
                if (cmd == "/photos" and subcmd in ("delete", "del", "remove") and arg) or cmd == "/photo_del":
                    pid = arg
                    if not pid:
                        await self.send_message(chat_id, "Использование: <code>/photos delete &lt;photo_id&gt;</code>", parse_mode="HTML")
                        return
                    ok = await PhotoPoolService.delete_photo(session, pid, delete_file=True)
                    if ok:
                        await self.send_message(chat_id, f"🗑 Фото <code>{pid}</code> удалено из пула.", parse_mode="HTML")
                    else:
                        await self.send_message(chat_id, f"❌ Фото с ID <code>{pid}</code> не найдено.", parse_mode="HTML")
                    return

                # /photos clear
                if cmd == "/photos" and subcmd == "clear":
                    deleted_cnt = await PhotoPoolService.clear_all_photos(session, delete_files=True)
                    await self.send_message(chat_id, f"🧹 Пул фотографий очищен (удалено фото: <b>{deleted_cnt}</b>).", parse_mode="HTML")
                    return

                # /photo_toggle <photo_id>
                if cmd == "/photo_toggle":
                    if not arg:
                        await self.send_message(chat_id, "Использование: <code>/photo_toggle &lt;photo_id&gt;</code>", parse_mode="HTML")
                        return
                    curr = await PhotoPoolService.get_photo(session, arg)
                    if not curr:
                        await self.send_message(chat_id, f"❌ Фото с ID <code>{arg}</code> не найдено.", parse_mode="HTML")
                        return
                    new_state = not curr.enabled
                    await PhotoPoolService.toggle_photo_status(session, arg, new_state)
                    st_label = "🟢 Включено" if new_state else "⚪ Отключено"
                    await self.send_message(chat_id, f"✅ Фото <code>{arg}</code> теперь: {st_label}", parse_mode="HTML")
                    return

                # /photo_info <photo_id>
                if cmd == "/photo_info":
                    if not arg:
                        await self.send_message(chat_id, "Использование: <code>/photo_info &lt;photo_id&gt;</code>", parse_mode="HTML")
                        return
                    ph = await PhotoPoolService.get_photo(session, arg)
                    if not ph:
                        await self.send_message(chat_id, f"❌ Фото с ID <code>{arg}</code> не найдено.", parse_mode="HTML")
                        return
                    from companion.models.entities import ChatPhotoUsage
                    u_res = await session.execute(select(ChatPhotoUsage).where(ChatPhotoUsage.photo_id == arg))
                    usages = u_res.scalars().all()
                    chats_list = ", ".join([f"{u.chat_id} ({u.status})" for u in usages]) or "еще ни разу не отправлялось"
                    msg = (
                        f"📷 <b>ИНФОРМАЦИЯ О ФОТО:</b> <code>{ph.photo_id}</code>\n\n"
                        f"📁 <b>Категория:</b> <code>{ph.category}</code>\n"
                        f"🏷 <b>Теги:</b> {', '.join(ph.get_tags()) or '—'}\n"
                        f"📝 <b>Описание:</b> {ph.description or '—'}\n"
                        f"🟢 <b>Статус:</b> {'Активно' if ph.enabled else 'Отключено'}\n"
                        f"📐 <b>Разрешение:</b> {ph.width}x{ph.height}\n"
                        f"📤 <b>Отправок:</b> {ph.times_used}\n"
                        f"💾 <b>Размер:</b> {ph.file_size // 1024} КБ\n"
                        f"📊 <b>dHash:</b> <code>{ph.perceptual_hash}</code>\n"
                        f"🔒 <b>SHA-256:</b> <code>{ph.file_hash[:16]}...</code>\n\n"
                        f"👥 <b>Использовано в чатах:</b>\n{chats_list}"
                    )
                    await self.send_message(chat_id, msg, parse_mode="HTML")
                    return

                # Default: /photos or /photos stats
                stats = await PhotoPoolService.get_pool_stats(session)
                report = PhotoAdminService.format_stats_message(stats)
                kb = {
                    "inline_keyboard": [
                        [{"text": "📋 Список фото", "callback_data": "photos_list_0"}],
                        [{"text": "➕ Как загрузить фото", "callback_data": "photos_upload_help"}],
                        [{"text": "🔄 Обновить", "callback_data": "photos_refresh"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                    ]
                }
                await self.send_message(chat_id, report, reply_markup=kb, parse_mode="HTML")
                return

            if text == "/help":
                help_text = (
                    "❓ <b>СПРАВКА И СПИСОК КОМАНД БОТА</b>\n\n"
                    "🌸 <b>AI-Компаньон</b> автоматизирует общение в Telegram от лица девушки с естественными голосовыми и перехватом встреч.\n\n"
                    "<b>Основные команды:</b>\n"
                    "• /start или /menu — Открыть главное меню\n"
                    "• /sub — Статус подписки, продление, лимиты\n"
                    "• /accounts — Подключенные рабочие аккаунты\n"
                    "• /legend — Настройка анкеты девушки (имя, возраст, город)\n"
                    "• /styles — Выбор характера и стиля общения\n"
                    "• /voice — Тестирование и прослушивание голосовых\n"
                    "• /leads — Список горячих лидов (согласных на свидание)\n"
                    "• /stats — Статистика диалогов и конверсий\n"
                    "• /test_voice — Быстрая отправка тестового войса\n"
                )
                if is_admin:
                    help_text += (
                        "\n👑 <b>Команды администратора:</b>\n"
                        "• /admin — Панель управления и выдачи подписок\n"
                        "• /users — База подписчиков и статус их аккаунтов\n"
                        "• /grant &lt;@user/id&gt; &lt;дни&gt; — Выдать подписку\n"
                        "• /key &lt;ключ&gt; — Подключить API-ключ интеллекта\n"
                        "• /clone &lt;ключ&gt; — Настройка цифрового голоса\n"
                    )
                help_text += "\n💡 <i>Также можно использовать меню внизу экрана или кнопки под сообщениями.</i>"
                kb = {"inline_keyboard": [[{"text": "◀️ В главное меню", "callback_data": "menu_main"}]]}
                await self.send_message(chat_id, help_text, reply_markup=kb, parse_mode="HTML")
                return

            if is_admin and text in ("/diag", "/diagnostics", "/status_system"):
                # System diagnostics report without exposing secrets
                accounts_stmt = select(AccountSession).where(AccountSession.is_active == True)
                active_accs = (await session.execute(accounts_stmt)).scalars().all()

                contacts_count = (await session.execute(select(func.count(Contact.id)))).scalar() or 0
                prefs_count = (await session.execute(select(func.count(ContactBehavioralPreference.id)))).scalar() or 0
                mem_count = (await session.execute(select(func.count(ContactMemory.id)))).scalar() or 0

                acc_lines = []
                for a in active_accs:
                    conn_id_masked = f"{a.business_connection_id[:6]}...{a.business_connection_id[-4:]}" if a.business_connection_id else "None"
                    acc_lines.append(f"• <b>@{a.phone_or_username or a.account_name}</b> (Conn: <code>{conn_id_masked}</code>)")

                diag_msg = (
                    "🛠 <b>СИСТЕМНАЯ ДИАГНОСТИКА БОТА</b>\n\n"
                    f"🤖 <b>Control Bot:</b> @YourHelperW_bot (ID <code>8757255956</code>)\n"
                    f"👑 <b>Admin ID:</b> <code>{self.admin_id}</code>\n\n"
                    f"📱 <b>Telegram Business Connections:</b>\n"
                    + ("\n".join(acc_lines) if acc_lines else "<i>Нет активных бизнес-подключений</i>")
                    + f"\n\n🧠 <b>AI Провайдер:</b> Google Gemini\n"
                    f"• Модель: <code>{settings.GEMINI_MODEL}</code>\n"
                    f"• API Key: <code>{settings.GEMINI_API_KEY[:6]}...{settings.GEMINI_API_KEY[-4:] if settings.GEMINI_API_KEY else 'NOT_SET'}</code>\n\n"
                    f"🎙 <b>Голосовой синтез (Voice Engine):</b>\n"
                    f"• ElevenLabs Voice: <code>Ясна</code> (ID: <code>{settings.ELEVENLABS_VOICE_ID or 'oik5x6jBI6Xj6MPIciZU'}</code>)\n"
                    f"• Эквалайзер: <i>6-band Studio Mastering EQ (Low Cut 62Hz 24dB/oct, 4-bell notches, High Shelf)</i>\n"
                    f"• Формат: <i>48kHz Mono Ogg Opus (Single-pass)</i>\n\n"
                    f"💾 <b>Трехуровневая память:</b>\n"
                    f"• Уровень 1 (Short-term buffer): <i>Активен</i>\n"
                    f"• Уровень 2 (Факты собеседников): <code>{mem_count}</code> записей\n"
                    f"• Уровень 3 (Выученные предпочтения): <code>{prefs_count}</code> записей\n"
                    f"• База контактов: <code>{contacts_count}</code> собеседников\n\n"
                    f"❤️ <b>Контекстный модуль Дайвинчика:</b> <i>Активен (детектор анкет и диалогов)</i>\n"
                    f"⚡ <b>Rhythm & Timings:</b> <i>Snappy (0.8-2.0s audio listening, 0.45-3.5s typing)</i>"
                )
                await self.send_message(chat_id, diag_msg, parse_mode="HTML")
                return

            if is_admin and text in ("/admin", "/panel"):
                admin_text = (
                    "👑 <b>ПАНЕЛЬ ГЛАВНОГО АДМИНИСТРАТОРА</b>\n\n"
                    "Здесь вы можете смотреть подключенные аккаунты всех пользователей, "
                    "дарить подписки, настраивать API-ключи и цифровой голос.\n\n"
                    "Выберите нужный раздел:"
                )
                await self.send_message(chat_id, admin_text, reply_markup=self.build_admin_grant_keyboard(), parse_mode="HTML")
                return

            if is_admin and text in ("/users", "/subscribers"):
                users_data = await AccountService.get_admin_subscribers_overview(session)
                overview_text = self.format_subscribers_overview_text(users_data)
                kb = self.build_subscribers_overview_keyboard(users_data)
                await self.send_message(chat_id, overview_text, reply_markup=kb, parse_mode="HTML")
                return

            # 4b. Human-Like Manager Conversational Layer (Context-Aware Operations Manager)
            def _attach_resume_if_interrupted(
                reply_str: str, markup: Optional[Dict[str, Any]]
            ) -> tuple[str, Optional[Dict[str, Any]]]:
                if not mgr_state.interrupted_flow:
                    return reply_str, markup
                raw_f = mgr_state.interrupted_flow
                base_f = raw_f.split(":", 1)[0] if ":" in raw_f else raw_f
                label_f = FLOW_LABELS.get(base_f, "предыдущий шаг")
                note = f"\n\n💡 <i>Кстати, мы остановились на вводе: <b>{label_f}</b>. Продолжим?</i>"
                rows = list(markup.get("inline_keyboard", [])) if markup else []
                rows.insert(
                    0,
                    [
                        {"text": "▶️ Продолжить ввод", "callback_data": "resume_interrupted_flow"},
                        {"text": "✖️ Забыть ввод", "callback_data": "cancel_input_flow"},
                    ],
                )
                return reply_str + note, {"inline_keyboard": rows}

            # (i) Pending Confirmation resolution via conversational "да" / "нет"
            if mgr_state.pending_confirmation:
                if ConversationManagerEngine.is_confirm_intent(text):
                    pending = mgr_state.pending_confirmation
                    mgr_state.pending_confirmation = None
                    act = pending.get("action")
                    t_id = pending.get("target_id")
                    if act == "delete_contact" and t_id:
                        stmt_c = select(Contact).where(Contact.id == t_id)
                        if not is_admin:
                            stmt_c = stmt_c.where(Contact.user_id == db_user.id)
                        contact = (await session.execute(stmt_c)).scalars().first()
                        if contact:
                            c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                            await session.delete(contact)
                            await session.commit()
                            await self.send_message(
                                chat_id,
                                f"Готово — удалил карточку и переписку с <b>{c_name}</b>.",
                                reply_markup={"inline_keyboard": [[{"text": "💬 К списку чатов", "callback_data": "menu_leads_chats"}]]},
                                parse_mode="HTML",
                            )
                            return
                    elif act == "cancel_sub":
                        await SubscriptionService.cancel_subscription(session, db_user.id)
                        await self.send_message(
                            chat_id,
                            "Подписка отменена, автоответы остановлены. Когда понадобится снова — можно включить в разделе оплаты.",
                            reply_markup={"inline_keyboard": [[{"text": "💳 Тарифы", "callback_data": "menu_buy_sub"}], [{"text": "🏠 Главное меню", "callback_data": "menu_main"}]]},
                            parse_mode="HTML",
                        )
                        return
                elif ConversationManagerEngine.is_cancel_intent(text):
                    mgr_state.pending_confirmation = None
                    await self.send_message(
                        chat_id,
                        self.manager.pick_variation(user_id, ConversationManagerEngine.CANCEL_VARIATIONS),
                        reply_markup=self.build_main_menu_keyboard(is_admin),
                        parse_mode="HTML",
                    )
                    return

            # (ii) Resume interrupted flow when user says "да" / "продолжим" / "yes"
            if mgr_state.interrupted_flow and ConversationManagerEngine.is_confirm_intent(text):
                resumed_raw, _ = mgr_state.resume_interrupted()
                if resumed_raw:
                    self._user_input_states[user_id] = resumed_raw
                    base_f = resumed_raw.split(":", 1)[0] if ":" in resumed_raw else resumed_raw
                    lbl = FLOW_LABELS.get(base_f, "нужные данные")
                    await self.send_message(
                        chat_id,
                        f"Возвращаемся к настройке — отправь <b>{lbl}</b>:",
                        reply_markup=self.build_cancel_flow_kb(mgr_state.current_menu or "menu_main"),
                        parse_mode="HTML",
                    )
                    return

            # (iii) Contextual Correction ("make it 23 instead", "давай лучше Казань", "поменяй на 14")
            corr_val = ConversationManagerEngine.extract_correction_value(text)
            if corr_val:
                corr_res = await self.manager.apply_contextual_correction(
                    session=session,
                    user_id=user_id,
                    db_user_id=db_user.id,
                    new_value=corr_val,
                )
                if corr_res:
                    msg_c, kb_c = _attach_resume_if_interrupted(corr_res[0], corr_res[1])
                    await self.send_message(chat_id, msg_c, reply_markup=kb_c, parse_mode="HTML")
                    return

            # (iv) "Check the latest one" / "покажи последнего" / "кто последний писал"
            if ConversationManagerEngine.is_latest_contact_intent(text):
                contacts = await StatsService.get_chat_leads(session, db_user.id)
                if not contacts and is_admin:
                    stmt_all = select(Contact).order_by(Contact.last_incoming_at.desc(), Contact.id.desc())
                    contacts = list((await session.execute(stmt_all)).scalars().all())
                if not contacts:
                    msg_l, kb_l = _attach_resume_if_interrupted(
                        "Сейчас в базе пока нет ни одного начатого диалога. Как только напишет первый клиент — он сразу появится здесь.",
                        {"inline_keyboard": [[{"text": "📱 Мои аккаунты", "callback_data": "menu_accounts"}], [{"text": "🏠 Главное меню", "callback_data": "menu_main"}]]},
                    )
                    await self.send_message(chat_id, msg_l, reply_markup=kb_l, parse_mode="HTML")
                    return
                latest_c = contacts[0]
                mgr_state.last_entity_type = "contact"
                mgr_state.last_entity_id = latest_c.id
                card_txt, card_kb = self.build_contact_card_payload(latest_c)
                msg_l, kb_l = _attach_resume_if_interrupted(
                    f"Вот последний активный диалог:\n\n{card_txt}",
                    card_kb,
                )
                await self.send_message(chat_id, msg_l, reply_markup=kb_l, parse_mode="HTML")
                return

            # (v) Frustrated / Troubleshooting ("почему опять не работает", "бот молчит", "сломалось")
            if mgr_state.user_style == "frustrated" or ConversationManagerEngine.is_navigation_or_question_interruption(text) == "frustrated":
                has_key = bool(settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY"))
                diag_txt, diag_kb = await self.manager.build_system_diagnostics_reply(
                    session=session,
                    db_user_id=db_user.id,
                    is_admin=is_admin,
                    has_ai_key=has_key,
                )
                msg_d, kb_d = _attach_resume_if_interrupted(diag_txt, diag_kb)
                await self.send_message(chat_id, msg_d, reply_markup=kb_d, parse_mode="HTML")
                return

            # (vi) Natural Manager Navigation without slash commands ("статистика", "чаты", "подписка", "мои аккаунты")
            nav_intent = ConversationManagerEngine.is_navigation_or_question_interruption(text)
            if nav_intent == "stats":
                stats = await StatsService.get_overview(session, db_user.id)
                report = AdminNotifier.format_stats_report(stats)
                kb_s = {
                    "inline_keyboard": [
                        [{"text": "📊 Детальная статистика и CSV", "callback_data": "menu_leads_stats_today"}],
                        [{"text": "🔥 Список горячих лидов", "callback_data": "menu_leads"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                    ]
                }
                msg_s, kb_s = _attach_resume_if_interrupted(report, kb_s)
                await self.send_message(chat_id, msg_s, reply_markup=kb_s, parse_mode="HTML")
                return
            elif nav_intent == "chats":
                crm_txt, crm_kb = await self.build_chats_crm_payload(
                    session=session,
                    db_user_id=db_user.id,
                    is_admin=is_admin,
                    filter_key=mgr_state.chat_filter,
                    page=1,
                    search_query=mgr_state.chat_search_query,
                )
                msg_c, kb_c = _attach_resume_if_interrupted(crm_txt, crm_kb)
                await self.send_message(chat_id, msg_c, reply_markup=kb_c, parse_mode="HTML")
                return
            elif nav_intent == "sub":
                sub = await SubscriptionService.get_active_subscription(session, db_user.id)
                sub_info = SubscriptionService.get_detailed_status(sub)
                if sub_info["is_active"]:
                    sub_txt = (
                        f"Подписка активна: <b>{sub_info['plan_name']}</b> (осталось <b>{sub_info['remaining_str']}</b>, "
                        f"до <code>{sub_info['expires_at_str']}</code> МСК). Лимит: до {sub_info['max_accounts']} акк."
                    )
                else:
                    sub_txt = "Сейчас активной подписки нет. Можно подключить тариф ниже:"
                msg_sub, kb_sub = _attach_resume_if_interrupted(
                    sub_txt, self.build_my_sub_keyboard(sub_info["is_active"])
                )
                await self.send_message(chat_id, msg_sub, reply_markup=kb_sub, parse_mode="HTML")
                return
            elif nav_intent == "accounts":
                accounts = await AccountService.get_user_accounts(session, db_user.id)
                personas = {a.id: await AccountService.ensure_account_persona(session, a) for a in accounts}
                acc_txt = (
                    f"Подключено аккаунтов: <b>{len(accounts)}</b>. Выбери аккаунт для настройки:"
                    if accounts
                    else "Пока нет подключенных аккаунтов Telegram Business. Создай ссылку ниже:"
                )
                acc_kb = (
                    self.build_accounts_keyboard(accounts, personas)
                    if accounts
                    else {"inline_keyboard": [[{"text": "➕ Подключить аккаунт", "callback_data": "menu_invites"}], [{"text": "◀️ В меню", "callback_data": "menu_main"}]]}
                )
                msg_a, kb_a = _attach_resume_if_interrupted(acc_txt, acc_kb)
                await self.send_message(chat_id, msg_a, reply_markup=kb_a, parse_mode="HTML")
                return
            elif nav_intent == "menu":
                sub = await SubscriptionService.get_active_subscription(session, db_user.id)
                sub_info = SubscriptionService.get_detailed_status(sub)
                msg_m, kb_m = _attach_resume_if_interrupted(
                    self.format_main_menu_text(is_admin, sub_info),
                    self.build_main_menu_keyboard(is_admin),
                )
                await self.send_message(chat_id, msg_m, reply_markup=kb_m, parse_mode="HTML")
                return

            # (vii) Concise Manager Responses for short acknowledgments ("спасибо", "wait", "отмена")
            if ConversationManagerEngine.is_thanks_intent(text):
                reply_thx = self.manager.pick_variation(user_id, ConversationManagerEngine.THANKS_VARIATIONS)
                msg_t, kb_t = _attach_resume_if_interrupted(reply_thx, None)
                await self.send_message(chat_id, msg_t, reply_markup=kb_t, parse_mode="HTML")
                return
            if ConversationManagerEngine.is_wait_intent(text):
                reply_w = self.manager.pick_variation(user_id, ConversationManagerEngine.WAIT_VARIATIONS)
                msg_w, kb_w = _attach_resume_if_interrupted(reply_w, None)
                await self.send_message(chat_id, msg_w, reply_markup=kb_w, parse_mode="HTML")
                return
            if ConversationManagerEngine.is_cancel_intent(text):
                mgr_state.clear_flow()
                mgr_state.interrupted_flow = None
                reply_c = self.manager.pick_variation(user_id, ConversationManagerEngine.CANCEL_VARIATIONS)
                await self.send_message(
                    chat_id,
                    reply_c,
                    reply_markup=self.build_main_menu_keyboard(is_admin),
                    parse_mode="HTML",
                )
                return

            # 5. Direct companion conversational mode in @YourHelperW_bot
            from companion.services.health_manager import HealthManager
            if HealthManager.is_paused():
                logger.warning("[TELEGRAM_ENGINE] System is in SAFE PAUSED state (%s). Suppressing reply to %s", HealthManager.get_paused_reason(), user_id)
                return

            v_data = message.get("voice") or message.get("audio") or message.get("video_note")
            v_file_id = v_data.get("file_id") if v_data else None
            is_incoming_voice = bool(v_data)
            voice_duration = v_data.get("duration", 0) if v_data else 0
            msg_id = message.get("message_id")

            try:
                res = await UserbotHandler.process_incoming_message(
                    session=session,
                    user_id=db_user.id,
                    sender_tg_id=user_id,
                    sender_username=user_tg.get("username"),
                    sender_first_name=user_tg.get("first_name"),
                    text=text,
                    now=datetime.now(timezone.utc),
                    auto_handover_on_date=True,
                    is_voice=is_incoming_voice,
                    voice_duration=voice_duration,
                    voice_file_id=v_file_id,
                    telegram_message_id=msg_id,
                )
            except Exception as e:
                logger.exception("Error processing message in TelegramBotEngine: %s", e)
                HealthManager.report_provider_error(e, context="telegram_engine_handle_message")
                return
            action = res.get("action")
            reply_text = res.get("reply_text")
            voice_tag = res.get("voice_tag")
            response_plan = res.get("response_plan") or {}
            planned_messages = response_plan.get("messages") or [
                {"text": b.strip(), "reply_to_message_id": None}
                for b in (reply_text or "").split("||")
                if b.strip()
            ]
            planned_sticker = response_plan.get("sticker")

            contact = res.get("contact")
            c_name = (contact.first_name if contact else None) or user_tg.get("first_name") or "Собеседник"
            c_user = (contact.username if contact else None) or user_tg.get("username")
            c_id = contact.id if contact else user_id
            alert_msg = f"🎙 [ГС {voice_duration} сек]: «{text}»" if is_incoming_voice else text

            detected_event = res.get("detected_event")
            meetup_intent = IntentService.detect_meetup_proposal(text)
            meetup_agreed = IntentService.detect_meetup_agreement(text)

            if detected_event == "QUEST_AGREED" or (meetup_agreed and getattr(contact, "funnel_stage", "") in ("date_proposed", "quest_offered", "quest_agreed")):
                if contact and not getattr(contact, "alert_quest_sent", False):
                    contact.alert_quest_sent = True
                    contact.funnel_stage = "quest_agreed"
                    contact.status = ContactStatus.LEAD_MEETING
                    if not contact.lead_detected_at:
                        contact.lead_detected_at = datetime.now(timezone.utc)
                    if not contact.lead_summary:
                        contact.lead_summary = f"Согласился на квест: «{alert_msg[:100]}»"
                    await session.commit()
                    await AdminNotifier.send_quest_agreed_alarm(
                        owner_tg_id=self.admin_id,
                        contact_name=c_name,
                        contact_username=c_user,
                        contact_tg_id=user_id,
                        user_message=alert_msg,
                        contact_id=c_id,
                    )
            elif detected_event == "DATE_PROPOSED" or meetup_intent.is_proposal or meetup_agreed:
                if contact and not getattr(contact, "alert_date_sent", False):
                    contact.alert_date_sent = True
                    contact.funnel_stage = "date_proposed"
                    contact.status = ContactStatus.LEAD_MEETING
                    if not contact.lead_detected_at:
                        contact.lead_detected_at = datetime.now(timezone.utc)
                    if not contact.lead_summary:
                        contact.lead_summary = f"Предложение встречи: «{alert_msg[:100]}»"
                    await session.commit()
                    await AdminNotifier.send_date_proposed_alert(
                        owner_tg_id=self.admin_id,
                        contact_name=c_name,
                        contact_username=c_user,
                        contact_tg_id=user_id,
                        user_message=alert_msg,
                        contact_id=c_id,
                    )

            if res.get("detected_violation"):
                await AdminNotifier.send_insult_alert(
                    owner_tg_id=self.admin_id,
                    contact_name=c_name,
                    contact_username=c_user,
                    contact_tg_id=user_id,
                    user_message=text,
                    analysis_reason=res.get("violation_analysis") or "Выявлены грубые выражения или оскорбления.",
                    contact_id=c_id,
                    has_voice=is_incoming_voice,
                    voice_duration=voice_duration,
                    voice_file_id=v_file_id,
                )

            if action == "sleeping_overnight":
                sleep_state = res.get("sleep_state", {})
                time_str = sleep_state.get("local_time_str", "ночь")
                await self.send_message(
                    chat_id,
                    f"🌙 <i>Девушка уже спит (локальное время {time_str}). Бот ответит утром после пробуждения (~09:00)!</i>",
                    parse_mode="HTML",
                )
                return

            if action in ("ignored", "silence", "paused_due_to_health", "paused_due_to_error") or not reply_text:
                return

            if reply_text:
                from companion.services.typing_simulator import TypingSimulator
                is_incoming_voice = bool("voice" in message or "audio" in message or "video_note" in message)
                if voice_tag or VoiceService.should_send_voice(user_message=text, bot_reply=reply_text, is_incoming_voice=is_incoming_voice):
                    if self.client:
                        await self.client.post(f"{self.base_url}/sendChatAction", json={"chat_id": chat_id, "action": "record_voice"})
                    await asyncio.sleep(random.uniform(1.2, 2.0))
                    clean_v = reply_text.replace("||", " ")
                    v_text = VoiceService.shorten_for_voice(clean_v, max_chars=180)
                    sent = await VoiceService.send_telegram_voice_note(
                        bot_token=self.token, chat_id=chat_id, text=v_text, voice_tag=voice_tag
                    )
                    if not sent:
                        for msg_item in planned_messages:
                            bubble = msg_item["text"]
                            if self.client:
                                await self.client.post(f"{self.base_url}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
                            typing_dur = TypingSimulator.calculate_typing_duration(bubble)
                            await asyncio.sleep(typing_dur)
                            await self.send_message(
                                chat_id,
                                bubble,
                                reply_to_message_id=msg_item.get("reply_to_message_id"),
                            )
                else:
                    for msg_item in planned_messages:
                        bubble = msg_item["text"]
                        if self.client:
                            await self.client.post(f"{self.base_url}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
                        typing_dur = TypingSimulator.calculate_typing_duration(bubble)
                        await asyncio.sleep(typing_dur)
                        await self.send_message(
                            chat_id,
                            bubble,
                            reply_to_message_id=msg_item.get("reply_to_message_id"),
                        )
                    if planned_sticker and planned_sticker.get("file_id"):
                        await asyncio.sleep(0.4)
                        await self.send_sticker(chat_id, planned_sticker["file_id"])
                return

    async def handle_callback(self, cq: Dict[str, Any]):
        cq_id = cq["id"]
        data = cq.get("data", "")
        message = cq.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        msg_id = message.get("message_id")
        from_user = cq.get("from", {})
        user_tg_id = from_user.get("id")
        is_admin = self.is_admin_user(user_tg_id)

        async with async_session_maker() as session:
            db_user = await self.get_or_create_user(session, from_user)
            db_user_id = db_user.id
            mgr_state = self.manager.get_state(user_tg_id)

            if data == "cancel_input_flow":
                self._user_input_states.pop(user_tg_id, None)
                mgr_state.clear_flow()
                mgr_state.interrupted_flow = None
                mgr_state.pending_confirmation = None
                await self.answer_callback_query(cq_id, "Ок, ввод отменен")
                sub = await SubscriptionService.get_active_subscription(session, db_user_id)
                sub_info = SubscriptionService.get_detailed_status(sub)
                text = self.format_main_menu_text(is_admin, sub_info)
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_main_menu_keyboard(is_admin), parse_mode="HTML"
                )
                return

            # Photo Pool Callbacks (Owner/Admin only)
            if data.startswith("photos_") or data.startswith("photo_"):
                if not is_admin:
                    await self.answer_callback_query(cq_id, "⛔ Доступ только для владельца")
                    return

            if data in ("photos_stats", "photos_refresh"):
                from companion.services.photo_pool_service import PhotoAdminService, PhotoPoolService
                stats = await PhotoPoolService.get_pool_stats(session)
                report = PhotoAdminService.format_stats_message(stats)
                kb = {
                    "inline_keyboard": [
                        [{"text": "📋 Список фото", "callback_data": "photos_list_0"}],
                        [{"text": "➕ Как загрузить фото", "callback_data": "photos_upload_help"}],
                        [{"text": "🔄 Обновить", "callback_data": "photos_refresh"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                    ]
                }
                await self.answer_callback_query(cq_id)
                await self.edit_message(chat_id, msg_id, report, reply_markup=kb, parse_mode="HTML")
                return

            if data.startswith("photos_list_"):
                page = int(data.split("_")[-1])
                limit = 5
                offset = page * limit
                from companion.services.photo_pool_service import PhotoPoolService
                photos = await PhotoPoolService.list_photos(session, limit=limit, offset=offset)
                stats = await PhotoPoolService.get_pool_stats(session)
                total = stats["total_photos"]

                if not photos:
                    text_p = "📸 <b>В пуле пока нет загруженных фото.</b>\n\nОтправьте фото в чат с ботом с подписью <code>#photo [категория]</code> для добавления."
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Как загрузить", "callback_data": "photos_upload_help"}],
                            [{"text": "📊 Статистика пула", "callback_data": "photos_stats"}],
                            [{"text": "◀️ В меню", "callback_data": "menu_main"}],
                        ]
                    }
                else:
                    text_p = f"📸 <b>СПИСОК ФОТО В ПУЛЕ</b> (Стр. {page + 1} из {max(1, (total + limit - 1) // limit)}):\n\n"
                    kb_rows = []
                    for p in photos:
                        status_emoji = "🟢" if p.enabled else "⚪"
                        tags_str = ", ".join(p.get_tags()) or "нет"
                        text_p += (
                            f"{status_emoji} <b>{p.photo_id}</b> [{p.category}]\n"
                            f"   📝 {p.description or 'Без описания'}\n"
                            f"   🏷 Теги: {tags_str} | Отправок: {p.times_used}\n\n"
                        )
                        btn_toggle = f"{'⏸ Выкл' if p.enabled else '▶️ Вкл'} {p.photo_id}"
                        kb_rows.append([
                            {"text": btn_toggle, "callback_data": f"photos_tog_{p.photo_id}_{page}"},
                            {"text": "🗑 Удалить", "callback_data": f"photos_del_{p.photo_id}_{page}"},
                        ])

                    nav_row = []
                    if page > 0:
                        nav_row.append({"text": "⬅️ Назад", "callback_data": f"photos_list_{page - 1}"})
                    if offset + limit < total:
                        nav_row.append({"text": "Вперед ➡️", "callback_data": f"photos_list_{page + 1}"})
                    if nav_row:
                        kb_rows.append(nav_row)

                    kb_rows.append([{"text": "📊 Статистика пула", "callback_data": "photos_stats"}])
                    kb_rows.append([{"text": "◀️ В меню", "callback_data": "menu_main"}])
                    kb = {"inline_keyboard": kb_rows}

                await self.answer_callback_query(cq_id)
                await self.edit_message(chat_id, msg_id, text_p, reply_markup=kb, parse_mode="HTML")
                return

            if data.startswith("photos_tog_"):
                tail = data[len("photos_tog_"):]
                pid, page_str = tail.rsplit("_", 1) if "_" in tail else (tail, "0")
                page = int(page_str) if page_str.isdigit() else 0
                from companion.services.photo_pool_service import PhotoPoolService
                curr = await PhotoPoolService.get_photo(session, pid)
                if curr:
                    new_st = not curr.enabled
                    await PhotoPoolService.toggle_photo_status(session, pid, new_st)
                    await self.answer_callback_query(cq_id, f"Статус {pid}: {'Включено' if new_st else 'Отключено'}")
                cq["data"] = f"photos_list_{page}"
                await self.handle_callback(cq)
                return

            if data.startswith("photos_del_"):
                tail = data[len("photos_del_"):]
                pid, page_str = tail.rsplit("_", 1) if "_" in tail else (tail, "0")
                page = int(page_str) if page_str.isdigit() else 0
                from companion.services.photo_pool_service import PhotoPoolService
                await PhotoPoolService.delete_photo(session, pid, delete_file=True)
                await self.answer_callback_query(cq_id, f"Фото {pid} удалено")
                cq["data"] = f"photos_list_{page}"
                await self.handle_callback(cq)
                return

            if data == "photos_upload_help":
                help_upload = (
                    "➕ <b>КАК ЗАГРУЗИТЬ ФОТО В PHOTO POOL</b>\n\n"
                    "Просто отправьте фотографию сюда в чат с ботом с подписью (caption):\n\n"
                    "<code>#photo [категория] [теги_через_запятую] [описание]</code>\n\n"
                    "<b>Примеры:</b>\n"
                    "• <code>#photo selfie morning,casual Утреннее селфи дома</code>\n"
                    "• <code>#photo coffee cafe,relax В любимой кофейне на Арбате</code>\n"
                    "• <code>#photo outdoors walk,autumn Прогулка в парке</code>\n\n"
                    "Доступные категории: <code>selfie, casual, outdoors, home, food, coffee, travel, city, pet, outfit, evening, daytime, weather, hobby, random_life, mood, event</code>\n\n"
                    "🔒 Бот автоматически вычислит SHA-256 и перцептивный dHash для дедупликации, "
                    "сохранит фото на диск и свяжет с персоной."
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "📋 Список фото", "callback_data": "photos_list_0"}],
                        [{"text": "📊 Статистика пула", "callback_data": "photos_stats"}],
                        [{"text": "◀️ В меню", "callback_data": "menu_main"}],
                    ]
                }
                await self.answer_callback_query(cq_id)
                await self.edit_message(chat_id, msg_id, help_upload, reply_markup=kb, parse_mode="HTML")
                return

            if data.startswith("photos_acc_"):
                self._user_input_states.pop(user_tg_id, None)
                await self.answer_callback_query(cq_id)
                acc_code = data.replace("photos_acc_", "")
                from companion.services.photo_pool_service import PhotoPoolService

                if acc_code in ("default", "0"):
                    stmt = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    if not persona:
                        persona = Persona(user_id=db_user_id)
                        session.add(persona)
                        await session.commit()
                        await session.refresh(persona)
                    model_name = persona.name or "Общий шаблон"
                    acc_title = "⭐ Общий шаблон по умолчанию"
                    target_persona_id = persona.id
                    back_cb = "legend_acc_default"
                else:
                    acc_id = int(acc_code)
                    stmt_acc = select(AccountSession).where(AccountSession.id == acc_id, AccountSession.user_id == db_user_id)
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    if not acc:
                        await self.answer_callback_query(cq_id, "Аккаунт не найден", show_alert=True)
                        return
                    persona = await AccountService.ensure_account_persona(session, acc)
                    model_name = persona.name or acc.first_name or "Девушка"
                    acc_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")
                    target_persona_id = persona.id
                    back_cb = f"legend_acc_{acc.id}"

                stats = await PhotoPoolService.get_persona_photo_stats(session, target_persona_id)
                total = stats.get("total_photos", 0)
                active = stats.get("active_photos", 0)
                disabled = stats.get("disabled_photos", 0)
                cats = stats.get("categories", {})
                cats_str = ", ".join([f"{k} ({v})" for k, v in cats.items()]) if cats else "пока нет"

                card_text = (
                    f"📸 <b>ФОТОГРАФИИ МОДЕЛИ: {acc_title}</b>\n\n"
                    f"👤 <b>Анкета:</b> {model_name}, {persona.age or 22} лет\n"
                    f"📍 <b>Локация:</b> {persona.city or 'Москва'}\n\n"
                    f"📊 <b>Статус галереи:</b>\n"
                    f"• Всего загружено: <b>{total}</b> шт.\n"
                    f"• Готовы к отправке: <b>{active}</b> 🟢\n"
                    f"• Отключено: <b>{disabled}</b> ⚪\n"
                    f"• Категории: <code>{cats_str}</code>\n\n"
                    f"🔒 <i>Все фото в этой секции привязаны строго к анкете {model_name}!\n"
                    f"ИИ отправляет их собеседникам от имени этой модели с защитой от повторов (12 часов кулдаун на каждый чат).</i>\n\n"
                    f"Выберите действие кнопками ниже:"
                )

                kb_rows = [
                    [{"text": "➕ Загрузить фото для модели", "callback_data": f"photo_up_acc_{acc_code}"}],
                ]
                if total > 0:
                    kb_rows.append([
                        {"text": f"📋 Список фото ({total} шт.)", "callback_data": f"photo_list_acc_{acc_code}_0"}
                    ])
                    kb_rows.append([
                        {"text": "🧹 Очистить все фото модели", "callback_data": f"photo_clr_acc_{acc_code}"}
                    ])
                kb_rows.append([{"text": "◀️ Назад к анкете модели", "callback_data": back_cb}])

                await self.edit_message(chat_id, msg_id, card_text, reply_markup={"inline_keyboard": kb_rows}, parse_mode="HTML")
                return

            if data.startswith("photo_up_acc_"):
                await self.answer_callback_query(cq_id)
                acc_code = data.replace("photo_up_acc_", "")
                self._user_input_states[user_tg_id] = f"photo_upload_acc:{acc_code}"

                if acc_code in ("default", "0"):
                    model_title = "Общий шаблон"
                else:
                    acc_id = int(acc_code)
                    stmt_acc = select(AccountSession).where(AccountSession.id == acc_id, AccountSession.user_id == db_user_id)
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    model_title = (acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")) if acc else "Девушка"

                text = (
                    f"📸 <b>Режим загрузки фото для модели {model_title}</b>\n\n"
                    f"Просто пришлите сюда в чат фотографию (можно сразу пачкой или по одной)!\n\n"
                    f"💡 <b>Удобно:</b>\n"
                    f"• Не нужно вводить никаких команд.\n"
                    f"• Можно отправить фото <b>с подписью</b> (например: <code>селфи дома</code> или <code>кофе</code>).\n"
                    f"• Или отправьте фото <b>без подписи</b> — оно автоматически сохранится в категорию <code>casual</code>.\n"
                    f"• Все фото автоматически привяжутся к анкете <b>{model_title}</b>."
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "◀️ Назад к фото модели", "callback_data": f"photos_acc_{acc_code}"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")
                return

            if data.startswith("photo_list_acc_"):
                tail = data[len("photo_list_acc_"):]
                parts = tail.rsplit("_", 1)
                acc_code = parts[0]
                page = int(parts[1]) if (len(parts) > 1 and parts[1].isdigit()) else 0

                from companion.services.photo_pool_service import PhotoPoolService

                if acc_code in ("default", "0"):
                    stmt = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    model_title = "Общий шаблон"
                    target_persona_id = persona.id if persona else None
                else:
                    acc_id = int(acc_code)
                    stmt_acc = select(AccountSession).where(AccountSession.id == acc_id, AccountSession.user_id == db_user_id)
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    if not acc:
                        await self.answer_callback_query(cq_id, "Аккаунт не найден", show_alert=True)
                        return
                    persona = await AccountService.ensure_account_persona(session, acc)
                    model_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")
                    target_persona_id = persona.id

                limit = 5
                offset = page * limit
                photos = await PhotoPoolService.list_photos(session, persona_id=target_persona_id, limit=limit, offset=offset)
                stats = await PhotoPoolService.get_persona_photo_stats(session, target_persona_id)
                total = stats.get("total_photos", 0)

                if not photos:
                    text_p = (
                        f"📸 <b>У модели {model_title} пока нет фотографий.</b>\n\n"
                        f"Нажмите кнопку <b>«➕ Загрузить фото»</b> ниже или просто пришлите фото в чат!"
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Загрузить фото", "callback_data": f"photo_up_acc_{acc_code}"}],
                            [{"text": "◀️ Назад к фото модели", "callback_data": f"photos_acc_{acc_code}"}],
                        ]
                    }
                else:
                    total_pages = max(1, (total + limit - 1) // limit)
                    text_p = (
                        f"📸 <b>ФОТОГРАФИИ МОДЕЛИ: {model_title}</b>\n"
                        f"<i>Страница {page + 1} из {total_pages} (Всего: {total} фото)</i>\n\n"
                    )
                    kb_rows = []
                    for idx, p in enumerate(photos, start=offset + 1):
                        st_emoji = "🟢" if p.enabled else "⚪"
                        st_text = "Активно" if p.enabled else "Отключено"
                        tags_str = ", ".join(p.get_tags()) or "нет"
                        desc_str = p.description or "Без описания"
                        text_p += (
                            f"{st_emoji} <b>Фото #{idx}:</b> <code>{p.photo_id}</code> [{p.category}]\n"
                            f"   📝 {desc_str}\n"
                            f"   🏷 Теги: {tags_str} | Статус: {st_text} | Отправок: {p.times_used}\n\n"
                        )
                        btn_toggle = f"{'⏸ Выкл' if p.enabled else '▶️ Вкл'} #{idx}"
                        kb_rows.append([
                            {"text": btn_toggle, "callback_data": f"photo_tog_acc_{p.photo_id}_{acc_code}_{page}"},
                            {"text": f"🗑 Удалить #{idx}", "callback_data": f"photo_del_acc_{p.photo_id}_{acc_code}_{page}"},
                        ])

                    nav_row = []
                    if page > 0:
                        nav_row.append({"text": "⬅️ Назад", "callback_data": f"photo_list_acc_{acc_code}_{page - 1}"})
                    if offset + limit < total:
                        nav_row.append({"text": "Вперед ➡️", "callback_data": f"photo_list_acc_{acc_code}_{page + 1}"})
                    if nav_row:
                        kb_rows.append(nav_row)

                    kb_rows.append([{"text": "➕ Загрузить ещё фото", "callback_data": f"photo_up_acc_{acc_code}"}])
                    kb_rows.append([{"text": "◀️ Назад к фото модели", "callback_data": f"photos_acc_{acc_code}"}])
                    kb = {"inline_keyboard": kb_rows}

                await self.answer_callback_query(cq_id)
                await self.edit_message(chat_id, msg_id, text_p, reply_markup=kb, parse_mode="HTML")
                return

            if data.startswith("photo_tog_acc_"):
                tail = data[len("photo_tog_acc_"):]
                parts = tail.split("_")
                page = int(parts[-1]) if parts[-1].isdigit() else 0
                acc_code = parts[-2]
                pid = "_".join(parts[:-2])
                from companion.services.photo_pool_service import PhotoPoolService
                curr = await PhotoPoolService.get_photo(session, pid)
                if curr:
                    new_st = not curr.enabled
                    await PhotoPoolService.toggle_photo_status(session, pid, new_st)
                    await self.answer_callback_query(cq_id, f"Статус: {'Включено' if new_st else 'Отключено'}")
                else:
                    await self.answer_callback_query(cq_id, "Фото не найдено")
                cq["data"] = f"photo_list_acc_{acc_code}_{page}"
                await self.handle_callback(cq)
                return

            if data.startswith("photo_del_acc_"):
                tail = data[len("photo_del_acc_"):]
                parts = tail.split("_")
                page = int(parts[-1]) if parts[-1].isdigit() else 0
                acc_code = parts[-2]
                pid = "_".join(parts[:-2])
                from companion.services.photo_pool_service import PhotoPoolService
                await PhotoPoolService.delete_photo(session, pid, delete_file=True)
                await self.answer_callback_query(cq_id, f"Фото {pid} удалено")
                cq["data"] = f"photo_list_acc_{acc_code}_{page}"
                await self.handle_callback(cq)
                return

            if data.startswith("photo_clr_acc_"):
                await self.answer_callback_query(cq_id)
                acc_code = data.replace("photo_clr_acc_", "")

                if acc_code in ("default", "0"):
                    model_title = "Общий шаблон"
                else:
                    acc_id = int(acc_code)
                    stmt_acc = select(AccountSession).where(AccountSession.id == acc_id, AccountSession.user_id == db_user_id)
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    model_title = (acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")) if acc else "Девушка"

                text = (
                    f"⚠️ <b>ОЧИСТКА ВСЕХ ФОТО МОДЕЛИ: {model_title}</b>\n\n"
                    f"Вы действительно хотите безвозвратно удалить <b>ВСЕ</b> загруженные фотографии этой модели из базы и с диска?\n\n"
                    f"<i>Это действие нельзя отменить.</i>"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "🗑 Да, удалить все фото модели", "callback_data": f"photo_clr_cfm_{acc_code}"}],
                        [{"text": "◀️ Отмена", "callback_data": f"photos_acc_{acc_code}"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")
                return

            if data.startswith("photo_clr_cfm_"):
                acc_code = data.replace("photo_clr_cfm_", "")
                from companion.services.photo_pool_service import PhotoPoolService

                if acc_code in ("default", "0"):
                    stmt = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    model_title = "Общий шаблон"
                    target_persona_id = persona.id if persona else None
                else:
                    acc_id = int(acc_code)
                    stmt_acc = select(AccountSession).where(AccountSession.id == acc_id, AccountSession.user_id == db_user_id)
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    if not acc:
                        await self.answer_callback_query(cq_id, "Аккаунт не найден", show_alert=True)
                        return
                    persona = await AccountService.ensure_account_persona(session, acc)
                    model_title = acc.first_name or (f"@{acc.username}" if acc.username else f"#{acc.id}")
                    target_persona_id = persona.id

                del_count = 0
                if target_persona_id is not None:
                    del_count = await PhotoPoolService.clear_persona_photos(session, target_persona_id, delete_files=True)

                await self.answer_callback_query(cq_id, f"Удалено {del_count} фото")
                text = (
                    f"✅ <b>Все фотографии модели {model_title} успешно удалены!</b>\n\n"
                    f"Удалено файлов: <b>{del_count}</b> шт."
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "➕ Загрузить новые фото", "callback_data": f"photo_up_acc_{acc_code}"}],
                        [{"text": "◀️ Назад к анкете модели", "callback_data": f"legend_acc_{acc_code}"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")
                return

            if data == "resume_interrupted_flow":
                resumed_raw, _ = mgr_state.resume_interrupted()
                await self.answer_callback_query(cq_id, "▶️ Продолжаем ввод")
                if resumed_raw:
                    self._user_input_states[user_tg_id] = resumed_raw
                    base_f = resumed_raw.split(":", 1)[0] if ":" in resumed_raw else resumed_raw
                    lbl = FLOW_LABELS.get(base_f, "нужные данные")
                    await self.send_message(
                        chat_id,
                        f"Продолжаем настройку — отправь <b>{lbl}</b>:",
                        reply_markup=self.build_cancel_flow_kb(mgr_state.current_menu or "menu_main"),
                        parse_mode="HTML",
                    )
                return

            # ------------------------------------------
            # Daivinchik Dating Profile Bio Generation (Sections 16-20)
            # ------------------------------------------
            if data.startswith("daiv_"):
                import hashlib
                import json
                from companion.services.daivinchik_service import DaivinchikService
                from companion.models import DaivinchikProfile
                parts = data.split("_")
                action = parts[1]

                if action == "regen" and len(parts) >= 3:
                    prof_id = int(parts[2])
                    stmt = select(DaivinchikProfile).where(DaivinchikProfile.id == prof_id)
                    prof = (await session.execute(stmt)).scalars().first()
                    if prof:
                        facts = json.loads(prof.facts_json or "{}")
                        new_seed = hashlib.md5(f"{prof.profile_hash}_{random.random()}".encode("utf-8")).hexdigest()
                        synth = DaivinchikService.generate_consistent_persona_details(facts, new_seed)
                        prof.generated_details_json = json.dumps(synth, ensure_ascii=False)
                        prof.current_bio = DaivinchikService.generate_bio(facts, synth, style_variant=prof.style_variant)
                        await session.commit()
                        await self.answer_callback_query(cq_id, "🔄 Анкета обновлена!")
                        card_text = DaivinchikService.format_profile_card(prof)
                        kb = DaivinchikService.build_bio_actions_keyboard(prof.id)
                        await self.edit_message(chat_id, msg_id, card_text, reply_markup=kb, parse_mode="HTML")
                    else:
                        await self.answer_callback_query(cq_id, "⚠️ Анкета не найдена", show_alert=True)
                    return

                elif action == "style" and len(parts) >= 4:
                    prof_id = int(parts[2])
                    new_style = parts[3]
                    stmt = select(DaivinchikProfile).where(DaivinchikProfile.id == prof_id)
                    prof = (await session.execute(stmt)).scalars().first()
                    if prof:
                        facts = json.loads(prof.facts_json or "{}")
                        synth = json.loads(prof.generated_details_json or "{}")
                        prof.current_bio = DaivinchikService.generate_bio(facts, synth, style_variant=new_style)
                        prof.style_variant = new_style
                        await session.commit()
                        style_names = {"natural": "Естественный", "short": "Короткий", "playful": "Игривый", "serious": "Серьезный"}
                        await self.answer_callback_query(cq_id, f"✅ Стиль: {style_names.get(new_style, new_style)}")
                        card_text = DaivinchikService.format_profile_card(prof)
                        kb = DaivinchikService.build_bio_actions_keyboard(prof.id)
                        await self.edit_message(chat_id, msg_id, card_text, reply_markup=kb, parse_mode="HTML")
                    else:
                        await self.answer_callback_query(cq_id, "⚠️ Анкета не найдена", show_alert=True)
                    return

                elif action == "copy" and len(parts) >= 3:
                    prof_id = int(parts[2])
                    stmt = select(DaivinchikProfile).where(DaivinchikProfile.id == prof_id)
                    prof = (await session.execute(stmt)).scalars().first()
                    if prof:
                        await self.answer_callback_query(cq_id, "📋 Текст отправлен сообщением ниже")
                        await self.send_message(
                            chat_id,
                            f"📋 <b>Текст анкеты для копирования в Дайвинчик:</b>\n\n<code>{prof.current_bio}</code>",
                            parse_mode="HTML"
                        )
                    else:
                        await self.answer_callback_query(cq_id, "⚠️ Анкета не найдена", show_alert=True)
                    return

            # ------------------------------------------
            # Main Menu
            # ------------------------------------------
            if data == "menu_main":
                mgr_state.set_menu("menu_main")
                await self.answer_callback_query(cq_id)
                sub = await SubscriptionService.get_active_subscription(session, db_user_id)
                sub_info = SubscriptionService.get_detailed_status(sub)
                text = self.format_main_menu_text(is_admin, sub_info)
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_main_menu_keyboard(is_admin), parse_mode="HTML"
                )

            # ------------------------------------------
            # My Subscription Center
            # ------------------------------------------
            elif data == "menu_my_sub":
                mgr_state.set_menu("menu_my_sub")
                await self.answer_callback_query(cq_id)
                sub = await SubscriptionService.get_active_subscription(session, db_user_id)
                sub_info = SubscriptionService.get_detailed_status(sub)

                if sub_info["is_active"]:
                    text = (
                        f"⭐ <b>УПРАВЛЕНИЕ ВАШЕЙ ПОДПИСКОЙ</b>\n\n"
                        f"📋 <b>Тарифный план:</b> {sub_info['plan_name']}\n"
                        f"⏳ <b>Действует до:</b> <code>{sub_info['expires_at_str']}</code> (МСК)\n"
                        f"⏱ <b>Осталось точно:</b> <b>{sub_info['remaining_str']}</b>\n"
                        f"📱 <b>Лимит аккаунтов:</b> до {sub_info['max_accounts']} акк.\n"
                        f"🎙 <b>Голосовых сегодня:</b> {sub_info['voice_used_today']} / {sub_info['max_daily_voice']}\n\n"
                        f"Вы можете в любой момент добавить время или отменить подписку:"
                    )
                else:
                    text = (
                        "⭐ <b>УПРАВЛЕНИЕ ВАШЕЙ ПОДПИСКОЙ</b>\n\n"
                        "❌ <b>У вас нет активной подписки.</b>\n\n"
                        "Для доступа к автоматизации чатов, выбору стилей и реалистичным войсам "
                        "выберите подходящий тариф в разделе «Добавить время / Оплата»."
                    )
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_my_sub_keyboard(sub_info["is_active"]), parse_mode="HTML"
                )

            elif data == "confirm_cancel_sub":
                mgr_state.pending_confirmation = {"action": "cancel_sub", "target_id": db_user_id}
                await self.answer_callback_query(cq_id)
                text = (
                    "⚠️ <b>ВЫ УВЕРЕНЫ, ЧТО ХОТИТЕ ОТМЕНИТЬ ПОДПИСКУ?</b>\n\n"
                    "После отмены:\n"
                    "• Автоматические ответы в чатах будут немедленно остановлены\n"
                    "• Генерация голосовых сообщений будет заблокирована\n"
                    "• Оставшееся время будет аннулировано\n\n"
                    "Подтвердите действие:"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": " Да, отменить подписку", "callback_data": "do_cancel_sub"}],
                        [{"text": "◀️ Нет, оставить", "callback_data": "menu_my_sub"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data == "do_cancel_sub":
                await SubscriptionService.cancel_subscription(session, db_user_id)
                await self.answer_callback_query(cq_id, "❌ Подписка отменена")
                text = (
                    "❌ <b>Ваша подписка успешно отменена.</b>\n\n"
                    "Автоматизация чатов остановлена. Вы можете снова возобновить работу "
                    "в любой момент через меню оплаты."
                )
                kb = {"inline_keyboard": [[{"text": "💳 Оформить подписку заново", "callback_data": "menu_buy_sub"}], [{"text": "◀️ В меню", "callback_data": "menu_main"}]]}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            # ------------------------------------------
            # CryptoBot (@CryptoBot) Payment Center
            # ------------------------------------------
            elif data == "menu_buy_sub":
                await self.answer_callback_query(cq_id)
                text = (
                    "💳 <b>ОПЛАТА ПОДПИСКИ ЧЕРЕЗ CRYPTOBOT (@CryptoBot)</b>\n\n"
                    "Выберите тарифный план:\n\n"
                    "• ⚡ <b>3 дня</b> — <b>3.00 USDT</b> (до 1 акк.)\n"
                    "• ⚡ <b>7 дней</b> — <b>7.00 USDT</b> (до 2 акк.)\n"
                    "• 💎 <b>14 дней</b> — <b>12.00 USDT</b> (до 5 акк.)\n"
                    "• 👑 <b>31 день</b> — <b>25.00 USDT</b> (до 10 акк.)\n\n"
                    "Оплата моментальная криптовалютой (USDT, TON, BTC) или чеком. "
                    "Подписка выдается <b>сразу же</b>, как только оплата получена!"
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup=self.build_buy_sub_keyboard(), parse_mode="HTML")

            elif data.startswith("buy_plan_"):
                plan_map = {
                    "buy_plan_3d": PlanDuration.DAYS_3,
                    "buy_plan_7d": PlanDuration.DAYS_7,
                    "buy_plan_14d": PlanDuration.DAYS_14,
                    "buy_plan_31d": PlanDuration.DAYS_31,
                }
                plan = plan_map.get(data, PlanDuration.DAYS_7)
                await self.answer_callback_query(cq_id, "⏳ Создаю счет в @CryptoBot...")

                invoice, pay_url = await CryptoPayService.create_invoice(
                    session=session, user_id=db_user_id, plan=plan
                )

                text = (
                    f"🧾 <b>СЧЁТ НА ОПЛАТУ #{invoice.invoice_id}</b>\n\n"
                    f"📋 <b>Тариф:</b> {plan.value} ({plan.days_count} дней)\n"
                    f"💵 <b>Сумма к оплате:</b> <b>{invoice.amount:.2f} USDT</b>\n"
                    f"🤖 <b>Способ оплаты:</b> @CryptoBot\n\n"
                    f"Нажмите кнопку ниже, чтобы оплатить в Telegram. "
                    f"После завершения платежа нажмите <b>«Проверить оплату»</b>:"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "💳 Оплатить в @CryptoBot", "url": pay_url}],
                        [{"text": "🔄 Проверить оплату", "callback_data": f"check_pay_{invoice.invoice_id}"}],
                        [{"text": "◀️ Назад к тарифам", "callback_data": "menu_buy_sub"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("check_pay_"):
                inv_id = data.replace("check_pay_", "")
                is_paid, inv, sub = await CryptoPayService.check_invoice(session, inv_id)

                if is_paid and sub:
                    await self.answer_callback_query(cq_id, "🎉 Оплата подтверждена!")
                    text = (
                        f"🎉 <b>ОПЛАТА УСПЕШНО ПОЛУЧЕНА!</b>\n\n"
                        f"Вам начислена подписка на <b>{inv.plan.value}</b>!\n"
                        f"⏳ Срок действия продлен до: <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b> (МСК)\n"
                        f"📱 Лимит аккаунтов: до <b>{sub.max_accounts}</b>\n\n"
                        f"Спасибо за покупку! Автоматизация чатов и голосовые активированы."
                    )
                    kb = {"inline_keyboard": [[{"text": "⭐ Моя подписка", "callback_data": "menu_my_sub"}], [{"text": "◀️ В меню", "callback_data": "menu_main"}]]}
                    await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

                    # Alert Admin
                    await AdminNotifier.send_telegram_alert(
                        f"💰 <b>Новая оплата!</b> Пользователь ID <code>{user_tg_id}</code> оплатил {inv.amount} USDT за тариф {inv.plan.value}.",
                        self.admin_id,
                    )
                else:
                    await self.answer_callback_query(
                        cq_id, "⏳ Оплата еще не зафиксирована. Оплатите счет в @CryptoBot и нажмите снова!"
                    )

            elif data == "cheque_info":
                await self.answer_callback_query(cq_id)
                text = (
                    "🧾 <b>АКТИВАЦИЯ ЧЕКОМ @CryptoBot</b>\n\n"
                    "Если вам подарили чек от @CryptoBot или вы создали его самостоятельно:\n\n"
                    "1. Просто отправьте ссылку на чек прямо в этот чат:\n"
                    "<code>https://t.me/CryptoBot?start=CQ...</code>\n\n"
                    "2. Либо отправьте команду:\n"
                    "<code>/check &lt;код_чека&gt;</code>\n\n"
                    "Бот мгновенно активирует подписку на ваш аккаунт!"
                )
                kb = {"inline_keyboard": [[{"text": "◀️ Назад к тарифам", "callback_data": "menu_buy_sub"}]]}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            # ------------------------------------------
            # Admin Subscription Gifting Center
            # ------------------------------------------
            elif data == "admin_grant_menu":
                await self.answer_callback_query(cq_id)
                if not is_admin:
                    return

                text = (
                    "🎁 <b>ВЫДАЧА И ПОДАРКИ ПОДПИСОК (АДМИН)</b>\n\n"
                    "Вы можете моментально начислить подписку любому пользователю <b>по юзернейму</b> (@username) "
                    "или <b>числовому ID</b> на абсолютно любой срок:\n\n"
                    "👉 <b>Команда в чат:</b>\n"
                    "<code>/grant &lt;@username или ID&gt; &lt;тариф или кол-во дней&gt;</code>\n\n"
                    "<b>Доступные тарифы:</b>\n"
                    "• <code>3</code> или <code>3d</code> — 3 дня (1 акк.)\n"
                    "• <code>7</code> или <code>7d</code> — 7 дней (1 акк.)\n"
                    "• <code>14</code> или <code>14d</code> — 14 дней (2 акк.)\n"
                    "• <code>31</code> или <code>1m</code> — 31 день (5 акк.)\n"
                    "• <code>90</code> или <code>3m</code> — 90 дней (5 акк.)\n"
                    "• <code>365</code> или <code>1y</code> — 1 год (5 акк.)\n\n"
                    "<i>Примеры: <code>/grant @username 14</code> или <code>/grant 8501711204 365</code></i>\n\n"
                    "👉 <b>Быстрая выдача себе кнопками или шаблоны:</b>"
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup=self.build_admin_grant_keyboard(), parse_mode="HTML")

            elif data.startswith("grant_self_") and is_admin:
                code = data.replace("grant_self_", "")
                days, plan, plan_label = self.parse_plan_and_days(code)
                sub = await SubscriptionService.activate_plan(session, db_user_id, plan, custom_days=days, extend=False)
                await self.answer_callback_query(cq_id, f"🎁 Начислено {days} дн.!")
                text = (
                    f"👑 <b>Вам успешно начислено {days} дней подписки ({plan_label})!</b>\n\n"
                    f"⏳ Действует до: <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b> (МСК)\n"
                    f"📱 Лимит: до {sub.max_accounts} акк."
                )
                kb = {"inline_keyboard": [[{"text": "◀️ Назад в меню", "callback_data": "menu_main"}]]}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("grant_prompt_") and is_admin:
                code = data.replace("grant_prompt_", "")
                days, plan, plan_label = self.parse_plan_and_days(code)
                await self.answer_callback_query(cq_id)
                text = (
                    f"🎁 <b>Выдача подписки на {days} дней ({plan_label})</b>\n\n"
                    f"Отправьте в чат команду с юзернеймом или ID:\n"
                    f"<code>/grant @username {days}</code>\n"
                    f"<i>или:</i>\n"
                    f"<code>/grant &lt;ID пользователя&gt; {days}</code>\n\n"
                    f"Пользователь мгновенно получит уведомление, что ему было подарено {days} дней подписки!"
                )
                kb = {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "admin_grant_menu"}]]}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            # ------------------------------------------
            # Admin Subscribers & Accounts Overview
            # ------------------------------------------
            elif data == "admin_users_overview" and is_admin:
                await self.answer_callback_query(cq_id, "👥 Загрузка базы подписчиков...")
                users_data = await AccountService.get_admin_subscribers_overview(session)
                text = self.format_subscribers_overview_text(users_data)
                kb = self.build_subscribers_overview_keyboard(users_data)
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("admin_user_detail_") and is_admin:
                target_uid = int(data.replace("admin_user_detail_", ""))
                await self.answer_callback_query(cq_id)
                users_data = await AccountService.get_admin_subscribers_overview(session)
                user_match = next((u for u in users_data if u["user_id"] == target_uid), None)
                if user_match:
                    text = self.format_single_user_detail_text(user_match)
                    kb = self.build_single_user_detail_keyboard(target_uid)
                    await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")
                else:
                    await self.answer_callback_query(cq_id, "⚠️ Пользователь не найден", show_alert=True)

            elif data.startswith("grant_user_") and is_admin:
                parts = data.split("_")
                if len(parts) >= 4:
                    target_uid = int(parts[2])
                    days = int(parts[3])
                    _, plan, plan_label = self.parse_plan_and_days(str(days))
                    sub = await SubscriptionService.activate_plan(session, target_uid, plan, custom_days=days, extend=True)

                    stmt_u = select(User).where(User.id == target_uid)
                    target_u = (await session.execute(stmt_u)).scalars().first()

                    if target_u and target_u.telegram_id:
                        await self.send_message(
                            chat_id=target_u.telegram_id,
                            text=(
                                f"🎁 <b>Вам начислено {days} дней подписки ({plan_label})!</b>\n\n"
                                f"⏳ Новый срок действия: до <b>{sub.expires_at.strftime('%d.%m.%Y %H:%M')}</b> (МСК)\n"
                                f"📱 Лимит подключений: <b>до {sub.max_accounts} акк.</b>\n\n"
                                f"Приятного использования! Нажмите /start для обновления статуса."
                            ),
                            parse_mode="HTML",
                        )

                    await self.answer_callback_query(cq_id, f"✅ Начислено {days} дн.!")
                    users_data = await AccountService.get_admin_subscribers_overview(session)
                    user_match = next((u for u in users_data if u["user_id"] == target_uid), None)
                    if user_match:
                        text = self.format_single_user_detail_text(user_match)
                        kb = self.build_single_user_detail_keyboard(target_uid)
                        await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("cancel_user_sub_") and is_admin:
                target_uid = int(data.replace("cancel_user_sub_", ""))
                await SubscriptionService.cancel_subscription(session, target_uid)
                await self.answer_callback_query(cq_id, "❌ Подписка аннулирована", show_alert=True)
                users_data = await AccountService.get_admin_subscribers_overview(session)
                user_match = next((u for u in users_data if u["user_id"] == target_uid), None)
                if user_match:
                    text = self.format_single_user_detail_text(user_match)
                    kb = self.build_single_user_detail_keyboard(target_uid)
                    await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            # ------------------------------------------
            # Styles Menu
            # ------------------------------------------
            elif data == "menu_styles":
                await self.answer_callback_query(cq_id)
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                acc_info = f" (подключено аккаунтов: <b>{len(accounts)}</b>)" if accounts else ""
                text = (
                    "🎭 <b>СТИЛЬ ОБЩЕНИЯ: АДАПТИВНЫЙ РЕЖИМ</b>\n\n"
                    f"В боте активирован единый <b>интеллектуальный адаптивный стиль</b>{acc_info}.\n\n"
                    "Ручной выбор отдельных стилей отключен: модель самостоятельно анализирует манеру общения каждого мужчины и общается со всеми по-разному, максимально реалистично!\n\n"
                    "💡 <b>Как нейросеть общается с разными клиентами:</b>\n"
                    "• <b>Анализирует характер и вайб:</b> сдержанным отвечает спокойно и лаконично, веселым — с юмором и иронией, романтичным — с девичьим смущением и теплом, уставшим — с искренней заботой.\n"
                    "• <b>Удерживает границы:</b> при наглости или напоре осаживает с достоинством и легким сарказмом, не позволяя собой командовать.\n"
                    "• <b>Зеркалит ритм:</b> подстраивает длину реплик, темп и эмоциональный градус под собеседника.\n"
                    "• <b>Уникальность:</b> каждый диалог строится по-своему, без шаблонных повторов.\n\n"
                    "<i>Все ваши аккаунты работают в этом режиме.</i>"
                )
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_styles_keyboard("adaptive_all", account_id=None), parse_mode="HTML"
                )

            elif data.startswith("set_style_"):
                raw_payload = data.replace("set_style_", "")
                target_acc_id = None
                parts = raw_payload.split("_")
                if len(parts) >= 2 and parts[-1].isdigit():
                    target_acc_id = int(parts[-1])
                    new_style = "_".join(parts[:-1])
                else:
                    new_style = raw_payload

                acc = None
                if target_acc_id:
                    stmt_acc = select(AccountSession).where(
                        AccountSession.id == target_acc_id, AccountSession.user_id == db_user_id
                    )
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    persona = await AccountService.ensure_account_persona(session, acc) if acc else None
                else:
                    stmt = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    if not persona:
                        persona = Persona(user_id=db_user_id, current_style=new_style)
                        session.add(persona)

                if persona:
                    persona.current_style = new_style
                    await session.commit()

                style_title = StyleType(new_style).title if new_style in StyleType._value2member_map_ else "Адаптивный"
                acc_note = f" для {acc.first_name or acc.username}" if acc else ""
                await self.answer_callback_query(cq_id, f"✅ Адаптивный стиль активен{acc_note}")
                text = (
                    f"✅ <b>Адаптивный стиль общения{acc_note} активен!</b>\n\n"
                    "Нейросеть гибко адаптируется под каждого собеседника и ведет диалог естественно, реалистично и неповторимо."
                )
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_styles_keyboard("adaptive_all", account_id=target_acc_id), parse_mode="HTML"
                )

            elif data.startswith("style_acc_"):
                await self.answer_callback_query(cq_id)
                acc_code = data.replace("style_acc_", "")
                target_acc_id = int(acc_code) if acc_code.isdigit() else None
                acc = None
                if target_acc_id:
                    stmt_acc = select(AccountSession).where(
                        AccountSession.id == target_acc_id, AccountSession.user_id == db_user_id
                    )
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    persona = await AccountService.ensure_account_persona(session, acc) if acc else None
                else:
                    stmt_p = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res_p = await session.execute(stmt_p)
                    persona = res_p.scalars().first()
                current = persona.current_style if persona else "adaptive_all"
                acc_label = f" для аккаунта <b>{acc.first_name or acc.username}</b>" if acc else ""
                text = (
                    f"🎭 <b>СТИЛЬ ОБЩЕНИЯ{acc_label}: АДАПТИВНЫЙ</b>\n\n"
                    "Для этого аккаунта активен <b>адаптивный живой режим</b>.\n\n"
                    "Нейросеть самостоятельно считывает манеру речи, характер и настроение каждого клиента, выстраивая персональный и реалистичный диалог."
                )
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_styles_keyboard("adaptive_all", account_id=target_acc_id), parse_mode="HTML"
                )

            # ------------------------------------------
            # Girl's Legend & Profile Menu
            # ------------------------------------------
            elif data == "menu_legend":
                await self.answer_callback_query(cq_id)
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                if len(accounts) > 1:
                    personas = {}
                    for acc in accounts:
                        personas[acc.id] = await AccountService.ensure_account_persona(session, acc)
                    text = (
                        "👤 <b>НАСТРОЙКА ЛЕГЕНД ДЛЯ ПОДКЛЮЧЕННЫХ АККАУНТОВ</b>\n\n"
                        f"У вас подключено: <b>{len(accounts)} акк.</b>\n\n"
                        "Для каждого вашего аккаунта действует <b>своя независимая легенда</b> (имя, возраст, город, контекст дейтинга и стиль общения).\n\n"
                        "Выберите аккаунт, анкету которого хотите настроить:"
                    )
                    await self.edit_message(
                        chat_id, msg_id, text, reply_markup=self.build_legend_accounts_keyboard(accounts, personas), parse_mode="HTML"
                    )
                elif len(accounts) == 1:
                    acc = accounts[0]
                    persona = await AccountService.ensure_account_persona(session, acc)
                    from companion.services.photo_pool_service import PhotoPoolService
                    p_stats = await PhotoPoolService.get_persona_photo_stats(session, persona.id)
                    p_count = p_stats.get("total_photos", 0)
                    text = self.format_legend_card(persona, account=acc, photo_count=p_count)
                    await self.edit_message(
                        chat_id, msg_id, text, reply_markup=self.build_legend_keyboard(account_id=acc.id, has_multiple_accounts=False, photo_count=p_count), parse_mode="HTML"
                    )
                else:
                    stmt = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    if not persona:
                        persona = Persona(user_id=db_user_id)
                        session.add(persona)
                        await session.commit()
                    from companion.services.photo_pool_service import PhotoPoolService
                    p_stats = await PhotoPoolService.get_persona_photo_stats(session, persona.id)
                    p_count = p_stats.get("total_photos", 0)
                    text = self.format_legend_card(persona, account=None, photo_count=p_count)
                    await self.edit_message(
                        chat_id, msg_id, text, reply_markup=self.build_legend_keyboard(account_id=None, has_multiple_accounts=False, photo_count=p_count), parse_mode="HTML"
                    )

            elif data.startswith("legend_acc_"):
                self._user_input_states.pop(user_tg_id, None)
                await self.answer_callback_query(cq_id)
                acc_code = data.replace("legend_acc_", "")
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                from companion.services.photo_pool_service import PhotoPoolService
                if acc_code in ("default", "0"):
                    stmt = select(Persona).where(Persona.user_id == db_user_id).order_by(Persona.id.asc())
                    res = await session.execute(stmt)
                    persona = res.scalars().first()
                    if not persona:
                        persona = Persona(user_id=db_user_id)
                        session.add(persona)
                        await session.commit()
                    p_stats = await PhotoPoolService.get_persona_photo_stats(session, persona.id)
                    p_count = p_stats.get("total_photos", 0)
                    text = self.format_legend_card(persona, account=None, photo_count=p_count)
                    await self.edit_message(
                        chat_id, msg_id, text, reply_markup=self.build_legend_keyboard(account_id=None, has_multiple_accounts=len(accounts) > 0, photo_count=p_count), parse_mode="HTML"
                    )
                else:
                    acc_id = int(acc_code)
                    stmt_acc = select(AccountSession).where(AccountSession.id == acc_id, AccountSession.user_id == db_user_id)
                    res_acc = await session.execute(stmt_acc)
                    acc = res_acc.scalars().first()
                    if acc:
                        persona = await AccountService.ensure_account_persona(session, acc)
                        p_stats = await PhotoPoolService.get_persona_photo_stats(session, persona.id)
                        p_count = p_stats.get("total_photos", 0)
                        text = self.format_legend_card(persona, account=acc, photo_count=p_count)
                        await self.edit_message(
                            chat_id, msg_id, text, reply_markup=self.build_legend_keyboard(account_id=acc.id, has_multiple_accounts=len(accounts) > 1, photo_count=p_count), parse_mode="HTML"
                        )

            elif data.startswith("legend_edit_name_age"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_name_age", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_NAME_AGE:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_NAME_AGE"
                await self.answer_callback_query(cq_id)
                text = (
                    "✏️ <b>Введите имя и возраст девушки:</b>\n\n"
                    "Например: <code>Алина, 21</code> или <code>Вика 20</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_city_district"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_city_district", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_CITY_DISTRICT:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_CITY_DISTRICT"
                await self.answer_callback_query(cq_id)
                text = (
                    "📍 <b>Введите город и район проживания девушки:</b>\n\n"
                    "Например: <code>Москва, ЦАО / Хамовники</code> или <code>Санкт-Петербург, Петроградка</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_birth_city"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_birth_city", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_BIRTH_CITY:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_BIRTH_CITY"
                await self.answer_callback_query(cq_id)
                text = (
                    "🏠 <b>Введите город или регион, откуда девушка родом:</b>\n\n"
                    "Например: <code>Самара</code>, <code>Казань</code>, <code>Краснодар</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_move_reason"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_move_reason", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_MOVE_REASON:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_MOVE_REASON"
                await self.answer_callback_query(cq_id)
                text = (
                    "✈️ <b>Введите причину переезда девушки в текущий город:</b>\n\n"
                    "Например:\n"
                    "<code>Поступила в университет на дизайн и осталась жить</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_years_in_city"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_years_in_city", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_YEARS_IN_CITY:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_YEARS_IN_CITY"
                await self.answer_callback_query(cq_id)
                text = (
                    "⏳ <b>Введите, сколько времени девушка живет в текущем городе:</b>\n\n"
                    "Например:\n"
                    "<code>Живу тут около 4 лет</code> или <code>Уже третий год</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_dating_app"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_dating_app", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_DATING_APP:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_DATING_APP"
                await self.answer_callback_query(cq_id)
                text = (
                    "💬 <b>Введите контекст знакомства (Дайвинчик / Тиндер / Пьюр):</b>\n\n"
                    "Например:\n"
                    "<code>совпали в Дайвинчике пару дней назад, ты мне сразу понравился, вчера удалила анкету потому что надоело</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_study_hobbies"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_study_hobbies", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_STUDY_HOBBIES:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_STUDY_HOBBIES"
                await self.answer_callback_query(cq_id)
                text = (
                    "🎨 <b>Введите информацию об учебе, работе и хобби девушки:</b>\n\n"
                    "Например:\n"
                    "<code>учусь на 4 курсе графического дизайна, беру заказы на фрилансе, обожаю выставки, винтаж, кофе и долгие прогулки</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_extra_facts"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_extra_facts", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_EXTRA_FACTS:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_EXTRA_FACTS"
                await self.answer_callback_query(cq_id)
                text = (
                    "✨ <b>Введите детали, фишки и предпочтения девушки:</b>\n\n"
                    "Например:\n"
                    "<code>живу одна с котиком, не люблю банальные свидания в душных кафе, обожаю разгадывать загадки и мечтаю сходить на атмосферный квест</code>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_edit_sleep"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_edit_sleep", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_SLEEP_SCHEDULE:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_SLEEP_SCHEDULE"
                await self.answer_callback_query(cq_id)
                text = (
                    "🌙 <b>Введите часы сна девушки (по её местному времени):</b>\n\n"
                    "Например: <code>23:00 - 08:30</code> или <code>00:30 - 09:30</code>\n\n"
                    "<i>В эти часы бот не отвечает мгновенно ночью, а откладывает сообщение до утреннего пробуждения.</i>"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("legend_wizard_auto"):
                mgr_state.set_menu("menu_legend")
                raw_code = data.replace("legend_wizard_auto", "").lstrip("_")
                self._user_input_states[user_tg_id] = f"WAIT_LEGEND_WIZARD:{raw_code}" if (raw_code and raw_code != "default") else "WAIT_LEGEND_WIZARD"
                await self.answer_callback_query(cq_id)
                text = (
                    "⚡️ <b>AI-Мастер заполнения легенды</b>\n\n"
                    "Отправьте в чат любое сообщение со свободным описанием девушки. Например:\n"
                    "<i>«меня зовут Настя, 22 года, Питер Васька, учусь на медсестру, люблю рок и кофе, не люблю душные кафе, хочу на квест»</i>\n\n"
                    "Нейросеть сама всё поймет, распарсит и обновит анкету вашей девушки!"
                )
                await self.send_message(chat_id, text, reply_markup=self.build_cancel_flow_kb("menu_legend"), parse_mode="HTML")

            elif data.startswith("lead_mute_") or data.startswith("lead_stop_alarm_"):
                contact_id = int(data.replace("lead_mute_", "").replace("lead_stop_alarm_", ""))
                AdminNotifier.stop_quest_alarm(contact_id)

                stmt_c = select(Contact).where(Contact.id == contact_id)
                res_c = await session.execute(stmt_c)
                contact = res_c.scalars().first()
                if contact:
                    contact.quest_alarm_muted = True
                    if contact.status != ContactStatus.LEAD_MEETING:
                        contact.status = ContactStatus.LEAD_MEETING
                    if not contact.lead_detected_at:
                        contact.lead_detected_at = datetime.now(timezone.utc)
                    if not contact.lead_summary:
                        contact.lead_summary = "Согласился на квест 🎯"
                    await session.commit()

                c_name = (contact.first_name if contact else None) or (contact.username if contact else None) or "собеседником"
                tg_link = f"https://t.me/{contact.username}" if (contact and contact.username) else (f"tg://user?id={contact.tg_id}" if contact else "#")
                await self.answer_callback_query(cq_id, "🔕 Напоминания остановлены! ИИ продолжает общение.")
                kb = {
                    "inline_keyboard": [
                        [{"text": "⏸ Принять лида (Остановить ИИ)", "callback_data": f"lead_accept_{contact_id}"}],
                        [{"text": "🔥 Вкладка: Лиды (Встречи)", "callback_data": "menu_leads"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]
                    ]
                }
                await self.edit_message(
                    chat_id,
                    msg_id,
                    f"🔕 <b>Напоминание успешно остановлено!</b>\n\n"
                    f"👤 <b>Собеседник:</b> <a href=\"{tg_link}\">{c_name}</a>\n"
                    f"• Будильник и повторные напоминания выключены.\n"
                    f"• ИИ в чате по-прежнему активен и продолжает вести диалог.\n"
                    f"• Лид сохранен во вкладке <b>«Лиды (Встречи)»</b> в категории «Ожидающие».\n\n"
                    f"<i>Если захотите перехватить диалог и писать лично — нажмите кнопку «Принять лида» ниже:</i>",
                    reply_markup=kb,
                    parse_mode="HTML",
                )

            elif data.startswith("lead_accept_"):
                contact_id = int(data.replace("lead_accept_", ""))
                AdminNotifier.stop_quest_alarm(contact_id)

                stmt_c = select(Contact).where(Contact.id == contact_id)
                res_c = await session.execute(stmt_c)
                contact = res_c.scalars().first()
                if contact:
                    contact.status = ContactStatus.LEAD_MEETING
                    contact.ai_active = False
                    contact.bot_paused = True
                    contact.paused_reason = "quest_lead_accepted_by_owner"
                    if not contact.lead_detected_at:
                        contact.lead_detected_at = datetime.now(timezone.utc)
                    if not contact.lead_summary:
                        contact.lead_summary = "Согласился на квест 🎯"
                    await session.commit()

                c_name = (contact.first_name if contact else None) or (contact.username if contact else None) or "собеседником"
                tg_link = f"https://t.me/{contact.username}" if (contact and contact.username) else (f"tg://user?id={contact.tg_id}" if contact else "#")
                await self.answer_callback_query(cq_id, "✅ Лид принят! Будильник остановлен, ИИ выключен.")
                kb = {
                    "inline_keyboard": [
                        [{"text": "🤖 Вернуть управление боту (Включить ИИ)", "callback_data": f"lead_resume_{contact_id}"}],
                        [{"text": "🔥 Вкладка: Лиды (Встречи)", "callback_data": "menu_leads"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]
                    ]
                }
                await self.edit_message(
                    chat_id,
                    msg_id,
                    f"✅ <b>Лид успешно принят!</b>\n\n"
                    f"👤 <b>Собеседник:</b> <a href=\"{tg_link}\">{c_name}</a>\n"
                    f"• Будильник и повторные напоминания выключены.\n"
                    f"• ИИ для диалога отключен (бот молчит).\n"
                    f"• Лид перемещен в категорию <b>«Забрали»</b> во вкладке «Лиды (Встречи)».\n"
                    f"• Вы можете продолжить общение с мужчиной лично в Telegram.\n\n"
                    f"<i>Если захотите вернуть автоматические ответы нейросети, нажмите кнопку ниже:</i>",
                    reply_markup=kb,
                    parse_mode="HTML",
                )

            elif data.startswith("lead_resume_"):
                contact_id = int(data.replace("lead_resume_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                res_c = await session.execute(stmt_c)
                contact = res_c.scalars().first()
                if contact:
                    contact.ai_active = True
                    contact.bot_paused = False
                    contact.paused_reason = None
                    await session.commit()

                c_name = (contact.first_name if contact else None) or (contact.username if contact else None) or "собеседником"
                tg_link = f"https://t.me/{contact.username}" if (contact and contact.username) else (f"tg://user?id={contact.tg_id}" if contact else "#")
                await self.answer_callback_query(cq_id, "🤖 ИИ снова включен! Бот продолжит общение.")
                kb = {
                    "inline_keyboard": [
                        [{"text": "⏸ Остановить ИИ (Принять лида)", "callback_data": f"lead_accept_{contact_id}"}],
                        [{"text": "🔥 Вкладка: Лиды (Встречи)", "callback_data": "menu_leads"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]
                    ]
                }
                await self.edit_message(
                    chat_id,
                    msg_id,
                    f"🤖 <b>Управление возвращено ИИ!</b>\n\n"
                    f"👤 <b>Собеседник:</b> <a href=\"{tg_link}\">{c_name}</a>\n"
                    f"• ИИ для диалога снова активен.\n"
                    f"• Бот продолжит отвечать собеседнику в обычном режиме.\n\n"
                    f"<i>Чтобы снова перехватить управление и отключить ИИ:</i>",
                    reply_markup=kb,
                    parse_mode="HTML",
                )

            # ------------------------------------------
            # Invites / Chat Automation Links
            # ------------------------------------------
            elif data == "menu_invites":
                await self.answer_callback_query(cq_id)
                sub = await SubscriptionService.get_active_subscription(session, db_user_id)
                active_plan = sub.plan if (sub and sub.is_active) else None

                if not is_admin and not active_plan:
                    text = (
                        "🔗 <b>ГЕНЕРАТОР ССЫЛОК ДЛЯ CHAT AUTOMATION</b>\n\n"
                        "❌ <b>У вас нет активной подписки.</b>\n\n"
                        "Для создания персональных ссылок и подключения аккаунтов "
                        "оформите подходящий тариф в разделе «Оплата»."
                    )
                else:
                    if is_admin:
                        text = (
                            "👑 <b>ГЕНЕРАТОР ССЫЛОК (РЕЖИМ АДМИНИСТРАТОРА)</b>\n\n"
                            "Вам доступны ссылки на любые тарифы без ограничений.\n"
                            "Выберите тариф для создания ссылки клиенту:"
                        )
                    else:
                        text = (
                            f"🔗 <b>ГЕНЕРАТОР ССЫЛОК ДЛЯ CHAT AUTOMATION</b>\n\n"
                            f"Ваш текущий тариф: <b>{active_plan.value}</b> (осталось {sub.days_remaining()} дн.).\n"
                            f"Вы можете создавать ссылки на тарифы <b>в пределах вашей активной подписки</b>:"
                        )

                await self.edit_message(
                    chat_id,
                    msg_id,
                    text,
                    reply_markup=self.build_invites_keyboard(active_plan=active_plan, is_admin=is_admin),
                    parse_mode="HTML",
                )

            elif data.startswith("gen_link_"):
                plan_map = {
                    "gen_link_3d": PlanDuration.DAYS_3,
                    "gen_link_7d": PlanDuration.DAYS_7,
                    "gen_link_14d": PlanDuration.DAYS_14,
                    "gen_link_31d": PlanDuration.DAYS_31,
                }
                plan = plan_map.get(data, PlanDuration.DAYS_7)

                sub = await SubscriptionService.get_active_subscription(session, db_user_id)
                if not is_admin:
                    if not sub or not sub.is_active:
                        await self.answer_callback_query(
                            cq_id,
                            "❌ У вас нет активной подписки! Оформите её в меню Оплаты.",
                            show_alert=True,
                        )
                        return
                    if plan.days_count > sub.plan.days_count:
                        await self.answer_callback_query(
                            cq_id,
                            f"🔒 Тариф недоступен!\n\nВаша текущая подписка: {sub.plan.value}.\nДля создания ссылки на {plan.value} повысьте тариф.",
                            show_alert=True,
                        )
                        return

                await self.answer_callback_query(cq_id, "⏳ Создаю персональную ссылку...")
                link, invite = await InviteService.create_subscription_invite(
                    session=session,
                    user_id=db_user_id,
                    plan=plan,
                    bot_username=settings.BOT_USERNAME,
                )
                text = (
                    f"🎉 <b>Персональная ссылка создана!</b>\n\n"
                    f"📋 <b>Тариф:</b> {plan.value} (до {invite.max_accounts} акк.)\n"
                    f"⏳ <b>Срок действия:</b> ровно {plan.days_count} дней с момента активации\n\n"
                    f"🔗 <b>Ссылка для активации клиентом:</b>\n<code>{link}</code>\n\n"
                    f"👉 <b>Инструкция для подключения:</b>\n"
                    f"1. Передайте ссылку клиенту. Он переходит по ней и жмет <b>Start</b> (подписка активируется в боте).\n"
                    f"2. Затем в Telegram: <i>Настройки</i> ➡️ <i>Telegram Business</i> ➡️ <i>Чат-боты</i> находит и выбирает <code>@{settings.BOT_USERNAME}</code>.\n\n"
                    f"⚠️ <i>Важно: в поле «Автоматизация чатов» вводится имя бота <code>@{settings.BOT_USERNAME}</code>, а не ссылка с start!</i>"
                )
                kb = {"inline_keyboard": [[{"text": "◀️ Назад к тарифам", "callback_data": "menu_invites"}]]}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("link_locked_"):
                code = data.replace("link_locked_", "")
                sub = await SubscriptionService.get_active_subscription(session, db_user_id)
                current_plan_name = sub.plan.value if (sub and sub.is_active) else "нет"
                await self.answer_callback_query(
                    cq_id,
                    f"🔒 Этот тариф закрыт!\n\nВаша текущая подписка: {current_plan_name}.\nЧтобы создавать ссылки на {code}, повысьте тариф в разделе «Оплата».",
                    show_alert=True,
                )

            # ------------------------------------------
            # Leads & Stats
            # ------------------------------------------
            # 1. Leads (Insults / Toxicity Moderation)
            elif data == "menu_leads_insults":
                await self.answer_callback_query(cq_id)
                insult_contacts = await StatsService.get_insult_leads(session, db_user_id)
                if not insult_contacts and is_admin:
                    from sqlalchemy import or_
                    stmt_all = select(Contact).where(or_(Contact.has_violation == True, Contact.violation_count > 0)).order_by(Contact.last_violation_at.desc(), Contact.id.desc())
                    res_all = await session.execute(stmt_all)
                    insult_contacts = list(res_all.scalars().all())

                if not insult_contacts:
                    text = (
                        "🔴 <b>ЛИДЫ (ОСКОРБЛЕНИЯ И ГРУБОСТЬ)</b>\n\n"
                        "🎉 <b>Нарушений не зафиксировано!</b>\n"
                        "Все клиенты общаются вежливо и корректно.\n\n"
                        "Когда нейросеть зафиксирует грубость, мат или оскорбление в адрес девушки — "
                        "клиент сразу появится здесь с описанием ситуации и кнопками управления."
                    )
                    kb = {"inline_keyboard": [[{"text": "◀️ В главное меню", "callback_data": "menu_main"}]]}
                else:
                    text = (
                        "🔴 <b>ЛИДЫ (ОСКОРБЛЕНИЯ И ГРУБОСТЬ):</b>\n\n"
                        "Список клиентов, чьи сообщения были отмечены системой как потенциально оскорбительные:\n"
                        "<i>Нажмите на клиента, чтобы открыть карточку и выбрать действие:</i>"
                    )
                    kb_rows = []
                    for c in insult_contacts[:15]:
                        c_name = html.escape(c.first_name or f"User_{c.tg_id}")
                        user_str = f" (@{c.username})" if c.username else ""
                        v_cnt = c.violation_count or 1
                        btn_txt = f"👤 {c_name}{user_str} — {v_cnt} нар."
                        kb_rows.append([{"text": btn_txt, "callback_data": f"insult_card_{c.id}"}])
                    kb_rows.append([{"text": "◀️ В главное меню", "callback_data": "menu_main"}])
                    kb = {"inline_keyboard": kb_rows}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("insult_card_"):
                contact_id = int(data.replace("insult_card_", ""))
                await self.answer_callback_query(cq_id)
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.edit_message(chat_id, msg_id, "⚠️ Карточка клиента не найдена.", reply_markup={"inline_keyboard": [[{"text": "◀️ К списку", "callback_data": "menu_leads_insults"}]]})
                    return

                c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                user_str = f"@{contact.username}" if contact.username else "нет"
                first_dt = contact.created_at.strftime("%d.%m.%Y %H:%M") if contact.created_at else "-"
                last_dt = contact.last_incoming_at.strftime("%d.%m.%Y %H:%M") if contact.last_incoming_at else "-"
                status_text = "🟢 ИИ общается (активен)" if (contact.ai_active and not contact.bot_paused and contact.status == ContactStatus.ACTIVE) else "⛔️ Заблокирован (ИИ остановлен)"
                v_text = html.escape(contact.last_violation_text or "Не зафиксировано")
                v_analysis = html.escape(contact.last_violation_analysis or "Грубое или провокационное выражение")
                diag_summary = html.escape(contact.dialogue_summary or "Диалог в процессе")

                card_text = (
                    f"🔴 <b>КАРТОЧКА ЛИДА (ОСКОРБЛЕНИЯ)</b>\n\n"
                    f"👤 <b>Имя:</b> {c_name}\n"
                    f"🔗 <b>Username:</b> {user_str}\n"
                    f"🆔 <b>Telegram ID:</b> <code>{contact.tg_id}</code>\n"
                    f"📅 <b>Дата первого обращения:</b> {first_dt}\n"
                    f"⏰ <b>Последнее сообщение:</b> {last_dt}\n"
                    f"⚠️ <b>Количество нарушений:</b> <b>{contact.violation_count or 1}</b>\n\n"
                    f"💬 <b>Последнее нарушение:</b>\n"
                    f"«<i>{v_text}</i>»\n\n"
                    f"🧠 <b>Анализ ситуации:</b>\n"
                    f"<i>{v_analysis}</i>\n\n"
                    f"🏷 <b>Теги:</b> {contact.tags or '🔴 Оскорбления'}\n"
                    f"📊 <b>Текущий статус:</b> {status_text}\n"
                    f"📝 <b>Краткое описание диалога:</b>\n"
                    f"{diag_summary}\n\n"
                    f"<i>Выберите действие для этого клиента:</i>"
                )

                kb = [
                    [
                        {"text": "🔓 Продолжить диалог", "callback_data": f"lead_action_continue_{contact.id}"},
                        {"text": "⛔️ Заблокировать клиента", "callback_data": f"lead_action_block_{contact.id}"},
                    ],
                    [
                        {"text": "💬 История сообщений", "callback_data": f"chat_history_{contact.id}"},
                        {"text": "🗑 Удалить диалог", "callback_data": f"lead_action_delete_{contact.id}"},
                    ],
                ]
                if contact.last_voice_file_id:
                    kb.append([{"text": "🎙 Послушать голосовое", "callback_data": f"lead_play_voice_{contact.id}"}])
                kb.append([{"text": "↩️ Назад к списку", "callback_data": "menu_leads_insults"}])

                await self.edit_message(chat_id, msg_id, card_text, reply_markup={"inline_keyboard": kb}, parse_mode="HTML")

            # 2. Leads (Conversations / Chats CRM)
            elif data == "menu_leads_chats":
                mgr_state.set_menu("menu_leads_chats")
                await self.answer_callback_query(cq_id)
                text, kb = await self.build_chats_crm_payload(
                    session=session,
                    db_user_id=db_user_id,
                    is_admin=is_admin,
                    filter_key=mgr_state.chat_filter,
                    page=mgr_state.chat_page,
                    search_query=mgr_state.chat_search_query,
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("chats_filter_"):
                mgr_state.set_menu("menu_leads_chats")
                f_key = data.replace("chats_filter_", "")
                mgr_state.chat_filter = f_key
                mgr_state.chat_page = 1
                await self.answer_callback_query(cq_id)
                text, kb = await self.build_chats_crm_payload(
                    session=session,
                    db_user_id=db_user_id,
                    is_admin=is_admin,
                    filter_key=mgr_state.chat_filter,
                    page=1,
                    search_query=mgr_state.chat_search_query,
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("chats_page_"):
                mgr_state.set_menu("menu_leads_chats")
                rest = data.replace("chats_page_", "")
                parts = rest.rsplit("_", 1)
                f_key = parts[0] if len(parts) == 2 else mgr_state.chat_filter
                pg = int(parts[1]) if (len(parts) == 2 and parts[1].isdigit()) else 1
                mgr_state.chat_filter = f_key
                mgr_state.chat_page = pg
                await self.answer_callback_query(cq_id)
                text, kb = await self.build_chats_crm_payload(
                    session=session,
                    db_user_id=db_user_id,
                    is_admin=is_admin,
                    filter_key=f_key,
                    page=pg,
                    search_query=mgr_state.chat_search_query,
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data == "chats_search":
                mgr_state.set_menu("menu_leads_chats")
                self._user_input_states[user_tg_id] = "WAIT_CONTACT_SEARCH"
                await self.answer_callback_query(cq_id)
                await self.send_message(
                    chat_id,
                    "🔍 <b>Поиск по диалогам (CRM):</b>\n\n"
                    "Отправьте имя, <code>@username</code>, тему разговора или тег клиента:",
                    reply_markup=self.build_cancel_flow_kb("menu_leads_chats"),
                    parse_mode="HTML",
                )

            elif data == "chats_clear_search":
                mgr_state.chat_search_query = None
                mgr_state.chat_page = 1
                await self.answer_callback_query(cq_id, "Поиск сброшен")
                text, kb = await self.build_chats_crm_payload(
                    session=session,
                    db_user_id=db_user_id,
                    is_admin=is_admin,
                    filter_key=mgr_state.chat_filter,
                    page=1,
                    search_query=None,
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("chat_card_"):
                contact_id = int(data.replace("chat_card_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "🔄 Это меню устарело. Обновляю актуальный список...")
                    crm_txt, crm_kb = await self.build_chats_crm_payload(
                        session=session,
                        db_user_id=db_user_id,
                        is_admin=is_admin,
                        filter_key=mgr_state.chat_filter,
                        page=1,
                        search_query=mgr_state.chat_search_query,
                    )
                    await self.edit_message(
                        chat_id,
                        msg_id,
                        "🔄 <i>Это меню устарело (карточка была удалена). Обновил актуальный список чатов:</i>\n\n" + crm_txt,
                        reply_markup=crm_kb,
                        parse_mode="HTML",
                    )
                    return

                await self.answer_callback_query(cq_id)
                mgr_state.last_entity_type = "contact"
                mgr_state.last_entity_id = contact.id
                card_text, card_kb = self.build_contact_card_payload(contact)
                await self.edit_message(chat_id, msg_id, card_text, reply_markup=card_kb, parse_mode="HTML")

            elif data.startswith("chat_context_clear_"):
                contact_id = int(data.replace("chat_context_clear_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "🔄 Это меню устарело.", show_alert=True)
                    return
                from companion.models.entities import ContactMemory
                from sqlalchemy import delete as sa_delete
                await session.execute(sa_delete(ContactMemory).where(ContactMemory.contact_id == contact_id))
                await session.commit()
                await self.answer_callback_query(cq_id, "🗑 Долгосрочные факты очищены")
                ctx_text, ctx_kb = await self.build_contact_context_payload(session, contact)
                await self.edit_message(chat_id, msg_id, ctx_text, reply_markup=ctx_kb, parse_mode="HTML")

            elif data.startswith("chat_context_"):
                contact_id = int(data.replace("chat_context_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "🔄 Это меню устарело. Обновляю список...")
                    crm_txt, crm_kb = await self.build_chats_crm_payload(
                        session=session, db_user_id=db_user_id, is_admin=is_admin
                    )
                    await self.edit_message(chat_id, msg_id, crm_txt, reply_markup=crm_kb, parse_mode="HTML")
                    return
                await self.answer_callback_query(cq_id)
                mgr_state.last_entity_type = "contact"
                mgr_state.last_entity_id = contact.id
                ctx_text, ctx_kb = await self.build_contact_context_payload(session, contact)
                await self.edit_message(chat_id, msg_id, ctx_text, reply_markup=ctx_kb, parse_mode="HTML")

            elif data.startswith("chat_manual_reply_"):
                contact_id = int(data.replace("chat_manual_reply_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "🔄 Контакт не найден. Обновляю список...")
                    crm_txt, crm_kb = await self.build_chats_crm_payload(
                        session=session, db_user_id=db_user_id, is_admin=is_admin
                    )
                    await self.edit_message(chat_id, msg_id, crm_txt, reply_markup=crm_kb, parse_mode="HTML")
                    return
                mgr_state.last_entity_type = "contact"
                mgr_state.last_entity_id = contact.id
                self._user_input_states[user_tg_id] = f"WAIT_CONTACT_MANUAL_REPLY:{contact.id}"
                await self.answer_callback_query(cq_id)
                c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                await self.send_message(
                    chat_id,
                    f"✉️ <b>Ручной ответ клиенту {c_name}:</b>\n\n"
                    f"Напишите текст сообщения в этот чат — бот отправит его клиенту от лица аккаунта "
                    f"и автоматически поставит ИИ на паузу на 15 минут:",
                    reply_markup=self.build_cancel_flow_kb(f"chat_card_{contact.id}"),
                    parse_mode="HTML",
                )

            elif data.startswith("chat_history_"):
                contact_id = int(data.replace("chat_history_", ""))
                await self.answer_callback_query(cq_id)
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.edit_message(chat_id, msg_id, "⚠️ Контакт не найден.", reply_markup={"inline_keyboard": [[{"text": "◀️ В меню", "callback_data": "menu_main"}]]})
                    return

                from companion.models.entities import ContactMessage
                stmt_m = select(ContactMessage).where(ContactMessage.contact_id == contact_id).order_by(ContactMessage.id.desc()).limit(15)
                res_m = await session.execute(stmt_m)
                db_messages = list(reversed(res_m.scalars().all()))

                c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                user_str = f" (@{contact.username})" if contact.username else ""

                header = f"💬 <b>ИСТОРИЯ ПЕРЕПИСКИ: {c_name}</b>{user_str}\n\n"
                if not db_messages:
                    from companion.services.ai_service import AIService
                    mem_turns = AIService.get_chat_history(contact_id)[-10:]
                    if not mem_turns:
                        history_text = header + "<i>История сообщений пуста.</i>"
                    else:
                        rows = []
                        for t in mem_turns:
                            role_icon = "👤 <b>Клиент:</b>" if t["role"] == "user" else "🌸 <b>Алина (бот):</b>"
                            rows.append(f"{role_icon} «{html.escape(t['text'])}»")
                        history_text = header + "\n\n".join(rows)
                else:
                    rows = []
                    for m in db_messages:
                        time_str = m.created_at.strftime("%H:%M") if m.created_at else ""
                        time_part = f" ({time_str})" if time_str else ""
                        if m.sender_role == "user":
                            voice_part = f" 🎙 <i>[ГС {m.voice_duration}с]</i>" if m.is_voice else ""
                            rows.append(f"👤 <b>Клиент{time_part}{voice_part}:</b>\n«{html.escape(m.text)}»")
                        else:
                            voice_part = " 🎙 <i>[Голосовой ответ]</i>" if m.is_voice else ""
                            rows.append(f"🌸 <b>Алина (бот){time_part}{voice_part}:</b>\n«{html.escape(m.text)}»")
                    history_text = header + "\n\n".join(rows)

                kb = [
                    [{"text": "🎙 Послушать голосовые", "callback_data": f"chat_voice_list_{contact.id}"}],
                    [{"text": "↩️ Назад к карточке", "callback_data": f"chat_card_{contact.id}"}],
                ]
                await self.edit_message(chat_id, msg_id, history_text, reply_markup={"inline_keyboard": kb}, parse_mode="HTML")

            elif data.startswith("chat_voice_list_") or data.startswith("lead_play_voice_"):
                contact_id = int(data.replace("chat_voice_list_", "").replace("lead_play_voice_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "⚠️ Клиент не найден.", show_alert=True)
                    return

                from companion.models.entities import ContactMessage
                stmt_v = select(ContactMessage).where(
                    ContactMessage.contact_id == contact_id,
                    ContactMessage.is_voice == True,
                    ContactMessage.voice_file_id != None
                ).order_by(ContactMessage.id.desc()).limit(3)
                v_msgs = list((await session.execute(stmt_v)).scalars().all())

                file_ids = [m.voice_file_id for m in v_msgs if m.voice_file_id]
                if not file_ids and contact.last_voice_file_id:
                    file_ids = [contact.last_voice_file_id]

                if not file_ids:
                    await self.answer_callback_query(cq_id, "📭 У этого клиента нет сохраненных голосовых записей.", show_alert=True)
                    return

                await self.answer_callback_query(cq_id, "🎙 Отправляю оригинальное голосовое сообщение...")
                for fid in file_ids:
                    caption = f"🎙 <b>Голосовое от: {html.escape(contact.first_name or 'Клиента')}</b>"
                    await self.send_voice_by_file_id(chat_id, fid, caption=caption)

            elif data.startswith("chat_tags_"):
                contact_id = int(data.replace("chat_tags_", ""))
                await self.answer_callback_query(cq_id)
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "⚠️ Клиент не найден.", show_alert=True)
                    return

                current_tags = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
                tag_options = [
                    ("active", "🟢 Активный"),
                    ("inactive", "🟡 Неактивный"),
                    ("insult", "🔴 Оскорбления"),
                    ("interested", "💬 Интересуется"),
                    ("voice", "🎙 Отправляет войсы"),
                    ("waiting", "⏳ Ожидает ответа"),
                    ("blocked", "⛔️ Заблокирован"),
                    ("vip", "⭐️ Приоритетный"),
                ]

                kb_rows = []
                for i in range(0, len(tag_options), 2):
                    row = []
                    for key, label in tag_options[i:i+2]:
                        is_set = label in current_tags
                        mark = "✅ " if is_set else ""
                        row.append({"text": f"{mark}{label}", "callback_data": f"chat_tag_toggle_{contact.id}_{key}"})
                    kb_rows.append(row)
                kb_rows.append([{"text": "↩️ Назад к карточке", "callback_data": f"chat_card_{contact.id}"}])

                c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                text = (
                    f"🏷 <b>УПРАВЛЕНИЕ ТЕГАМИ КЛИЕНТА</b>\n\n"
                    f"👤 <b>Клиент:</b> {c_name}\n"
                    f"📌 <b>Текущие теги:</b> {contact.tags or 'нет'}\n\n"
                    f"<i>Нажимайте на кнопки, чтобы включать или отключать нужные теги:</i>"
                )
                await self.edit_message(chat_id, msg_id, text, reply_markup={"inline_keyboard": kb_rows}, parse_mode="HTML")

            elif data.startswith("chat_tag_toggle_"):
                parts = data.replace("chat_tag_toggle_", "").split("_")
                contact_id = int(parts[0])
                tag_key = parts[1]
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if contact:
                    tag_map = {
                        "active": "🟢 Активный",
                        "inactive": "🟡 Неактивный",
                        "insult": "🔴 Оскорбления",
                        "interested": "💬 Интересуется",
                        "voice": "🎙 Отправляет войсы",
                        "waiting": "⏳ Ожидает ответа",
                        "blocked": "⛔️ Заблокирован",
                        "vip": "⭐️ Приоритетный",
                    }
                    target_tag = tag_map.get(tag_key)
                    if target_tag:
                        cur_tags = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
                        if target_tag in cur_tags:
                            cur_tags.remove(target_tag)
                        else:
                            cur_tags.append(target_tag)
                        contact.tags = ", ".join(cur_tags) if cur_tags else "Обычный"
                        await session.commit()
                await self.answer_callback_query(cq_id, "🏷 Теги обновлены!")
                cq["data"] = f"chat_tags_{contact_id}"
                await self.handle_callback(cq)
                return

            elif data.startswith("lead_action_continue_"):
                contact_id = int(data.replace("lead_action_continue_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if contact:
                    contact.status = ContactStatus.ACTIVE
                    contact.ai_active = True
                    contact.bot_paused = False
                    contact.paused_reason = None
                    cur_tags = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
                    if "🔴 Оскорбления" in cur_tags:
                        cur_tags.remove("🔴 Оскорбления")
                    if "🟢 Активный" not in cur_tags:
                        cur_tags.append("🟢 Активный")
                    contact.tags = ", ".join(cur_tags)
                    await session.commit()
                await self.answer_callback_query(cq_id, "🔓 Диалог продолжен! Нейросеть продолжит общение.")
                c_name = html.escape(contact.first_name if contact else "клиентом")
                await self.edit_message(
                    chat_id, msg_id,
                    f"🔓 <b>Диалог с {c_name} продолжен!</b>\n\n"
                    f"• Ограничение на общение снято.\n"
                    f"• Нейросеть продолжит диалог с учетом всей предыдущей истории переписки.\n"
                    f"• Клиент возвращен в список обычного общения.",
                    reply_markup={"inline_keyboard": [
                        [{"text": "💬 Открыть карточку", "callback_data": f"chat_card_{contact_id}"}],
                        [{"text": "◀️ В меню", "callback_data": "menu_main"}]
                    ]},
                    parse_mode="HTML"
                )

            elif data.startswith("lead_action_block_") or data.startswith("chat_toggle_block_"):
                contact_id = int(data.replace("lead_action_block_", "").replace("chat_toggle_block_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if contact:
                    if contact.status == ContactStatus.BLOCKED or not contact.ai_active:
                        contact.status = ContactStatus.ACTIVE
                        contact.ai_active = True
                        contact.bot_paused = False
                        contact.paused_reason = None
                        await session.commit()
                        await self.answer_callback_query(cq_id, "🔓 Клиент разблокирован! ИИ включен.")
                    else:
                        contact.status = ContactStatus.BLOCKED
                        contact.ai_active = False
                        contact.bot_paused = True
                        contact.paused_reason = "blocked_by_owner"
                        await session.commit()
                        await self.answer_callback_query(cq_id, "⛔️ Клиент заблокирован! Бот больше не отвечает.")

                c_name = html.escape(contact.first_name if contact else "клиентом")
                is_now_blocked = (contact.status == ContactStatus.BLOCKED or not contact.ai_active) if contact else True
                state_desc = "заблокирован (бот больше не отправит ему ни одного сообщения)" if is_now_blocked else "разблокирован (ИИ продолжает диалог)"
                await self.edit_message(
                    chat_id, msg_id,
                    f"<b>Статус клиента {c_name} изменен:</b>\n\n• Клиент <b>{state_desc}</b>.",
                    reply_markup={"inline_keyboard": [
                        [{"text": "💬 Карточка клиента", "callback_data": f"chat_card_{contact_id}"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]
                    ]},
                    parse_mode="HTML"
                )

            elif data.startswith("chat_toggle_pause_"):
                contact_id = int(data.replace("chat_toggle_pause_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if contact:
                    contact.bot_paused = not contact.bot_paused
                    contact.paused_reason = "paused_by_owner" if contact.bot_paused else None
                    await session.commit()
                    alert_txt = "⏸ Бот поставлен на паузу" if contact.bot_paused else "▶️ Бот снят с паузы"
                    await self.answer_callback_query(cq_id, alert_txt)
                cq["data"] = f"chat_card_{contact_id}"
                await self.handle_callback(cq)
                return

            elif data.startswith("unpause_contact_"):
                contact_id = int(data.replace("unpause_contact_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if contact:
                    contact.bot_paused = False
                    contact.ai_active = True
                    contact.paused_reason = None
                    await session.commit()
                    c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                    await self.answer_callback_query(cq_id, "▶️ Автоответ бота успешно возобновлен!")
                    await self.edit_message(
                        chat_id, msg_id,
                        f"✅ <b>Автоответ бота возобновлен в чате с {c_name}!</b>\n\n"
                        f"Нейросеть снова активна и продолжит диалог при поступлении новых сообщений.",
                        reply_markup={"inline_keyboard": [
                            [{"text": "💬 Карточка клиента", "callback_data": f"chat_card_{contact_id}"}],
                            [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]
                        ]},
                        parse_mode="HTML"
                    )
                else:
                    await self.answer_callback_query(cq_id, "⚠️ Контакт не найден", show_alert=True)
                return

            elif data.startswith("lead_action_delete_"):
                contact_id = int(data.replace("lead_action_delete_", ""))
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if not contact:
                    await self.answer_callback_query(cq_id, "🔄 Это меню устарело. Обновляю список...", show_alert=True)
                    crm_txt, crm_kb = await self.build_chats_crm_payload(
                        session=session, db_user_id=db_user_id, is_admin=is_admin
                    )
                    await self.edit_message(chat_id, msg_id, crm_txt, reply_markup=crm_kb, parse_mode="HTML")
                    return
                await self.answer_callback_query(cq_id)
                mgr_state.pending_confirmation = {"action": "delete_contact", "target_id": contact.id}
                c_name = html.escape(contact.first_name or f"User_{contact.tg_id}")
                text = (
                    f"⚠️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
                    f"Вы уверены, что хотите удалить переписку и карточку клиента <b>{c_name}</b>?\n\n"
                    f"• Карточка будет удалена из активной панели управления.\n"
                    f"• Бот забудет текущий контекст и историю с этим пользователем.\n\n"
                    f"<i>(Нажмите кнопку ниже или просто напишите «да» / «отмена»)</i>"
                )
                kb = [
                    [{"text": "🗑 Да, удалить", "callback_data": f"confirm_delete_{contact.id}"}],
                    [{"text": "❌ Отмена", "callback_data": f"chat_card_{contact.id}"}],
                ]
                await self.edit_message(chat_id, msg_id, text, reply_markup={"inline_keyboard": kb}, parse_mode="HTML")

            elif data.startswith("confirm_delete_"):
                contact_id = int(data.replace("confirm_delete_", ""))
                mgr_state.pending_confirmation = None
                stmt_c = select(Contact).where(Contact.id == contact_id)
                if not is_admin:
                    stmt_c = stmt_c.where(Contact.user_id == db_user_id)
                contact = (await session.execute(stmt_c)).scalars().first()
                if contact:
                    await session.delete(contact)
                    await session.commit()
                await self.answer_callback_query(cq_id, "🗑 Диалог успешно удален!")
                await self.edit_message(
                    chat_id, msg_id,
                    "🗑 <b>Диалог успешно удален!</b>\n\nКарточка и переписка удалены из панели управления.",
                    reply_markup={"inline_keyboard": [[{"text": "💬 К списку чатов", "callback_data": "menu_leads_chats"}], [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]]},
                    parse_mode="HTML"
                )

            # 3. Detailed Leads Statistics & CSV Export
            elif data.startswith("menu_leads_stats"):
                mgr_state.set_menu("menu_leads_stats")
                period = data.replace("menu_leads_stats_", "")
                if period not in ("today", "yesterday", "7d", "30d", "all"):
                    period = "today"
                await self.answer_callback_query(cq_id)

                stats = await StatsService.get_detailed_leads_stats(session, db_user_id, period=period)

                period_label = stats["period_label"]
                text = (
                    f"📊 <b>СТАТИСТИКА ОБЩЕНИЯ С ЛИДАМИ ({period_label})</b>\n\n"
                    f"📈 <b>ОБЩИЕ ПОКАЗАТЕЛИ:</b>\n"
                    f"• Всего лидов за период: <b>{stats['total_leads']}</b>\n"
                    f"• Новых клиентов: <b>{stats['new_clients']}</b>\n"
                    f"• Активных диалогов: <b>{stats['active_dialogues']}</b>\n"
                    f"• Горячих лидов (встреча / квест): <b>{stats.get('meetup_leads', 0)} / {stats.get('quest_leads', 0)}</b>\n"
                    f"• Завершенных диалогов: <b>{stats['completed_dialogues']}</b>\n"
                    f"• Заблокированных клиентов: <b>{stats['blocked_clients']}</b>\n"
                    f"• Лидов с оскорблениями/грубостью: <b>{stats['insult_leads']}</b>\n"
                    f"• Входящих текстовых сообщений: <b>{stats['incoming_text_count']}</b>\n"
                    f"• Входящих голосовых сообщений: <b>{stats['incoming_voice_count']}</b>\n"
                    f"• Отправлено голосовых ИИ: <b>{stats.get('outgoing_voice_count', 0)}</b>\n"
                    f"• Распознано голосовых: <b>{stats['voice_transcribed_count']}</b> (100%)\n"
                    f"• Среднее кол-во сообщений на клиента: <b>{stats['avg_messages_per_client']}</b>\n\n"
                    f"👥 <b>ДЕТАЛИЗАЦИЯ ПО ЛИДАМ:</b>\n"
                )

                if not stats["leads"]:
                    text += "<i>За этот период активных сообщений пока не было.</i>\n\n"
                else:
                    for idx, l in enumerate(stats["leads"][:5], 1):
                        user_str = f" (@{l['username']})" if l["username"] else ""
                        text += (
                            f"<b>{idx}. {html.escape(l['name'])}{user_str}</b> (ID: <code>{l['tg_id']}</code>)\n"
                            f"   • Статус: <b>{l['status']}</b> | Теги: <i>{l['tags']}</i>\n"
                            f"   • Сообщений: {l['total_msgs']} (войсов: {l['voice_msgs']}) | Оскорбления: {l['has_insult']}\n"
                            f"   • Темы: {l['topics']} | Решение: {l['owner_decision']}\n\n"
                        )
                    if len(stats["leads"]) > 5:
                        text += f"<i>...и еще {len(stats['leads']) - 5} лидов (доступны в полном CSV-отчете).</i>\n\n"

                text += "<i>Выберите период или скачайте полный Excel/CSV-отчет:</i>"

                today_btn = "• Сегодня •" if period == "today" else "Сегодня"
                yest_btn = "• Вчера •" if period == "yesterday" else "Вчера"
                w_btn = "• 7 дней •" if period == "7d" else "7 дней"
                m_btn = "• 30 дней •" if period == "30d" else "30 дней"
                all_btn = "• За всё время •" if period == "all" else "За всё время"

                kb = [
                    [
                        {"text": today_btn, "callback_data": "menu_leads_stats_today"},
                        {"text": yest_btn, "callback_data": "menu_leads_stats_yesterday"},
                    ],
                    [
                        {"text": w_btn, "callback_data": "menu_leads_stats_7d"},
                        {"text": m_btn, "callback_data": "menu_leads_stats_30d"},
                        {"text": all_btn, "callback_data": "menu_leads_stats_all"},
                    ],
                    [
                        {"text": "📥 Скачать CSV-отчет", "callback_data": f"export_leads_csv_{period}"},
                        {"text": "💬 Открыть чаты (CRM)", "callback_data": "menu_leads_chats"},
                    ],
                    [
                        {"text": "◀️ В главное меню", "callback_data": "menu_main"},
                    ],
                ]
                await self.edit_message(chat_id, msg_id, text, reply_markup={"inline_keyboard": kb}, parse_mode="HTML")

            elif data.startswith("export_leads_csv_"):
                period = data.replace("export_leads_csv_", "")
                await self.answer_callback_query(cq_id, "⏳ Формирую CSV-отчет...")

                csv_bytes = await StatsService.generate_leads_csv(session, db_user_id, period=period)
                today_str = datetime.now().strftime("%Y-%m-%d")
                filename = f"leads_stats_{period}_{today_str}.csv"
                caption = f"📊 <b>Подробный отчет по лидам ({period})</b>\nФайл готов для анализа в Excel / Google Таблицах."
                sent = await self.send_document(chat_id, csv_bytes, filename=filename, caption=caption)
                if sent:
                    await self.answer_callback_query(cq_id, "✅ Файл успешно отправлен в чат!", show_alert=True)
                else:
                    await self.answer_callback_query(cq_id, "❌ Не удалось отправить документ.", show_alert=True)

            elif data in ("menu_leads", "menu_leads_pending", "menu_leads_accepted"):
                await self.answer_callback_query(cq_id)
                filter_mode = "all"
                if data == "menu_leads_pending":
                    filter_mode = "pending"
                elif data == "menu_leads_accepted":
                    filter_mode = "accepted"

                leads = await StatsService.get_leads(session, db_user_id)
                text, kb = self.build_leads_view(leads, filter_mode=filter_mode)
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data == "menu_stats":
                await self.answer_callback_query(cq_id)
                stats = await StatsService.get_overview(session, db_user_id)
                text = (
                    f"📊 <b>ЖИВАЯ АНАЛИТИКА AI-КОМПАНЬОНА</b>\n\n"
                    f"👥 Собеседников в базе: <b>{stats['total_contacts']}</b>\n"
                    f"💬 Активных диалогов прямо сейчас: <b>{stats['active_dialogues']}</b>\n"
                    f"🔥 Закрыто на встречу (лиды): <b>{stats['successful_leads']}</b>\n"
                    f"📈 Конверсия во встречи: <b>{stats['conversion_rate_percent']}%</b>\n\n"
                    f"✉️ Всего входящих сообщений: {stats['total_messages_in']}\n"
                    f"🚀 Отправлено ответов ИИ: {stats['total_messages_out']}"
                )
                kb = {"inline_keyboard": [[{"text": "◀️ В меню", "callback_data": "menu_main"}]]}
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            # ------------------------------------------
            # Ultra-Realistic Voice Testing (OGG Opus with Waveform)
            # ------------------------------------------
            elif data == "voice_benchmark_test":
                await self.answer_callback_query(cq_id, "🎙 Записываю голосовое сообщение...")
                benchmark_text = "Слушай, ну я только что домой зашла, вообще без сил... Давай я сейчас быстренько в душ схожу, и минут через двадцать тебе сама наберу, хорошо?"
                await VoiceService.send_telegram_voice_note(
                    bot_token=self.token,
                    chat_id=chat_id,
                    text=benchmark_text,
                    caption="🎙 <b>Голосовое сообщение (Быстрый тест)</b>\n<i>«Слушай, ну я только что домой зашла, вообще без сил...»</i>",
                )

            elif data == "voice_custom_text":
                mgr_state.set_menu("menu_voice_test")
                self._user_input_states[user_tg_id] = "WAIT_CUSTOM_VOICE_TEXT"
                await self.answer_callback_query(cq_id)
                await self.send_message(
                    chat_id,
                    "✍️ <b>Озвучка вашего текста (Voice Lab):</b>\n\n"
                    "Отправьте любую фразу в этот чат — бот подготовит естественную фонетическую разметку "
                    "(паузы, дыхание, ударения) и пришлет готовое голосовое сообщение в формате Telegram OGG Opus:",
                    reply_markup=self.build_cancel_flow_kb("menu_voice_test"),
                    parse_mode="HTML",
                )

            elif data == "menu_voice_test":
                await self.answer_callback_query(cq_id)

                text = (
                    "🎙 <b>ТЕСТИРОВАНИЕ ГОЛОСОВЫХ СООБЩЕНИЙ</b>\n\n"
                    "В этом меню вы можете в реальном времени прослушать, как звучит живой голос девушки в диалогах.\n\n"
                    "Каждое голосовое синтезируется с естественными паузами, живыми девичьими интонациями "
                    "и аккуратной телефонной акустикой Telegram без акцента!\n\n"
                    "• 🎧 <b>Быстрый тест</b> — естественная повседневная реплика\n"
                    "• 🌸 <b>Скромная</b> — мягкая, нежная и спокойная интонация\n"
                    "• 🔥 <b>Игривая</b> — легкий кокетливый вайб\n"
                    "• ⚡ <b>Дерзкая</b> — уверенный тон с легким сарказмом\n"
                    "• ☕ <b>Заботливая</b> — теплое и участливое сообщение\n"
                    "• 🎭 <b>Адаптивная</b> — универсальный тон для любого диалога\n"
                    "• 🎲 <b>Случайная реплика</b> — проверка спонтанной речи\n\n"
                    "👉 <i>Нажмите на любой стиль ниже, чтобы прослушать аудио:</i>"
                )
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_voice_test_keyboard(), parse_mode="HTML"
                )

            elif data.startswith("sv_"):
                parts = data.split("_")
                if len(parts) >= 3:
                    cat_code = parts[1]
                    short_id = "_".join(parts[2:])
                    file_id = self._cached_voice_files.get(short_id)
                    if not file_id:
                        await self.answer_callback_query(cq_id, "⚠️ Срок действия кнопки истек, отправьте войс заново.", show_alert=True)
                    else:
                        cat_map = {
                            "greet": "greeting",
                            "meet": "meetup",
                            "sarc": "sarcastic",
                            "flirt": "flirt",
                            "shy": "shy",
                            "care": "caring",
                        }
                        tag = cat_map.get(cat_code, "greeting")
                        await self.answer_callback_query(cq_id, "⏳ Сохраняю аудиозапись...")
                        raw_bytes = await self.download_file_bytes(file_id)
                        if raw_bytes:
                            saved_path = VoiceService.save_voice_asset(tag, raw_bytes)
                            text = (
                                f"✅ <b>Живое голосовое успешно сохранено в категорию:</b> <code>{tag}</code>!\n\n"
                                f"📁 <b>Файл:</b> <code>{saved_path}</code>\n\n"
                                f"Теперь бот будет отправлять эту запись в диалогах!"
                            )
                            await self.send_message(chat_id, text, reply_markup=self.build_voice_test_keyboard(), parse_mode="HTML")
                        else:
                            await self.send_message(chat_id, "❌ Не удалось скачать файл из Telegram.")

            elif data.startswith("save_voice_"):
                parts = data.split("_")
                tag = parts[2]
                file_id = parts[3]
                await self.answer_callback_query(cq_id, "⏳ Скачиваю и оптимизирую аудио...")

                raw_bytes = await self.download_file_bytes(file_id)
                if raw_bytes:
                    saved_path = VoiceService.save_voice_asset(tag, raw_bytes)
                    text = (
                        f"✅ <b>Живое голосовое успешно сохранено в банк бота!</b>\n\n"
                        f"🏷 <b>Категория:</b> <code>{tag}</code>\n"
                        f"📁 <b>Файл:</b> <code>{saved_path}</code>\n\n"
                        f"Теперь бот будет автоматически отправлять именно эту запись в диалогах!"
                    )
                    await self.edit_message(
                        chat_id, msg_id, text, reply_markup=self.build_voice_test_keyboard(), parse_mode="HTML"
                    )
                else:
                    await self.send_message(chat_id, "❌ Не удалось скачать файл из Telegram.")

            elif data.startswith("stt_transcribe_"):
                file_id = data.replace("stt_transcribe_", "")
                await self.answer_callback_query(cq_id, "🎧 Распознаю аудио...")
                await self.send_message(chat_id, "⏳ <b>Нейросеть распознает речь...</b>", parse_mode="HTML")
                transcript, _ = await STTService.transcribe_telegram_voice(self.token, file_id)
                if transcript:
                    res_text = (
                        "🎉 <b>РЕЧЬ УСПЕШНО РАСПОЗНАНА (Speech-to-Text)!</b>\n\n"
                        f"🎙 <b>Транскрипция:</b>\n<i>«{transcript}»</i>\n\n"
                        f"✅ Система распознавания речи полностью активна и понимает все входящие голосовые от парней в личных чатах!"
                    )
                else:
                    res_text = "❌ <b>Не удалось расшифровать аудиозапись.</b> Убедитесь, что AI-ключ валиден."
                await self.send_message(chat_id, res_text, reply_markup=self.build_voice_test_keyboard(), parse_mode="HTML")

            elif data == "voice_stt_info":
                await self.answer_callback_query(cq_id)
                text = (
                    "🗣 <b>РАСПОЗНАВАНИЕ ГОЛОСА (SPEECH-TO-TEXT)</b>\n\n"
                    "В бота встроена передовая система распознавания входящей речи (Speech-to-Text).\n\n"
                    "<b>Как это работает в реальных диалогах:</b>\n"
                    "1️⃣ Когда парень записывает голосовое сообщение, бот мгновенно скачивает и транскрибирует его через нейросеть.\n"
                    "2️⃣ ИИ понимает интонацию, смысл сказанного, вопросы, сленг и эмоции собеседника.\n"
                    "3️⃣ Бот формулирует живой, точный ответ именно на его слова (а также может отправить войс в ответ!).\n"
                    "4️⃣ Если парень позвал на встречу или квест голосом — бот сразу зафиксирует лида и пришлет вам алерт с точным текстом его слов!\n\n"
                    "💡 <b>Тест прямо сейчас:</b>\n"
                    "Отправьте боту любое голосовое сообщение в этот чат — бот моментально расшифрует его в текст!"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "◀️ Назад к войсам", "callback_data": "menu_voice_test"}],
                        [{"text": "🏠 В главное меню", "callback_data": "menu_main"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data == "clone_voice_info":
                await self.answer_callback_query(cq_id)
                text = (
                    "🧬 <b>КЛОНИРОВАНИЕ ГОЛОСА (INSTANT VOICE CLONING)</b>\n\n"
                    "Мы уже вырезали и сохранили чистый эталон голоса девушки из вашего аудиосообщения:\n"
                    "📁 <code>assets/voice_pack/sarcastic_name.ogg</code>\n\n"
                    "<b>Как настроить этот голос за 1 минуту:</b>\n\n"
                    "1️⃣ Отправьте команду боту прямо в этот чат:\n"
                    "<code>/clone ВАШ_VOICE_API_KEY</code>\n\n"
                    "🤖 <b>Что сделает бот автоматически:</b>\n"
                    "• Обучит цифровую модель голоса\n"
                    "• Сохранит полученный Voice ID в конфигурации\n"
                    "• Моментально сгенерирует и пришлет вам проверочный войс в чат!"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "◀️ Назад к войсам", "callback_data": "menu_voice_test"}],
                    ]
                }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("voice_sample_"):
                target_style = data.replace("voice_sample_", "")
                if target_style == "random":
                    target_style = random.choice(["shy", "playful", "sarcastic", "caring", "adaptive_all"])

                sample_text = self.VOICE_SAMPLES.get(target_style, self.VOICE_SAMPLES["adaptive_all"])
                style_titles = {
                    "shy": "🌸 Скромная",
                    "playful": "🔥 Игривая",
                    "sarcastic": "⚡ Дерзкая",
                    "caring": "☕ Заботливая",
                    "adaptive_all": "🎭 Адаптивная",
                }
                style_title = style_titles.get(target_style, "Живой голос")

                await self.answer_callback_query(cq_id, f"🎙 Отправляю войс: {style_title}...")

                text = (
                    f"🎙 <b>ГОЛОСОВОЕ СООБЩЕНИЕ: {style_title.upper()}</b>\n\n"
                    f"Текст: <i>«{sample_text}»</i>\n\n"
                    f"✨ Голосовое отправлено в чат! Выберите следующий стиль для проверки:"
                )
                await self.edit_message(
                    chat_id, msg_id, text, reply_markup=self.build_voice_test_keyboard(), parse_mode="HTML"
                )

                await VoiceService.send_telegram_voice_note(
                    bot_token=self.token,
                    chat_id=chat_id,
                    text=sample_text,
                    caption=f"🎙 <b>Голосовое сообщение ({style_title})</b>\n<i>«{sample_text}»</i>",
                )

            # ------------------------------------------
            # Connected Accounts
            # ------------------------------------------
            # ------------------------------------------
            # Connected Accounts
            # ------------------------------------------
            elif data == "menu_accounts":
                await self.answer_callback_query(cq_id)
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                if accounts:
                    personas = {}
                    lines = []
                    for idx, acc in enumerate(accounts, 1):
                        p = await AccountService.ensure_account_persona(session, acc)
                        personas[acc.id] = p
                        if acc.is_active and acc.status == AccountStatus.CONNECTED:
                            status_badge = "🟢 Активен (Бот отвечает)"
                        elif acc.status == AccountStatus.DISCONNECTED or not acc.is_active:
                            status_badge = "⏸ Отключен (Пауза)"
                        else:
                            status_badge = f"⚠️ {acc.status.value if hasattr(acc.status, 'value') else acc.status}"

                        acc_title = acc.first_name or (f"@{acc.username}" if acc.username else f"ID {acc.tg_user_id or acc.id}")
                        cur_style = p.current_style or "adaptive_all"
                        style_title = StyleType(cur_style).title if cur_style in StyleType._value2member_map_ else cur_style
                        lines.append(
                            f"<b>{idx}. {acc_title}</b> ({status_badge})\n"
                            f"   • Имя в чатах: <b>{p.name}</b>, {p.age} лет, г. {p.city}\n"
                            f"   • Стиль общения: <b>{style_title}</b>\n"
                            f"   • Контекст дейтинга: <i>{p.dating_app_context or 'Стандартный'}</i>"
                        )

                    text = (
                        "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                        f"Всего подключено: <b>{len(accounts)} акк.</b>\n\n"
                        + "\n\n".join(lines) + "\n\n"
                        "💡 <i>Нажмите <b>«⚙️ Легенда»</b> или <b>«🎭 Стиль»</b> для индивидуальной настройки каждого аккаунта:</i>"
                    )
                    kb = self.build_accounts_keyboard(accounts, personas)
                else:
                    text = (
                        "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                        "У вас пока не подключено ни одного аккаунта.\n\n"
                        "<b>Как подключить аккаунт за 1 минуту:</b>\n"
                        "1. Нажмите <b>«➕ Подключить аккаунт»</b> ниже.\n"
                        "2. Откройте Telegram на рабочем аккаунте: <i>Настройки ➡️ Telegram Business ➡️ Чат-боты</i>.\n"
                        f"3. Выберите бота <code>@{settings.BOT_USERNAME}</code> и сохраните."
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Подключить аккаунт (Создать ссылку)", "callback_data": "menu_invites"}],
                            [{"text": "ℹ️ Инструкция по настройке", "callback_data": "menu_business_guide"}],
                            [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                        ]
                    }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data == "menu_business_guide":
                await self.answer_callback_query(cq_id)
                text = (
                    "ℹ️ <b>ИНСТРУКЦИЯ ПО НАСТРОЙКЕ TELEGRAM BUSINESS</b>\n\n"
                    "Чтобы бот корректно видел все переписки и отвечал клиентам:\n\n"
                    "1️⃣ <b>Откройте настройки Telegram на рабочем аккаунте:</b>\n"
                    "<b>Настройки ➔ Telegram Business ➔ Чат-боты</b> ➔ нажмите на нашего бота.\n\n"
                    "2️⃣ <b>Раздел «Чаты»:</b>\n"
                    "Обязательно выберите вариант <b>«Все личные чаты»</b>.\n"
                    "<i>⚠️ Если выбрать «Новые чаты», Telegram не будет передавать боту уже начатые диалоги!</i>\n\n"
                    "3️⃣ <b>Раздел «Разрешения»:</b>\n"
                    "Убедитесь, что переключатель <b>«Отвечать на сообщения»</b> включен.\n\n"
                    "4️⃣ <b>Как бот начинает отвечать:</b>\n"
                    "• Telegram передает боту входящие сообщения в режиме реального времени.\n"
                    "• Если у вас есть старые непрочитанные диалоги за прошлые дни, напишите клиенту любое сообщение сами — и бот подхватит диалог!\n"
                    "• Если вы напишете в чат вручную, бот временно приостановится на 15 минут, а затем автоматически возобновит автоответ."
                )
                await self.edit_message(
                    chat_id, msg_id, text,
                    reply_markup={"inline_keyboard": [
                        [{"text": "📱 Мои аккаунты", "callback_data": "menu_accounts"}],
                        [{"text": "◀️ В главное меню", "callback_data": "menu_main"}]
                    ]},
                    parse_mode="HTML"
                )

            elif data.startswith("disconnect_acc_"):
                acc_id = int(data.replace("disconnect_acc_", ""))
                stmt_a = select(AccountSession).where(AccountSession.id == acc_id)
                if not is_admin:
                    stmt_a = stmt_a.where(AccountSession.user_id == db_user_id)
                target_acc = (await session.execute(stmt_a)).scalars().first()
                ok = await AccountService.disconnect_account(session, acc_id) if target_acc else False
                if ok:
                    await self.answer_callback_query(cq_id, "⏸ Аккаунт отключен")
                else:
                    await self.answer_callback_query(cq_id, "⚠️ Аккаунт не найден", show_alert=True)
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                personas = {}
                lines = []
                for idx, acc in enumerate(accounts, 1):
                    p = await AccountService.ensure_account_persona(session, acc)
                    personas[acc.id] = p
                    if acc.is_active and acc.status == AccountStatus.CONNECTED:
                        status_badge = "🟢 Активен (Бот отвечает)"
                    elif acc.status == AccountStatus.DISCONNECTED or not acc.is_active:
                        status_badge = "⏸ Отключен (Пауза)"
                    else:
                        status_badge = f"⚠️ {acc.status.value if hasattr(acc.status, 'value') else acc.status}"
                    acc_title = acc.first_name or (f"@{acc.username}" if acc.username else f"ID {acc.tg_user_id or acc.id}")
                    cur_style = p.current_style or "adaptive_all"
                    style_title = StyleType(cur_style).title if cur_style in StyleType._value2member_map_ else cur_style
                    lines.append(
                        f"<b>{idx}. {acc_title}</b> ({status_badge})\n"
                        f"   • Имя в чатах: <b>{p.name}</b>, {p.age} лет, г. {p.city}\n"
                        f"   • Стиль общения: <b>{style_title}</b>"
                    )
                text = (
                    "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                    f"Всего подключено: <b>{len(accounts)} акк.</b>\n\n"
                    + "\n\n".join(lines) + "\n\n"
                    "💡 <i>Нажмите <b>«⚙️ Легенда»</b> или <b>«🎭 Стиль»</b> для индивидуальной настройки:</i>"
                )
                kb = self.build_accounts_keyboard(accounts, personas)
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("reconnect_acc_"):
                acc_id = int(data.replace("reconnect_acc_", ""))
                stmt_a = select(AccountSession).where(AccountSession.id == acc_id)
                if not is_admin:
                    stmt_a = stmt_a.where(AccountSession.user_id == db_user_id)
                target_acc = (await session.execute(stmt_a)).scalars().first()
                acc = await AccountService.toggle_account(session, acc_id, is_active=True) if target_acc else None
                if acc:
                    await self.answer_callback_query(cq_id, "▶️ Аккаунт включен")
                else:
                    await self.answer_callback_query(cq_id, "⚠️ Аккаунт не найден", show_alert=True)
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                personas = {}
                lines = []
                for idx, a in enumerate(accounts, 1):
                    p = await AccountService.ensure_account_persona(session, a)
                    personas[a.id] = p
                    if a.is_active and a.status == AccountStatus.CONNECTED:
                        status_badge = "🟢 Активен (Бот отвечает)"
                    elif a.status == AccountStatus.DISCONNECTED or not a.is_active:
                        status_badge = "⏸ Отключен (Пауза)"
                    else:
                        status_badge = f"⚠️ {a.status.value if hasattr(a.status, 'value') else a.status}"
                    acc_title = a.first_name or (f"@{a.username}" if a.username else f"ID {a.tg_user_id or a.id}")
                    cur_style = p.current_style or "adaptive_all"
                    style_title = StyleType(cur_style).title if cur_style in StyleType._value2member_map_ else cur_style
                    lines.append(
                        f"<b>{idx}. {acc_title}</b> ({status_badge})\n"
                        f"   • Имя в чатах: <b>{p.name}</b>, {p.age} лет, г. {p.city}\n"
                        f"   • Стиль общения: <b>{style_title}</b>"
                    )
                text = (
                    "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                    f"Всего подключено: <b>{len(accounts)} акк.</b>\n\n"
                    + "\n\n".join(lines) + "\n\n"
                    "💡 <i>Нажмите <b>«⚙️ Легенда»</b> или <b>«🎭 Стиль»</b> для индивидуальной настройки:</i>"
                )
                kb = self.build_accounts_keyboard(accounts, personas)
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")

            elif data.startswith("delete_acc_"):
                acc_id = int(data.replace("delete_acc_", ""))
                stmt_a = select(AccountSession).where(AccountSession.id == acc_id)
                if not is_admin:
                    stmt_a = stmt_a.where(AccountSession.user_id == db_user_id)
                target_acc = (await session.execute(stmt_a)).scalars().first()
                ok = await AccountService.delete_account(session, acc_id) if target_acc else False
                if ok:
                    await self.answer_callback_query(cq_id, "🗑 Аккаунт удален")
                else:
                    await self.answer_callback_query(cq_id, "⚠️ Аккаунт не найден", show_alert=True)
                accounts = await AccountService.get_user_accounts(session, db_user_id)
                if accounts:
                    personas = {}
                    lines = []
                    for idx, a in enumerate(accounts, 1):
                        p = await AccountService.ensure_account_persona(session, a)
                        personas[a.id] = p
                        if a.is_active and a.status == AccountStatus.CONNECTED:
                            status_badge = "🟢 Активен (Бот отвечает)"
                        elif a.status == AccountStatus.DISCONNECTED or not a.is_active:
                            status_badge = "⏸ Отключен (Пауза)"
                        else:
                            status_badge = f"⚠️ {a.status.value if hasattr(a.status, 'value') else a.status}"
                        acc_title = a.first_name or (f"@{a.username}" if a.username else f"ID {a.tg_user_id or a.id}")
                        cur_style = p.current_style or "adaptive_all"
                        style_title = StyleType(cur_style).title if cur_style in StyleType._value2member_map_ else cur_style
                        lines.append(
                            f"<b>{idx}. {acc_title}</b> ({status_badge})\n"
                            f"   • Имя в чатах: <b>{p.name}</b>, {p.age} лет, г. {p.city}\n"
                            f"   • Стиль общения: <b>{style_title}</b>"
                        )
                    text = (
                        "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                        f"Всего подключено: <b>{len(accounts)} акк.</b>\n\n"
                        + "\n\n".join(lines) + "\n\n"
                        "💡 <i>Нажмите <b>«⚙️ Легенда»</b> или <b>«🎭 Стиль»</b> для индивидуальной настройки:</i>"
                    )
                    kb = self.build_accounts_keyboard(accounts, personas)
                else:
                    text = (
                        "📱 <b>ПОДКЛЮЧЕННЫЕ АККАУНТЫ TELEGRAM</b>\n\n"
                        "У вас пока не подключено ни одного аккаунта.\n\n"
                        "<b>Как подключить аккаунт за 1 минуту:</b>\n"
                        "1. Нажмите <b>«➕ Подключить аккаунт»</b> ниже.\n"
                        "2. Откройте Telegram на рабочем аккаунте: <i>Настройки ➡️ Telegram Business ➡️ Чат-боты</i>.\n"
                        f"3. Выберите бота <code>@{settings.BOT_USERNAME}</code> и сохраните."
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "➕ Подключить аккаунт (Создать ссылку)", "callback_data": "menu_invites"}],
                            [{"text": "◀️ В главное меню", "callback_data": "menu_main"}],
                        ]
                    }
                await self.edit_message(chat_id, msg_id, text, reply_markup=kb, parse_mode="HTML")
