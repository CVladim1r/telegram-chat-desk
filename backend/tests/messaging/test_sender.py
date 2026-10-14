import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.contracts import BotInfo
from app.errors import AppError
from app.messaging.sender import MessageSender
from app.storage import Store

BOT_ID = "123456"


@pytest.fixture
async def store():
    database = Store("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    await database.upsert_bot(
        BotInfo(id=BOT_ID, username="unit_test_bot", first_name="Test")
    )
    for index, chat_id in enumerate(["42", "43"]):
        await database.persist_updates(BOT_ID, [{"update_id": index}])
        await database.apply_update(
            BOT_ID,
            index,
            {
                "contact": {
                    "telegram_chat_id": chat_id,
                    "name": chat_id,
                    "username": None,
                    "available": True,
                }
            },
        )
        await database.create_chat(BOT_ID, chat_id)
    yield database
    await database.close()


async def test_rate_limit_preserves_fifo_and_other_chat_can_progress(store):
    first_chat = await store.create_chat(BOT_ID, "42")
    other_chat = await store.create_chat(BOT_ID, "43")
    first = await store.enqueue_message(BOT_ID, first_chat.id, "first", "one")
    await store.enqueue_message(BOT_ID, first_chat.id, "second", "two")
    await store.enqueue_message(BOT_ID, other_chat.id, "other", "three")
    gateway = SimpleNamespace(
        send_text=AsyncMock(
            side_effect=[
                AppError("RATE_LIMITED", "Подождите.", 429, retry_after=60),
                {"telegram_message_id": "88"},
            ]
        )
    )
    sender = MessageSender(store, gateway, BOT_ID)
    assert await sender.send_one()
    assert await sender.send_one()
    assert not await sender.send_one()
    assert [call.args[1] for call in gateway.send_text.call_args_list] == [
        "first",
        "other",
    ]
    items = (await store.list_messages(BOT_ID, first_chat.id)).items
    assert [item.status for item in items] == ["queued", "queued"]
    assert items[0].id == first.id


async def test_uncertain_send_is_never_automatically_repeated(store):
    chat = await store.create_chat(BOT_ID, "42")
    await store.enqueue_message(BOT_ID, chat.id, "once", "one")
    gateway = SimpleNamespace(
        send_text=AsyncMock(
            side_effect=AppError(
                "SEND_RESULT_UNKNOWN", "Неизвестно.", 502, uncertain=True
            )
        )
    )
    sender = MessageSender(store, gateway, BOT_ID)
    await sender.send_one()
    assert not await sender.send_one()
    gateway.send_text.assert_awaited_once()
    assert (await store.list_messages(BOT_ID, chat.id)).items[0].status == "unknown"


async def test_forbidden_marks_contact_and_message_unavailable(store):
    chat = await store.create_chat(BOT_ID, "42")
    await store.enqueue_message(BOT_ID, chat.id, "once", "one")
    gateway = SimpleNamespace(
        send_text=AsyncMock(
            side_effect=AppError("RECIPIENT_UNAVAILABLE", "Запрещено.", 403)
        )
    )
    await MessageSender(store, gateway, BOT_ID).send_one()
    assert (await store.list_messages(BOT_ID, chat.id)).items[0].status == "failed"
    assert (
        next(
            contact
            for contact in await store.list_contacts(BOT_ID)
            if contact.telegram_chat_id == "42"
        ).available
        is False
    )


async def test_cancelling_in_flight_send_marks_unknown(store):
    chat = await store.create_chat(BOT_ID, "42")
    await store.enqueue_message(BOT_ID, chat.id, "once", "one")
    started = asyncio.Event()

    async def send(*args):
        started.set()
        await asyncio.Future()

    sender = MessageSender(store, SimpleNamespace(send_text=send), BOT_ID)
    task = asyncio.create_task(sender.send_one())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await store.list_messages(BOT_ID, chat.id)).items[0].status == "unknown"


async def test_commit_failure_after_send_never_resends(store):
    chat = await store.create_chat(BOT_ID, "42")
    await store.enqueue_message(BOT_ID, chat.id, "once", "one")
    gateway = SimpleNamespace(
        send_text=AsyncMock(return_value={"telegram_message_id": "99"})
    )
    sender = MessageSender(store, gateway, BOT_ID)
    sender.persistence_retry_delay = 0.01
    original_finish = store.finish_message
    attempts = 0

    async def finish(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database failed")
        await original_finish(*args, **kwargs)

    store.finish_message = finish
    await sender.send_one()
    assert not await sender.send_one()
    assert attempts == 2
    assert (await store.list_messages(BOT_ID, chat.id)).items[0].status == "sent"
    gateway.send_text.assert_awaited_once()


async def test_chat_pacing_delays_following_message_but_not_another_chat(store):
    first_chat = await store.create_chat(BOT_ID, "42")
    other_chat = await store.create_chat(BOT_ID, "43")
    await store.enqueue_message(BOT_ID, first_chat.id, "first", "one")
    await store.enqueue_message(BOT_ID, first_chat.id, "second", "two")
    await store.enqueue_message(BOT_ID, other_chat.id, "other", "three")
    gateway = SimpleNamespace(
        send_text=AsyncMock(return_value={"telegram_message_id": "100"})
    )
    sender = MessageSender(store, gateway, BOT_ID)
    sender.chat_interval = 60
    await sender.send_one()
    await sender.send_one()
    await sender.send_one()
    assert [call.args[1] for call in gateway.send_text.call_args_list] == [
        "first",
        "other",
    ]
    assert [
        item.status for item in (await store.list_messages(BOT_ID, first_chat.id)).items
    ] == ["sent", "queued"]
