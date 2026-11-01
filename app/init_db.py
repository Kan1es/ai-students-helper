from app.config import Settings
from app.db import database, initialize

if __name__ == "__main__":
    engine, _ = database(Settings().database_url)
    initialize(engine)
    engine.dispose()
