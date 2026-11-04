# Workday

Log your daily hours in 15-minute steps per category (Work, Study, Sick,
Vacation, …) with an optional note such as a project name. Notes are
suggested from what you entered before. Week and month overviews can be
exported to CSV (`;`-separated, decimal comma, opens directly in Dutch Excel).

## Requirements

The official **MariaDB** add-on must be installed and running. Workday creates
its own `workday` database there on first start. Because the data lives in the
MariaDB add-on, it is included in Home Assistant backups.

## Access and users

Workday is only reachable through Home Assistant (Ingress): open it from the
sidebar or the companion app. No port is exposed.

Every Home Assistant user has their **own hours and notes**; the list of
categories is shared. Non-admin users can open Workday too. Hours logged
before version 0.2.0 belong to the first user who opens Workday after the
update.

## End-of-day reminder

| Option | Description |
| --- | --- |
| `reminder_enabled` | Turn the reminder on/off. |
| `reminder_time` | When to check, `HH:MM` in Home Assistant's time zone. |
| `reminder_days` | Weekdays to check (`mon` … `sun`). |
| `notify_service` | Notify action, e.g. `notify.mobile_app_my_phone`. Find yours under *Developer tools → Actions*. |
| `min_hours` | Remind when fewer hours are logged. `0` = only when nothing is logged. |
| `reminder_user` | Home Assistant **username** whose hours are checked. Leave empty when you are the only Workday user. |

Tapping the notification opens Workday. Restart the add-on after changing options.

## Sensor

The add-on keeps `sensor.workday_hours_today` up to date with the hours the
reminder user logged today, so you can build your own automations, e.g. a
reminder on a speaker:

```yaml
triggers:
  - trigger: time
    at: "21:00:00"
conditions:
  - condition: numeric_state
    entity_id: sensor.workday_hours_today
    below: 1
actions:
  - action: tts.speak
    target:
      entity_id: tts.home_assistant_cloud
    data:
      media_player_entity_id: media_player.kitchen
      message: Don't forget to log your hours.
```

The sensor is created by the add-on (not an integration), so it has no history
before the add-on first ran and reappears within 5 minutes after a Home
Assistant restart.
