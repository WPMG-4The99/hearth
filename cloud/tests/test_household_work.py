"""Roles, private lists and the shared board.

Staff cannot read another person's list, assign a task, or become admin.
The admin can. A shared change is a household-log row. A private note is not.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_hub import sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud

ADMIN_HDR = {"X-Admin-Token": "admin-test"}


@pytest.fixture
def client():
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        public_base="http://cloud.test",
        households="cap@example.com|gwen@example.com|kelsi@example.com,other@example.com|stranger@example.com",
        household_admins="cap@example.com",
        admin_token="admin-test",
    )
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud)
    with TestClient(app) as c:
        c.sender = sender
        yield c


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _ids(client: TestClient) -> dict[str, str]:
    keys = {
        "cap": sign_up(client, "cap@example.com"),
        "gwen": sign_up(client, "gwen@example.com"),
        "kelsi": sign_up(client, "kelsi@example.com"),
        "other": sign_up(client, "other@example.com"),
    }
    people = client.get("/v1/household/work", headers=_auth(keys["cap"])).json()["people"]
    by_who = {p["who"]: p["account_id"] for p in people}
    return {**keys, "id": by_who}


def test_roles_come_from_the_operator_and_cannot_be_claimed(client):
    ids = _ids(client)
    cap = client.get("/v1/household/work", headers=_auth(ids["cap"])).json()
    gwen = client.get("/v1/household/work", headers=_auth(ids["gwen"])).json()
    assert cap["role"] == "admin" and gwen["role"] == "staff"
    claim = client.post("/v1/household/board", headers=_auth(ids["gwen"]), json={"text": "walk", "role": "admin"})
    assert claim.status_code == 403 and claim.json()["error"]["code"] == "not_allowed"
    assert client.get("/v1/household/work", headers=_auth(ids["gwen"])).json()["role"] == "staff"
    missing = client.post("/v1/household/role", headers=_auth(ids["gwen"]), json={"role": "admin"})
    assert missing.status_code == 404
    assert client.get("/v1/household/work", headers=_auth(ids["gwen"])).json()["role"] == "staff"


def test_staff_cannot_read_or_write_another_private_list(client):
    ids = _ids(client)
    gwen_id = ids["id"]["gwen"]
    wrote = client.put(
        f"/v1/household/private/{gwen_id}/notes",
        headers=_auth(ids["gwen"]),
        json={"body": "Gwen's door code"},
    )
    assert wrote.status_code == 200, wrote.text
    added = client.post(
        f"/v1/household/private/{gwen_id}/tasks",
        headers=_auth(ids["gwen"]),
        json={"text": "Gwen's private task"},
    )
    assert added.status_code == 200, added.text
    for method, path, body in (
        ("GET", f"/v1/household/private/{gwen_id}", None),
        ("PUT", f"/v1/household/private/{gwen_id}/notes", {"body": "stolen"}),
        ("POST", f"/v1/household/private/{gwen_id}/tasks", {"text": "stolen"}),
    ):
        response = client.request(method, path, headers=_auth(ids["kelsi"]), json=body)
        assert response.status_code == 403 and response.json()["error"]["code"] == "not_allowed", response.text
    own = client.get(f"/v1/household/private/{gwen_id}", headers=_auth(ids["gwen"])).json()
    assert own["notes"] == "Gwen's door code"
    # the admin sees and edits it
    seen = client.get(f"/v1/household/private/{gwen_id}", headers=_auth(ids["cap"])).json()
    assert seen["notes"] == "Gwen's door code" and seen["tasks"][0]["text"] == "Gwen's private task"
    edited = client.put(
        f"/v1/household/private/{gwen_id}/notes",
        headers=_auth(ids["cap"]),
        json={"body": "Cap updated Gwen's note"},
    )
    assert edited.status_code == 200
    assert client.get(f"/v1/household/private/{gwen_id}", headers=_auth(ids["gwen"])).json()["notes"] == "Cap updated Gwen's note"
    # another household cannot read it
    other = client.get(f"/v1/household/private/{gwen_id}", headers=_auth(ids["other"]))
    assert other.status_code == 403
    # private words never land in the household log
    events = client.get("/v1/household/events", headers=_auth(ids["cap"])).json()["events"]
    blob = " ".join(row["text"] for row in events)
    assert "door code" not in blob and "private task" not in blob


def test_only_the_admin_assigns_and_the_log_records_shared_changes(client):
    ids = _ids(client)
    gwen_id = ids["id"]["gwen"]
    created = client.post("/v1/household/board", headers=_auth(ids["gwen"]), json={"text": "Walk the dog"})
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    assert task["assignee_id"] == "" and task["done"] is False
    # staff cannot assign, even to herself
    denied = client.post(
        f"/v1/household/board/{task['id']}",
        headers=_auth(ids["gwen"]),
        json={"assignee_id": gwen_id},
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "not_allowed"
    denied_new = client.post(
        "/v1/household/board",
        headers=_auth(ids["kelsi"]),
        json={"text": "Secret assign", "assignee_id": gwen_id},
    )
    assert denied_new.status_code == 403
    # the unassigned task is on both staff boards
    for who in ("gwen", "kelsi", "cap"):
        board = client.get("/v1/household/work", headers=_auth(ids[who])).json()["board"]
        assert any(row["id"] == task["id"] for row in board)
    other_board = client.get("/v1/household/work", headers=_auth(ids["other"])).json()["board"]
    assert all(row["id"] != task["id"] for row in other_board)
    done = client.post(
        f"/v1/household/board/{task['id']}",
        headers=_auth(ids["kelsi"]),
        json={"done": True},
    )
    assert done.status_code == 200 and done.json()["task"]["done"] is True
    assigned = client.post(
        f"/v1/household/board/{task['id']}",
        headers=_auth(ids["cap"]),
        json={"assignee_id": gwen_id, "done": False},
    )
    assert assigned.status_code == 200 and assigned.json()["task"]["assignee"] == "gwen"
    events = client.get("/v1/household/events", headers=_auth(ids["gwen"])).json()["events"]
    task_rows = [row for row in events if row["kind"] == "task"]
    assert any(row["text"].startswith("done:") for row in task_rows)
    assert any(row["text"].startswith("assigned:") for row in task_rows)
    assert all("door code" not in row["text"] for row in events)
    # Jessica's feed sees the shared change and cannot write
    issued = client.post(
        "/v1/admin/household-feed",
        headers=ADMIN_HDR,
        json={"label": "Jessica", "member": "cap@example.com", "kinds": ["task", "check-in"]},
    )
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    feed = client.get("/v1/household/feed", headers=_auth(token)).json()["events"]
    assert any(row["kind"] == "task" and row["text"].startswith("done:") for row in feed)
    assert all("door code" not in row["text"] for row in feed)
    assert client.post("/v1/household/board", headers=_auth(token), json={"text": "no"}).status_code == 401
    assert client.get(f"/v1/household/private/{gwen_id}", headers=_auth(token)).status_code == 401


def test_feed_ceiling_hides_task_events():
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        public_base="http://cloud.test",
        households="cap@example.com|gwen@example.com",
        household_admins="cap@example.com",
        household_feed_kinds="check-in,panic,alert",
        admin_token="admin-test",
    )
    sender = LogSender()
    app = create_app(settings, Cloud(settings, Database(":memory:"), sender))
    with TestClient(app) as client:
        client.sender = sender
        cap = sign_up(client, "cap@example.com")
        sign_up(client, "gwen@example.com")
        assert client.post("/v1/household/board", headers=_auth(cap), json={"text": "Meds are not here"}).status_code == 200
        issued = client.post(
            "/v1/admin/household-feed",
            headers=ADMIN_HDR,
            json={"label": "Jessica", "member": "cap@example.com", "kinds": ["task", "check-in", "panic", "alert"]},
        )
        assert issued.status_code == 200, issued.text
        feed = client.get("/v1/household/feed", headers=_auth(issued.json()["token"])).json()["events"]
        assert feed == []
