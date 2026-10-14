import asyncio
from types import SimpleNamespace

import pytest

from app.auth import SessionService
from app.contracts import BotInfo, ConnectionState
from app.errors import AppError


class FakeStore:
    async def upsert_bot(self, bot):
        self.bot = bot


class Gateway:
    instances = []

    def __init__(self, token):
        self.token = token
        self.closed = 0
        self.instances.append(self)

    async def get_me(self):
        if self.token == "bad":
            raise AppError("INVALID_BOT_TOKEN", "Неверный токен.", 401)
        return BotInfo(
            id="2" if self.token == "other" else "1", username="bot", first_name="Bot"
        )

    async def webhook_url(self):
        return "https://example.com/webhook" if self.token == "webhook" else ""

    async def close(self):
        self.closed += 1


class Runtime:
    starts = 0

    def __init__(self, store, gateway, bot):
        self.gateway = gateway
        self.connection = ConnectionState()
        self.stops = 0

    async def start(self):
        Runtime.starts += 1
        self.connection = ConnectionState(status="connected")

    async def stop(self):
        self.stops += 1
        await self.gateway.close()


@pytest.fixture
async def service():
    Gateway.instances = []
    Runtime.starts = 0
    result = SessionService(
        FakeStore(), Gateway, Runtime, SimpleNamespace(session_ttl_seconds=3600)
    )
    yield result
    await result.close()


async def test_two_tabs_share_runtime_and_logout_keeps_it(service):
    first, second = await asyncio.gather(
        service.connect("good"), service.connect("good")
    )
    assert Runtime.starts == 1
    assert first[0] != second[0]
    assert Gateway.instances[1].closed == 1
    await service.logout(first[0])
    with pytest.raises(AppError) as exc:
        await service.require(first[0])
    assert exc.value.code == "SESSION_REQUIRED"
    assert (await service.require(second[0])).bot.id == "1"
    assert service.runtime.stops == 0


async def test_invalid_login_preserves_working_session(service):
    ident, _ = await service.connect("good")
    runtime = service.runtime
    with pytest.raises(AppError) as exc:
        await service.connect("bad")
    assert exc.value.code == "INVALID_BOT_TOKEN"
    assert service.runtime is runtime
    assert (await service.require(ident)).connection.status == "connected"
    assert Gateway.instances[-1].closed == 1
    assert runtime.stops == 0


async def test_webhook_and_other_bot_rejected_without_worker(service):
    with pytest.raises(AppError) as exc:
        await service.connect("webhook")
    assert exc.value.code == "BOT_WEBHOOK_ACTIVE"
    assert Gateway.instances[-1].closed == 1
    ident, _ = await service.connect("good")
    with pytest.raises(AppError) as exc:
        await service.connect("other")
    assert exc.value.code == "BOT_ALREADY_CONNECTED"
    assert (await service.require(ident)).bot.id == "1"


async def test_expiry_disconnect_and_unauthorized_disconnect(service):
    ident, _ = await service.connect("good")
    with pytest.raises(AppError):
        await service.disconnect("unknown")
    assert service.runtime is not None
    service._sessions[ident] = ("1", 0)
    with pytest.raises(AppError):
        await service.require(ident)
    first, _ = await service.connect("good")
    second, _ = await service.connect("good")
    runtime = service.runtime
    await service.disconnect(first)
    assert runtime.stops == 1 and runtime.gateway.closed == 1
    assert service.runtime is None and service.bot is None
    with pytest.raises(AppError):
        await service.require(second)


async def test_revoked_token_invalidates_all_sessions_and_reconnects(service):
    first, _ = await service.connect("good")
    second, _ = await service.connect("good")
    old = service.runtime
    old.connection = ConnectionState(status="error", error_code="INVALID_BOT_TOKEN")
    with pytest.raises(AppError) as exc:
        await service.require(first)
    assert exc.value.code == "INVALID_BOT_TOKEN"
    with pytest.raises(AppError):
        await service.require(second)
    assert service.runtime is None and service.bot is None
    third, result = await service.connect("replacement")
    assert old.stops == 1
    assert Runtime.starts == 2
    assert result.connection.status == "connected"
    assert (await service.require(third)).bot.id == "1"


async def test_new_bot_can_replace_revoked_bot_without_old_session(service):
    await service.connect("good")
    old = service.runtime
    old.connection = ConnectionState(status="error", error_code="INVALID_BOT_TOKEN")
    _, result = await service.connect("other")
    assert result.bot.id == "2"
    assert old.stops == 1


async def test_runtime_error_exposed_and_reconnect_replaces_worker(service):
    ident, _ = await service.connect("good")
    service.runtime.connection = ConnectionState(
        status="error", error_code="BOT_POLLING_CONFLICT", message="Другое подключение."
    )
    assert (
        await service.require(ident)
    ).connection.error_code == "BOT_POLLING_CONFLICT"
    await service.connect("good")
    assert Runtime.starts == 2
    with pytest.raises(AppError):
        await service.require(ident)


async def test_partial_start_cleanup(service):
    class BrokenRuntime(Runtime):
        async def start(self):
            raise RuntimeError("start failure")

    service.runtime_factory = BrokenRuntime
    with pytest.raises(RuntimeError):
        await service.connect("good")
    assert service.runtime is None
    assert Gateway.instances[-1].closed == 1
