import asyncio
import contextlib
import csv
import datetime as dt
import io
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote_plus

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import db
import reminder
import supervisor
from models import DEFAULT_CATEGORIES, Category, DayInfo, Entry, User, UserSettings

log = logging.getLogger("workday")

STATIC_DIR = Path(__file__).parent / "static"
INGRESS_PROXY = "172.30.32.2"
STEP = 15
DAY_MAX = 24 * 60
MAX_RANGE_DAYS = 400
MAX_FILL_DAYS = 62
MAX_KM = 5000
DEFAULT_WEEKLY_MINUTES = 40 * 60
DEFAULT_WORKDAYS = "01234"  # Monday-Friday
COLOR_RE = r"^#[0-9a-fA-F]{6}$"
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEV_USER = {"id": "dev", "username": "dev", "display_name": "Developer"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)

SessionDep = Annotated[Session, Depends(db.get_session)]


# ---------- schemas ----------

class CategoryOut(BaseModel):
    id: int
    name: str
    color: str
    archived: bool
    sort_order: int

    model_config = {"from_attributes": True}


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = Field(default="#64748b", pattern=COLOR_RE)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, pattern=COLOR_RE)
    archived: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class EntryIn(BaseModel):
    category_id: int
    minutes: int = Field(ge=STEP, le=DAY_MAX, multiple_of=STEP)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def clean_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        return v or None


class EntryOut(BaseModel):
    id: int
    date: dt.date
    category_id: int
    minutes: int
    note: str | None

    model_config = {"from_attributes": True}


class DayIn(BaseModel):
    entries: list[EntryIn] = Field(default_factory=list, max_length=50)
    location: Literal["office", "remote"] | None = None
    km: float | None = Field(default=None, ge=0, le=MAX_KM)


class SettingsIn(BaseModel):
    weekly_hours: float = Field(ge=0, le=168)
    workdays: list[int] = Field(max_length=7)
    balance_start: dt.date | None = None
    office_km: float | None = Field(default=None, ge=0, le=MAX_KM)
    default_location: Literal["office", "remote"] | None = None

    @field_validator("weekly_hours")
    @classmethod
    def quarter_hours(cls, v: float) -> float:
        if round(v * 60) % STEP:
            raise ValueError("weekly hours must be in steps of 0.25")
        return v

    @field_validator("workdays")
    @classmethod
    def valid_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("workdays are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


class FillIn(BaseModel):
    start: dt.date = Field(alias="from")
    end: dt.date = Field(alias="to")
    category_id: int
    minutes: int = Field(ge=STEP, le=DAY_MAX, multiple_of=STEP)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def clean_note(cls, v: str | None) -> str | None:
        return EntryIn.clean_note(v)


# ---------- helpers ----------

def seed_categories(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(Category)):
        return
    for i, (name, color) in enumerate(DEFAULT_CATEGORIES):
        session.add(Category(name=name, color=color, sort_order=i))
    session.commit()


def migrate(engine) -> None:
    """Add columns that create_all() cannot add to existing tables."""
    columns = {c["name"] for c in inspect(engine).get_columns("entries")}
    if "user_id" not in columns:
        log.info("Migrating: adding entries.user_id")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE entries ADD COLUMN user_id VARCHAR(64) NULL"))
            conn.execute(text("CREATE INDEX ix_entries_user_id ON entries (user_id)"))
    columns = {c["name"] for c in inspect(engine).get_columns("user_settings")}
    if "default_location" not in columns:
        log.info("Migrating: adding user_settings.default_location")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE user_settings ADD COLUMN default_location VARCHAR(10) NULL"))


def minutes_for_day(day: dt.date, user_id: str) -> int:
    with db.SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(Entry.minutes), 0)).where(Entry.date == day, Entry.user_id == user_id)
        )
        return int(total or 0)


def reminder_user_id(username: str) -> str | None:
    """The user the reminder and sensor are for: the configured one, else the only user."""
    with db.SessionLocal() as session:
        if username:
            return session.scalar(select(User.id).where(func.lower(User.username) == username.lower()))
        ids = session.scalars(select(User.id).limit(2)).all()
        return ids[0] if len(ids) == 1 else None


def get_settings(session: Session, user_id: str) -> UserSettings:
    st = session.get(UserSettings, user_id)
    if st is None:
        st = UserSettings(
            user_id=user_id, weekly_minutes=DEFAULT_WEEKLY_MINUTES, workdays=DEFAULT_WORKDAYS,
            balance_start=None, office_km=None, default_location=None,
        )
    return st


def workdays_of(st: UserSettings) -> set[int]:
    return {int(c) for c in st.workdays}


def daily_target(st: UserSettings) -> float:
    """Contract minutes per workday (0 without contract hours)."""
    days = workdays_of(st)
    return st.weekly_minutes / len(days) if days else 0


def date_range(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def target_minutes(st: UserSettings, start: dt.date, end: dt.date) -> int:
    days = workdays_of(st)
    return round(sum(daily_target(st) for d in date_range(start, end) if d.weekday() in days))


def settings_out(st: UserSettings) -> dict:
    return {
        "weekly_hours": st.weekly_minutes / 60,
        "workdays": sorted(workdays_of(st)),
        "balance_start": st.balance_start.isoformat() if st.balance_start else None,
        "office_km": st.office_km,
        "default_location": st.default_location,
        "daily_minutes": round(daily_target(st)),
    }


def format_km(km: float | None) -> str:
    return "" if km is None else f"{km:g}".replace(".", ",")


def check_range(start: dt.date, end: dt.date) -> None:
    if end < start:
        raise HTTPException(422, "'to' must not be before 'from'")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"range may span at most {MAX_RANGE_DAYS} days")


def csv_cell(value: str) -> str:
    # Neutralise spreadsheet formula injection.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def push_sensor_today(user_id: str) -> None:
    try:
        reminder.publish_sensor(minutes_for_day(dt.date.today(), user_id))
    except Exception:
        log.exception("Could not update %s", reminder.SENSOR)


# ---------- app ----------

def create_app(db_url: str, dev_mode: bool = False, reminder_cfg: reminder.ReminderConfig | None = None) -> FastAPI:
    db.init_engine(db_url)
    ha = supervisor.available()

    def reminder_minutes(day: dt.date) -> int | None:
        uid = reminder_user_id(reminder_cfg.user if reminder_cfg else "")
        return None if uid is None else minutes_for_day(day, uid)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.Base.metadata.create_all(db.engine)
        migrate(db.engine)
        with db.SessionLocal() as session:
            seed_categories(session)
            app.state.unclaimed = session.scalar(select(Entry.id).where(Entry.user_id.is_(None)).limit(1)) is not None
        task = None
        if ha and reminder_cfg is not None:
            task = asyncio.create_task(reminder.run(reminder_cfg, reminder_minutes))
        yield
        if task:
            task.cancel()

    app = FastAPI(
        title="Workday",
        lifespan=lifespan,
        docs_url="/docs" if dev_mode else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev_mode else None,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Inside Home Assistant only the Ingress proxy may talk to us;
        # it has already authenticated the user.
        if not dev_mode and (request.client is None or request.client.host != INGRESS_PROXY):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            # Revalidate the UI files (cheap via ETag) so updates show up at once.
            response.headers["Cache-Control"] = "no-cache"
        return response

    def current_user(request: Request, session: SessionDep) -> User:
        # Ingress adds these headers for the logged-in HA user; the guard above
        # ensures only the Ingress proxy can reach us, so they can be trusted.
        uid = request.headers.get("x-remote-user-id", "")
        if not uid and dev_mode:
            info = DEV_USER
        elif USER_ID_RE.match(uid):
            info = {
                "id": uid,
                "username": request.headers.get("x-remote-user-name", "")[:100],
                "display_name": request.headers.get("x-remote-user-display-name", "")[:200],
            }
        else:
            raise HTTPException(401, "no Home Assistant user")
        user = session.get(User, info["id"])
        now = dt.datetime.now()
        if user is None:
            user = User(**info, last_seen=now)
            session.add(user)
            log.info("New user: %s", info["username"] or info["id"])
        elif (user.username, user.display_name) != (info["username"], info["display_name"]) or (
            now - user.last_seen > dt.timedelta(hours=1)
        ):
            user.username, user.display_name, user.last_seen = info["username"], info["display_name"], now
        if app.state.unclaimed:
            # Hours logged before per-user support go to the first user.
            claimed = session.execute(update(Entry).where(Entry.user_id.is_(None)).values(user_id=user.id)).rowcount
            app.state.unclaimed = False
            if claimed:
                log.info("Assigned %d existing entries to %s", claimed, user.username or user.id)
        session.commit()
        return user

    UserDep = Annotated[User, Depends(current_user)]

    # In-memory change counters for live refresh: one per user plus one for the
    # shared categories. The boot id makes open pages reload after a restart.
    boot_id = secrets.token_hex(4)
    revs: dict[str, int] = {}

    def rev_for(user_id: str) -> str:
        return f"{boot_id}.{revs.get('categories', 0)}.{revs.get(user_id, 0)}"

    def changed(response: Response, key: str, user_id: str) -> None:
        revs[key] = revs.get(key, 0) + 1
        # Lets the page that made the change know it is already up to date.
        response.headers["X-Workday-Rev"] = rev_for(user_id)

    @app.get("/api/rev")
    def rev(user: UserDep):
        return {"rev": rev_for(user.id)}

    @app.get("/api/me")
    def me(user: UserDep):
        return {"username": user.username, "display_name": user.display_name or user.username}

    # --- categories ---

    @app.get("/api/categories", response_model=list[CategoryOut])
    def list_categories(session: SessionDep):
        return session.scalars(select(Category).order_by(Category.sort_order, Category.id)).all()

    @app.post("/api/categories", response_model=CategoryOut, status_code=201)
    def add_category(body: CategoryIn, session: SessionDep, user: UserDep, response: Response):
        top = session.scalar(select(func.coalesce(func.max(Category.sort_order), -1)))
        cat = Category(name=body.name, color=body.color, sort_order=top + 1)
        session.add(cat)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "a category with this name already exists") from None
        changed(response, "categories", user.id)
        return cat

    @app.patch("/api/categories/{cat_id}", response_model=CategoryOut)
    def patch_category(cat_id: int, body: CategoryPatch, session: SessionDep, user: UserDep, response: Response):
        cat = session.get(Category, cat_id)
        if cat is None:
            raise HTTPException(404, "category not found")
        for k, v in body.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(cat, k, v)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "a category with this name already exists") from None
        changed(response, "categories", user.id)
        return cat

    # --- days ---

    def day_out(session: Session, user: User, day: dt.date) -> dict:
        entries = session.scalars(
            select(Entry).where(Entry.date == day, Entry.user_id == user.id).order_by(Entry.id)
        ).all()
        info = session.get(DayInfo, (user.id, day))
        return {
            "date": day.isoformat(),
            "entries": [EntryOut.model_validate(e).model_dump(mode="json") for e in entries],
            "location": info.location if info else None,
            "km": info.km if info else None,
        }

    def check_categories(session: Session, ids: set[int]) -> None:
        if ids and ids - set(session.scalars(select(Category.id).where(Category.id.in_(ids)))):
            raise HTTPException(422, "unknown category")

    def maybe_push_sensor(days, user: User, background: BackgroundTasks) -> None:
        if ha and reminder_cfg is not None and dt.date.today() in days:
            if reminder_user_id(reminder_cfg.user) == user.id:
                background.add_task(push_sensor_today, user.id)

    @app.get("/api/day/{day}")
    def get_day(day: dt.date, session: SessionDep, user: UserDep):
        return day_out(session, user, day)

    @app.get("/api/day/{day}/previous")
    def previous_day(day: dt.date, session: SessionDep, user: UserDep):
        """The most recent earlier day with hours, for "copy from …"."""
        prev = session.scalar(select(func.max(Entry.date)).where(Entry.user_id == user.id, Entry.date < day))
        return day_out(session, user, prev) if prev else None

    @app.put("/api/day/{day}")
    def put_day(
        day: dt.date,
        body: Annotated[DayIn | Annotated[list[EntryIn], Field(max_length=50)], Body()],
        session: SessionDep,
        user: UserDep,
        background: BackgroundTasks,
        response: Response,
    ):
        # A bare list (older clients) only replaces the hours and keeps location/km.
        entries = body if isinstance(body, list) else body.entries
        if sum(e.minutes for e in entries) > DAY_MAX:
            raise HTTPException(422, "day total exceeds 24 hours")
        check_categories(session, {e.category_id for e in entries})
        for old in session.scalars(select(Entry).where(Entry.date == day, Entry.user_id == user.id)):
            session.delete(old)
        session.add_all(
            Entry(user_id=user.id, date=day, category_id=e.category_id, minutes=e.minutes, note=e.note)
            for e in entries
        )
        if isinstance(body, DayIn):
            info = session.get(DayInfo, (user.id, day))
            km = None if body.km is None else round(body.km, 1)
            if body.location is None and km is None:
                if info is not None:
                    session.delete(info)
            elif info is None:
                session.add(DayInfo(user_id=user.id, date=day, location=body.location, km=km))
            else:
                info.location, info.km = body.location, km
        session.commit()
        changed(response, user.id, user.id)
        maybe_push_sensor([day], user, background)
        return day_out(session, user, day)

    @app.post("/api/fill")
    def fill(body: FillIn, session: SessionDep, user: UserDep, background: BackgroundTasks, response: Response):
        """Log the same hours on every workday in a range that has nothing logged yet."""
        check_range(body.start, body.end)
        if (body.end - body.start).days >= MAX_FILL_DAYS:
            raise HTTPException(422, f"fill at most {MAX_FILL_DAYS} days at once")
        check_categories(session, {body.category_id})
        workdays = workdays_of(get_settings(session, user.id))
        logged = set(session.scalars(
            select(Entry.date).where(Entry.user_id == user.id, Entry.date >= body.start, Entry.date <= body.end)
        ))
        days = [d for d in date_range(body.start, body.end) if d.weekday() in workdays and d not in logged]
        session.add_all(
            Entry(user_id=user.id, date=d, category_id=body.category_id, minutes=body.minutes, note=body.note)
            for d in days
        )
        session.commit()
        if days:
            changed(response, user.id, user.id)
            maybe_push_sensor(days, user, background)
        return {"filled": [d.isoformat() for d in days]}

    # --- settings ---

    @app.get("/api/settings")
    def read_settings(session: SessionDep, user: UserDep):
        return settings_out(get_settings(session, user.id))

    @app.put("/api/settings")
    def write_settings(body: SettingsIn, session: SessionDep, user: UserDep, response: Response):
        st = get_settings(session, user.id)
        st.weekly_minutes = round(body.weekly_hours * 60)
        st.workdays = "".join(str(d) for d in body.workdays)
        st.balance_start = body.balance_start
        st.office_km = None if body.office_km is None else round(body.office_km, 1)
        st.default_location = body.default_location
        session.merge(st)
        session.commit()
        changed(response, user.id, user.id)
        return settings_out(st)

    # --- note suggestions ---

    @app.get("/api/notes", response_model=list[str])
    def suggest_notes(
        session: SessionDep,
        user: UserDep,
        category_id: int | None = None,
        q: Annotated[str, Query(max_length=200)] = "",
    ):
        last_used = func.max(Entry.date)
        stmt = select(Entry.note).where(Entry.user_id == user.id, Entry.note.is_not(None))
        if category_id is not None:
            stmt = stmt.where(Entry.category_id == category_id)
        if q.strip():
            stmt = stmt.where(Entry.note.contains(q.strip(), autoescape=True))
        stmt = stmt.group_by(Entry.note).order_by(last_used.desc(), func.count().desc()).limit(10)
        return list(session.scalars(stmt))

    # --- reports ---

    def range_entries(session: Session, user: User, start: dt.date, end: dt.date):
        return session.execute(
            select(Entry, Category)
            .join(Category, Entry.category_id == Category.id)
            .where(Entry.user_id == user.id, Entry.date >= start, Entry.date <= end)
            .order_by(Entry.date, Entry.id)
        ).all()

    @app.get("/api/report")
    def report(
        session: SessionDep,
        user: UserDep,
        start: Annotated[dt.date, Query(alias="from")],
        end: Annotated[dt.date, Query(alias="to")],
    ):
        check_range(start, end)
        day_totals: dict[str, int] = {}
        cats: dict[int, dict] = {}
        total = 0
        for entry, cat in range_entries(session, user, start, end):
            d = entry.date.isoformat()
            total += entry.minutes
            day_totals[d] = day_totals.get(d, 0) + entry.minutes
            c = cats.setdefault(cat.id, {
                "id": cat.id, "name": cat.name, "color": cat.color, "archived": cat.archived,
                "sort_order": cat.sort_order, "total": 0, "days": {}, "notes": {},
            })
            c["total"] += entry.minutes
            c["days"][d] = c["days"].get(d, 0) + entry.minutes
            n = c["notes"].setdefault(entry.note or "", {"note": entry.note, "total": 0, "days": {}})
            n["total"] += entry.minutes
            n["days"][d] = n["days"].get(d, 0) + entry.minutes
        categories = sorted(cats.values(), key=lambda c: (c["sort_order"], c["id"]))
        for c in categories:
            c["notes"] = sorted(c["notes"].values(), key=lambda n: -n["total"])
        return {
            "from": start.isoformat(), "to": end.isoformat(), "total": total,
            "day_totals": day_totals, "categories": categories,
            **contract_report(session, user, start, end, day_totals),
        }

    def contract_report(session: Session, user: User, start: dt.date, end: dt.date, day_totals: dict) -> dict:
        """Target hours, overtime balance, unlogged workdays and office/remote/km for a range."""
        st = get_settings(session, user.id)
        today = dt.date.today()
        upto = min(end, today)
        first = session.scalar(select(func.min(Entry.date)).where(Entry.user_id == user.id))
        since = st.balance_start or first
        workdays = workdays_of(st)

        missing = []
        if since is not None:
            missing = [
                d.isoformat() for d in date_range(max(start, since), upto)
                if d.weekday() in workdays and d.isoformat() not in day_totals
            ]
        balance = None
        if st.weekly_minutes and since is not None and since <= upto:
            logged = session.scalar(
                select(func.coalesce(func.sum(Entry.minutes), 0))
                .where(Entry.user_id == user.id, Entry.date >= since, Entry.date <= upto)
            )
            balance = int(logged or 0) - target_minutes(st, since, upto)

        infos = session.scalars(
            select(DayInfo).where(DayInfo.user_id == user.id, DayInfo.date >= start, DayInfo.date <= end)
        ).all()
        return {
            "target": target_minutes(st, start, end) if st.weekly_minutes else 0,
            "target_to_date": target_minutes(st, start, upto) if st.weekly_minutes and start <= upto else 0,
            "logged_to_date": sum(m for d, m in day_totals.items() if d <= upto.isoformat()),
            "balance": balance,
            "balance_since": since.isoformat() if since and balance is not None else None,
            "missing": missing,
            "day_info": {i.date.isoformat(): {"location": i.location, "km": i.km} for i in infos},
        }

    @app.get("/api/export.csv")
    def export_csv(
        session: SessionDep,
        user: UserDep,
        start: Annotated[dt.date, Query(alias="from")],
        end: Annotated[dt.date, Query(alias="to")],
    ):
        check_range(start, end)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        writer.writerow(["date", "category", "hours", "note", "location", "km"])
        infos = {
            i.date: i for i in session.scalars(
                select(DayInfo).where(DayInfo.user_id == user.id, DayInfo.date >= start, DayInfo.date <= end)
            )
        }
        rows: dict[dt.date, list] = {}
        for entry, cat in range_entries(session, user, start, end):
            hours = f"{entry.minutes / 60:g}".replace(".", ",")
            rows.setdefault(entry.date, []).append([csv_cell(cat.name), hours, csv_cell(entry.note or "")])
        for day in sorted(rows.keys() | infos.keys()):
            info = infos.get(day)
            # Location and km are per day: only on the day's first row, so km sums correctly.
            extra = [info.location or "", format_km(info.km)] if info else ["", ""]
            for i, row in enumerate(rows.get(day) or [["", "", ""]]):
                writer.writerow([day.isoformat(), *row, *(extra if i == 0 else ["", ""])])
        filename = f"workday_{start.isoformat()}_{end.isoformat()}.csv"
        return Response(
            "﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


# ---------- entrypoint ----------

DB_NAME = "workday"


MYSQL_ATTEMPTS = 30
MYSQL_RETRY_SECONDS = 10


def connect_mysql(attempts: int = MYSQL_ATTEMPTS, delay: float = MYSQL_RETRY_SECONDS, sleep=time.sleep) -> dict:
    """Get MariaDB credentials from Supervisor and make sure our database exists.

    Retries for a while: at boot this add-on may start before MariaDB does.
    """
    import pymysql

    for attempt in range(1, attempts + 1):
        try:
            mysql = supervisor.mysql_service()
            conn = pymysql.connect(
                host=mysql["host"], port=int(mysql["port"]), user=mysql["username"], password=mysql["password"],
                connect_timeout=10,
            )
            try:
                with conn.cursor() as cur:
                    # DB_NAME is a constant, not user input.
                    cur.execute(
                        f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
            finally:
                conn.close()
            return mysql
        except supervisor.SupervisorError as e:
            reason = f"Supervisor did not provide MariaDB credentials ({e.status or 'no response'}: {e.message})"
        except pymysql.MySQLError as e:
            reason = f"Could not connect to MariaDB ({e})"
        if attempt == attempts:
            break
        log.warning("%s. Is the MariaDB add-on installed and started? Retrying in %ds (%d/%d)",
                    reason, delay, attempt, attempts)
        sleep(delay)
    raise SystemExit(
        f"{reason}. Install and start the official MariaDB add-on "
        "(Settings → Add-ons → Add-on store → MariaDB), then restart Workday."
    )


def addon_settings() -> tuple[str, reminder.ReminderConfig]:
    """Resolve DB URL and reminder config from Supervisor (inside Home Assistant)."""
    info = supervisor.addon_info()
    mysql = connect_mysql()
    url = (
        f"mysql+pymysql://{quote_plus(mysql['username'])}:{quote_plus(mysql['password'])}"
        f"@{mysql['host']}:{int(mysql['port'])}/{DB_NAME}?charset=utf8mb4"
    )
    return url, reminder.config_from_options(info.get("options", {}), info.get("slug", ""))


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dev_mode = os.environ.get("DEV_MODE") == "1"
    cfg = None
    if supervisor.available():
        url, cfg = addon_settings()
        log.info("Using MariaDB add-on; reminder %s", "enabled" if cfg.enabled else "disabled")
    else:
        url = os.environ.get("DATABASE_URL", "sqlite:///./workday-dev.db")
        log.info("Standalone mode (dev=%s)", dev_mode)
    if not re.match(r"^(mysql\+pymysql|sqlite)://", url):
        raise SystemExit("Unsupported DATABASE_URL")
    app = create_app(url, dev_mode=dev_mode, reminder_cfg=cfg)
    uvicorn.run(app, host="0.0.0.0", port=8099, server_header=False, proxy_headers=False)  # noqa: S104


if __name__ == "__main__":
    main()
