import datetime as dt

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    color: Mapped[str] = mapped_column(String(7), default="#64748b")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    """A Home Assistant user, as reported by the Ingress proxy."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime)


class UserSettings(Base):
    """Per-user contract and commute settings."""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    weekly_minutes: Mapped[int] = mapped_column(Integer, default=40 * 60)  # 0 = no contract hours
    workdays: Mapped[str] = mapped_column(String(7), default="01234")  # Python weekdays, Monday = 0
    balance_start: Mapped[dt.date | None] = mapped_column(Date, default=None)  # None = first logged day
    office_km: Mapped[float | None] = mapped_column(Float, default=None)  # prefilled on office days
    day_minutes: Mapped[int | None] = mapped_column(Integer, default=None)  # hours per workday; None = 8h
    default_location: Mapped[str | None] = mapped_column(String(10), default=None)  # set on newly logged days


class DayInfo(Base):
    """Per-user, per-day details that are not hours: where you worked and km driven."""

    __tablename__ = "day_info"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    location: Mapped[str | None] = mapped_column(String(10), default=None)  # "office" | "remote"
    km: Mapped[float | None] = mapped_column(Float, default=None)


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL only for rows from before per-user support; claimed on first visit.
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
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
