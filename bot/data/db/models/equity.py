from sqlalchemy import BigInteger, Float
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class Equity(Base):
    __tablename__ = "equity"

    timestamp_ms: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
