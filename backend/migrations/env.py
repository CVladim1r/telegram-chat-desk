from alembic import context

from app.storage.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through Store.initialize()")
context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
