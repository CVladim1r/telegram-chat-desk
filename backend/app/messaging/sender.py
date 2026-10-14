import asyncio
from datetime import datetime, timedelta, timezone

from app.errors import AppError

TERMINAL_CODES = {"INVALID_BOT_TOKEN", "BOT_POLLING_CONFLICT"}
UNKNOWN_MESSAGE = "Не удалось подтвердить отправку. Проверьте переписку перед повтором."


class MessageSender:
    def __init__(self, store, gateway, bot_id: str):
        self.store = store
        self.gateway = gateway
        self.bot_id = bot_id
        self.chat_interval = 1.0
        self._chat_due: dict[str, datetime] = {}
        self.persistence_retry_delay = 0.5

    async def _finish(self, message_id: int, status: str, **kwargs) -> None:

        delay = self.persistence_retry_delay
        while True:
            try:
                await self.store.finish_message(
                    self.bot_id, message_id, status, **kwargs
                )
                return
            except Exception:
                await asyncio.sleep(delay)
                delay = min(5.0, delay * 2)

    async def send_one(self) -> bool:
        message = await self.store.claim_next_message(self.bot_id)
        if message is None:
            return False
        now = datetime.now(timezone.utc)
        due = self._chat_due.get(message.telegram_chat_id)
        if due is not None and due > now:
            await self._finish(message.id, "queued", retry_at=due.isoformat())
            return True
        self._chat_due[message.telegram_chat_id] = now + timedelta(
            seconds=self.chat_interval
        )
        try:
            result = await self.gateway.send_text(
                message.telegram_chat_id, message.text
            )
        except asyncio.CancelledError:
            try:
                await self.store.finish_message(
                    self.bot_id,
                    message.id,
                    "unknown",
                    error_code="SEND_RESULT_UNKNOWN",
                    error_message=UNKNOWN_MESSAGE,
                )
            except Exception:
                pass
            raise
        except AppError as error:
            if error.code == "RATE_LIMITED" and not error.uncertain:
                retry_at = datetime.now(timezone.utc) + timedelta(
                    seconds=max(1, error.retry_after or 1)
                )
                await self._finish(
                    message.id,
                    "queued",
                    error_code=error.code,
                    error_message=error.message,
                    retry_at=retry_at.isoformat(),
                )
            else:
                await self._finish(
                    message.id,
                    "unknown" if error.uncertain else "failed",
                    error_code=error.code,
                    error_message=error.message,
                )
            if error.code in TERMINAL_CODES:
                raise
        except Exception:
            await self._finish(
                message.id,
                "unknown",
                error_code="SEND_RESULT_UNKNOWN",
                error_message=UNKNOWN_MESSAGE,
            )
        else:
            await self._finish(
                message.id, "sent", telegram_message_id=result["telegram_message_id"]
            )
        return True
