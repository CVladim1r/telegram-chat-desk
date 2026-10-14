import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Update, User

from app.contracts import BotInfo
from app.errors import AppError
from app.storage import Store
from app.telegram.handlers import create_dispatcher
from app.telegram.runtime import TelegramRuntime

BOT_INFO = BotInfo(id="123456", username="unit_test_bot", first_name="Test")


def payload(update_id=1, *, text="Привет", chat_type="private", chat_id=42):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": chat_type, "first_name": "Анна"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Анна"},
            "text": text,
        },
    }


def fake_gateway():
    bot = Bot("123456:THIS_IS_A_FAKE_TOKEN_FOR_UNIT_TESTS")
    bot._me = User(id=123456, is_bot=True, first_name="Test", username="unit_test_bot")
    return SimpleNamespace(
        bot=bot,
        get_updates=AsyncMock(return_value=[]),
        send_text=AsyncMock(
            return_value={
                "telegram_message_id": "100",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        close=AsyncMock(),
    )


@pytest.fixture
async def store():
    database = Store("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    await database.upsert_bot(BOT_INFO)
    yield database
    await database.close()


async def wait_until(check, timeout=3):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.01)


async def test_collector_never_acknowledges_failed_commit(store):
    gateway = fake_gateway()
    offsets = []
    persisted = store.persist_updates
    attempts = 0

    async def persist(bot_id, batch):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database temporarily unavailable")
        await persisted(bot_id, batch)

    async def receive(offset, timeout):
        offsets.append(offset)
        return [payload()] if offset is None else []

    store.persist_updates = persist
    gateway.get_updates.side_effect = receive
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    runtime.retry_delay = runtime.idle_delay = 0.01
    await runtime.start()

    async def ready():
        return len(offsets) >= 3 and not await store.pending_updates(BOT_INFO.id)

    try:
        await wait_until(ready)
    finally:
        await runtime.stop()
    assert offsets[:3] == [None, None, 2]
    chats, _ = await store.bootstrap(BOT_INFO.id)
    messages = await store.list_messages(BOT_INFO.id, chats[0].id)
    assert [item.text for item in messages.items] == ["Привет"]


async def test_start_update_and_system_reply_are_applied_once(store):
    gateway = fake_gateway()
    raw = payload(text="/start@unit_test_bot")
    await store.persist_updates(BOT_INFO.id, [raw])
    dispatcher = create_dispatcher()
    action = await dispatcher.feed_update(gateway.bot, Update.model_validate(raw))
    await store.apply_update(BOT_INFO.id, 1, action)
    await store.persist_updates(BOT_INFO.id, [raw])
    await store.apply_update(BOT_INFO.id, 1, action)
    chats, _ = await store.bootstrap(BOT_INFO.id)
    items = (await store.list_messages(BOT_INFO.id, chats[0].id)).items
    assert len(items) == 2
    assert items[0].status == "received"
    assert items[1].source == "system" and items[1].status == "queued"
    assert items[1].client_message_id == "system:1"
    gateway.send_text.assert_not_called()


async def test_group_media_and_malformed_updates_do_not_block_private_text(store):
    gateway = fake_gateway()
    media = payload(2)
    del media["message"]["text"]
    malformed = {"update_id": 3, "message": {"text": "broken"}}
    await store.persist_updates(
        BOT_INFO.id,
        [
            payload(1, chat_type="group", chat_id=-5),
            media,
            malformed,
            payload(4, text="valid"),
        ],
    )
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    runtime.idle_delay = 0.01
    await runtime.start()

    async def ready():
        return not await store.pending_updates(BOT_INFO.id)

    try:
        await wait_until(ready)
    finally:
        await runtime.stop()
    chats, _ = await store.bootstrap(BOT_INFO.id)
    assert len(chats) == 1
    assert [
        item.text
        for item in (await store.list_messages(BOT_INFO.id, chats[0].id)).items
    ] == ["valid"]


async def test_terminal_polling_conflict_stops_workers_and_closes_once(store):
    gateway = fake_gateway()
    gateway.get_updates.side_effect = AppError("BOT_POLLING_CONFLICT", "Конфликт.", 409)
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    await runtime.start()
    await runtime.start()

    async def ready():
        return runtime.connection.status == "error" and gateway.close.await_count == 1

    await wait_until(ready)
    await asyncio.gather(runtime.stop(), runtime.stop())
    gateway.close.assert_awaited_once()
    assert runtime.connection.error_code == "BOT_POLLING_CONFLICT"
    assert all(task.done() for task in runtime._tasks)


async def test_interrupted_sending_recovered_before_workers_start(store):
    gateway = fake_gateway()
    raw = payload()
    await store.persist_updates(BOT_INFO.id, [raw])
    action = await create_dispatcher().feed_update(
        gateway.bot, Update.model_validate(raw)
    )
    await store.apply_update(BOT_INFO.id, 1, action)
    chat = (await store.bootstrap(BOT_INFO.id))[0][0]
    await store.enqueue_message(BOT_INFO.id, chat.id, "outgoing", "interrupted")
    await store.claim_next_message(BOT_INFO.id)
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    await runtime.start()
    await runtime.stop()
    items = (await store.list_messages(BOT_INFO.id, chat.id)).items
    assert items[-1].status == "unknown"
    gateway.send_text.assert_not_called()


async def test_two_terminal_errors_share_one_teardown(store):
    gateway = fake_gateway()
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    both_ready = asyncio.Event()
    started = 0

    async def fail(code):
        nonlocal started
        started += 1
        if started == 2:
            both_ready.set()
        await both_ready.wait()
        await runtime._terminal(AppError(code, "Ошибка.", 409))

    runtime._tasks = [
        asyncio.create_task(fail("BOT_POLLING_CONFLICT")),
        asyncio.create_task(fail("INVALID_BOT_TOKEN")),
    ]
    async with asyncio.timeout(2):
        await asyncio.gather(*runtime._tasks, return_exceptions=True)
        await runtime.stop()
    gateway.close.assert_awaited_once()
    assert runtime.connection.status == "error"


async def test_parallel_start_creates_only_one_worker_set(store):
    gateway = fake_gateway()
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    recovery = AsyncMock(wraps=store.recover_sending)
    store.recover_sending = recovery
    await asyncio.gather(runtime.start(), runtime.start(), runtime.start())
    await runtime.stop()
    recovery.assert_awaited_once_with(BOT_INFO.id)
    assert len(runtime._tasks) == 3


async def test_bounded_shutdown_marks_inflight_message_unknown(store):
    gateway = fake_gateway()
    raw = payload()
    await store.persist_updates(BOT_INFO.id, [raw])
    action = await create_dispatcher().feed_update(
        gateway.bot, Update.model_validate(raw)
    )
    await store.apply_update(BOT_INFO.id, 1, action)
    chat = (await store.bootstrap(BOT_INFO.id))[0][0]
    await store.enqueue_message(BOT_INFO.id, chat.id, "sending now", "in-flight")
    started = asyncio.Event()

    async def send(*args):
        started.set()
        await asyncio.Future()

    gateway.send_text.side_effect = send
    runtime = TelegramRuntime(store, gateway, BOT_INFO)
    runtime.shutdown_timeout = 0.01
    await runtime.start()
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.wait_for(runtime.stop(), timeout=2)
    assert (await store.list_messages(BOT_INFO.id, chat.id)).items[
        -1
    ].status == "unknown"
    gateway.close.assert_awaited_once()
