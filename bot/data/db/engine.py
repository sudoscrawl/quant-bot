from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from bot.config import config


class Base(DeclarativeBase):
    pass


engine = create_engine(config.db_url, echo=False)

session_maker = sessionmaker(bind=engine, expire_on_commit=False)
