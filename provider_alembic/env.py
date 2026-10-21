"""Provider migrations have an isolated version table and database connection."""
import os

from sqlalchemy import create_engine

from alembic import context
from demo_provider.models import Base

config = context.config


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata,
                      version_table="provider_alembic_version",
                      version_table_schema=config.attributes.get("schema"))
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise ValueError("provider migration requires an actual database connection")
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    url = os.getenv("PROVIDER_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        raise ValueError("PROVIDER_DATABASE_URL must select the isolated PostgreSQL provider database")
    engine = create_engine(url)
    with engine.connect() as connection:
        migrate(connection)
