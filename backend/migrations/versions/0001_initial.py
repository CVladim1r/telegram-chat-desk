import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("offset", sa.Integer()),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.String(), sa.ForeignKey("bots.id"), nullable=False),
        sa.Column("telegram_chat_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("username", sa.String()),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("bot_id", "telegram_chat_id"),
    )
    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.String(), sa.ForeignKey("bots.id"), nullable=False),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("last_message", sa.Text()),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.String(), sa.ForeignKey("bots.id"), nullable=False),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("telegram_message_id", sa.String()),
        sa.Column("client_message_id", sa.String()),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String()),
        sa.Column("error_message", sa.String()),
        sa.Column("retry_at", sa.String()),
        sa.UniqueConstraint("bot_id", "client_message_id"),
        sa.UniqueConstraint("bot_id", "chat_id", "direction", "telegram_message_id"),
    )
    op.create_table(
        "inbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.String(), sa.ForeignKey("bots.id"), nullable=False),
        sa.Column("update_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("failure", sa.String()),
        sa.UniqueConstraint("bot_id", "update_id"),
    )
    op.create_table(
        "changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.String(), sa.ForeignKey("bots.id"), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
    )
    op.create_index("ix_messages_chat_id_id", "messages", ["chat_id", "id"])
    op.create_index("ix_messages_bot_status", "messages", ["bot_id", "status"])
    op.create_index("ix_changes_bot_id_id", "changes", ["bot_id", "id"])
    op.create_index("ix_inbox_pending", "inbox", ["bot_id", "processed", "update_id"])


def downgrade():
    for name in ("changes", "inbox", "messages", "chats", "contacts", "bots"):
        op.drop_table(name)
