from logging.config import fileConfig

from alembic import context

from sqlalchemy import create_engine
from app.credentials import configured_url
from app.models import Base
from app.auth import models as auth_models  # Register additive central auth tables.
from app.departments import models as department_models  # Central administration only.
from app.users import models as user_models


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# SQLAlchemy Models
target_metadata = Base.metadata
engine = create_engine(configured_url("CENTRAL_MIGRATION_DATABASE_URL"), hide_parameters=True)


def run_migrations_offline():
    context.configure(
        url=engine.url.render_as_string(
            hide_password=False
        ),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named"
        },
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
