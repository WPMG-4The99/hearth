"""The household log, and a feed key that can only read it.

A panic is stored even when the other phone is offline. A check-in is the same
log. An account outside the household sees nothing. A feed key is not an
account key: the hub and /v1/me refuse it, and revoking it leaves accounts alone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_hub import connect, hello, sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud

ADMIN = {"X-Admin-Token": "admin-test"}


@pytest.fixture
def client():
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        public_base="http://cloud.test",
        households="a@example.com|b@example.com,c@example.com|d@example.com",
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


def test_check_in_is_visible_to_the_household_only(client):
    key_a = sign_up(client, "a@example.com")
    key_b = sign_up(client, "b@example.com")
    key_c = sign_up(client, "c@example.com")
    wrote = client.post("/v1/household/events", headers=_auth(key_a), json={"kind": "check-in", "text": "home"})
    assert wrote.status_code == 200, wrote.text
    event = wrote.json()["event"]
    assert event["kind"] == "check-in" and event["who"] == "a" and event["text"] == "home"
    seen = client.get("/v1/household/events", headers=_auth(key_b)).json()["events"]
    assert [row["id"] for row in seen] == [event["id"]]
    other = client.get("/v1/household/events", headers=_auth(key_c)).json()["events"]
    assert other == []
    stranger = sign_up(client, "z@example.com")
    refused = client.get("/v1/household/events", headers=_auth(stranger))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_linked"
    forged = client.post("/v1/household/events", headers=_auth(key_a), json={"kind": "panic"})
    assert forged.status_code == 400 and forged.json()["error"]["code"] == "bad_kind"


def test_feed_key_reads_one_household_and_nothing_else(client):
    key_a = sign_up(client, "a@example.com")
    sign_up(client, "b@example.com")
    key_c = sign_up(client, "c@example.com")
    client.post("/v1/household/events", headers=_auth(key_a), json={"kind": "check-in"})
    with connect(client, key_a) as left:
        left.send_json(hello("phone", "phone-a", "Cap", actions=("panic", "alert")))
        assert left.receive_json()["type"] == "welcome"
        left.receive_json()
        left.send_json({"type": "call", "id": "p1", "to": "household", "action": "panic", "args": {"text": "help"}})
        saved = left.receive_json()
        assert saved["body"]["delivered"] is False and saved["body"]["saved"] is True
    client.post("/v1/household/events", headers=_auth(key_c), json={"kind": "check-in", "text": "other house"})

    issued = client.post(
        "/v1/admin/household-feed",
        headers=ADMIN,
        json={"label": "Jessica", "member": "a@example.com", "kinds": ["check-in", "panic", "alert"]},
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    token = body["token"]
    assert token.startswith("nmf_") and body["who"] == ["a", "b"] and "token" not in body["prefix"]
    assert client.get("/v1/admin/household-feed", headers=ADMIN).json()["keys"][0]["id"] == body["id"]
    # the token is not in the list
    assert all("token" not in row for row in client.get("/v1/admin/household-feed", headers=ADMIN).json()["keys"])

    feed = client.get("/v1/household/feed", headers=_auth(token))
    assert feed.status_code == 200, feed.text
    kinds = [row["kind"] for row in feed.json()["events"]]
    assert kinds == ["panic", "check-in"]
    assert all(row["text"] != "other house" for row in feed.json()["events"])

    me = client.get("/v1/me", headers=_auth(token))
    assert me.status_code == 401 and me.json()["error"]["code"] == "bad_key"
    chat = client.post("/v1/chat/completions", headers=_auth(token), json={"model": "x", "messages": []})
    assert chat.status_code == 401
    with connect(client, token) as sock:
        sock.send_json(hello("computer", "jessica", "Jessica"))
        err = sock.receive_json()
        assert err["type"] == "error" and err["code"] == "bad_key"
        with pytest.raises(WebSocketDisconnect):
            sock.receive_json()

    narrow = client.post(
        "/v1/admin/household-feed",
        headers=ADMIN,
        json={"label": "Jessica", "member": "b@example.com", "kinds": ["check-in"]},
    )
    quiet = client.get("/v1/household/feed", headers=_auth(narrow.json()["token"])).json()["events"]
    assert [row["kind"] for row in quiet] == ["check-in"]

    revoked = client.post(f"/v1/admin/household-feed/{body['id']}/revoke", headers=ADMIN)
    assert revoked.status_code == 200 and revoked.json()["revoked_at"]
    dead = client.get("/v1/household/feed", headers=_auth(token))
    assert dead.status_code == 401
    # the accounts are still signed in
    assert client.get("/v1/me", headers=_auth(key_a)).status_code == 200


def test_feed_kinds_follow_the_operator_setting():
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        public_base="http://cloud.test",
        households="a@example.com|b@example.com",
        admin_token="admin-test",
        household_feed_kinds="check-in",
    )
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud)
    with TestClient(app) as client:
        client.sender = sender
        key_a = sign_up(client, "a@example.com")
        client.post("/v1/household/events", headers=_auth(key_a), json={"kind": "check-in"})
        with connect(client, key_a) as left:
            left.send_json(hello("phone", "phone-a", "Cap", actions=("panic",)))
            left.receive_json()
            left.receive_json()
            left.send_json({"type": "call", "id": "p1", "to": "household", "action": "panic", "args": {}})
            assert left.receive_json()["body"]["saved"] is True
        issued = client.post(
            "/v1/admin/household-feed",
            headers=ADMIN,
            json={"label": "Jessica", "member": "a@example.com", "kinds": ["check-in", "panic", "alert"]},
        )
        assert issued.status_code == 200, issued.text
        events = client.get("/v1/household/feed", headers=_auth(issued.json()["token"])).json()["events"]
        assert [row["kind"] for row in events] == ["check-in"]
        missing = client.post(
            "/v1/admin/household-feed",
            headers=ADMIN,
            json={"label": "nobody", "member": "not-in@example.com"},
        )
        assert missing.status_code == 400 and missing.json()["error"]["code"] == "not_linked"
        assert client.post("/v1/admin/household-feed", json={"label": "x", "member": "a@example.com"}).status_code == 401
