"""What the operator does to an account leaves a trace, and what a disabled or deleted
account can still do is nothing: the audit log and the timeline after every admin action,
the sign-in code a disabled account no longer gets, the live state and the day counters a
deletion takes with it, and the two figures the console reads from ``/v1/config``."""

from __future__ import annotations

import sqlite3
import threading
import time

from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.db import BUSY_TIMEOUT_S, Database

ADMIN = {"X-Admin-Token": "admin", "X-Admin-Actor": "ops@console"}


def audit_actions(cloud) -> list[tuple[str, str, str]]:
    return [(r["actor"], r["action"], r["target"]) for r in cloud.db.audit_recent(50)]


def timeline(cloud, account_id: str) -> list[tuple[str, str]]:
    return [(r["kind"], r["detail"]) for r in cloud.db.events_for(account_id, 50)]


async def test_a_disabled_account_gets_no_code_and_the_answer_says_nothing():
    """A disabled account's code request is refused quietly: the same 204 as anyone's, no
    SMS or mail sent, and the per-identifier limit counting as it does for an enabled
    account, so a probe at /v1/auth/code learns neither that the number has an account nor
    that the operator switched it off. The person who holds the account is told at
    /v1/auth/verify, as before."""
    app, client, sender, up, cloud, settings = make()
    me = await sign_up(client, sender)
    sent = len(sender.sent)
    r = await client.post("/v1/admin/disable", headers=ADMIN, json={"account_id": me["account"]["id"]})
    assert r.status_code == 204, r.text
    cloud.db._conn.execute("DELETE FROM codes")  # the sign-up's own code, so the count below starts at zero
    # the same limit as an enabled account: `code_per_identifier_10m` quiet 204s, then 429
    for _ in range(settings.code_per_identifier_10m):
        r = await client.post("/v1/auth/code", json={"identifier": "13800138000"})
        assert r.status_code == 204 and r.content == b"", r.text
    r = await client.post("/v1/auth/code", json={"identifier": "13800138000"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "code_too_often"
    assert len(sender.sent) == sent, "no code left the relay"
    # a stranger's number gets the very same answers
    r = await client.post("/v1/auth/code", json={"identifier": "13900001111"})
    assert r.status_code == 204 and r.content == b""
    # a code that was never sent cannot be guessed into a sign-in
    r = await client.post("/v1/auth/verify", json={"identifier": "13800138000", "code": "000000", "device": "t"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_wrong"
    # enabled again, the code comes
    await client.post("/v1/admin/disable", headers=ADMIN, json={"account_id": me["account"]["id"], "disabled": False})
    cloud.db._conn.execute("DELETE FROM codes")
    r = await client.post("/v1/auth/code", json={"identifier": "13800138000"})
    assert r.status_code == 204 and len(sender.sent) == sent + 2


async def test_every_operator_action_writes_the_audit_log_and_the_timeline():
    app, client, sender, up, cloud, settings = make()
    me = await sign_up(client, sender)
    account_id = me["account"]["id"]
    short = account_id[:8]
    for path, body in (
        ("/v1/admin/disable", {"account_id": account_id}),
        ("/v1/admin/disable", {"account_id": account_id, "disabled": False}),
        ("/v1/admin/unlimited", {"account_id": account_id}),
        ("/v1/admin/unlimited", {"account_id": account_id, "unlimited": False}),
        ("/v1/admin/grant", {"account_id": account_id, "tokens": 500}),
        ("/v1/admin/credit", {"account_id": account_id, "cny": 2, "note": "a merged pull request"}),
        ("/v1/admin/pool", {"account_id": account_id, "left_cny": 1}),
        ("/v1/admin/pool/batch", {"all": True, "delta_cny": 1}),
        ("/v1/admin/credit-all", {"cny": 1}),
        ("/v1/admin/settings", {"allowance_cny": 30}),
        ("/v1/admin/allowance/apply", {}),
    ):
        r = await client.post(path, headers=ADMIN, json=body)
        assert r.status_code in (200, 204), (path, r.text)
    actions = audit_actions(cloud)
    for action in (
        "account.disabled",
        "account.enabled",
        "member.set",
        "member.unset",
        "grant.tokens",
        "credit",
        "pool.set",
        "pool.set.many",
        "credit.all",
        "settings.changed",
        "allowance.applied",
    ):
        assert any(a == action for _, a, _ in actions), (action, actions)
    # who did it comes from X-Admin-Actor, the account by the first eight characters only
    assert all(actor == "ops@console" for actor, _, _ in actions), actions
    assert all(target in ("", short) or account_id not in target for _, _, target in actions), actions
    kinds = dict(timeline(cloud, account_id))
    for kind in ("account.disabled", "account.enabled", "member.set", "member.unset", "grant.tokens"):
        assert kind in kinds, (kind, kinds)
    assert kinds["account.disabled"] == "by ops@console" and kinds["grant.tokens"] == "+500 tokens by ops@console"
    # the account's detail view carries them
    detail = (await client.get(f"/v1/admin/accounts/{account_id}", headers=ADMIN)).json()
    assert any(e["kind"] == "member.set" for e in detail["events"])


async def test_a_figure_that_is_not_a_number_is_a_400_not_a_500():
    """`tokens` on the grant route and `n` on the image route were cast with a bare `int()`;
    a string that is not a number raised ValueError and the person saw a 500."""
    app, client, sender, up, cloud, settings = make()
    me = await sign_up(client, sender)
    r = await client.post("/v1/admin/grant", headers=ADMIN, json={"account_id": me["account"]["id"], "tokens": "many"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", r.text
    r = await client.post(
        "/v1/images/generations", headers=auth(me["api_key"]), json={"model": "qwen-image-3.0", "prompt": "a cat", "n": "two"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", r.text
    assert cloud.db.account(me["account"]["id"])["granted"] == 0


async def test_the_operator_deleting_an_account_forgets_its_live_state_and_counts_the_deletion():
    app, client, sender, up, cloud, settings = make()
    me = await sign_up(client, sender)
    account_id = me["account"]["id"]
    key = me["api_key"]
    cloud.db.upsert_device(account_id, "pc-1", "Desk", "computer", "Linux", "0.1", "[]")
    app.state.sync.presence.set(account_id, "cid-1", "pc-1", "Desk", True)
    assert app.state.sync.presence.working(account_id)
    assert (await client.get("/v1/me", headers=auth(key))).status_code == 200
    with cloud.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM daily WHERE metric='active' AND key=?", (account_id,)).fetchone()[0] == 1
    r = await client.post("/v1/admin/delete", headers=ADMIN, json={"account_id": account_id})
    assert r.status_code == 204, r.text
    assert app.state.sync.presence.working(account_id) == []
    with cloud.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM daily WHERE key=?", (account_id,)).fetchone()[0] == 0
    deleted = [r for r in cloud.db.events_recent(50) if r["kind"] == "account.deleted"]
    assert len(deleted) == 1 and deleted[0]["account_id"] == "" and deleted[0]["detail"] == f"{account_id[:8]} by ops@console"
    assert ("ops@console", "account.delete", account_id[:8]) in audit_actions(cloud)
    # the Stats page counts the deletion (the column read 0 while no event was written)
    rows = (await client.get("/v1/admin/stats/accounts_daily?days=2", headers=ADMIN)).json()["rows"]
    assert sum(r["deleted"] for r in rows) == 1


async def test_invite_abuse_deletions_forget_the_live_state_too():
    app, client, sender, up, cloud, settings = make(signup_tokens=1000, invite_bonus_cny=1)
    inviter = await sign_up(client, sender)
    code = (await client.get("/v1/me/invite", headers=auth(inviter["api_key"]))).json()["code"]
    invitee = await sign_up(client, sender, identifier="13900001111", device="p2", invite=code)
    invitee_id = invitee["account"]["id"]
    app.state.sync.presence.set(invitee_id, "cid-2", "p2", "Phone", True)
    r = await client.post(
        "/v1/admin/invites/abuse",
        headers=ADMIN,
        json={"account_id": inviter["account"]["id"], "disable": True, "delete_unused": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["deleted"] == 1 and "deleted_ids" not in out
    assert app.state.sync.presence.working(invitee_id) == []
    kinds = {(r["kind"], r["detail"]) for r in cloud.db.events_recent(50)}
    assert ("account.deleted", f"{invitee_id[:8]} by ops@console (invite abuse)") in kinds
    assert ("account.disabled", "by ops@console (invite abuse)") in kinds


async def test_public_config_carries_the_code_lifetime_and_the_password_length():
    app, client, sender, up, cloud, settings = make(code_ttl_s=300)
    cfg = (await client.get("/v1/config")).json()
    assert cfg["code_ttl_s"] == 300 and "password_min_len" not in cfg


def test_the_database_waits_for_a_busy_writer_instead_of_failing_at_once(tmp_path):
    db = Database(str(tmp_path / "cloud.db"))
    assert BUSY_TIMEOUT_S >= 1
    assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == int(BUSY_TIMEOUT_S * 1000)
    assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # a second connection holding a write lock: the relay's write waits, it does not fail at once
    other = sqlite3.connect(str(tmp_path / "cloud.db"), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    threading.Timer(0.3, other.execute, ("COMMIT",)).start()
    db.add_event("", "test.waited", "the lock was held for a moment")
    assert time.monotonic() - started >= 0.25
    other.close()
