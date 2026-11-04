import io
import urllib.error

import pymysql
import pytest

import main
import supervisor


def http_error(code, body):
    return urllib.error.HTTPError("http://supervisor/services/mysql", code, "Bad Request", {}, io.BytesIO(body))


def test_error_message_from_supervisor(monkeypatch):
    def fail(*_):
        raise http_error(400, b'{"result": "error", "message": "Service not enabled"}')

    monkeypatch.setattr(supervisor, "_send", fail)
    with pytest.raises(supervisor.SupervisorError) as exc:
        supervisor.mysql_service()
    assert exc.value.status == 400
    assert exc.value.message == "Service not enabled"


def test_connect_mysql_retries_then_succeeds(monkeypatch):
    calls = {"service": 0, "sql": []}
    creds = {"host": "core-mariadb", "port": 3306, "username": "service", "password": "pw"}

    def service():
        calls["service"] += 1
        if calls["service"] < 3:
            raise supervisor.SupervisorError("/services/mysql", 400, "Service not enabled")
        return creds

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            calls["sql"].append(sql)

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(supervisor, "mysql_service", service)
    monkeypatch.setattr(pymysql, "connect", lambda **_: Conn())
    slept = []
    assert main.connect_mysql(attempts=5, delay=1, sleep=slept.append) == creds
    assert calls["service"] == 3 and slept == [1, 1]
    assert "CREATE DATABASE IF NOT EXISTS `workday`" in calls["sql"][0]


def test_connect_mysql_gives_clear_error(monkeypatch):
    def service():
        raise supervisor.SupervisorError("/services/mysql", 400, "Service not enabled")

    monkeypatch.setattr(supervisor, "mysql_service", service)
    with pytest.raises(SystemExit) as exc:
        main.connect_mysql(attempts=2, delay=0, sleep=lambda _: None)
    assert "Service not enabled" in str(exc.value)
    assert "MariaDB add-on" in str(exc.value)
