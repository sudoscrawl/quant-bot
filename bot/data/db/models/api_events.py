from sqlalchemy import BigInteger, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class ApiEvents(Base):
    __tablename__ = "api_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[int] = mapped_column(Integer, nullable=False)   # 0 or 1, matches trade-bot
    message: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_api_events_ts", "timestamp_ms"),)
