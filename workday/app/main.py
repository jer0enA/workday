import asyncio
import contextlib
import csv
import datetime as dt
import io
import logging
import os
import re
import time
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import db
import reminder
import supervisor
from models import DEFAULT_CATEGORIES, Category, Entry

log = logging.getLogger("workday")

STATIC_DIR = Path(__file__).parent / "static"
INGRESS_PROXY = "172.30.32.2"
STEP = 15
DAY_MAX = 24 * 60
MAX_RANGE_DAYS = 400
COLOR_RE = r"^#[0-9a-fA-F]{6}$"

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


# ---------- helpers ----------

def seed_categories(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(Category)):
        return
    for i, (name, color) in enumerate(DEFAULT_CATEGORIES):
        session.add(Category(name=name, color=color, sort_order=i))
    session.commit()


def minutes_for_day(day: dt.date) -> int:
    with db.SessionLocal() as session:
        return session.scalar(select(func.coalesce(func.sum(Entry.minutes), 0)).where(Entry.date == day)) or 0


def check_range(start: dt.date, end: dt.date) -> None:
    if end < start:
        raise HTTPException(422, "'to' must not be before 'from'")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"range may span at most {MAX_RANGE_DAYS} days")


def csv_cell(value: str) -> str:
    # Neutralise spreadsheet formula injection.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def push_sensor_today() -> None:
    try:
        reminder.publish_sensor(minutes_for_day(dt.date.today()))
    except Exception:
        log.exception("Could not update %s", reminder.SENSOR)


# ---------- app ----------

def create_app(db_url: str, dev_mode: bool = False, reminder_cfg: reminder.ReminderConfig | None = None) -> FastAPI:
    db.init_engine(db_url)
    ha = supervisor.available()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.Base.metadata.create_all(db.engine)
        with db.SessionLocal() as session:
            seed_categories(session)
        task = None
        if ha and reminder_cfg is not None:
            task = asyncio.create_task(reminder.run(reminder_cfg, minutes_for_day))
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
        return response

    # --- categories ---

    @app.get("/api/categories", response_model=list[CategoryOut])
    def list_categories(session: SessionDep):
        return session.scalars(select(Category).order_by(Category.sort_order, Category.id)).all()

    @app.post("/api/categories", response_model=CategoryOut, status_code=201)
    def add_category(body: CategoryIn, session: SessionDep):
        top = session.scalar(select(func.coalesce(func.max(Category.sort_order), -1)))
        cat = Category(name=body.name, color=body.color, sort_order=top + 1)
        session.add(cat)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "a category with this name already exists") from None
        return cat

    @app.patch("/api/categories/{cat_id}", response_model=CategoryOut)
    def patch_category(cat_id: int, body: CategoryPatch, session: SessionDep):
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
        return cat

    # --- days ---

    @app.get("/api/day/{day}", response_model=list[EntryOut])
    def get_day(day: dt.date, session: SessionDep):
        return session.scalars(select(Entry).where(Entry.date == day).order_by(Entry.id)).all()

    @app.put("/api/day/{day}", response_model=list[EntryOut])
    def put_day(
        day: dt.date,
        body: Annotated[list[EntryIn], Body(max_length=50)],
        session: SessionDep,
        background: BackgroundTasks,
    ):
        if sum(e.minutes for e in body) > DAY_MAX:
            raise HTTPException(422, "day total exceeds 24 hours")
        ids = {e.category_id for e in body}
        if ids:
            known = set(session.scalars(select(Category.id).where(Category.id.in_(ids))))
            if ids - known:
                raise HTTPException(422, "unknown category")
        for old in session.scalars(select(Entry).where(Entry.date == day)):
            session.delete(old)
        rows = [Entry(date=day, category_id=e.category_id, minutes=e.minutes, note=e.note) for e in body]
        session.add_all(rows)
        session.commit()
        if ha and day == dt.date.today():
            background.add_task(push_sensor_today)
        return rows

    # --- note suggestions ---

    @app.get("/api/notes", response_model=list[str])
    def suggest_notes(
        session: SessionDep,
        category_id: int | None = None,
        q: Annotated[str, Query(max_length=200)] = "",
    ):
        last_used = func.max(Entry.date)
        stmt = select(Entry.note).where(Entry.note.is_not(None))
        if category_id is not None:
            stmt = stmt.where(Entry.category_id == category_id)
        if q.strip():
            stmt = stmt.where(Entry.note.contains(q.strip(), autoescape=True))
        stmt = stmt.group_by(Entry.note).order_by(last_used.desc(), func.count().desc()).limit(10)
        return list(session.scalars(stmt))

    # --- reports ---

    def range_entries(session: Session, start: dt.date, end: dt.date):
        return session.execute(
            select(Entry, Category)
            .join(Category, Entry.category_id == Category.id)
            .where(Entry.date >= start, Entry.date <= end)
            .order_by(Entry.date, Entry.id)
        ).all()

    @app.get("/api/report")
    def report(
        session: SessionDep,
        start: Annotated[dt.date, Query(alias="from")],
        end: Annotated[dt.date, Query(alias="to")],
    ):
        check_range(start, end)
        day_totals: dict[str, int] = {}
        cats: dict[int, dict] = {}
        total = 0
        for entry, cat in range_entries(session, start, end):
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
        }

    @app.get("/api/export.csv")
    def export_csv(
        session: SessionDep,
        start: Annotated[dt.date, Query(alias="from")],
        end: Annotated[dt.date, Query(alias="to")],
    ):
        check_range(start, end)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        writer.writerow(["date", "category", "hours", "note"])
        for entry, cat in range_entries(session, start, end):
            hours = f"{entry.minutes / 60:g}".replace(".", ",")
            writer.writerow([entry.date.isoformat(), csv_cell(cat.name), hours, csv_cell(entry.note or "")])
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
