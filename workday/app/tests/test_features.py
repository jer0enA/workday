# Dates are in the past, so "up to today" covers the whole range.
MON, TUE, WED = "2025-10-06", "2025-10-07", "2025-10-08"
WEEK = "from=2025-10-06&to=2025-10-12"


def test_location_and_km_per_day(client, cats):
    work = cats["Work"]
    body = {"entries": [{"category_id": work, "minutes": 480}], "location": "office", "km": 42.26}
    got = client.put(f"/api/day/{MON}", json=body).json()
    assert got["location"] == "office" and got["km"] == 42.3
    assert client.put(f"/api/day/{MON}", json={**body, "location": "home"}).status_code == 422
    assert client.put(f"/api/day/{MON}", json={**body, "km": -1}).status_code == 422

    # A bare list (older clients) keeps location and km.
    client.put(f"/api/day/{MON}", json=[{"category_id": work, "minutes": 240}])
    got = client.get(f"/api/day/{MON}").json()
    assert got["location"] == "office" and got["entries"][0]["minutes"] == 240

    client.put(f"/api/day/{TUE}", json={"entries": [], "location": "remote"})
    csv_text = client.get(f"/api/export.csv?{WEEK}").content.decode("utf-8-sig")
    assert csv_text.splitlines()[1:] == ["2025-10-06;Work;4;;office;42,3", "2025-10-07;;;;remote;"]

    rep = client.get(f"/api/report?{WEEK}").json()
    assert rep["day_info"] == {MON: {"location": "office", "km": 42.3}, TUE: {"location": "remote", "km": None}}


def test_previous_day(client, cats):
    assert client.get(f"/api/day/{WED}/previous").json() is None
    client.put(f"/api/day/{MON}", json={"entries": [{"category_id": cats["Work"], "minutes": 480, "note": "X"}],
                                        "location": "remote"})
    prev = client.get(f"/api/day/{WED}/previous").json()
    assert prev["date"] == MON and prev["entries"][0]["note"] == "X" and prev["location"] == "remote"


def test_fill_only_empty_workdays(client, cats):
    client.put(f"/api/day/{TUE}", json=[{"category_id": cats["Work"], "minutes": 480}])
    body = {"from": "2025-10-06", "to": "2025-10-12", "category_id": cats["Vacation"], "minutes": 480}
    r = client.post("/api/fill", json=body)
    assert r.json()["filled"] == ["2025-10-06", "2025-10-08", "2025-10-09", "2025-10-10"]
    assert client.get(f"/api/day/{TUE}").json()["entries"][0]["category_id"] == cats["Work"]
    assert client.post("/api/fill", json=body).json()["filled"] == []  # nothing left to fill
    too_long = {**body, "to": "2026-10-12"}
    assert client.post("/api/fill", json=too_long).status_code == 422


def test_settings(client):
    assert client.get("/api/settings").json() == {
        "weekly_hours": 40, "workdays": [0, 1, 2, 3, 4], "balance_start": None, "office_km": None,
        "default_location": None, "daily_minutes": 480,
    }
    new = {"weekly_hours": 36, "workdays": [3, 0, 1, 2, 2], "balance_start": "2025-10-01", "office_km": 35}
    got = client.put("/api/settings", json=new).json()
    assert got["workdays"] == [0, 1, 2, 3] and got["daily_minutes"] == 540 and got["office_km"] == 35
    assert client.put("/api/settings", json={**new, "weekly_hours": 36.1}).status_code == 422
    assert client.put("/api/settings", json={**new, "workdays": [7]}).status_code == 422
    got = client.put("/api/settings", json={**new, "default_location": "remote"}).json()
    assert got["default_location"] == "remote"
    assert client.put("/api/settings", json={**new, "default_location": "home"}).status_code == 422


def test_target_balance_and_missing(client, cats):
    work = cats["Work"]
    client.put(f"/api/day/{MON}", json=[{"category_id": work, "minutes": 540}])
    client.put(f"/api/day/{WED}", json=[{"category_id": work, "minutes": 480}])
    rep = client.get(f"/api/report?{WEEK}").json()
    assert rep["target"] == 2400 and rep["logged_to_date"] == 1020
    # Counting starts at the first logged day, so no "missing" days before it.
    assert rep["missing"] == ["2025-10-07", "2025-10-09", "2025-10-10"]
    assert rep["balance"] == 1020 - 2400 and rep["balance_since"] == MON

    client.put("/api/settings", json={"weekly_hours": 16, "workdays": [0, 2], "balance_start": None})
    rep = client.get(f"/api/report?{WEEK}").json()
    assert rep["missing"] == [] and rep["balance"] == 1020 - 960

    client.put("/api/settings", json={"weekly_hours": 0, "workdays": [0, 2], "balance_start": None})
    rep = client.get(f"/api/report?{WEEK}").json()
    assert rep["target"] == 0 and rep["balance"] is None
