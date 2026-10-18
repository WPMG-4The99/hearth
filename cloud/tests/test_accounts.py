"""Passwords, sign-ins, the usage breakdown, calls and the operator's detail views."""

from __future__ import annotations

import httpx
import pytest
from test_cloud import fake_upstream, sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.identifiers import BadIdentifier, parse
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud, check_password, hash_password


def make(**overrides):
    up = fake_upstream()
    kw = dict(
        database=":memory:",
        secret="test-secret",
        admin_token="admin",
        upstream_base="http://upstream/compat/v1",
        upstream_key="sk-upstream",
        dashscope_base="http://upstream/ds/api/v1",
        signup_tokens=0,
        daily_cap_tokens=0,
        per_minute_requests=100,
        allowance_cny=25,
        public_base="http://cloud.test",
        password_max_attempts=3,
        lockout_s=600,
    )
    kw.update(overrides)
    settings = Settings(**kw)
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud, upstream_transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test")
    return app, client, sender, up, cloud, settings


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_password_hashing_roundtrip():
    h = hash_password("correct horse")
    assert h.startswith("scrypt$") and check_password("correct horse", h)
    assert not check_password("wrong", h)
    assert not check_password("x", "garbage")
    assert hash_password("a") != hash_password("a")  # salted


async def test_password_set_login_change_and_lockout():
    app, client, sender, up, cloud, settings = make()
    first = await sign_up(client, sender, "13800138000", "pixel")
    key1 = first["api_key"]
    assert first["account"]["has_password"] is False and first["account"]["signed_in_via"] == "code"

    # No password yet: the password sign-in says so instead of pretending.
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "whatever1", "device": "mac"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_password"

    # Weak ones are refused; a proper one is set without `current` (none exists).
    r = await client.post("/v1/auth/password", json={"password": "short"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_short"
    r = await client.post("/v1/auth/password", json={"password": "aaaaaaaaaa"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_weak"
    r = await client.post("/v1/auth/password", json={"password": "correct horse 1"}, headers=auth(key1))
    assert r.status_code == 204
    me = (await client.get("/v1/me", headers=auth(key1))).json()
    assert me["account"]["has_password"] is True and me["account"]["password_set_at"]

    # A second device signs in with the password: a new key, same account, via=password.
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "correct horse 1", "device": "mac"})
    assert r.status_code == 200, r.text
    second = r.json()
    key2 = second["api_key"]
    assert key2 != key1 and second["account"]["id"] == first["account"]["id"]
    assert second["account"]["signed_in_via"] == "password" and second["account"]["sessions"] == 2

    # Unknown numbers and wrong passwords get the same answer.
    r = await client.post("/v1/auth/login", json={"identifier": "13900001111", "password": "correct horse 1"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials"
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "nope nope nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials"

    # Changing it needs the current one — this key is well past the reset window.
    cloud.db._conn.execute("UPDATE api_keys SET created_at=created_at-7200")
    r = await client.post("/v1/auth/password", json={"password": "another one 2"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_required"
    r = await client.post("/v1/auth/password", json={"password": "another one 2", "current": "wrong"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_wrong"
    r = await client.post("/v1/auth/password", json={"password": "another one 2", "current": "correct horse 1"}, headers=auth(key1))
    assert r.status_code == 204

    # Three wrong tries lock the password (the code still works); a fresh code
    # sign-in may then set a new password without the old one.
    for _ in range(2):
        r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "bad"})
        assert r.status_code == 401
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "bad"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "locked"
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "another one 2"})
    assert r.status_code == 429
    third = await sign_up(client, sender, "13800138000", "ipad")
    r = await client.post("/v1/auth/password", json={"password": "reset by code 3"}, headers=auth(third["api_key"]))
    assert r.status_code == 204
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "reset by code 3", "device": "win"})
    assert r.status_code == 200

    # The timeline shows what happened, never what was said.
    events = (await client.get("/v1/me/events", headers=auth(key1))).json()["events"]
    kinds = [e["kind"] for e in events]
    assert "password.set" in kinds and "password.changed" in kinds and "sign_in.password" in kinds and "sign_in.failed" in kinds
    assert all("detail" in e and len(e["detail"]) <= 200 for e in events)


async def test_a_disabled_account_is_told_so_only_with_the_right_password():
    """A probe at /v1/auth/login must not learn that an account exists and is disabled: a
    wrong password gets the answer any wrong password gets, the right one hears disabled."""
    app, client, sender, up, cloud, settings = make()
    first = await sign_up(client, sender, "someone@example.com", "pixel")
    r = await client.post("/v1/auth/password", json={"password": "correct horse 1"}, headers=auth(first["api_key"]))
    assert r.status_code == 204
    admin = {"X-Admin-Token": "admin"}
    r = await client.post("/v1/admin/disable", headers=admin, json={"identifier": "someone@example.com"})
    assert r.status_code == 204
    r = await client.post("/v1/auth/login", json={"identifier": "someone@example.com", "password": "nope nope nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials"
    r = await client.post("/v1/auth/login", json={"identifier": "someone@example.com", "password": "correct horse 1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"


async def test_sessions_can_be_listed_and_revoked():
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    c = await sign_up(client, sender, "dev-a@example.com", "ipad")
    r = await client.get("/v1/me/sessions", headers=auth(a["api_key"]))
    sessions = r.json()["sessions"]
    assert [s["device"] for s in sessions][0] == "pixel" and sessions[0]["current"] is True
    assert {s["device"] for s in sessions} == {"pixel", "mac", "ipad"}
    assert all(s["via"] == "code" and len(s["prefix"]) == 10 for s in sessions)

    # Sign the tablet out from the phone; it stops working at once.
    ipad = next(s for s in sessions if s["device"] == "ipad")
    r = await client.delete(f"/v1/me/sessions/{ipad['prefix']}", headers=auth(a["api_key"]))
    assert r.status_code == 204
    assert (await client.get("/v1/me", headers=auth(c["api_key"]))).status_code == 401
    r = await client.delete("/v1/me/sessions/nm_nothere", headers=auth(a["api_key"]))
    assert r.status_code == 404

    # Everyone else out: only the phone stays.
    r = await client.post("/v1/auth/sign-out-all", json={}, headers=auth(a["api_key"]))
    assert r.status_code == 200 and r.json()["signed_out"] == 1
    assert (await client.get("/v1/me", headers=auth(b["api_key"]))).status_code == 401
    assert (await client.get("/v1/me", headers=auth(a["api_key"]))).status_code == 200
    r = await client.post("/v1/auth/sign-out-all", json={"all": True}, headers=auth(a["api_key"]))
    assert r.json()["signed_out"] == 1
    assert (await client.get("/v1/me", headers=auth(a["api_key"]))).status_code == 401


async def test_usage_is_broken_down_by_kind_and_model():
    app, client, sender, up, cloud, settings = make()
    data = await sign_up(client, sender)
    headers = auth(data["api_key"])
    for _ in range(2):
        r = await client.post(
            "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=headers
        )
        assert r.status_code == 200
    r = await client.post(
        "/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}, headers=headers
    )
    assert r.status_code == 200
    r = await client.post("/v1/images/generations", json={"model": "qwen-image-3.0", "prompt": "a cat", "n": 1}, headers=headers)
    assert r.status_code == 200, r.text

    me = (await client.get("/v1/me", headers=headers)).json()
    usage = me["usage"]
    assert usage["kinds"] == ["chat", "image", "video", "realtime"]
    by_kind = {row["kind"]: row for row in usage["today"]["by_kind"]}
    assert by_kind["chat"]["requests"] == 3 and by_kind["chat"]["prompt_tokens"] == 300 and by_kind["chat"]["completion_tokens"] == 150
    assert by_kind["image"]["requests"] == 1 and by_kind["image"]["cost_cny"] == 0.18
    by_model = {(row["model"], row["kind"]): row for row in usage["total"]["by_model"]}
    assert by_model[("qwen3.8-27b", "chat")]["requests"] == 2 and by_model[("qwen3.8-flash", "chat")]["requests"] == 1
    assert by_model[("qwen-image-3.0", "image")]["requests"] == 1
    # Money adds up across kinds: 2 × (100×3 + 50×12) + (100×0.8 + 50×2.7) micro-yuan + ¥0.18
    total_cny = sum(row["cost_cny"] for row in usage["total"]["by_kind"])
    assert round(total_cny, 4) == round(2 * 0.0009 + 0.000215 + 0.18, 4)


async def test_admin_overview_and_account_detail():
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    ha, hb = auth(a["api_key"]), auth(b["api_key"])
    await client.post("/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=ha)
    await client.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}, headers=hb)
    await client.post("/v1/images/generations", json={"model": "qwen-image-3.0", "prompt": "a cat"}, headers=hb)
    await client.post("/v1/auth/password", json={"password": "correct horse 1"}, headers=hb)
    await client.post("/v1/auth/login", json={"identifier": "dev-a@example.com", "password": "wrong wrong"})
    admin = {"X-Admin-Token": "admin"}

    assert (await client.get("/v1/admin/overview")).status_code == 401
    r = await client.get("/v1/admin/overview", headers=admin)
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov["accounts"]["total"] == 2 and ov["accounts"]["with_password"] == 1 and ov["accounts"]["live_keys"] == 2
    assert ov["today"]["requests"] == 3 and ov["today"]["active_accounts"] == 2 and ov["today"]["new_accounts"] == 2
    kinds = {row["kind"]: row for row in ov["today"]["by_kind"]}
    assert kinds["chat"]["requests"] == 2 and kinds["image"]["requests"] == 1
    models = {row["model"] for row in ov["period"]["by_model"]}
    assert models == {"qwen3.8-27b", "qwen3.8-flash", "qwen-image-3.0"}
    assert ov["signals_today"]["sign_ins"] == 2 and ov["signals_today"]["sign_in_failures"] == 1
    assert ov["top_accounts"][0]["hint"] == "de***@example.com" and ov["top_accounts"][0]["requests"] == 2
    # 0.22: the console shows accounts in full — the identifier rides beside the masked hint
    # (the hint stays for logs, mail and anything that leaves the console)
    assert ov["top_accounts"][0]["identifier"] == "dev-a@example.com"
    assert ov["events"][0]["kind"] == "sign_in.failed" and ov["events"][0]["hint"] == "de***@example.com"
    assert ov["events"][0]["identifier"] == "dev-a@example.com"

    r = await client.get(f"/v1/admin/accounts/{b['account']['id']}", headers=admin)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["account"]["identifier"] == "dev-a@example.com" and d["account"]["has_password"] is True
    assert "password_hash" not in d["account"]
    assert (
        d["spend"]["requests_total"] == 2
        and d["spend"]["grant_cny"] == 25
        and d["spend"]["left_cny"] == round(25 - d["spend"]["total_cny"], 4)
    )
    assert {row["kind"] for row in d["usage"]["period"]["by_kind"]} == {"chat", "image"}
    assert len(d["usage"]["period"]["by_day"]) == 2
    assert d["sessions"][0]["device"] == "mac" and d["sessions"][0]["revoked_at"] is None
    assert [e["kind"] for e in d["events"]][:3] == ["sign_in.failed", "password.set", "sign_in.code"]
    # the statement: the two requests and, first of all, the allowance itself
    assert len(d["recent"]) == 3 and {x["kind"] for x in d["recent"]} == {"chat", "image", "credit"}
    assert d["recent"][-1]["detail"] == {"credit_uy": 25_000_000, "from": "signup"}
    assert (await client.get("/v1/admin/accounts/nope", headers=admin)).status_code == 404

    r = await client.get("/v1/admin/events?kind=sign_in.failed,password.set", headers=admin)
    assert [e["kind"] for e in r.json()["events"]] == ["sign_in.failed", "password.set"]

    # The list view carries the new flags too.
    accounts = (await client.get("/v1/admin/accounts", headers=admin)).json()["accounts"]
    byid = {x["id"]: x for x in accounts}
    assert byid[b["account"]["id"]]["has_password"] is True and byid[a["account"]["id"]]["has_password"] is False
    assert all("password_hash" not in x for x in accounts)


async def test_the_accounts_list_is_one_grouped_query_with_the_old_numbers():
    """0.23: `admin_accounts` joins grouped sums instead of seven correlated subqueries per
    account. The numbers must be the ones the correlated form gave, row for row."""
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    c = await sign_up(client, sender, "dev-b@example.com", "pc")
    ha, hb = auth(a["api_key"]), auth(b["api_key"])
    for _ in range(3):
        await client.post(
            "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=ha
        )
    await client.post("/v1/images/generations", json={"model": "qwen-image-3.0", "prompt": "a cat"}, headers=hb)
    await client.post("/v1/auth/session-key", json={"device": "mac-2"}, headers=hb)
    await client.delete("/v1/me/sessions/" + b["api_key"][:12], headers=hb)
    day_start = cloud.s.day_start(int(cloud.clock()))
    rows = {r["id"]: dict(r) for r in cloud.db.admin_accounts(day_start, limit=1000)}
    assert set(rows) == {a["account"]["id"], b["account"]["id"], c["account"]["id"]}
    old = cloud.db._conn.execute(
        """SELECT a.id,
                  (SELECT COALESCE(SUM(l.charged),0) FROM ledger l WHERE l.account_id=a.id AND l.ts>=? AND l.charged>0) AS used_today,
                  (SELECT COALESCE(SUM(l.cost_uy),0) FROM ledger l WHERE l.account_id=a.id AND l.ts>=? AND l.cost_uy>0) AS spent_today_uy,
                  (SELECT COALESCE(SUM(l.cost_uy),0) FROM ledger l WHERE l.account_id=a.id AND l.cost_uy>0) AS spent_uy,
                  (SELECT COUNT(*) FROM ledger l WHERE l.account_id=a.id AND l.kind IN ('chat','image','video','realtime')) AS requests,
                  (SELECT MAX(k.last_used_at) FROM api_keys k WHERE k.account_id=a.id) AS last_active_at,
                  (SELECT COUNT(*) FROM api_keys k WHERE k.account_id=a.id AND k.revoked_at IS NULL) AS live_keys,
                  (SELECT COUNT(*) FROM devices d WHERE d.account_id=a.id) AS device_count
           FROM accounts a""",
        (day_start, day_start),
    ).fetchall()
    fields = ("used_today", "spent_today_uy", "spent_uy", "requests", "last_active_at", "live_keys", "device_count")
    for o in old:
        assert {k: rows[o["id"]][k] for k in fields} == {k: o[k] for k in fields}, o["id"]
    ra, rb, rc = rows[a["account"]["id"]], rows[b["account"]["id"]], rows[c["account"]["id"]]
    assert ra["requests"] == 3 and ra["used_today"] > 0 and ra["spent_uy"] == ra["spent_today_uy"] > 0
    assert rb["requests"] == 1 and rb["live_keys"] == 1  # the first key revoked, the session key live
    assert rc["requests"] == 0 and rc["spent_uy"] == 0 and rc["live_keys"] == 1 and rc["last_active_at"] is None

    # labels come from the ids a page shows, not from the newest N accounts
    brief = {r["id"]: r for r in cloud.db.accounts_brief([a["account"]["id"], "no-such-account", ""])}
    assert set(brief) == {a["account"]["id"]} and brief[a["account"]["id"]]["hint"] == "138****8000"
    hints, idents = cloud._labels([a["account"]["id"], c["account"]["id"]])
    assert hints[c["account"]["id"]] == "de***@example.com" and idents[a["account"]["id"]] == "+8613800138000"
    assert cloud.db.accounts_brief([]) == []

    # the allowance distribution reads four columns per account and the same spend
    allowance = {r["id_hash"]: dict(r) for r in cloud.db.allowance_rows()}
    assert len(allowance) == 3 and sorted(r["spent_uy"] for r in allowance.values()) == sorted(r["spent_uy"] for r in rows.values())
    assert all(r["grant_uy"] == 25_000_000 and r["unlimited"] == 0 for r in allowance.values())


VIDEO = "/api/v1/services/aigc/video-generation/video-synthesis"


async def clip(client, key: str, prompt: str = "a dragon waves"):
    r = await client.post(VIDEO, headers=auth(key), json={"model": "wan2.2-i2v-flash", "input": {"prompt": prompt}, "parameters": {}})
    if r.status_code == 200:
        task = r.json()["output"]["task_id"]
        for _ in range(2):  # RUNNING, then SUCCEEDED: charged once
            await client.get(f"/api/v1/tasks/{task}", headers=auth(key))
    return r


async def chat(client, key: str, text: str = "hi", headers: dict | None = None):
    """One charged chat turn on the cheap model, so an invited account counts as used."""
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": text}]},
        headers={**auth(key), **(headers or {})},
    )
    assert r.status_code == 200, r.text
    return r


async def test_invites_and_the_operator_grow_the_one_pool():
    """A friend's code at sign-up links the two accounts; the bonus reaches both pools, the
    inviter's and the newcomer's, at the newcomer's first charged request and never again;
    clips are not counted apart, a clip is just a dearer line on the same allowance; the
    operator can add more; the estimate says what a new face costs before it is made."""
    app, client, sender, up, cloud, settings = make(allowance_cny=0.6, invite_bonus_cny=3)
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    ka = a["api_key"]
    inv = (await client.get("/v1/me/invite", headers=auth(ka))).json()
    assert len(inv["code"]) == 8 and inv["url"] == "https://hearth-cloud-the99.zocomputer.io/app/?invite=" + inv["code"]
    assert (
        inv["invites"] == 0 and inv["bonus_cny"] == 3 and inv["invitee_bonus_cny"] == 3 and inv["earned_cny"] == 0 and inv["friends"] == []
    )
    assert inv["bonus_when"] == "first_use" and inv["settled"] == 0 and inv["pending"] == 0
    assert (await client.get("/v1/me", headers=auth(ka))).json()["invite"]["code"] == inv["code"]  # stable
    assert a["clips"]["unlimited"] is True and a["spend"]["grant"] == 0.6 and a["spend"]["left"] == 0.6
    # there is no co-creation bonus (0.9): the 0.5 fields say so for the apps of the time
    assert a["spend"]["contribute_bonus_available"] is False and a["contribute"]["bonus_available"] is False

    # B signs up with A's code, typed sloppily: the link is made and nothing is paid yet
    async def verify(identifier: str, device: str, invite: str):
        await client.post("/v1/auth/code", json={"identifier": identifier})
        _, code = sender.sent[-1]
        r = await client.post("/v1/auth/verify", json={"identifier": identifier, "code": code, "device": device, "invite": invite})
        assert r.status_code == 200, r.text
        return r.json()

    sloppy = inv["code"][:4].lower() + "-" + inv["code"][4:].lower()
    b = await verify("dev-b@example.com", "mac", sloppy)
    assert b["created"] is True and b["invite"]["invites"] == 0 and b["spend"]["grant"] == 0.6 and b["spend"]["left"] == 0.6
    assert [r["detail"].get("from") for r in b["recent"] if r["kind"] == "credit"] == ["signup"], "nothing from the invite at sign-up"
    me_a = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me_a["invite"]["invites"] == 1 and me_a["invite"]["pending"] == 1 and me_a["invite"]["settled"] == 0
    assert me_a["invite"]["earned_cny"] == 0 and me_a["spend"]["grant"] == 0.6 and me_a["spend"]["left"] == 0.6
    assert me_a["invite"]["friends"][0]["hint"].endswith("example.com") and me_a["invite"]["friends"][0]["settled"] is False
    assert all(r["kind"] != "credit" or r["detail"].get("from") != "invite" for r in me_a["recent"])
    kinds_a = [e["kind"] for e in (await client.get("/v1/me/events", headers=auth(ka))).json()["events"]]
    assert "invite.accepted" in kinds_a and "invite.settled" not in kinds_a

    # B's first charged request pays both sides the bonus in force now; a second one pays nothing more
    await chat(client, b["api_key"])
    me_b = (await client.get("/v1/me", headers=auth(b["api_key"]))).json()
    assert me_b["spend"]["grant"] == 3.6 and round(me_b["spend"]["left"] + me_b["spend"]["total"], 4) == 3.6
    credits_b = [r for r in me_b["recent"] if r["kind"] == "credit"]
    assert credits_b[0]["detail"]["from"] == "invited" and credits_b[0]["detail"]["friend"] == a["account"]["id"][:8]
    me_a = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me_a["invite"]["invites"] == 1 and me_a["invite"]["settled"] == 1 and me_a["invite"]["pending"] == 0
    assert me_a["invite"]["earned_cny"] == 3 and me_a["spend"]["grant"] == 3.6 and me_a["spend"]["left"] == 3.6
    assert me_a["invite"]["friends"][0]["settled"] is True
    assert me_a["recent"][0]["kind"] == "credit" and me_a["recent"][0]["detail"]["from"] == "invite"
    assert me_a["recent"][0]["detail"]["friend"] == b["account"]["id"][:8]
    assert [e["kind"] for e in (await client.get("/v1/me/events", headers=auth(ka))).json()["events"]][0] == "invite.settled"
    await chat(client, b["api_key"], "again")
    assert (await client.get("/v1/me", headers=auth(ka))).json()["spend"]["grant"] == 3.6
    assert (await client.get("/v1/me", headers=auth(b["api_key"]))).json()["spend"]["grant"] == 3.6
    assert cloud.db.invite_pending(b["account"]["id"]) is False
    # B signing in again on another device does not count twice
    again = await verify("dev-b@example.com", "ipad", inv["code"])
    assert again["created"] is False and again["spend"]["grant"] == 3.6
    assert (await client.get("/v1/me", headers=auth(ka))).json()["invite"]["invites"] == 1
    # an unknown code (or one's own) is not an error: the person is signed in, nobody is paid
    c = await verify("13900001111", "pixel", "NOPE1234")
    assert c["created"] is True and c["spend"]["grant"] == 0.6
    assert (await client.get("/v1/me", headers=auth(ka))).json()["invite"]["invites"] == 1

    # Clips: ¥0.5 each here (five seconds at ¥0.10). Seven fit in A's ¥3.6; the eighth
    # would go over and is refused before the provider hears of it.
    for _ in range(7):
        assert (await clip(client, ka)).status_code == 200
    me_a = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me_a["spend"]["total"] == 3.5 and me_a["spend"]["left"] == round(3.6 - 3.5, 4) and me_a["spend"]["warn"] is True
    r = await clip(client, ka)
    assert r.status_code == 429 and r.json()["error"]["code"] == "allowance_exhausted"
    assert r.json()["error"]["left"] == 0.1 and r.json()["error"]["contribute_bonus_available"] is False
    assert "co-creation" not in r.json()["error"]["message"] and "invite a friend" in r.json()["error"]["message"]
    r = await client.get("/api/v1/uploads", params={"action": "getPolicy", "model": "wan2.2-i2v-flash"}, headers=auth(ka))
    assert r.status_code == 429 and r.json()["error"]["code"] == "allowance_exhausted"
    # a probe costs nothing and is not refused for money
    r = await client.post(VIDEO, headers=auth(ka), json={"model": "wan2.2-i2v-flash", "input": {}, "parameters": {}})
    assert r.status_code == 400

    # C (no inviter) has ¥0.6: one clip fits, a second does not. The estimate said so beforehand.
    kc = c["api_key"]
    est = (await client.get("/v1/estimate", params={"images": 5, "clips": 4}, headers=auth(kc))).json()
    assert est["cny"] == 2.9 and est["left_cny"] == 0.6 and est["grant_cny"] == 0.6 and est["affordable"] is False
    assert est["unlimited"] is False and est["clips_ok"] is True and est["left_today_cny"] == 0.6
    assert [p["kind"] for p in est["parts"]] == ["image", "video"] and est["parts"][1]["seconds"] == 5
    est = (await client.get("/v1/estimate", params={"clips": 1}, headers=auth(kc))).json()
    assert est["cny"] == 0.5 and est["affordable"] is True
    assert (await clip(client, kc)).status_code == 200
    r = await clip(client, kc)
    assert r.status_code == 429 and r.json()["error"]["code"] == "allowance_exhausted"

    # The operator credits C for a pull request: straight into the pool.
    admin = {"X-Admin-Token": "admin"}
    r = await client.post("/v1/admin/credit", headers=admin, json={"identifier": "13900001111", "cny": 10, "note": "PR #12"})
    assert r.status_code == 200 and r.json()["grant_cny"] == 10.6 and r.json()["left_cny"] == 10.1 and "grant_uy" not in r.json()
    est = (await client.get("/v1/estimate", params={"images": 5, "clips": 4}, headers=auth(kc))).json()
    assert est["affordable"] is True and est["left_cny"] == 10.1
    assert (await clip(client, kc)).status_code == 200
    me_c = (await client.get("/v1/me", headers=auth(kc))).json()
    assert me_c["spend"]["total"] == 1.0 and me_c["spend"]["left"] == 9.6 and me_c["recent"][0]["kind"] == "video"
    assert any(row["kind"] == "credit" and row["detail"].get("note") == "PR #12" for row in me_c["recent"])
    r = await client.post("/v1/admin/credit", headers=admin, json={"identifier": "13900001111", "cny": 5000})
    assert r.status_code == 400

    # The operator's views carry it all.
    listing = (await client.get("/v1/admin/accounts", headers=admin)).json()["accounts"]
    row_a = next(x for x in listing if x["identifier"] == "dev-a@example.com")
    assert row_a["invites"] == 1 and row_a["grant_cny"] == 3.6 and row_a["left_cny"] == 0.1 and "credit_uy" not in row_a
    detail = (await client.get(f"/v1/admin/accounts/{row_a['id']}", headers=admin)).json()
    assert detail["account"]["invited"][0]["hint"].endswith("example.com") and detail["spend"]["left_cny"] == 0.1
    assert "clips_used" not in detail["account"]
    s = (await client.get("/v1/admin/accounts", headers=admin)).json()["settings"]
    assert s["invite_bonus_cny"] == 3 and s["invitee_bonus_cny"] == 3 and s["allowance_cny"] == 0.6 and s["contribute_bonus_cny"] == 0
    kinds = {e["kind"] for e in (await client.get("/v1/admin/events", headers=admin)).json()["events"]}
    assert {"invite.accepted", "invite.used", "invite.unknown", "credit.granted", "budget.refused"} <= kinds


async def test_admin_series_and_traffic(tmp_path):
    """The operator's time series come from the relay's own tables; the site's visits and
    downloads from the traffic database the showcase's script keeps — or, without one, a
    panel that says so."""
    import importlib.util
    import json
    from pathlib import Path

    traffic_py = Path(__file__).resolve().parents[2] / "demo" / "showcase" / "mirror" / "traffic.py"
    db_path = tmp_path / "traffic.db"
    app, client, sender, up, cloud, settings = make(traffic_db=str(db_path), signup_tokens=1000, allowance_cny=10)
    a = await sign_up(client, sender, "13800138000", "pixel")
    await sign_up(client, sender, "dev-a@example.com", "mac")
    await client.post(
        "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=auth(a["api_key"])
    )
    admin = {"X-Admin-Token": "admin"}

    assert (await client.get("/v1/admin/series")).status_code == 401
    r = await client.get("/v1/admin/series?days=7", headers=admin)
    assert r.status_code == 200, r.text
    series = r.json()
    assert len(series["days"]) == 7 and series["web"] is None and series["online_devices"] == 0
    today = series["days"][-1]
    assert today["sign_ins"] == 2 and today["new_accounts"] == 2 and today["active_accounts"] == 1
    assert series["days"][0]["sign_ins"] == 0 and series["invites"]["invited"] == 0 and series["contributors"] == 0
    assert "13800138000" not in r.text and "dev-a@example.com" not in r.text

    # no database yet: the panel is told, nothing fails
    r = await client.get("/v1/admin/traffic", headers=admin)
    assert r.status_code == 200 and r.json() == {"available": False}

    # the showcase's script fills one from Caddy's JSON log; the relay reads it as it is
    if not traffic_py.exists():
        return
    spec = importlib.util.spec_from_file_location("nm_traffic", traffic_py)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.DB_PATH = db_path
    conn = mod.open_db()
    tally = mod.Tally()
    ts = float(__import__("time").time())
    for uri, ua, ctype, status in (
        ("/", "Mozilla/5.0", "text/html; charset=utf-8", 200),
        ("/", "Mozilla/5.0", "text/html; charset=utf-8", 200),
        ("/own-key/", "Mozilla/5.0 (X11)", "text/html", 200),
        ("/", "Googlebot/2.1", "text/html", 200),
        ("/dl/v0.1.22/nanoMuse-0.1.22-arm64.apk", "Mozilla/5.0", "application/octet-stream", 200),
        ("/dl/v0.1.22/nanoMuse-0.1.22-arm64.apk.sha256", "Mozilla/5.0", "application/octet-stream", 200),
    ):
        rec = {
            "ts": ts,
            "status": status,
            "size": 1000,
            "request": {
                "host": "nanomuse.cn",
                "uri": uri,
                "client_ip": "203.0.113.7",
                "headers": {"User-Agent": [ua], "Referer": ["https://github.com/nano-muse/nanoMuse"]},
            },
            "resp_headers": {"Content-Type": [ctype]},
        }
        tally.add(rec, conn)
    conn.execute("BEGIN")
    tally.flush(conn)
    conn.execute("COMMIT")
    conn.execute(
        "INSERT INTO github(day, ts, stars, forks, watchers, downloads, assets) VALUES (?,?,?,?,?,?,?)",
        (mod.local_day(ts), int(ts), 24, 5, 3, 188, json.dumps({"v0.1.22": {"nanoMuse-0.1.22-arm64.apk": 100}})),
    )
    conn.close()

    r = await client.get("/v1/admin/traffic?days=7", headers=admin)
    assert r.status_code == 200, r.text
    tr = r.json()
    assert tr["available"] is True and len(tr["days"]) == 7 and tr["days"][0]["pages"] == 0
    day = tr["days"][-1]
    assert day["pages"] == 3 and day["visitors"] == 2 and day["bots"] == 1 and day["downloads"] == 1 and day["requests"] == 6
    assert tr["pages"][0] == {"name": "/", "hits": 2} and tr["referrers"] == [{"name": "github.com", "hits": 3}]
    assert tr["downloads"] == [{"name": "nanoMuse-0.1.22-arm64.apk", "hits": 1, "bytes": 1000}]
    assert tr["github"]["stars"] == 24 and tr["github"]["downloads"] == 188 and tr["github"]["days"][0]["stars"] == 24
    assert "203.0.113.7" not in r.text


async def test_an_operator_zero_survives_a_restart_and_old_rows_still_get_seeded(tmp_path):
    """seed_grants used to pick every account with grant_uy = 0, so an account the operator
    had set to zero from the page got the allowance back at the next restart. Only rows no
    0.15+ relay has seen (allowance_uy < 0) are seeded; the operator's zero stays."""
    path = str(tmp_path / "relay.sqlite")
    app, client, sender, up, cloud, settings = make(database=path, allowance_cny=0.6)
    cloud.db.close()
    cloud = Cloud(settings, Database(path), sender)
    app = create_app(settings, cloud, upstream_transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test")
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    b = await sign_up(client, sender, "dev-b@example.com", "mac")
    r = await client.post(
        "/v1/admin/pool", headers={"X-Admin-Token": "admin"}, json={"account_id": a["account"]["id"], "grant_cny": 0, "note": "abuse"}
    )
    assert r.status_code == 200 and r.json()["grant_cny"] == 0
    # a row from before 0.15 that never got its pool: the column default says so
    with cloud.db.tx() as c:
        c.execute("UPDATE accounts SET allowance_uy=-1, grant_uy=0 WHERE id=?", (b["account"]["id"],))
    cloud.db.close()
    again = Cloud(settings, Database(path), sender)
    assert again.db.account(a["account"]["id"])["grant_uy"] == 0, "the operator's zero came back"
    assert again.db.account(b["account"]["id"])["grant_uy"] == settings.allowance_uy
    assert again.db.seed_grants(settings.allowance_uy) == 0  # idempotent
    again.db.close()


async def test_invite_earnings_are_what_the_ledger_says_not_todays_bonus_times_invites():
    app, client, sender, up, cloud, settings = make(allowance_cny=0.6, invite_bonus_cny=3)
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    code = (await client.get("/v1/me/invite", headers=auth(a["api_key"]))).json()["code"]

    async def join(identifier: str) -> dict:
        await client.post("/v1/auth/code", json={"identifier": identifier})
        _, c = sender.sent[-1]
        r = await client.post("/v1/auth/verify", json={"identifier": identifier, "code": c, "device": "mac", "invite": code})
        assert r.status_code == 200, r.text
        return r.json()

    # the bonus is the figure in force when the friend first uses the relay, not at sign-up
    b = await join("dev-b@example.com")
    await chat(client, b["api_key"])
    r = await client.post("/v1/admin/settings", headers={"X-Admin-Token": "admin"}, json={"invite_bonus_cny": 1})
    assert r.status_code == 200, r.text
    c = await join("dev-c@example.com")
    inv = (await client.get("/v1/me/invite", headers=auth(a["api_key"]))).json()
    assert inv["invites"] == 2 and inv["settled"] == 1 and inv["pending"] == 1 and inv["earned_cny"] == 3
    await chat(client, c["api_key"])
    inv = (await client.get("/v1/me/invite", headers=auth(a["api_key"]))).json()
    assert inv["invites"] == 2 and inv["bonus_cny"] == 1 and inv["settled"] == 2 and inv["pending"] == 0
    assert inv["earned_cny"] == 4, "3 for the first friend plus 1 for the second, as the ledger has it"


async def test_an_invitee_whose_inviter_is_gone_or_disabled_is_still_paid():
    """When the inviter has deleted their account, or the operator disabled it, the newcomer's
    first request still pays the newcomer; the inviter gets nothing and the account is marked
    settled, so the question is never asked again."""
    app, client, sender, up, cloud, settings = make(allowance_cny=1, invite_bonus_cny=2)
    admin = {"X-Admin-Token": "admin"}
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    code_a = (await client.get("/v1/me/invite", headers=auth(a["api_key"]))).json()["code"]
    b = await sign_up(client, sender, "dev-b@example.com", "mac", invite=code_a)
    c = await sign_up(client, sender, "dev-c@example.com", "mac", invite=code_a)
    # the inviter is disabled: C's first use pays C alone
    assert (
        await client.post("/v1/admin/disable", headers=admin, json={"account_id": a["account"]["id"], "disabled": True})
    ).status_code == 204
    await chat(client, c["api_key"])
    assert (await client.get("/v1/me", headers=auth(c["api_key"]))).json()["spend"]["grant"] == 3
    assert cloud.db.account(a["account"]["id"])["grant_uy"] == settings.allowance_uy
    assert cloud.db.invite_pending(c["account"]["id"]) is False
    # the inviter is gone: B's first use pays B alone, and the link by id stays
    assert (await client.post("/v1/admin/delete", headers=admin, json={"account_id": a["account"]["id"]})).status_code == 204
    await chat(client, b["api_key"])
    me_b = (await client.get("/v1/me", headers=auth(b["api_key"]))).json()
    assert me_b["spend"]["grant"] == 3
    detail = (await client.get(f"/v1/admin/accounts/{b['account']['id']}", headers=admin)).json()["account"]
    assert detail["invited_by"] == a["account"]["id"] and detail["inviter"] is None
    kinds = [e["kind"] for e in (await client.get("/v1/me/events", headers=auth(b["api_key"]))).json()["events"]]
    assert kinds.count("invite.settled") == 1


async def test_existing_invited_accounts_are_marked_settled_once_by_the_migration(tmp_path):
    """A relay upgraded in place paid every invite at sign-up: when the column arrives, every
    invited account is marked settled with its sign-up time, so nobody is paid a second time
    at their next request; opening the file again changes nothing, and an account invited
    afterwards starts pending."""
    import sqlite3

    from nanomuse_cloud.db import Database

    path = str(tmp_path / "paid.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY, id_hash TEXT NOT NULL UNIQUE, channel TEXT NOT NULL, hint TEXT NOT NULL,
            created_at INTEGER NOT NULL, granted INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0,
            disabled INTEGER NOT NULL DEFAULT 0, invited_by TEXT NOT NULL DEFAULT '', invites INTEGER NOT NULL DEFAULT 0,
            grant_uy INTEGER NOT NULL DEFAULT 0, allowance_uy INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO accounts (id, id_hash, channel, hint, created_at, invites) VALUES ('inviter', 'h1', 'email', 'a***@example.com', 100, 1);
        INSERT INTO accounts (id, id_hash, channel, hint, created_at, invited_by) VALUES ('friend', 'h2', 'email', 'b***@example.com', 200, 'inviter');
        INSERT INTO accounts (id, id_hash, channel, hint, created_at) VALUES ('alone', 'h3', 'email', 'c***@example.com', 300);
        """
    )
    conn.commit()
    conn.close()
    db = Database(path)
    assert "accounts.invite_settled_at" in db.added
    assert db.account("friend")["invite_settled_at"] == 200 and db.account("alone")["invite_settled_at"] is None
    assert db.invite_pending("friend") is False and db.settle_invite("friend", 5_000_000) == (False, "inviter", False)
    assert db.account("inviter")["grant_uy"] == 0
    db.close()
    again = Database(path)
    assert "accounts.invite_settled_at" not in again.added and again.account("friend")["invite_settled_at"] == 200
    again._conn.execute(
        "INSERT INTO accounts (id, id_hash, channel, hint, created_at) VALUES ('later', 'h4', 'email', 'd***@example.com', 400)"
    )
    again.link_invite("inviter", "later")
    assert again.invite_pending("later") is True and again.account("inviter")["invites"] == 2
    assert again.settle_invite("later", 5_000_000) == (True, "inviter", True)
    assert again.account("inviter")["grant_uy"] == 5_000_000 and again.account("later")["grant_uy"] == 5_000_000
    assert again.settle_invite("later", 5_000_000) == (False, "inviter", False)
    again.close()


async def test_operator_sees_who_invited_whom_and_the_ranking():
    """`GET /v1/admin/invites` ranks the inviters by the accounts their code brought, says how
    many of those ever made a request and how many came from one of the inviter's own
    addresses (a pool being farmed looks like many of the second and none of the first),
    and the account page names the inviter and lists the people brought."""
    app, client, sender, up, cloud, settings = make(allowance_cny=5, invite_bonus_cny=2)
    admin = {"X-Admin-Token": "admin"}
    a = await sign_up(client, sender, "dev-a@example.com", "pixel", headers={"X-Forwarded-For": "203.0.113.7"})
    b = await sign_up(client, sender, "dev-b@example.com", "mac", headers={"X-Forwarded-For": "198.51.100.2"})
    # every request says where it comes from: a request without the header would be seen from
    # the test client's own address and make two unrelated accounts share one
    from_ip = lambda key, ip: {**auth(key), "X-Forwarded-For": ip}  # noqa: E731
    code_a = (await client.get("/v1/me/invite", headers=from_ip(a["api_key"], "203.0.113.7"))).json()["code"]
    code_b = (await client.get("/v1/me/invite", headers=from_ip(b["api_key"], "198.51.100.2"))).json()["code"]

    async def join(identifier: str, code: str, ip: str) -> dict:
        return await sign_up(client, sender, identifier, "mac", invite=code, headers={"X-Forwarded-For": ip})

    # A's code brings three: two from A's own address that never use anything, one elsewhere who chats
    await join("13900000001", code_a, "203.0.113.7")
    await join("13900000002", code_a, "203.0.113.7")
    c3 = await join("dev-c3@example.com", code_a, "192.0.2.9")
    await chat(client, c3["api_key"], headers={"X-Forwarded-For": "192.0.2.9"})
    # B's code brings one, from somewhere else, who also uses it
    d = await join("dev-d@example.com", code_b, "192.0.2.10")
    await chat(client, d["api_key"], headers={"X-Forwarded-For": "192.0.2.10"})

    # nobody but the operator
    assert (await client.get("/v1/admin/invites")).status_code == 401
    view = (await client.get("/v1/admin/invites", headers=admin)).json()
    # every account is given its code at sign-up, so six have one; two have brought someone
    assert view["funnel"] == {"with_code": 6, "inviters": 2, "invited": 4, "contribute_bonuses": 0}
    assert [x["identifier"] for x in view["leaders"]] == ["dev-a@example.com", "dev-b@example.com"]
    top = view["leaders"][0]
    assert top["invites"] == 3 and top["invited"] == 3 and top["invited_active"] == 1 and top["invited_same_ip"] == 2
    # only the one who used the relay has been paid for: ¥2 earned, two sign-ups pending
    assert top["invited_settled"] == 1 and top["invited_pending"] == 2
    assert top["earned_cny"] == 2 and top["invited_spent_cny"] > 0 and top["last_ip"] == "203.0.113.7"
    assert top["first_invite_at"] <= top["last_invite_at"]
    assert [i["hint"] for i in top["invitees"]][-1] == "139****0001", "newest first, with the hint and no identifier"
    assert "identifier" not in top["invitees"][0] and top["invitees"][0]["requests"] == 1 and top["invitees"][0]["last_ip"] == "192.0.2.9"
    assert [i["settled"] for i in top["invitees"]] == [True, False, False]
    second = view["leaders"][1]
    assert second["invited"] == 1 and second["invited_same_ip"] == 0 and second["invitees"][0]["id"] == d["account"]["id"]
    assert second["invited_settled"] == 1 and second["invited_pending"] == 0 and second["earned_cny"] == 2
    assert "places" in view
    # the newest `per` of each inviter's people, and none when asked for none
    few = (await client.get("/v1/admin/invites", params={"per": 1}, headers=admin)).json()
    assert len(few["leaders"][0]["invitees"]) == 1 and few["leaders"][0]["invited"] == 3
    assert (await client.get("/v1/admin/invites", params={"per": 0}, headers=admin)).json()["leaders"][0]["invitees"] == []
    assert len((await client.get("/v1/admin/invites", params={"limit": 1}, headers=admin)).json()["leaders"]) == 1

    # the account page: who brought this one, and whom this one brought
    detail = (await client.get(f"/v1/admin/accounts/{c3['account']['id']}", headers=admin)).json()["account"]
    assert detail["inviter"] == {"id": a["account"]["id"], "hint": "de***@example.com", "identifier": "dev-a@example.com"}
    assert detail["invited"] == [] and detail["invited_total"] == 0 and detail["invite_earned_cny"] == 0
    detail = (await client.get(f"/v1/admin/accounts/{a['account']['id']}", headers=admin)).json()["account"]
    assert detail["inviter"] is None and detail["invited_total"] == 3 and detail["invite_earned_cny"] == 2
    assert [i["hint"] for i in detail["invited"]] == ["de***@example.com", "139****0002", "139****0001"]
    assert (
        detail["invited"][0]["requests"] == 1
        and detail["invited"][0]["settled"] is True
        and detail["invited"][1]["requests"] == 0
        and detail["invited"][1]["settled"] is False
        and detail["invited"][1]["first_ip"] == "203.0.113.7"
    )

    # an inviter who deleted their account: the invitee still says who, by id, and the ranking lets them go
    r = await client.post("/v1/admin/delete", headers=admin, json={"account_id": b["account"]["id"]})
    assert r.status_code == 204, r.text
    detail = (await client.get(f"/v1/admin/accounts/{d['account']['id']}", headers=admin)).json()["account"]
    assert detail["invited_by"] == b["account"]["id"] and detail["inviter"] is None
    assert [x["identifier"] for x in (await client.get("/v1/admin/invites", headers=admin)).json()["leaders"]] == ["dev-a@example.com"]


async def test_new_accounts_of_an_unknown_domain_are_limited_per_day():
    """At most SIGNUP_PER_DOMAIN_DAY new accounts per e-mail domain per rolling day, for a
    domain that is not a well known provider or on SIGNUP_DOMAIN_ALLOW: the next new address
    of it is refused a code (`429 domain_limited`, an event without the address); an account
    of that domain that already exists always gets its code; phone numbers, gmail.com and an
    allow-listed domain are never limited; 0 switches the limit off."""
    app, client, sender, up, cloud, settings = make(
        signup_per_domain_day=3, signup_domain_allow="Friends.example.org, @club.example.net", code_per_ip_hour=1000
    )
    admin = {"X-Admin-Token": "admin"}
    for i in range(3):
        await sign_up(client, sender, f"farm{i}@farm.example.com", "mac")
    r = await client.post("/v1/auth/code", json={"identifier": "farm3@farm.example.com"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "domain_limited", r.text
    assert "tomorrow" in r.json()["error"]["message"] and "farm3" not in r.text
    events = (await client.get("/v1/admin/events", params={"kind": "signup.domain_limited"}, headers=admin)).json()["events"]
    assert len(events) == 1 and events[0]["detail"] == "farm.example.com" and events[0]["account_id"] == ""
    # the domain is matched whole: a sub-domain, or a domain that merely ends the same, is its own
    assert (await client.post("/v1/auth/code", json={"identifier": "x@notfarm.example.com"})).status_code == 204
    # an existing account of the limited domain still gets its code
    assert (await client.post("/v1/auth/code", json={"identifier": "farm0@farm.example.com"})).status_code == 204
    # the well known providers, the allow list (case and a leading @ do not matter) and phone numbers are never limited
    for i in range(4):
        await sign_up(client, sender, f"person{i}@gmail.com", "mac")
        await sign_up(client, sender, f"friend{i}@friends.example.org", "mac")
        await sign_up(client, sender, f"member{i}@club.example.net", "mac")
        await sign_up(client, sender, f"1390000000{i}", "mac")
    settings_view = (await client.get("/v1/admin/accounts", headers=admin)).json()["settings"]
    assert settings_view["signup_per_domain_day"] == 3 and settings_view["signup_domain_allow"] == [
        "Friends.example.org",
        "@club.example.net",
    ]
    # off
    app, client, sender, up, cloud, settings = make(signup_per_domain_day=0, code_per_ip_hour=1000)
    for i in range(5):
        await sign_up(client, sender, f"farm{i}@farm.example.com", "mac")


async def test_health_raises_invite_velocity_and_unused_invitees():
    """`GET /v1/admin/health` names an inviter whose code brought more than ten accounts in a
    day (`invite_velocity`) and one with twenty or more invitees of whom one in twenty or
    fewer ever made a request (`invite_unused`); both as a sentence in `problems`, which the
    self-check mails, and with the inviters under `alerts`. A code that brought people who
    use the relay raises nothing."""
    app, client, sender, up, cloud, settings = make(allowance_cny=5, invite_bonus_cny=1, code_per_ip_hour=1000)
    admin = {"X-Admin-Token": "admin"}
    health = (await client.get("/v1/admin/health", headers=admin)).json()
    assert health["ok"] is True and health["alerts"] == []
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    code_a = (await client.get("/v1/me/invite", headers=auth(a["api_key"]))).json()["code"]
    farmed = [await sign_up(client, sender, f"1390000{i:04d}", "mac", invite=code_a) for i in range(21)]
    await chat(client, farmed[0]["api_key"])  # one in twenty-one used it: still 5 % or fewer
    health = (await client.get("/v1/admin/health", headers=admin)).json()
    assert health["ok"] is False and [x["kind"] for x in health["alerts"]] == ["invite_velocity", "invite_unused"]
    fast, idle = health["alerts"]
    assert fast["inviters"] == [{"id": a["account"]["id"], "hint": "de***@example.com", "n": 21}]
    assert idle["inviters"] == [{"id": a["account"]["id"], "hint": "de***@example.com", "n": 21, "active": 1}]
    assert len(health["problems"]) == 2 and all("Invites view" in p for p in health["problems"])
    assert a["account"]["id"][:8] in health["problems"][0] and "more than 10 accounts in the last 24 hours" in health["problems"][0]
    assert "20 or more invitees" in health["problems"][1] and "5% or fewer" in health["problems"][1]
    # an inviter whose people use the relay: many sign-ups still trip the velocity alert for the day, nothing else
    b = await sign_up(client, sender, "dev-b@example.com", "pixel")
    code_b = (await client.get("/v1/me/invite", headers=auth(b["api_key"]))).json()["code"]
    for i in range(11):
        friend = await sign_up(client, sender, f"friend{i}@gmail.com", "mac", invite=code_b)
        await chat(client, friend["api_key"])
    health = (await client.get("/v1/admin/health", headers=admin)).json()
    assert [x["id"] for x in health["alerts"][0]["inviters"]] == [a["account"]["id"], b["account"]["id"]]
    assert [x["id"] for x in health["alerts"][1]["inviters"]] == [a["account"]["id"]]
    # the sign-ups of yesterday do not count for velocity; the unused share stays
    cloud.db._conn.execute("UPDATE accounts SET created_at=created_at-90000 WHERE invited_by<>''")
    health = (await client.get("/v1/admin/health", headers=admin)).json()
    assert [x["kind"] for x in health["alerts"]] == ["invite_unused"]


async def test_the_abuse_action_reclaims_the_credit_and_cleans_up():
    """`POST /v1/admin/invites/abuse` takes back the invite credit still in an inviter's pool
    (never below what it has spent), disables the account when asked, deletes the invited
    accounts that never used anything when asked and keeps the ones that did; a second call
    reclaims nothing; an unknown account is 404. The inviter's timeline and the audit log say
    what was done."""
    app, client, sender, up, cloud, settings = make(allowance_cny=1, invite_bonus_cny=2, code_per_ip_hour=1000, signup_per_domain_day=20)
    admin = {"X-Admin-Token": "admin", "X-Admin-Actor": "ops"}
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    ka = a["api_key"]
    code_a = (await client.get("/v1/me/invite", headers=auth(ka))).json()["code"]
    used = [await sign_up(client, sender, f"used{i}@example.com", "mac", invite=code_a) for i in range(3)]
    idle = [await sign_up(client, sender, f"idle{i}@example.com", "mac", invite=code_a) for i in range(4)]
    for u in used:
        await chat(client, u["api_key"])
    me = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me["spend"]["grant"] == 7 and me["invite"]["earned_cny"] == 6 and me["invite"]["pending"] == 4
    # A spends a little of it; 404 for an unknown account
    await chat(client, ka)
    spent = (await client.get("/v1/me", headers=auth(ka))).json()["spend"]["total"]
    assert 0 < spent < 1
    r = await client.post("/v1/admin/invites/abuse", headers=admin, json={"account_id": "nobody"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_account"

    # reclaim only: ¥6 back, the pool is the allowance again, nothing else changes
    r = await client.post("/v1/admin/invites/abuse", headers=admin, json={"account_id": a["account"]["id"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"reclaimed_cny": 6, "disabled": False, "deleted": 0, "kept": 7}
    me = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me["spend"]["grant"] == 1 and me["spend"]["left"] == round(1 - spent, 4) and me["invite"]["earned_cny"] == 0
    line = [x for x in me["recent"] if x["kind"] == "credit"][0]
    assert line["detail"]["from"] == "clawback" and line["detail"]["credit_uy"] == -6_000_000 and "ops" in line["detail"]["note"]
    assert cloud.db.invite_reclaimable_uy(a["account"]["id"]) == 0
    # a second call reclaims nothing (the ledger says the credit is gone); the invitees are untouched
    r = await client.post("/v1/admin/invites/abuse", headers=admin, json={"account_id": a["account"]["id"]})
    assert r.json() == {"reclaimed_cny": 0, "disabled": False, "deleted": 0, "kept": 7}
    # the floor: a pool spent past the allowance keeps `left` at zero, never below
    b = await sign_up(client, sender, "dev-b@example.com", "pixel")
    code_b = (await client.get("/v1/me/invite", headers=auth(b["api_key"]))).json()["code"]
    for i in range(2):
        await chat(client, (await sign_up(client, sender, f"bf{i}@example.com", "mac", invite=code_b))["api_key"])
    assert (await client.get("/v1/me", headers=auth(b["api_key"]))).json()["spend"]["grant"] == 5
    assert (await clip(client, b["api_key"])).status_code == 200  # ¥0.5 of a ¥1 allowance plus ¥4 of invites
    for _ in range(8):
        assert (await clip(client, b["api_key"])).status_code == 200
    b_spent = (await client.get("/v1/me", headers=auth(b["api_key"]))).json()["spend"]["total"]
    assert b_spent == 4.5
    r = await client.post("/v1/admin/invites/abuse", headers=admin, json={"identifier": "dev-b@example.com"})
    assert r.json()["reclaimed_cny"] == 0.5, "¥4 was earned but only ¥0.5 is still unspent"
    me_b = (await client.get("/v1/me", headers=auth(b["api_key"]))).json()
    assert me_b["spend"]["grant"] == 4.5 and me_b["spend"]["left"] == 0

    # disable and delete the unused: the four idle invitees go, the three who used it stay
    r = await client.post(
        "/v1/admin/invites/abuse", headers=admin, json={"account_id": a["account"]["id"], "disable": True, "delete_unused": True}
    )
    assert r.json() == {"reclaimed_cny": 0, "disabled": True, "deleted": 4, "kept": 3}
    assert (await client.get("/v1/me", headers=auth(ka))).status_code == 401
    for u in used:
        assert (await client.get("/v1/me", headers=auth(u["api_key"]))).status_code == 200
    for i in idle:
        assert (await client.get("/v1/me", headers=auth(i["api_key"]))).json()["error"]["code"] == "account_deleted"
    detail = (await client.get(f"/v1/admin/accounts/{a['account']['id']}", headers=admin)).json()["account"]
    assert detail["disabled"] is True and detail["invited_total"] == 3 and detail["invites"] == 7
    events = (await client.get(f"/v1/admin/accounts/{a['account']['id']}/events", headers=admin)).json()["rows"]
    claw = [e for e in events if e["kind"] == "invite.clawback"]
    assert len(claw) == 3 and claw[0]["detail"] == "¥0 reclaimed, disabled, 4 unused invitee(s) deleted, 3 kept"
    assert claw[-1]["detail"] == "¥6 reclaimed"
    audit = (await client.get("/v1/admin/controls/audit", headers=admin)).json()["audit"]
    mine = [x for x in audit if x["action"] == "invite.abuse"]
    assert len(mine) == 4 and mine[0]["actor"] == "ops" and mine[0]["target"] == a["account"]["id"][:8]


async def test_gmail_dot_plus_and_googlemail_canonicalisation_and_legacy_lookup():
    """Gmail and Googlemail addresses have dots and `+tag` stripped and `googlemail.com`
    folded to `gmail.com`, so dot/plus aliases cannot open extra accounts; any other domain
    also strips `+tag` subaddresses; an existing account whose `id_hash` was recorded from
    a dotted Gmail address before canonicalisation still matches on sign-in and upgrades
    its `id_hash` in place."""
    assert parse("First.Last+tag@GoogleMail.COM").value == "firstlast@gmail.com"
    assert parse("First.Last+tag@GoogleMail.COM").raw_value == "first.last+tag@googlemail.com"
    assert parse("a.b.c@gmail.com").value == "abc@gmail.com"
    assert parse("dev.a+1@example.com").value == "dev.a@example.com"
    for bad in ("...@gmail.com", "+tag@gmail.com", "+tag@example.com"):
        with pytest.raises(BadIdentifier):
            parse(bad)

    app, client, sender, up, cloud, settings = make(allowance_cny=10, code_per_identifier_10m=10, code_per_ip_hour=1000)
    first = await sign_up(client, sender, "a.b.c@gmail.com", "pixel")
    assert first["created"] is True
    for alias in ("ab.c@gmail.com", "abc@gmail.com", "a.b.c+2@gmail.com", "abc@googlemail.com"):
        again = await sign_up(client, sender, alias, "mac")
        assert again["created"] is False and again["account"]["id"] == first["account"]["id"]

    # An account created before canonicalisation (stored with the legacy dotted hash)
    # still matches when the holder signs in with dots or with password, and upgrades its
    # id_hash to the canonical hash.
    legacy_raw = "old.user@gmail.com"
    legacy_ident = parse(legacy_raw)
    legacy_hash = legacy_ident.legacy_hash(cloud.s.hmac_key)
    assert legacy_hash is not None and legacy_hash != legacy_ident.hash(cloud.s.hmac_key)
    legacy_id = cloud.db.new_account_id()
    enc = cloud.crypto.encrypt(legacy_id, legacy_raw)
    cloud.db.create_account(legacy_hash, "email", "ol***@gmail.com", 0, enc, account_id=legacy_id, grant_uy=10_000_000)
    cloud.db.set_password(legacy_id, hash_password("correct horse 1"))

    r = await client.post("/v1/auth/login", json={"identifier": "old.user@gmail.com", "password": "correct horse 1", "device": "mac"})
    assert r.status_code == 200 and r.json()["account"]["id"] == legacy_id
    # The row's id_hash is now upgraded to the canonical hash, so undotted sign-in matches too.
    again_login = await sign_up(client, sender, "olduser@gmail.com", "pixel")
    assert again_login["created"] is False and again_login["account"]["id"] == legacy_id


async def test_blocked_disposable_domains_are_refused():
    """Domains in DEFAULT_SIGNUP_DOMAIN_BLOCK and SIGNUP_DOMAIN_BLOCK are refused for new
    sign-ups with `429 domain_limited` even when `signup_per_domain_day` is not yet reached
    or is 0; an entry in `SIGNUP_DOMAIN_ALLOW` overrides the blocklist."""
    app, client, sender, up, cloud, settings = make(
        signup_per_domain_day=0,
        signup_domain_block="throwaway.example.org",
        signup_domain_allow="allowed.maxxspace.com",
        code_per_ip_hour=1000,
    )
    for blocked in ("bot1@maxxspace.com", "bot2@duckmail.sbs", "sub@mail.duckmail.sbs", "x@throwaway.example.org"):
        r = await client.post("/v1/auth/code", json={"identifier": blocked})
        assert r.status_code == 429 and r.json()["error"]["code"] == "domain_limited", blocked
    ok = await sign_up(client, sender, "friend@allowed.maxxspace.com", "mac")
    assert ok["created"] is True


async def test_invite_settlement_refuses_same_ip_and_tiny_probes():
    """An invite bonus does not settle when the inviter and invitee share the same
    non-local `first_ip`, and does not settle on a tiny probe below
    `invite_settle_min_spent_uy` unless the invitee has registered a Hub device or crosses
    the spend threshold."""
    app, client, sender, up, cloud, settings = make(
        allowance_cny=5, invite_bonus_cny=2, invite_settle_min_spent_uy=200, code_per_ip_hour=1000
    )
    inviter = await sign_up(client, sender, "inviter@gmail.com", "pixel", headers={"X-Forwarded-For": "198.51.100.10"})
    code = (await client.get("/v1/me/invite", headers=auth(inviter["api_key"]))).json()["code"]

    # Same IP as the inviter: even after a normal chat, the invite bonus stays pending.
    same_ip = await sign_up(client, sender, "selfinvite@gmail.com", "mac", invite=code, headers={"X-Forwarded-For": "198.51.100.10"})
    await chat(client, same_ip["api_key"])
    me_inviter = (await client.get("/v1/me", headers=auth(inviter["api_key"]))).json()
    assert me_inviter["invite"]["settled"] == 0 and me_inviter["invite"]["pending"] == 1

    # Different IP, but a tiny streamed turn (40 prompt + 6 completion tokens on
    # qwen3.8-flash = 48 micro-yuan < 200 micro-yuan): stays pending until real usage
    # crosses the threshold.
    friend = await sign_up(client, sender, "friend@gmail.com", "mac", invite=code, headers={"X-Forwarded-For": "203.0.113.25"})
    r = await client.post(
        "/v1/chat/completions",
        headers=auth(friend["api_key"]),
        json={"model": "qwen3.8-flash", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    me_friend = (await client.get("/v1/me", headers=auth(friend["api_key"]))).json()
    assert me_friend["spend"]["grant"] == 5
    me_inviter = (await client.get("/v1/me", headers=auth(inviter["api_key"]))).json()
    assert me_inviter["invite"]["settled"] == 0

    # A full chat turn (215 micro-yuan, bringing total spend > 200 micro-yuan) settles both.
    await chat(client, friend["api_key"])
    me_friend = (await client.get("/v1/me", headers=auth(friend["api_key"]))).json()
    assert me_friend["spend"]["grant"] == 7
    me_inviter = (await client.get("/v1/me", headers=auth(inviter["api_key"]))).json()
    assert me_inviter["invite"]["settled"] == 1 and me_inviter["spend"]["grant"] == 7
