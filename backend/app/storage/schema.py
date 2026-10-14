from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)


metadata = MetaData()
bots = Table(
    "bots",
    metadata,
    Column("id", String, primary_key=True),
    Column("username", String, nullable=False),
    Column("first_name", String, nullable=False),
    Column("offset", Integer),
)
contacts = Table(
    "contacts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("bot_id", ForeignKey("bots.id"), nullable=False),
    Column("telegram_chat_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("username", String),
    Column("available", Boolean, nullable=False, default=True),
    UniqueConstraint("bot_id", "telegram_chat_id"),
)
chats = Table(
    "chats",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("bot_id", ForeignKey("bots.id"), nullable=False),
    Column("contact_id", ForeignKey("contacts.id"), nullable=False, unique=True),
    Column("last_message", Text),
    Column("updated_at", String, nullable=False),
)
messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("bot_id", ForeignKey("bots.id"), nullable=False),
    Column("chat_id", ForeignKey("chats.id"), nullable=False),
    Column("direction", String, nullable=False),
    Column("text", Text, nullable=False),
    Column("source", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("telegram_message_id", String),
    Column("client_message_id", String),
    Column("status", String, nullable=False),
    Column("error_code", String),
    Column("error_message", String),
    Column("retry_at", String),
    UniqueConstraint("bot_id", "client_message_id"),
    UniqueConstraint("bot_id", "chat_id", "direction", "telegram_message_id"),
)
inbox = Table(
    "inbox",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("bot_id", ForeignKey("bots.id"), nullable=False),
    Column("update_id", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Column("processed", Boolean, nullable=False, default=False),
    Column("failure", String),
    UniqueConstraint("bot_id", "update_id"),
)
changes = Table(
    "changes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("bot_id", ForeignKey("bots.id"), nullable=False),
    Column("type", String, nullable=False),
    Column("data", Text, nullable=False),
)

Index("ix_messages_chat_id_id", messages.c.chat_id, messages.c.id)
Index("ix_messages_bot_status", messages.c.bot_id, messages.c.status)
Index("ix_changes_bot_id_id", changes.c.bot_id, changes.c.id)
Index("ix_inbox_pending", inbox.c.bot_id, inbox.c.processed, inbox.c.update_id)