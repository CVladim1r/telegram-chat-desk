import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.contracts import BotInfo, ChangeEvent, Chat, Contact, Message, MessagesPage
from app.errors import AppError

from .schema import bots, changes, chats, contacts, inbox, messages


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4096:
        raise AppError("INVALID_TEXT", "Введите текст от 1 до 4096 символов.", 422)


class Store:
    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url)
        self._lock = asyncio.Lock()

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(connection, _):
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        @event.listens_for(self.engine.sync_engine, "begin")
        def begin(connection):
            connection.exec_driver_sql("BEGIN")

    @asynccontextmanager
    async def _transaction(self):
        async with self._lock:
            async with self.engine.begin() as connection:
                yield connection

    async def initialize(self):
        database = self.engine.url.database
        if database and database != ":memory:":
            Path(database).resolve().parent.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))

        def migrate(connection):
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        async with self._transaction() as connection:
            await connection.run_sync(migrate)

    async def close(self):
        await self.engine.dispose()

    async def upsert_bot(self, bot: BotInfo):
        async with self._transaction() as conn:
            stmt = sqlite_insert(bots).values(**bot.model_dump())
            await conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[bots.c.id],
                    set_={"username": bot.username, "first_name": bot.first_name},
                )
            )

    def _chat_query(self, bot_id):
        return (
            select(
                chats.c.id,
                contacts.c.telegram_chat_id,
                contacts.c.name,
                contacts.c.username,
                contacts.c.available,
                chats.c.last_message,
                chats.c.updated_at,
            )
            .join(contacts, contacts.c.id == chats.c.contact_id)
            .where(chats.c.bot_id == bot_id)
        )

    async def _chat(self, conn, bot_id, chat_id):
        row = (
            (await conn.execute(self._chat_query(bot_id).where(chats.c.id == chat_id)))
            .mappings()
            .first()
        )
        if row is None:
            raise AppError("CHAT_NOT_FOUND", "Диалог не найден.", 404)
        return Chat(**row)

    async def _message(self, conn, bot_id, message_id):
        query = (
            select(messages, contacts.c.telegram_chat_id)
            .join(chats, chats.c.id == messages.c.chat_id)
            .join(contacts, contacts.c.id == chats.c.contact_id)
            .where(messages.c.bot_id == bot_id, messages.c.id == message_id)
        )
        row = (await conn.execute(query)).mappings().one()
        return Message(**row)

    async def _emit(self, conn, bot_id, kind, dto):
        await conn.execute(
            insert(changes).values(bot_id=bot_id, type=kind, data=dto.model_dump_json())
        )

    async def _emit_message(self, conn, bot_id, message_id):
        message = await self._message(conn, bot_id, message_id)
        await self._emit(conn, bot_id, "message.upsert", message)
        await self._emit(
            conn, bot_id, "chat.upsert", await self._chat(conn, bot_id, message.chat_id)
        )
        return message

    async def bootstrap(self, bot_id):
        async with self._transaction() as conn:
            rows = (
                await conn.execute(
                    self._chat_query(bot_id).order_by(
                        chats.c.updated_at.desc(), chats.c.id.desc()
                    )
                )
            ).mappings()
            result = [Chat(**row) for row in rows]
            cursor = (
                await conn.scalar(
                    select(func.max(changes.c.id)).where(changes.c.bot_id == bot_id)
                )
                or 0
            )
            return result, cursor

    async def list_contacts(self, bot_id, query: str = ""):
        async with self._transaction() as conn:
            rows = (
                await conn.execute(
                    select(contacts)
                    .where(contacts.c.bot_id == bot_id)
                    .order_by(contacts.c.name, contacts.c.id)
                )
            ).mappings()

            query = query.strip().casefold()
            return [
                Contact(**row)
                for row in rows
                if not query
                or any(
                    query in str(row[key] or "").casefold()
                    for key in ("name", "username", "telegram_chat_id")
                )
            ]

    async def _ensure_chat(self, conn, bot_id, contact_id):
        chat_id = await conn.scalar(
            select(chats.c.id).where(
                chats.c.bot_id == bot_id, chats.c.contact_id == contact_id
            )
        )
        if chat_id is None:
            result = await conn.execute(
                insert(chats).values(
                    bot_id=bot_id, contact_id=contact_id, updated_at=now()
                )
            )
            chat_id = result.inserted_primary_key[0]
            await self._emit(
                conn, bot_id, "chat.upsert", await self._chat(conn, bot_id, chat_id)
            )
        return chat_id

    async def create_chat(self, bot_id, telegram_chat_id: str):
        async with self._transaction() as conn:
            row = (
                (
                    await conn.execute(
                        select(contacts).where(
                            contacts.c.bot_id == bot_id,
                            contacts.c.telegram_chat_id == telegram_chat_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AppError(
                    "RECIPIENT_NOT_REGISTERED",
                    "Получатель должен сначала написать боту в Telegram.",
                    422,
                )
            if not row["available"]:
                raise AppError(
                    "RECIPIENT_UNAVAILABLE",
                    "Получатель недоступен. Попросите его открыть бота в Telegram.",
                    403,
                )
            chat_id = await self._ensure_chat(conn, bot_id, row["id"])
            return await self._chat(conn, bot_id, chat_id)

    async def list_messages(
        self, bot_id, chat_id: int, before_id: int | None = None, limit: int = 50
    ):
        limit = max(1, min(limit, 100))
        async with self._transaction() as conn:
            await self._chat(conn, bot_id, chat_id)
            query = select(messages.c.id).where(
                messages.c.bot_id == bot_id, messages.c.chat_id == chat_id
            )
            if before_id is not None:
                query = query.where(messages.c.id < before_id)
            ids = list(
                (
                    await conn.scalars(
                        query.order_by(messages.c.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            has_more = len(ids) > limit
            ids = ids[:limit]
            items = [
                await self._message(conn, bot_id, ident) for ident in reversed(ids)
            ]
            return MessagesPage(
                items=items, next_before_id=min(ids) if has_more else None
            )

    async def _enqueue(self, conn, bot_id, chat_id, text, client_message_id, source):
        validate_text(text)
        existing = (
            (
                await conn.execute(
                    select(messages).where(
                        messages.c.bot_id == bot_id,
                        messages.c.client_message_id == client_message_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if existing:
            if (
                existing["chat_id"] != chat_id
                or existing["text"] != text
                or existing["source"] != source
            ):
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    "Этот идентификатор уже использован для другого сообщения.",
                    409,
                )
            return await self._message(conn, bot_id, existing["id"])
        chat = await self._chat(conn, bot_id, chat_id)
        if not chat.available:
            raise AppError(
                "RECIPIENT_UNAVAILABLE",
                "Получатель недоступен. Попросите его открыть бота в Telegram.",
                403,
            )
        created_at = now()
        result = await conn.execute(
            insert(messages).values(
                bot_id=bot_id,
                chat_id=chat_id,
                direction="outgoing",
                text=text,
                source=source,
                created_at=created_at,
                client_message_id=client_message_id,
                status="queued",
            )
        )
        await conn.execute(
            update(chats)
            .where(chats.c.id == chat_id)
            .values(last_message=text, updated_at=created_at)
        )
        return await self._emit_message(conn, bot_id, result.inserted_primary_key[0])

    async def enqueue_message(
        self,
        bot_id,
        chat_id: int,
        text: str,
        client_message_id: str,
        source: str = "user",
    ):
        async with self._transaction() as conn:
            return await self._enqueue(
                conn, bot_id, chat_id, text, client_message_id, source
            )

    async def get_changes(self, bot_id, cursor: int, limit: int = 100):
        limit = max(1, min(limit, 200))
        async with self._transaction() as conn:
            rows = (
                (
                    await conn.execute(
                        select(changes)
                        .where(changes.c.bot_id == bot_id, changes.c.id > cursor)
                        .order_by(changes.c.id)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
            items = [
                ChangeEvent(
                    id=row["id"], type=row["type"], data=json.loads(row["data"])
                )
                for row in rows[:limit]
            ]
            return items, items[-1].id if items else cursor, len(rows) > limit

    async def get_offset(self, bot_id):
        async with self._transaction() as conn:
            return await conn.scalar(select(bots.c.offset).where(bots.c.id == bot_id))

    async def persist_updates(self, bot_id, updates: list[dict]):
        if not updates:
            return
        async with self._transaction() as conn:
            for payload in updates:
                stmt = sqlite_insert(inbox).values(
                    bot_id=bot_id,
                    update_id=payload["update_id"],
                    payload=json.dumps(payload, ensure_ascii=False),
                    processed=False,
                )
                await conn.execute(
                    stmt.on_conflict_do_nothing(
                        index_elements=[inbox.c.bot_id, inbox.c.update_id]
                    )
                )
            previous = await conn.scalar(
                select(bots.c.offset).where(bots.c.id == bot_id)
            )
            offset = max(max(item["update_id"] for item in updates) + 1, previous or 0)
            await conn.execute(
                update(bots).where(bots.c.id == bot_id).values(offset=offset)
            )

    async def pending_updates(self, bot_id, limit: int = 100):
        async with self._transaction() as conn:
            rows = await conn.scalars(
                select(inbox.c.payload)
                .where(inbox.c.bot_id == bot_id, inbox.c.processed.is_(False))
                .order_by(inbox.c.update_id)
                .limit(limit)
            )
            return [json.loads(row) for row in rows]

    async def apply_update(self, bot_id, update_id: int, action: dict | None):
        async with self._transaction() as conn:
            row = (
                (
                    await conn.execute(
                        select(inbox).where(
                            inbox.c.bot_id == bot_id, inbox.c.update_id == update_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["processed"]:
                return
            if action is not None:
                contact = Contact(**action["contact"])
                stmt = sqlite_insert(contacts).values(
                    bot_id=bot_id, **contact.model_dump()
                )
                await conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[contacts.c.bot_id, contacts.c.telegram_chat_id],
                        set_=contact.model_dump(),
                    )
                )
                await self._emit(conn, bot_id, "contact.upsert", contact)
                contact_id = await conn.scalar(
                    select(contacts.c.id).where(
                        contacts.c.bot_id == bot_id,
                        contacts.c.telegram_chat_id == contact.telegram_chat_id,
                    )
                )
                chat_id = await conn.scalar(
                    select(chats.c.id).where(
                        chats.c.bot_id == bot_id, chats.c.contact_id == contact_id
                    )
                )
                if action.get("message") or action.get("system_reply"):
                    chat_id = await self._ensure_chat(conn, bot_id, contact_id)
                elif chat_id is not None:
                    await self._emit(
                        conn,
                        bot_id,
                        "chat.upsert",
                        await self._chat(conn, bot_id, chat_id),
                    )
                if action.get("message"):
                    message = action["message"]
                    exists = await conn.scalar(
                        select(messages.c.id).where(
                            messages.c.bot_id == bot_id,
                            messages.c.chat_id == chat_id,
                            messages.c.direction == "incoming",
                            messages.c.telegram_message_id
                            == str(message["telegram_message_id"]),
                        )
                    )
                    if exists is None:
                        result = await conn.execute(
                            insert(messages).values(
                                bot_id=bot_id,
                                chat_id=chat_id,
                                direction="incoming",
                                source="user",
                                status="received",
                                text=message["text"],
                                telegram_message_id=str(message["telegram_message_id"]),
                                created_at=message["created_at"],
                            )
                        )
                        await conn.execute(
                            update(chats)
                            .where(chats.c.id == chat_id)
                            .values(last_message=message["text"], updated_at=now())
                        )
                        await self._emit_message(
                            conn, bot_id, result.inserted_primary_key[0]
                        )
                if action.get("system_reply"):
                    await self._enqueue(
                        conn,
                        bot_id,
                        chat_id,
                        action["system_reply"],
                        f"system:{update_id}",
                        "system",
                    )
            await conn.execute(
                update(inbox).where(inbox.c.id == row["id"]).values(processed=True)
            )

    async def fail_update(self, bot_id, update_id: int, reason: str):

        safe_reason = (
            reason
            if reason.isascii()
            and len(reason) <= 80
            and all(c.isalnum() or c in "_-" for c in reason)
            else "MALFORMED_UPDATE"
        )
        async with self._transaction() as conn:
            await conn.execute(
                update(inbox)
                .where(
                    inbox.c.bot_id == bot_id,
                    inbox.c.update_id == update_id,
                    inbox.c.processed.is_(False),
                )
                .values(processed=True, failure=safe_reason)
            )

    async def claim_next_message(self, bot_id):
        async with self._transaction() as conn:
            earlier = messages.alias("earlier")
            blocked = (
                select(earlier.c.id)
                .where(
                    earlier.c.chat_id == messages.c.chat_id,
                    earlier.c.id < messages.c.id,
                    earlier.c.status.in_(["queued", "sending"]),
                )
                .exists()
            )
            query = (
                select(messages.c.id)
                .where(
                    messages.c.bot_id == bot_id,
                    messages.c.status == "queued",
                    or_(messages.c.retry_at.is_(None), messages.c.retry_at <= now()),
                    ~blocked,
                )
                .order_by(messages.c.id)
                .limit(1)
            )
            ident = await conn.scalar(query)
            if ident is None:
                return None
            await conn.execute(
                update(messages)
                .where(messages.c.id == ident)
                .values(
                    status="sending", retry_at=None, error_code=None, error_message=None
                )
            )
            return await self._emit_message(conn, bot_id, ident)

    async def finish_message(
        self,
        bot_id,
        message_id: int,
        status: str,
        telegram_message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_at: str | None = None,
    ):
        if status not in {"sent", "failed", "unknown", "queued"}:
            raise ValueError("Unsupported outgoing status")
        async with self._transaction() as conn:
            row = (
                (
                    await conn.execute(
                        select(messages).where(
                            messages.c.bot_id == bot_id, messages.c.id == message_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["status"] != "sending":
                return
            await conn.execute(
                update(messages)
                .where(messages.c.id == message_id)
                .values(
                    status=status,
                    telegram_message_id=telegram_message_id,
                    error_code=error_code,
                    error_message=error_message,
                    retry_at=retry_at,
                )
            )
            if error_code == "RECIPIENT_UNAVAILABLE":
                contact_id = await conn.scalar(
                    select(chats.c.contact_id).where(chats.c.id == row["chat_id"])
                )
                await conn.execute(
                    update(contacts)
                    .where(contacts.c.id == contact_id)
                    .values(available=False)
                )
                contact = (
                    (
                        await conn.execute(
                            select(contacts).where(contacts.c.id == contact_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                await self._emit(conn, bot_id, "contact.upsert", Contact(**contact))
            await self._emit_message(conn, bot_id, message_id)

    async def recover_sending(self, bot_id):
        async with self._transaction() as conn:
            ids = (
                await conn.scalars(
                    select(messages.c.id).where(
                        messages.c.bot_id == bot_id, messages.c.status == "sending"
                    )
                )
            ).all()
            for ident in ids:
                await conn.execute(
                    update(messages)
                    .where(messages.c.id == ident)
                    .values(
                        status="unknown",
                        error_code="SEND_RESULT_UNKNOWN",
                        error_message="Отправка была прервана. Проверьте Telegram перед повторной отправкой.",
                        retry_at=None,
                    )
                )
                await self._emit_message(conn, bot_id, ident)
