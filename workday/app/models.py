import datetime as dt

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    color: Mapped[str] = mapped_column(String(7), default="#64748b")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    minutes: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(200), index=True, default=None)

    category: Mapped[Category] = relationship()


DEFAULT_CATEGORIES = [
    ("Work", "#2563eb"),
    ("Study", "#9333ea"),
    ("Sick", "#dc2626"),
    ("Vacation", "#16a34a"),
    ("Public holiday", "#d97706"),
]
