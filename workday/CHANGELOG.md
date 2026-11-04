# Changelog

## 0.2.3

- Browsers always fetch the latest UI after an update instead of a cached copy.

## 0.2.2

- New `1h` shortcut next to `4h` and `8h`.

## 0.2.1

- Tapping the reminder notification opens Workday instead of a 404 page.

## 0.2.0

- Hours and notes are now per Home Assistant user; categories stay shared.
  Existing hours go to the first user who opens Workday after the update.
- Non-admin Home Assistant users can open Workday.
- New option `reminder_user`: whose hours the reminder and sensor use.
- The settings page shows who you are signed in as.

## 0.1.2

- Wait up to 5 minutes for the MariaDB add-on at startup instead of crashing.
- Clear log message when MariaDB is missing or not running.

## 0.1.1

- New icon and logo, add-on store README with screenshots.
- Favicon in the web app.

## 0.1.0

- First release: day/week/month views, categories, note autocomplete, CSV export.
- End-of-day reminder via a Home Assistant notify service.
- `sensor.workday_hours_today` entity.
