# Changelog

## 0.3.4

- The end-of-day reminder is sent as a high-priority notification, so Android
  delivers it right away instead of holding it back while the phone is idle.

## 0.3.3

- **Hours per workday** setting (default 8): used as the day target and the
  default for new rows and *Fill week*, instead of contract hours divided by
  workdays (which could give odd values like 7:12).

## 0.3.2

- **Default location:** choose *Office* or *Remote* on the ⚙ page; days you
  start logging get it automatically (you can still change it per day).

## 0.3.1

- Fix: with a daily target that is not a whole quarter (e.g. 36 h/week = 7:12),
  new rows could not be saved and `−` / `+` left the 15-minute grid. New rows
  now start at the nearest quarter and the stepper snaps to it.
- Clearer error messages when saving fails.

## 0.3.0

- **Copy previous day:** an empty day offers to copy the last day you logged.
- **Fill week:** log the same hours (e.g. Vacation 8h) on all empty workdays of a week.
- **Contract hours:** set your weekly hours and workdays under ⚙. Week and month
  show logged versus target hours and your running overtime balance; the day
  view shows the daily target.
- **Missing days:** week and month list the workdays you have not logged yet.
- **Office / remote and km:** mark each day as office or remote and enter the km
  you drove (prefilled on office days). Week and month count office and remote
  days and total km; the CSV export has `location` and `km` columns.
- New option `skip_entity`: no reminder while an entity is on, e.g. a vacation
  calendar or `input_boolean`.

## 0.2.5

- Live refresh: changes made on another device (e.g. your phone) show up
  within about 20 seconds in an open Workday page, without reloading.

## 0.2.4

- The `1h` / `4h` / `8h` shortcuts now also show on phones, on their own line.

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
