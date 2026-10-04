from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from bot.config import config


class Base(DeclarativeBase):
    pass


if config.sqlite_path is not None:
    config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(config.db_url, echo=False)

session_maker = sessionmaker(bind=engine, expire_on_commit=False)
