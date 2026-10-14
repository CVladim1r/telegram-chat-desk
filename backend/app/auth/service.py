import asyncio
import secrets
import time

from app.contracts import SessionResponse
from app.errors import AppError


class SessionService:
    def __init__(self, store, gateway_factory, runtime_factory, settings):
        self.store = store
        self.gateway_factory = gateway_factory
        self.runtime_factory = runtime_factory
        self.settings = settings
        self.runtime = None
        self.bot = None
        self._sessions = {}
        self._lock = asyncio.Lock()

    def _new_session(self):
        current = time.monotonic()
        self._sessions = {
            key: value for key, value in self._sessions.items() if value[1] > current
        }
        ident = secrets.token_urlsafe(32)
        self._sessions[ident] = (
            self.bot.id,
            current + self.settings.session_ttl_seconds,
        )
        return ident, SessionResponse(bot=self.bot, connection=self.runtime.connection)

    async def connect(self, token):
        async with self._lock:
            gateway = self.gateway_factory(token.strip())
            owns_gateway = True
            try:
                bot = await gateway.get_me()
                revoked = (
                    self.runtime is not None
                    and self.runtime.connection.error_code == "INVALID_BOT_TOKEN"
                )
                if self.bot is not None and self.bot.id != bot.id and not revoked:
                    raise AppError(
                        "BOT_ALREADY_CONNECTED",
                        "Уже подключён другой бот. Сначала отключите его.",
                        409,
                    )
                if await gateway.webhook_url():
                    raise AppError(
                        "BOT_WEBHOOK_ACTIVE",
                        "У бота настроен webhook. Отключите его в прежней интеграции перед подключением.",
                        409,
                    )
                if (
                    self.runtime is not None
                    and self.runtime.connection.status != "error"
                ):
                    return self._new_session()
                if self.runtime is not None:
                    await self.runtime.stop()
                    self.runtime = None
                    self._sessions.clear()
                await self.store.upsert_bot(bot)
                runtime = self.runtime_factory(self.store, gateway, bot)
                try:
                    await runtime.start()
                except BaseException:
                    owns_gateway = False
                    await runtime.stop()
                    raise
                self.runtime = runtime
                self.bot = bot
                owns_gateway = False
                return self._new_session()
            finally:
                if owns_gateway:
                    await gateway.close()

    def _require(self, session_id):
        session = self._sessions.get(session_id)
        if (
            session is None
            or session[1] <= time.monotonic()
            or self.bot is None
            or self.runtime is None
            or session[0] != self.bot.id
        ):
            self._sessions.pop(session_id, None)
            raise AppError(
                "SESSION_REQUIRED", "Подключите бота, чтобы открыть чат.", 401
            )
        if self.runtime.connection.error_code == "INVALID_BOT_TOKEN":
            self._sessions.clear()
            raise AppError(
                "INVALID_BOT_TOKEN",
                "Токен бота отозван или недействителен. Подключите бота заново.",
                401,
            )
        return SessionResponse(bot=self.bot, connection=self.runtime.connection)

    async def require(self, session_id):
        async with self._lock:
            try:
                return self._require(session_id)
            except AppError as exc:
                if exc.code == "INVALID_BOT_TOKEN":
                    await self.runtime.stop()
                    self.runtime = None
                    self.bot = None
                raise

    async def logout(self, session_id):
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def disconnect(self, session_id):
        async with self._lock:
            self._require(session_id)
            if self.runtime is not None:
                await self.runtime.stop()
            self.runtime = None
            self.bot = None
            self._sessions.clear()

    async def close(self):
        async with self._lock:
            if self.runtime is not None:
                await self.runtime.stop()
            self.runtime = None
            self.bot = None
            self._sessions.clear()
