"""A household may exchange a panic or an alert, and nothing else.

Linking is the operator's HOUSEHOLDS list. An account that is not on it cannot
reach another account, and a linked account cannot ask the other phone to run
a shell, read files, take the screen, open a link or start a task.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_hub import connect, hello, sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.households import groups, partner_hashes
from nanomuse_cloud.identifiers import parse
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud

BLOCKED = ("shell", "files", "file.get", "file.put", "screen", "task", "open", "notify")


@pytest.fixture
def client():
    settings = Settings(
        database=":memory:",
        secret="test-secret",
        public_base="http://cloud.test",
        households="a@example.com|b@example.com",
    )
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud)
    with TestClient(app) as c:
        c.sender = sender
        yield c


def _join(sock, device_id: str, name: str) -> None:
    sock.send_json(hello("phone", device_id, name, actions=("info", "panic", "alert")))
    assert sock.receive_json()["type"] == "welcome"
    assert sock.receive_json()["type"] == "devices"


def test_groups_need_two_addresses_and_canonicalise():
    linked = groups("A@Example.com | B@Example.com, c@example.com")
    assert len(linked) == 1
    assert [m.value for m in linked[0]] == ["a@example.com", "b@example.com"]
    assert groups("only@example.com") == []
    assert groups("not an address|also-not") == []


def test_partner_hashes_skip_the_caller():
    key = b"test-secret"
    raw = "a@example.com|b@example.com"
    mine = parse("a@example.com").hash(key)
    found = partner_hashes(raw, key, mine)
    assert parse("b@example.com").hash(key) in found
    assert mine not in found
    assert partner_hashes(raw, key, parse("c@example.com").hash(key)) == []


def test_panic_reaches_the_linked_phone_and_the_result_comes_back(client):
    key_a = sign_up(client, "a@example.com")
    key_b = sign_up(client, "b@example.com")
    with connect(client, key_a) as left, connect(client, key_b) as right:
        _join(left, "phone-a", "Cap")
        _join(right, "phone-b", "Gwen")
        left.send_json({"type": "call", "id": "p1", "to": "household", "action": "panic", "args": {"text": "panic"}})
        call = right.receive_json()
        assert call["type"] == "call" and call["action"] == "panic" and call["id"] == "p1"
        assert call["from"]["who"] == "a"
        assert call["from"]["name"] == "Cap"
        right.send_json({"type": "result", "id": "p1", "ok": True, "body": {"delivered": True}})
        res = left.receive_json()
        assert res["type"] == "result" and res["ok"] is True and res["body"] == {"delivered": True}

        left.send_json({"type": "call", "id": "a1", "to": "household", "action": "alert", "args": {"text": "home"}})
        alert = right.receive_json()
        assert alert["action"] == "alert" and alert["from"]["who"] == "a"
        right.send_json({"type": "result", "id": "a1", "ok": True, "body": {"delivered": True}})
        assert left.receive_json()["ok"] is True


def test_partner_offline_is_not_delivered(client):
    key_a = sign_up(client, "a@example.com")
    sign_up(client, "b@example.com")  # the account exists; the phone does not connect
    with connect(client, key_a) as left:
        _join(left, "phone-a", "Cap")
        left.send_json({"type": "call", "id": "p1", "to": "household", "action": "panic", "args": {"text": "panic"}})
        err = left.receive_json()
        assert err["code"] == "device_offline" and err["id"] == "p1"


def test_unlinked_account_is_rejected(client):
    key_c = sign_up(client, "c@example.com")
    key_b = sign_up(client, "b@example.com")
    with connect(client, key_c) as stranger, connect(client, key_b) as right:
        _join(stranger, "phone-c", "Sam")
        _join(right, "phone-b", "Gwen")
        stranger.send_json({"type": "call", "id": "p1", "to": "household", "action": "panic", "args": {}})
        assert stranger.receive_json()["code"] == "not_linked"
        right.send_json({"type": "ping"})
        assert right.receive_json()["type"] == "pong"
        stranger.send_json({"type": "call", "id": "p2", "to": "phone-b", "action": "panic", "args": {}})
        assert stranger.receive_json()["code"] == "device_offline"
        right.send_json({"type": "ping"})
        assert right.receive_json()["type"] == "pong"


@pytest.mark.parametrize("action", BLOCKED)
def test_other_actions_do_not_cross_accounts(client, action):
    key_a = sign_up(client, "a@example.com")
    key_b = sign_up(client, "b@example.com")
    with connect(client, key_a) as left, connect(client, key_b) as right:
        _join(left, "phone-a", "Cap")
        _join(right, "phone-b", "Gwen")
        left.send_json({"type": "call", "id": "x", "to": "household", "action": action, "args": {"command": "id"}})
        err = left.receive_json()
        assert err["code"] == "not_allowed" and "panic" in err["message"]
        right.send_json({"type": "ping"})
        assert right.receive_json()["type"] == "pong"
        left.send_json({"type": "call", "id": "y", "to": "phone-b", "action": action, "args": {"command": "id"}})
        err = left.receive_json()
        assert err["code"] == "device_offline"
        right.send_json({"type": "ping"})
        assert right.receive_json()["type"] == "pong"
