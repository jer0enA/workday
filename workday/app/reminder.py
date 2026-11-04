"""End-of-day reminder and the `sensor.workday_hours_today` entity in Home Assistant."""

import asyncio
import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import supervisor

log = logging.getLogger("workday.reminder")

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SENSOR = "sensor.workday_hours_today"
TICK_SECONDS = 30
SENSOR_REFRESH = dt.timedelta(minutes=5)


@dataclass
class ReminderConfig:
    enabled: bool = False
    time: dt.time = dt.time(18, 0)
    days: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})
    notify_service: str = ""
    min_hours: float = 0.0
    panel_url: str = ""
    user: str = ""  # HA username; empty = the only Workday user


def config_from_options(options: dict, slug: str = "") -> ReminderConfig:
    hh, mm = (options.get("reminder_time") or "18:00").split(":")
    days = {WEEKDAYS.index(d) for d in options.get("reminder_days", WEEKDAYS[:5]) if d in WEEKDAYS}
    return ReminderConfig(
        enabled=bool(options.get("reminder_enabled", False)),
        time=dt.time(int(hh), int(mm)),
        days=days,
        notify_service=(options.get("notify_service") or "").strip(),
        min_hours=float(options.get("min_hours") or 0),
        panel_url=f"/{slug}" if slug else "",
        user=(options.get("reminder_user") or "").strip(),
    )


def is_due(cfg: ReminderConfig, last: dt.datetime, now: dt.datetime) -> bool:
    """True when the reminder time was crossed between two ticks.

    Starting the add-on after the reminder time does not trigger it.
    """
    if not cfg.enabled or not cfg.notify_service or now.weekday() not in cfg.days:
        return False
    target = dt.datetime.combine(now.date(), cfg.time)
    return last < target <= now


def needs_reminder(cfg: ReminderConfig, logged_minutes: int) -> bool:
    threshold = max(1, round(cfg.min_hours * 60))
    return logged_minutes < threshold


def build_message(cfg: ReminderConfig, logged_minutes: int) -> dict:
    if logged_minutes == 0:
        text = "You haven't logged any hours today."
    else:
        h, m = divmod(logged_minutes, 60)
        text = f"Only {h}:{m:02d} logged today. Don't forget to fill in your hours."
    data: dict = {"tag": "workday-reminder"}
    if cfg.panel_url:
        data["url"] = cfg.panel_url  # iOS
        data["clickAction"] = cfg.panel_url  # Android
    return {"title": "Workday", "message": text, "data": data}


def send_notification(cfg: ReminderConfig, logged_minutes: int) -> None:
    domain, _, service = cfg.notify_service.partition(".")
    if not service:
        domain, service = "notify", domain
    supervisor.call_service(domain, service, build_message(cfg, logged_minutes))


def publish_sensor(logged_minutes: int) -> None:
    supervisor.set_state(SENSOR, f"{logged_minutes / 60:g}", {
        "unit_of_measurement": "h",
        "friendly_name": "Workday hours today",
        "icon": "mdi:clock-outline",
        "minutes": logged_minutes,
    })


async def run(cfg: ReminderConfig, minutes_for_day: Callable[[dt.date], int | None]) -> None:
    """Background loop: refreshes the sensor and sends the daily reminder.

    ``minutes_for_day`` returns None while it is unknown whose hours to use
    (nobody opened Workday yet, or several users and no ``reminder_user``).
    """
    last = dt.datetime.now()
    last_sensor: tuple[dt.date, dt.datetime] | None = None
    warned = False
    while True:
        await asyncio.sleep(TICK_SECONDS)
        now = dt.datetime.now()
        try:
            if last_sensor is None or last_sensor[0] != now.date() or now - last_sensor[1] >= SENSOR_REFRESH:
                minutes = await asyncio.to_thread(minutes_for_day, now.date())
                if minutes is not None:
                    await asyncio.to_thread(publish_sensor, minutes)
                    warned = False
                elif not warned:
                    log.warning(
                        "No user for the reminder and sensor yet: open Workday once, or set "
                        "reminder_user when several people use it"
                    )
                    warned = True
                last_sensor = (now.date(), now)
            if is_due(cfg, last, now):
                minutes = await asyncio.to_thread(minutes_for_day, now.date())
                if minutes is not None and needs_reminder(cfg, minutes):
                    await asyncio.to_thread(send_notification, cfg, minutes)
                    log.info("Reminder sent via %s (%d min logged)", cfg.notify_service, minutes)
        except Exception:
            log.exception("Reminder/sensor update failed")
        last = now
