import datetime as dt

import reminder

CFG = reminder.config_from_options(
    {
        "reminder_enabled": True,
        "reminder_time": "18:00",
        "reminder_days": ["mon", "tue", "wed", "thu", "fri"],
        "notify_service": "notify.mobile_app_phone",
        "min_hours": 0,
    },
    slug="local_workday",
)

TUE = dt.date(2026, 11, 3)
SAT = dt.date(2026, 11, 7)


def at(day, hh, mm, ss=0):
    return dt.datetime.combine(day, dt.time(hh, mm, ss))


def test_due_only_when_crossing_time():
    assert reminder.is_due(CFG, at(TUE, 17, 59, 45), at(TUE, 18, 0, 15))
    assert not reminder.is_due(CFG, at(TUE, 18, 0, 15), at(TUE, 18, 0, 45))
    assert not reminder.is_due(CFG, at(TUE, 17, 0), at(TUE, 17, 0, 30))


def test_not_due_on_unselected_day_or_disabled():
    assert not reminder.is_due(CFG, at(SAT, 17, 59, 45), at(SAT, 18, 0, 15))
    off = reminder.ReminderConfig(**{**CFG.__dict__, "enabled": False})
    assert not reminder.is_due(off, at(TUE, 17, 59, 45), at(TUE, 18, 0, 15))
    no_target = reminder.ReminderConfig(**{**CFG.__dict__, "notify_service": ""})
    assert not reminder.is_due(no_target, at(TUE, 17, 59, 45), at(TUE, 18, 0, 15))


def test_threshold():
    assert reminder.needs_reminder(CFG, 0)
    assert not reminder.needs_reminder(CFG, 15)
    eight = reminder.ReminderConfig(**{**CFG.__dict__, "min_hours": 8})
    assert reminder.needs_reminder(eight, 465)
    assert not reminder.needs_reminder(eight, 480)


def test_message_and_service_call(monkeypatch):
    calls = []
    monkeypatch.setattr(reminder.supervisor, "call_service", lambda d, s, data: calls.append((d, s, data)))
    reminder.send_notification(CFG, 0)
    domain, service, data = calls[0]
    assert (domain, service) == ("notify", "mobile_app_phone")
    assert data["message"] == "You haven't logged any hours today."
    assert data["data"]["url"] == "/local_workday"
    assert "4:30" in reminder.build_message(CFG, 270)["message"]


def test_skip_entities(monkeypatch):
    cfg = reminder.config_from_options({"skip_entity": "input_boolean.vacation, calendar.Off ,bad/../x"})
    assert cfg.skip_entities == ["input_boolean.vacation", "calendar.off"]
    states = {"input_boolean.vacation": "off", "calendar.off": "on"}
    monkeypatch.setattr(reminder.supervisor, "get_state", lambda e: {"state": states[e]})
    assert reminder.day_off_entity(cfg) == "calendar.off"
    states["calendar.off"] = "off"
    assert reminder.day_off_entity(cfg) is None

    def missing(e):
        raise reminder.supervisor.SupervisorError(f"/core/api/states/{e}", 404, "Entity not found")

    monkeypatch.setattr(reminder.supervisor, "get_state", missing)
    assert reminder.day_off_entity(cfg) is None  # unreadable entity: remind anyway
