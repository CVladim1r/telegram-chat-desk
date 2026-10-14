from datetime import timezone
from typing import Awaitable, TypeVar

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import LinkPreviewOptions
from aiogram.utils.token import TokenValidationError

from app.contracts import BotInfo
from app.errors import AppError

T = TypeVar("T")


class TelegramGateway:
    def __init__(self, token: str):
        try:
            self.bot = Bot(token=token)
        except (TokenValidationError, ValueError):
            raise AppError("INVALID_BOT_TOKEN", "Неверный токен бота.", 401) from None
        self._closed = False

    async def _call(self, operation: Awaitable[T], *, sending: bool = False) -> T:
        try:
            return await operation
        except TelegramUnauthorizedError:
            raise AppError(
                "INVALID_BOT_TOKEN",
                "Токен бота недействителен. Подключите бота заново.",
                401,
            ) from None
        except TelegramConflictError:
            raise AppError(
                "BOT_POLLING_CONFLICT",
                "Этот бот уже получает события в другом приложении.",
                409,
            ) from None
        except TelegramForbiddenError:
            raise AppError(
                "RECIPIENT_UNAVAILABLE",
                "Получатель запретил боту отправлять сообщения.",
                403,
            ) from None
        except TelegramRetryAfter as error:
            raise AppError(
                "RATE_LIMITED",
                "Telegram ограничил частоту запросов. Отправка будет повторена автоматически.",
                429,
                retry_after=max(1, error.retry_after),
            ) from None
        except TelegramBadRequest:
            raise AppError(
                "TELEGRAM_UNAVAILABLE",
                "Telegram отклонил запрос. Проверьте текст и доступность получателя.",
                422,
            ) from None
        except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError):
            if sending:
                raise AppError(
                    "SEND_RESULT_UNKNOWN",
                    "Не удалось подтвердить отправку. Проверьте переписку перед повтором.",
                    502,
                    uncertain=True,
                ) from None
            raise AppError(
                "TELEGRAM_UNAVAILABLE",
                "Связь с Telegram потеряна. Повторяем подключение.",
                503,
            ) from None
        except Exception:
            raise AppError(
                "SEND_RESULT_UNKNOWN" if sending else "TELEGRAM_UNAVAILABLE",
                "Не удалось получить подтверждение Telegram.",
                502 if sending else 503,
                uncertain=sending,
            ) from None

    async def get_me(self) -> BotInfo:
        user = await self._call(self.bot.get_me())

        self.bot._me = user
        return BotInfo(
            id=str(user.id), username=user.username or "", first_name=user.first_name
        )

    async def webhook_url(self) -> str:
        result = await self._call(self.bot.get_webhook_info())
        return result.url

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        updates = await self._call(
            self.bot.get_updates(
                offset=offset,
                timeout=timeout,
                limit=100,
                allowed_updates=["message", "my_chat_member"],
                request_timeout=timeout + 10,
            )
        )
        return [update.model_dump(mode="json", exclude_none=True) for update in updates]

    async def send_text(self, telegram_chat_id: str, text: str) -> dict:
        if not text.strip() or len(text) > 4096:
            raise AppError("INVALID_TEXT", "Введите текст от 1 до 4096 символов.", 422)
        result = await self._call(
            self.bot.send_message(
                chat_id=int(telegram_chat_id),
                text=text,
                parse_mode=None,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ),
            sending=True,
        )
        return {
            "telegram_message_id": str(result.message_id),
            "created_at": result.date.astimezone(timezone.utc).isoformat(),
        }

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self.bot.session.close()
