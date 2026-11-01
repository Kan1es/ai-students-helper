from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base


def database(url: str):
    engine = create_engine(
        url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")

    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize(engine):
    Base.metadata.create_all(engine)
