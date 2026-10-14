from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetUpdates, SendMessage

from app.errors import AppError
from app.telegram.gateway import TelegramGateway


def gateway():
    instance = TelegramGateway("123456:THIS_IS_A_FAKE_TOKEN_FOR_UNIT_TESTS")
    instance.bot = SimpleNamespace(
        send_message=AsyncMock(),
        get_updates=AsyncMock(),
        get_me=AsyncMock(),
        get_webhook_info=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )
    return instance


@pytest.mark.parametrize(
    "error_class,code,uncertain",
    [
        (TelegramUnauthorizedError, "INVALID_BOT_TOKEN", False),
        (TelegramForbiddenError, "RECIPIENT_UNAVAILABLE", False),
        (TelegramConflictError, "BOT_POLLING_CONFLICT", False),
        (TelegramBadRequest, "TELEGRAM_UNAVAILABLE", False),
        (TelegramNetworkError, "SEND_RESULT_UNKNOWN", True),
        (TelegramServerError, "SEND_RESULT_UNKNOWN", True),
    ],
)
async def test_send_errors_are_safe_and_classified(error_class, code, uncertain):
    client = gateway()
    client.bot.send_message.side_effect = error_class(
        method=SendMessage(chat_id=42, text="test"),
        message="secret-token-and-user-content",
    )
    with pytest.raises(AppError) as caught:
        await client.send_text("42", "test")
    assert caught.value.code == code
    assert caught.value.uncertain is uncertain
    assert "secret-token" not in str(caught.value)


async def test_read_network_error_is_retryable_and_send_retry_after_is_explicit():
    client = gateway()
    client.bot.get_updates.side_effect = TelegramNetworkError(
        method=GetUpdates(), message="secret"
    )
    with pytest.raises(AppError) as caught:
        await client.get_updates(12)
    assert caught.value.code == "TELEGRAM_UNAVAILABLE"
    assert caught.value.uncertain is False
    client.bot.send_message.side_effect = TelegramRetryAfter(
        method=SendMessage(chat_id=42, text="test"), message="secret", retry_after=7
    )
    with pytest.raises(AppError) as caught:
        await client.send_text("42", "test")
    assert caught.value.retry_after == 7
    assert caught.value.uncertain is False


async def test_plain_text_preserved_and_session_closed_once():
    client = gateway()
    text = "<b>Привет</b>\n*буквально* 😀"
    client.bot.send_message.return_value = SimpleNamespace(
        message_id=99, date=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert (await client.send_text("42", text))["telegram_message_id"] == "99"
    kwargs = client.bot.send_message.call_args.kwargs
    assert kwargs["text"] == text
    assert kwargs["parse_mode"] is None
    assert kwargs["link_preview_options"].is_disabled is True
    await client.close()
    await client.close()
    client.bot.session.close.assert_awaited_once()


@pytest.mark.parametrize("text", ["", " \n ", "x" * 4097])
async def test_invalid_text_never_calls_telegram(text):
    client = gateway()
    with pytest.raises(AppError, match="4096"):
        await client.send_text("42", text)
    client.bot.send_message.assert_not_called()


def test_invalid_token_has_safe_error():
    with pytest.raises(AppError) as caught:
        TelegramGateway("bad-secret")
    assert caught.value.code == "INVALID_BOT_TOKEN"
    assert "bad-secret" not in str(caught.value)
