import sqlite3

from fastapi.testclient import TestClient

import main

ALICE = {"X-Remote-User-Id": "a" * 32, "X-Remote-User-Name": "alice", "X-Remote-User-Display-Name": "Alice"}
BOB = {"X-Remote-User-Id": "b" * 32, "X-Remote-User-Name": "bob", "X-Remote-User-Display-Name": "Bob"}


def test_users_see_only_their_own_hours(client, cats):
    work = cats["Work"]
    alice_day = [{"category_id": work, "minutes": 480, "note": "Alice project"}]
    bob_day = [{"category_id": work, "minutes": 240, "note": "Bob project"}]
    client.put("/api/day/2026-11-03", json=alice_day, headers=ALICE)
    client.put("/api/day/2026-11-03", json=bob_day, headers=BOB)

    assert [e["note"] for e in client.get("/api/day/2026-11-03", headers=ALICE).json()["entries"]] == ["Alice project"]
    assert [e["note"] for e in client.get("/api/day/2026-11-03", headers=BOB).json()["entries"]] == ["Bob project"]
    assert client.get(f"/api/notes?category_id={work}&q=", headers=BOB).json() == ["Bob project"]
    rep = client.get("/api/report?from=2026-11-02&to=2026-11-08", headers=ALICE).json()
    assert rep["total"] == 480
    csv_text = client.get("/api/export.csv?from=2026-11-02&to=2026-11-08", headers=BOB).content.decode("utf-8-sig")
    assert "Bob project" in csv_text and "Alice project" not in csv_text

    # Replacing a day only touches your own rows.
    client.put("/api/day/2026-11-03", json=[], headers=BOB)
    assert len(client.get("/api/day/2026-11-03", headers=ALICE).json()["entries"]) == 1


def test_me_and_user_registry(client):
    assert client.get("/api/me", headers=ALICE).json() == {"username": "alice", "display_name": "Alice"}
    client.get("/api/me", headers=BOB)
    assert main.reminder_user_id("ALICE") == "a" * 32  # case-insensitive
    assert main.reminder_user_id("") is None  # two users and none configured
    assert main.reminder_user_id("nobody") is None


def test_single_user_is_reminder_user(client):
    client.get("/api/me", headers=ALICE)
    assert main.reminder_user_id("") == "a" * 32


def test_invalid_user_header_rejected(client):
    assert client.get("/api/day/2026-11-03", headers={"X-Remote-User-Id": "x'; DROP TABLE--"}).status_code == 401


def test_existing_database_is_migrated_and_claimed(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE categories (id INTEGER PRIMARY KEY, name VARCHAR(50) UNIQUE, color VARCHAR(7),
                                 archived BOOLEAN, sort_order INTEGER);
        CREATE TABLE entries (id INTEGER PRIMARY KEY, date DATE, category_id INTEGER REFERENCES categories(id),
                              minutes INTEGER, note VARCHAR(200));
        INSERT INTO categories VALUES (1, 'Work', '#2563eb', 0, 0);
        INSERT INTO entries VALUES (1, '2026-11-02', 1, 480, 'Old project');
    """)
    con.commit()
    con.close()

    app = main.create_app(f"sqlite:///{path}", dev_mode=True)
    with TestClient(app) as c:
        # The first user to open Workday gets the hours from before per-user support.
        assert [e["note"] for e in c.get("/api/day/2026-11-02", headers=ALICE).json()["entries"]] == ["Old project"]
        assert c.get("/api/day/2026-11-02", headers=BOB).json()["entries"] == []
    main.db.engine.dispose()


def test_rev_changes_only_for_own_and_shared_changes(client, cats):
    work = cats["Work"]
    alice0 = client.get("/api/rev", headers=ALICE).json()["rev"]
    bob0 = client.get("/api/rev", headers=BOB).json()["rev"]

    r = client.put("/api/day/2026-11-03", json=[{"category_id": work, "minutes": 60}], headers=ALICE)
    alice1 = client.get("/api/rev", headers=ALICE).json()["rev"]
    assert alice1 != alice0 and r.headers["x-workday-rev"] == alice1
    assert client.get("/api/rev", headers=BOB).json()["rev"] == bob0  # Bob's view is unaffected

    # Categories are shared, so a change by Bob reaches Alice too.
    client.post("/api/categories", json={"name": "Training"}, headers=BOB)
    assert client.get("/api/rev", headers=ALICE).json()["rev"] != alice1
