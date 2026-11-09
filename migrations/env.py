from alembic import context
from pipeline.db import Base, engine
from pipeline import models
from pipeline import upload_models
from pipeline import csv_uploads
from pipeline import assessment_agent

target_metadata = Base.metadata
if context.is_offline_mode():
    raise RuntimeError('Use online migrations with DATABASE_URL')
else:
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
