import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Subscription
from companion.models.enums import PlanDuration
from companion.bot.telegram_bot_engine import TelegramBotEngine


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_01_menu_command_direct_execution(async_session, monkeypatch):
    """Verifies that /menu executes immediately and returns formatted menu with buttons."""
    monkeypatch.setattr("companion.bot.telegram_bot_engine.async_session_maker", lambda: async_session)

    bot = TelegramBotEngine("mock_token", admin_id=8541203642)
    sent_messages = []

    async def mock_send_message(chat_id, text, reply_markup=None, parse_mode="HTML", reply_to_message_id=None):
        sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return True

    bot.send_message = mock_send_message

    message = {
        "message_id": 101,
        "from": {"id": 8541203642, "is_bot": False, "first_name": "Admin"},
        "chat": {"id": 8541203642, "type": "private"},
        "text": "/menu",
    }

    await bot.handle_message(message)

    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == 8541203642
    assert "AI-Компаньон" in sent_messages[0]["text"]
    assert "Панель Главного Администратора" in sent_messages[0]["text"]
    assert sent_messages[0]["reply_markup"] is not None
    # Check that main action buttons exist
    kb = sent_messages[0]["reply_markup"].get("inline_keyboard", [])
    all_buttons = [btn["text"] for row in kb for btn in row]
    assert any("Анкета" in b for b in all_buttons)
    assert any("Аккаунты" in b for b in all_buttons)


@pytest.mark.asyncio
async def test_02_menu_command_with_bot_username_suffix(async_session, monkeypatch):
    """Verifies that /menu@YourHelperW_bot normalizes cleanly and opens menu."""
    monkeypatch.setattr("companion.bot.telegram_bot_engine.async_session_maker", lambda: async_session)

    bot = TelegramBotEngine("mock_token", admin_id=8541203642)
    sent_messages = []

    async def mock_send_message(chat_id, text, reply_markup=None, parse_mode="HTML", reply_to_message_id=None):
        sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return True

    bot.send_message = mock_send_message

    message = {
        "message_id": 102,
        "from": {"id": 8541203642, "is_bot": False, "first_name": "Admin"},
        "chat": {"id": 8541203642, "type": "private"},
        "text": "/menu@YourHelperW_bot",
    }

    await bot.handle_message(message)

    assert len(sent_messages) == 1
    assert "AI-Компаньон" in sent_messages[0]["text"]
    assert "Панель Главного Администратора" in sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_03_start_command_with_bot_username_suffix(async_session, monkeypatch):
    """Verifies that /start@YourHelperW_bot normalizes cleanly and opens menu."""
    monkeypatch.setattr("companion.bot.telegram_bot_engine.async_session_maker", lambda: async_session)

    bot = TelegramBotEngine("mock_token", admin_id=8541203642)
    sent_messages = []

    async def mock_send_message(chat_id, text, reply_markup=None, parse_mode="HTML", reply_to_message_id=None):
        sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return True

    bot.send_message = mock_send_message

    message = {
        "message_id": 103,
        "from": {"id": 8541203642, "is_bot": False, "first_name": "Admin"},
        "chat": {"id": 8541203642, "type": "private"},
        "text": "/start@YourHelperW_bot",
    }

    await bot.handle_message(message)

    assert len(sent_messages) == 1
    assert "AI-Компаньон" in sent_messages[0]["text"]
    assert "Панель Главного Администратора" in sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_04_get_updates_handles_502_bad_gateway_cleanly():
    """Verifies that get_updates survives 502 Bad Gateway without crashing and returns empty list."""
    bot = TelegramBotEngine("mock_token", admin_id=8541203642)

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.text = '{"ok":false,"error_code":502,"description":"Bad Gateway"}'
    mock_client.post.return_value = mock_resp

    bot.client = mock_client
    bot.poll_client = mock_client
    bot._running = True

    # Call get_updates with short timeout
    updates = await bot.get_updates(offset=0, timeout=1)

    assert updates == []
    assert mock_client.post.called


@pytest.mark.asyncio
async def test_05_get_updates_handles_read_timeout_cleanly():
    """Verifies that get_updates survives httpx.ReadTimeout and returns empty list."""
    bot = TelegramBotEngine("mock_token", admin_id=8541203642)

    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ReadTimeout("Read timed out")

    bot.client = mock_client
    bot.poll_client = mock_client
    bot._running = True

    updates = await bot.get_updates(offset=0, timeout=1)

    assert updates == []


@pytest.mark.asyncio
async def test_06_send_message_retries_on_502_and_succeeds():
    """Verifies that send_message retries when Telegram returns 502 and succeeds on retry."""
    bot = TelegramBotEngine("mock_token", admin_id=8541203642)

    mock_client = AsyncMock()
    resp_502 = MagicMock()
    resp_502.status_code = 502
    resp_502.text = "Bad Gateway"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 999}}

    mock_client.post.side_effect = [resp_502, resp_200]
    bot.client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        success = await bot.send_message(chat_id=8541203642, text="Test Message")

    assert success is True
    assert mock_client.post.call_count == 2
    mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_07_handle_update_dispatches_message(async_session, monkeypatch):
    """Verifies that handle_update passes message updates directly to handle_message."""
    monkeypatch.setattr("companion.bot.telegram_bot_engine.async_session_maker", lambda: async_session)

    bot = TelegramBotEngine("mock_token", admin_id=8541203642)
    handled_messages = []

    async def mock_handle_message(msg):
        handled_messages.append(msg)

    bot.handle_message = mock_handle_message

    update = {
        "update_id": 10001,
        "message": {
            "message_id": 555,
            "from": {"id": 8541203642, "first_name": "Admin"},
            "chat": {"id": 8541203642, "type": "private"},
            "text": "/menu",
        },
    }

    await bot.handle_update(update)

    assert len(handled_messages) == 1
    assert handled_messages[0]["text"] == "/menu"
