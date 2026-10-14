import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.contracts import BotInfo, Chat, Contact, Message
from app.errors import AppError
from app.storage import Store
from app.storage.schema import inbox


@pytest.fixture
async def store(tmp_path):
    result = Store(f"sqlite+aiosqlite:///{(tmp_path / 'chat.db').as_posix()}")
    await result.initialize()
    await result.upsert_bot(BotInfo(id="1", username="bot", first_name="Bot"))
    yield result
    await result.close()


async def register(store, chat="10", update_id=1, text=None):
    action = {
        "contact": {
            "telegram_chat_id": chat,
            "name": "Иван",
            "username": "ivan",
            "available": True,
        }
    }
    if text:
        action["message"] = {
            "telegram_message_id": str(update_id),
            "text": text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    await store.persist_updates("1", [{"update_id": update_id}])
    await store.apply_update("1", update_id, action)
    return await store.create_chat("1", chat)


async def test_migrations_restart_and_durable_inbox(tmp_path):
    url = (
        f"sqlite+aiosqlite:///{(tmp_path / 'new' / 'nested' / 'durable.db').as_posix()}"
    )
    first = Store(url)
    await first.initialize()
    await first.upsert_bot(BotInfo(id="1", username="bot", first_name="Bot"))
    await first.persist_updates("1", [{"update_id": 42, "message": {"text": "hello"}}])
    await first.close()
    second = Store(url)
    await second.initialize()
    assert await second.get_offset("1") == 43
    assert (await second.pending_updates("1"))[0]["message"]["text"] == "hello"
    await second.persist_updates("1", [{"update_id": 42, "message": {"text": "hello"}}])
    assert len(await second.pending_updates("1")) == 1
    await second.persist_updates("1", [{"update_id": 30}])
    assert await second.get_offset("1") == 43
    await second.close()


async def test_batch_rollback_does_not_advance_offset(store):
    with pytest.raises(KeyError):
        await store.persist_updates("1", [{"update_id": 5}, {"not_update_id": 6}])
    assert await store.get_offset("1") is None
    assert await store.pending_updates("1") == []


async def test_incoming_and_system_reply_atomic_and_replay_safe(store):
    action = {
        "contact": {"telegram_chat_id": "10", "name": "Иван"},
        "message": {
            "telegram_message_id": "11",
            "text": "/start",
            "created_at": "2026-10-14T10:00:00+00:00",
        },
        "system_reply": "Добро пожаловать",
    }
    await store.persist_updates("1", [{"update_id": 7}])
    await store.apply_update("1", 7, action)
    before = await store.bootstrap("1")
    await store.apply_update("1", 7, action)
    assert await store.bootstrap("1") == before
    assert await store.pending_updates("1") == []
    page = await store.list_messages("1", before[0][0].id)
    assert [item.text for item in page.items] == ["/start", "Добро пожаловать"]
    assert page.items[-1].client_message_id == "system:7"
    events, cursor, _ = await store.get_changes("1", 0)
    models = {"message.upsert": Message, "chat.upsert": Chat, "contact.upsert": Contact}
    for event in events:
        models[event.type].model_validate(event.data)
    assert cursor == before[1]


async def test_domain_failure_rolls_back_message_contact_and_processing(
    store, monkeypatch
):
    await store.persist_updates("1", [{"update_id": 10}])
    action = {
        "contact": {"telegram_chat_id": "10", "name": "Name"},
        "system_reply": "hello",
    }
    original = store._emit

    async def fail(*args):
        if args[2] == "message.upsert":
            raise RuntimeError("simulated database failure")
        return await original(*args)

    monkeypatch.setattr(store, "_emit", fail)
    with pytest.raises(RuntimeError):
        await store.apply_update("1", 10, action)
    assert len(await store.pending_updates("1")) == 1
    assert await store.list_contacts("1") == []
    assert await store.bootstrap("1") == ([], 0)
    monkeypatch.setattr(store, "_emit", original)
    await store.apply_update("1", 10, action)
    assert await store.pending_updates("1") == []


async def test_contact_only_and_unicode_search(store):
    await store.persist_updates("1", [{"update_id": 1}])
    await store.apply_update(
        "1", 1, {"contact": {"telegram_chat_id": "10", "name": "Иван"}}
    )
    assert (await store.bootstrap("1"))[0] == []
    assert len(await store.list_contacts("1", "ИВАН")) == 1
    with pytest.raises(AppError, match="сначала написать"):
        await store.create_chat("1", "99")


async def test_concurrent_idempotency_replay_and_ownership(store):
    chat = await register(store)
    results = await asyncio.gather(
        *(store.enqueue_message("1", chat.id, "hello", "same") for _ in range(10))
    )
    assert len({item.id for item in results}) == 1
    with pytest.raises(AppError) as exc:
        await store.enqueue_message("1", chat.id, "changed", "same")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(AppError) as exc:
        await store.list_messages("other", chat.id)
    assert exc.value.code == "CHAT_NOT_FOUND"
    await store.claim_next_message("1")
    await store.finish_message(
        "1", results[0].id, "failed", error_code="RECIPIENT_UNAVAILABLE"
    )
    replay = await store.enqueue_message("1", chat.id, "hello", "same")
    assert replay.status == "failed"
    with pytest.raises(AppError) as exc:
        await store.enqueue_message("1", chat.id, "new", "new")
    assert exc.value.code == "RECIPIENT_UNAVAILABLE"
    assert not (await store.bootstrap("1"))[0][0].available


async def test_fifo_delayed_chat_does_not_block_other_chats(store):
    first = await register(store)
    second = await register(store, "20", 2)
    a = await store.enqueue_message("1", first.id, "A", "a")
    await store.enqueue_message("1", first.id, "B", "b")
    c = await store.enqueue_message("1", second.id, "C", "c")
    assert (await store.claim_next_message("1")).id == a.id
    assert (await store.claim_next_message("1")).id == c.id
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    await store.finish_message(
        "1", a.id, "queued", error_code="RATE_LIMITED", retry_at=future
    )
    assert await store.claim_next_message("1") is None
    await store.finish_message("1", c.id, "sent", telegram_message_id="700")
    assert await store.claim_next_message("1") is None


async def test_restart_marks_sending_unknown_no_auto_resend(store):
    chat = await register(store)
    first = await store.enqueue_message("1", chat.id, "A", "a")
    await store.enqueue_message("1", chat.id, "B", "b")
    await store.claim_next_message("1")
    await store.recover_sending("1")
    page = await store.list_messages("1", chat.id)
    assert page.items[0].id == first.id
    assert page.items[0].status == "unknown"
    assert page.items[0].error_code == "SEND_RESULT_UNKNOWN"
    assert (await store.claim_next_message("1")).text == "B"


async def test_pagination_and_changes_cursor_isolation(store):
    chat = await register(store)
    for i in range(5):
        await store.enqueue_message("1", chat.id, f"hello {i}", f"id{i}")
    page = await store.list_messages("1", chat.id, limit=2)
    older = await store.list_messages(
        "1", chat.id, before_id=page.next_before_id, limit=2
    )
    oldest = await store.list_messages(
        "1", chat.id, before_id=older.next_before_id, limit=2
    )
    assert [m.text for m in oldest.items + older.items + page.items] == [
        f"hello {i}" for i in range(5)
    ]
    assert oldest.next_before_id is None
    events, cursor, has_more = await store.get_changes("1", 0, 2)
    assert has_more and len(events) == 2
    assert await store.get_changes("other", 0) == ([], 0, False)
    await store.upsert_bot(BotInfo(id="2", username="other", first_name="Other"))
    assert await store.bootstrap("2") == ([], 0)


@pytest.mark.parametrize("text", ["", " \n\t", "x" * 4097])
async def test_invalid_text(store, text):
    chat = await register(store)
    with pytest.raises(AppError) as exc:
        await store.enqueue_message("1", chat.id, text, "a")
    assert exc.value.code == "INVALID_TEXT"


async def test_unicode_limit_and_quarantine(store):
    chat = await register(store)
    sent = await store.enqueue_message("1", chat.id, "😀" * 4096, "emoji")
    assert len(sent.text) == 4096
    await store.persist_updates("1", [{"update_id": 2}])
    await store.fail_update("1", 2, "secret text that should not persist")
    assert await store.pending_updates("1") == []
    async with store.engine.connect() as conn:
        assert (
            await conn.scalar(select(inbox.c.failure).where(inbox.c.update_id == 2))
            == "MALFORMED_UPDATE"
        )
