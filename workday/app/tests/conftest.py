import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    app = main.create_app("sqlite://", dev_mode=True)
    with TestClient(app) as c:
        yield c
        main.db.Base.metadata.drop_all(main.db.engine)


@pytest.fixture
def cats(client):
    return {c["name"]: c["id"] for c in client.get("/api/categories").json()}
