"""Minimal Home Assistant Supervisor client (stdlib only, blocking).

Only active inside the add-on, where Supervisor injects SUPERVISOR_TOKEN.
"""

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("workday.supervisor")

BASE_URL = "http://supervisor"
TIMEOUT = 10


class SupervisorError(Exception):
    def __init__(self, path: str, status: int | None, message: str):
        self.path = path
        self.status = status
        self.message = message
        super().__init__(f"{path}: {status or 'no response'} {message}".strip())


def available() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    try:
        return _send(method, path, payload)
    except urllib.error.HTTPError as e:
        # Supervisor explains errors as {"result": "error", "message": "..."}.
        try:
            message = json.loads(e.read() or b"{}").get("message", "") or e.reason
        except (ValueError, AttributeError):
            message = e.reason
        raise SupervisorError(path, e.code, str(message)) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise SupervisorError(path, None, str(getattr(e, "reason", e))) from None


def _send(method: str, path: str, payload: dict | None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(  # noqa: S310 - fixed internal URL
        BASE_URL + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        body = resp.read()
    return json.loads(body) if body else {}


def mysql_service() -> dict:
    """Credentials of the MariaDB add-on: host, port, username, password."""
    return _request("GET", "/services/mysql")["data"]


def addon_info() -> dict:
    """Info about this add-on, including its slug and current options."""
    return _request("GET", "/addons/self/info")["data"]


def call_service(domain: str, service: str, data: dict) -> None:
    _request("POST", f"/core/api/services/{domain}/{service}", data)


def get_state(entity_id: str) -> dict:
    return _request("GET", f"/core/api/states/{entity_id}")


def set_state(entity_id: str, state: str, attributes: dict) -> None:
    _request("POST", f"/core/api/states/{entity_id}", {"state": state, "attributes": attributes})
