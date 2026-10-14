import asyncio

from aiogram.types import Update
from pydantic import ValidationError

from app.contracts import BotInfo, ConnectionState
from app.errors import AppError
from app.messaging.sender import TERMINAL_CODES, MessageSender
from app.telegram.handlers import create_dispatcher


class TelegramRuntime:
    def __init__(self, store, gateway, bot: BotInfo):
        self.store = store
        self.gateway = gateway
        self.bot = bot
        self.connection = ConnectionState()
        self.dispatcher = create_dispatcher()
        self.sender = MessageSender(store, gateway, bot.id)
        self._tasks: list[asyncio.Task] = []
        self._halt = asyncio.Event()
        self._stop_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._terminal_started = False
        self.poll_timeout = 25
        self.idle_delay = 0.2
        self.retry_delay = 1.0
        self.shutdown_timeout = 3.0

    async def start(self) -> None:
        async with self._stop_lock:
            if self._tasks:
                return
            if self._closed:
                raise RuntimeError("A stopped Telegram runtime cannot be restarted")
            await self.store.recover_sending(self.bot.id)
            self.connection = ConnectionState(status="connected")
            self._tasks = [
                asyncio.create_task(self._collect(), name="telegram-collector"),
                asyncio.create_task(self._consume(), name="telegram-inbox"),
                asyncio.create_task(self._send(), name="telegram-sender"),
            ]

    async def _pause(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._halt.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _close_gateway(self) -> None:
        async with self._close_lock:
            if not self._closed:
                self._closed = True
                await self.gateway.close()

    async def _terminal(self, error: AppError) -> None:

        if self._terminal_started:
            return
        self._terminal_started = True
        self.connection = ConnectionState(
            status="error", error_code=error.code, message=error.message
        )
        self._halt.set()
        current = asyncio.current_task()
        peers = [task for task in self._tasks if task is not current]
        for task in peers:
            task.cancel()
        await asyncio.gather(*peers, return_exceptions=True)
        await self._close_gateway()

    def _reconnecting(self, message: str) -> None:
        if not self._halt.is_set():
            self.connection = ConnectionState(
                status="reconnecting",
                error_code="TELEGRAM_UNAVAILABLE",
                message=message,
            )

    async def _collect(self) -> None:
        backoff = self.retry_delay
        while not self._halt.is_set():
            try:
                offset = await self.store.get_offset(self.bot.id)
                updates = await self.gateway.get_updates(offset, self.poll_timeout)
                if updates:
                    await self.store.persist_updates(self.bot.id, updates)
                if not self._halt.is_set():
                    self.connection = ConnectionState(status="connected")
                backoff = self.retry_delay
                if not updates:
                    await self._pause(self.idle_delay)
            except AppError as error:
                if error.code in TERMINAL_CODES:
                    await self._terminal(error)
                    return
                self._reconnecting(error.message)
                await self._pause(max(backoff, error.retry_after or 0))
                backoff = min(30, backoff * 2)
            except Exception:
                self._reconnecting(
                    "Не удалось сохранить или получить события. Повторяем подключение."
                )
                await self._pause(backoff)
                backoff = min(30, backoff * 2)

    async def _consume(self) -> None:
        while not self._halt.is_set():
            try:
                pending = await self.store.pending_updates(self.bot.id)
                for raw in pending:
                    if self._halt.is_set():
                        return
                    try:
                        update = Update.model_validate(raw)
                    except ValidationError:
                        await self.store.fail_update(
                            self.bot.id, raw["update_id"], "MALFORMED_UPDATE"
                        )
                        continue
                    action = await self.dispatcher.feed_update(self.gateway.bot, update)
                    await self.store.apply_update(
                        self.bot.id,
                        update.update_id,
                        action if isinstance(action, dict) else None,
                    )
                if not pending:
                    await self._pause(self.idle_delay)
            except Exception:
                self._reconnecting(
                    "Не удалось обработать сохранённые сообщения. Повторяем обработку."
                )
                await self._pause(self.retry_delay)

    async def _send(self) -> None:
        while not self._halt.is_set():
            try:
                if not await self.sender.send_one():
                    await self._pause(self.idle_delay)
            except AppError as error:
                if error.code in TERMINAL_CODES:
                    await self._terminal(error)
                    return
                await self._pause(self.retry_delay)
            except Exception:
                self._reconnecting("Не удалось сохранить состояние отправки.")
                await self._pause(self.retry_delay)

    async def stop(self) -> None:
        async with self._stop_lock:
            self._halt.set()
            current = asyncio.current_task()
            tasks = [task for task in self._tasks if task is not current]

            for task in tasks:
                if task.get_name() == "telegram-collector":
                    task.cancel()
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=self.shutdown_timeout)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._close_gateway()
            if self.connection.status != "error":
                self.connection = ConnectionState(status="disconnected")
