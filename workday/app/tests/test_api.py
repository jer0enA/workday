from fastapi.testclient import TestClient

import main


def test_seeded_categories(cats):
    assert list(cats) == ["Work", "Study", "Sick", "Vacation", "Public holiday"]


def test_put_and_get_day(client, cats):
    body = [
        {"category_id": cats["Work"], "minutes": 240, "note": "  Project   X "},
        {"category_id": cats["Study"], "minutes": 240, "note": ""},
    ]
    r = client.put("/api/day/2026-11-03", json=body)
    assert r.status_code == 200
    got = client.get("/api/day/2026-11-03").json()
    assert [(e["minutes"], e["note"]) for e in got] == [(240, "Project X"), (240, None)]

    # Replacing the day removes old rows.
    client.put("/api/day/2026-11-03", json=[{"category_id": cats["Sick"], "minutes": 480}])
    got = client.get("/api/day/2026-11-03").json()
    assert len(got) == 1 and got[0]["category_id"] == cats["Sick"]


def test_validation(client, cats):
    work = cats["Work"]
    assert client.put("/api/day/2026-11-03", json=[{"category_id": work, "minutes": 10}]).status_code == 422
    assert client.put("/api/day/2026-11-03", json=[{"category_id": work, "minutes": 0}]).status_code == 422
    too_much = [{"category_id": work, "minutes": 720}] * 3
    assert client.put("/api/day/2026-11-03", json=too_much).status_code == 422
    assert client.put("/api/day/2026-11-03", json=[{"category_id": 999, "minutes": 60}]).status_code == 422
    long_note = [{"category_id": work, "minutes": 60, "note": "x" * 201}]
    assert client.put("/api/day/2026-11-03", json=long_note).status_code == 422
    assert client.get("/api/day/not-a-date").status_code == 422
    assert client.get("/api/report?from=2026-11-05&to=2026-11-01").status_code == 422


def test_note_suggestions(client, cats):
    work, study = cats["Work"], cats["Study"]
    client.put("/api/day/2026-11-02", json=[{"category_id": work, "minutes": 480, "note": "Project X"}])
    client.put("/api/day/2026-11-03", json=[{"category_id": work, "minutes": 480, "note": "Project Y"}])
    client.put("/api/day/2026-11-04", json=[{"category_id": study, "minutes": 480, "note": "Prometheus course"}])

    assert client.get(f"/api/notes?category_id={work}&q=pro").json() == ["Project Y", "Project X"]
    assert client.get(f"/api/notes?category_id={study}&q=").json() == ["Prometheus course"]
    # LIKE wildcards are escaped.
    assert client.get(f"/api/notes?category_id={work}&q=%25").json() == []


def test_report_and_csv(client, cats):
    work, study = cats["Work"], cats["Study"]
    client.put("/api/day/2026-11-02", json=[{"category_id": work, "minutes": 480, "note": "Project X"}])
    client.put("/api/day/2026-11-03", json=[
        {"category_id": work, "minutes": 270, "note": "Project X"},
        {"category_id": study, "minutes": 210, "note": "=HYPERLINK(1)"},
    ])
    rep = client.get("/api/report?from=2026-11-02&to=2026-11-08").json()
    assert rep["total"] == 960
    assert rep["day_totals"] == {"2026-11-02": 480, "2026-11-03": 480}
    w = rep["categories"][0]
    assert w["name"] == "Work" and w["total"] == 750
    assert w["notes"][0] == {"note": "Project X", "total": 750, "days": {"2026-11-02": 480, "2026-11-03": 270}}

    r = client.get("/api/export.csv?from=2026-11-02&to=2026-11-08")
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines == [
        "date;category;hours;note",
        "2026-11-02;Work;8;Project X",
        "2026-11-03;Work;4,5;Project X",
        "2026-11-03;Study;3,5;'=HYPERLINK(1)",
    ]


def test_categories_crud(client, cats):
    r = client.post("/api/categories", json={"name": "Training", "color": "#0891b2"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.post("/api/categories", json={"name": "Training"}).status_code == 409
    assert client.post("/api/categories", json={"name": "X", "color": "red"}).status_code == 422
    r = client.patch(f"/api/categories/{cid}", json={"archived": True, "name": "Courses"})
    assert r.json()["archived"] is True and r.json()["name"] == "Courses"
    assert client.patch(f"/api/categories/{cid}", json={"name": "Work"}).status_code == 409
    assert client.patch("/api/categories/999", json={"name": "Nope"}).status_code == 404


def test_security_headers_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>Workday</title>" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-cache"
    assert client.get("/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/api/categories").headers["cache-control"] == "no-store"


def test_non_ingress_clients_rejected(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    app = main.create_app("sqlite://", dev_mode=False)
    with TestClient(app) as c:  # TestClient's client host is "testclient"
        assert c.get("/api/categories").status_code == 403
        assert c.get("/docs").status_code == 403
    main.db.Base.metadata.drop_all(main.db.engine)
