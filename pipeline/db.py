import os
from functools import lru_cache
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

class Base(DeclarativeBase):
    pass

def engine(url=None):
    local = Path(__file__).resolve().parents[1] / 'acceptance.db'
    configured = url or os.getenv('DATABASE_URL') or f'sqlite:///{local.as_posix()}'
    return _engine(configured)

@lru_cache(maxsize=8)
def _engine(configured):
    url = make_url(configured)
    if url.drivername in ('postgres', 'postgresql'):
        url = url.set(drivername='postgresql+psycopg')
    options = dict(pool_pre_ping=True)
    if url.get_backend_name() == 'postgresql':
        options.update(pool_size=3, max_overflow=2, pool_recycle=300,
                       connect_args={'connect_timeout': 15})
    return create_engine(url, **options)

def sessions(url=None):
    db = engine(url)
    if db.dialect.name == 'sqlite':
        from . import models
        from . import upload_models
        from . import csv_uploads
        from . import assessment_agent
        Base.metadata.create_all(db)
    return sessionmaker(db, expire_on_commit=False)
