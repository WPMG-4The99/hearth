"""The visitors' sign-in: a nanoMuse Cloud account before a demo Muse."""

from __future__ import annotations

import httpx
import pytest

from showcase_gateway.app import create_app
from showcase_gateway.sessions import SessionManager
from showcase_gateway.visitors import Visitor, VisitorBook, VisitorStore

from .conftest import Clock, FakeRunner, Upstream, make_settings


@pytest.fixture
def showcase():
    settings = make_settings(demo_signin_required=True)
    runner = FakeRunner()
    upstream = Upstream()
    clock = Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    manager = SessionManager(settings, runner, http=client, clock=clock)
    book = VisitorBook(settings, VisitorStore(":memory:"), http=client, clock=clock)
    app = create_app(settings, manager, client=client, visitors=book)
    return settings, runner, upstream, clock, book, app


async def client_for(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://localhost:8000")


async def sign_in(c, ident: str, ip: str = "1.2.3.4") -> httpx.Response:
    r = await c.post(
        "/api/demo/signin/code", json={"identifier": ident}, headers={"x-forwarded-for": ip}
    )
    assert r.status_code == 204, r.text
    return await c.post(
        "/api/demo/signin/verify",
        json={"identifier": ident, "code": "246810"},
        headers={"x-forwarded-for": ip},
    )


async def test_a_demo_needs_a_sign_in(showcase):
    settings, runner, upstream, clock, book, app = showcase
    async with await client_for(app) as c:
        info = (await c.get("/api/demo/info")).json()
        assert info["signin_required"] is True
        assert info["signin"] == {"signin_required": True, "visitors": 0, "visitors_7d": 0}

        # no ticket: the page is told to sign in, nothing is started
        r = await c.post("/api/demo/session", json={}, headers={"x-forwarded-for": "1.2.3.4"})
        assert r.status_code == 401
        assert r.json()["error"] == "signin_required"
        assert runner.running == {}

        # a wrong code is the relay's own refusal, passed on
        await c.post("/api/demo/signin/code", json={"identifier": "someone@example.com"})
        r = await c.post(
            "/api/demo/signin/verify", json={"identifier": "someone@example.com", "code": "000000"}
        )
        assert r.status_code == 400
        assert r.json()["error"] == "code_wrong"

        r = await sign_in(c, "someone@example.com")
        assert r.status_code == 200, r.text
        data = r.json()
        ticket = data["ticket"]
        assert data["visitor"] == {"hint": "so…", "channel": "email", "created": True}
        # the key the relay issued for the sign-in is revoked at once
        assert upstream.revoked == ["nm_key1"]
        # the relay saw the visitor's address, not the gateway's
        verify = [q for q in upstream.calls if q.url.path == "/v1/auth/verify"][-1]
        assert verify.headers["x-forwarded-for"] == "1.2.3.4"

        # the ticket opens the demo, and the session carries the account (never the identifier)
        auth = {"authorization": f"Bearer {ticket}", "x-forwarded-for": "1.2.3.4"}
        r = await c.post("/api/demo/session", json={}, headers=auth)
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert list(runner.running) == [f"nm-{sid}"]
        me = (await c.get("/api/demo/me", headers=auth)).json()
        assert me["visitor"]["hint"] == "so…"
        assert book.store.get("acct-someone-at-example.com").sessions == 1
        info = (await c.get("/api/demo/info")).json()
        assert info["signin"]["visitors"] == 1


async def test_the_operator_sees_where_visitors_came_from_and_what_each_demo_used(showcase):
    """0.3: the sign-in and every demo keep the address and the browser; a visit row per
    demo with what it used; GET /api/demo/admin hands it to the relay's admin page."""
    settings, runner, upstream, clock, book, app = showcase
    settings = make_settings(demo_signin_required=True, admin_token="shh")
    manager = SessionManager(settings, runner, http=book.http, clock=clock)
    app = create_app(settings, manager, client=book.http, visitors=book)
    async with await client_for(app) as c:
        # no token, wrong token: not for the public
        assert (await c.get("/api/demo/admin")).status_code == 403
        assert (await c.get("/api/demo/admin", headers={"x-admin-token": "no"})).status_code == 403

        r = await c.post(
            "/api/demo/signin/code",
            json={"identifier": "someone@example.com"},
            headers={"x-forwarded-for": "1.2.3.4"},
        )
        assert r.status_code == 204
        r = await c.post(
            "/api/demo/signin/verify",
            json={"identifier": "someone@example.com", "code": "246810"},
            headers={"x-forwarded-for": "1.2.3.4", "user-agent": "Mozilla/5.0 (Android 14) Chrome"},
        )
        ticket = r.json()["ticket"]
        v = book.store.get("acct-someone-at-example.com")
        assert (v.first_ip, v.last_ip, v.signins) == ("1.2.3.4", "1.2.3.4", 1)
        assert v.last_ua.startswith("Mozilla/5.0 (Android 14)")

        # a demo from another address: the visitor's latest moves, the first stays
        auth = {
            "authorization": f"Bearer {ticket}",
            "x-forwarded-for": "5.6.7.8",
            "user-agent": "Mozilla/5.0 (X11; Linux) Firefox",
        }
        r = await c.post("/api/demo/session", json={}, headers=auth)
        assert r.status_code == 201, r.text
        sess = r.json()
        v = book.store.get("acct-someone-at-example.com")
        assert (v.first_ip, v.last_ip) == ("1.2.3.4", "5.6.7.8")
        # the Muse talks to the model twice, then the visitor stops it
        for _ in range(2):
            rr = await c.post(
                f"/llm/{sess['id']}/main/chat/completions",
                headers={"authorization": f"Bearer {manager.sessions[sess['id']].llm_key}"},
                json={"model": "demo-model", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert rr.status_code == 200, rr.text
        r = await c.delete(
            f"/api/demo/session/{sess['id']}", headers={"authorization": f"Bearer {sess['token']}"}
        )
        assert r.status_code == 204

        admin = {"x-admin-token": "shh"}
        view = (await c.get("/api/demo/admin", headers=admin)).json()
        assert view["visitors_total"] == 1 and view["visits_total"] == 1 and view["active"] == []
        [visitor] = view["visitors"]
        assert (
            visitor["hint"] == "so…"
            and visitor["last_ip"] == "5.6.7.8"
            and visitor["sessions"] == 1
        )
        [visit] = view["visits"]
        assert visit["visitor"] == "acct-someone-at-example.com" and visit["ip"] == "5.6.7.8"
        assert visit["ua"].startswith("Mozilla/5.0 (X11; Linux)") and visit["byok"] == 0
        assert visit["requests"] == 2 and visit["tokens"] > 0 and visit["ended"] is not None
        assert visit["reason"] == "ended by the visitor"
        # one visitor's visits, for the account drawer
        mine = (
            await c.get("/api/demo/admin?account=acct-someone-at-example.com", headers=admin)
        ).json()
        assert mine["visitor"]["first_ip"] == "1.2.3.4" and [x["id"] for x in mine["visits"]] == [
            sess["id"]
        ]
        # the public info still says only the counts
        info = (await c.get("/api/demo/info")).json()
        assert info["signin"] == {"signin_required": True, "visitors": 1, "visitors_7d": 1}


async def test_one_account_is_one_person(showcase):
    settings, runner, upstream, clock, book, app = showcase
    async with await client_for(app) as c:
        # the same account from two browsers in two places
        t1 = (await sign_in(c, "someone@example.com", ip="1.1.1.1")).json()["ticket"]
        t2 = (await sign_in(c, "someone@example.com", ip="2.2.2.2")).json()["ticket"]
        assert book.store.count() == 1  # one visitor, seen twice
        r = await c.post(
            "/api/demo/session",
            json={},
            headers={"authorization": f"Bearer {t1}", "x-forwarded-for": "1.1.1.1"},
        )
        assert r.status_code == 201
        first = r.json()
        r = await c.post(
            "/api/demo/session",
            json={},
            headers={"authorization": f"Bearer {t2}", "x-forwarded-for": "2.2.2.2"},
        )
        assert r.status_code == 429
        assert r.json()["error"] == "already_running"

        # ending the first makes room; the day's allowance (two here) then runs out
        r = await c.delete(
            f"/api/demo/session/{first['id']}",
            headers={"authorization": f"Bearer {first['token']}"},
        )
        assert r.status_code == 204
        r = await c.post(
            "/api/demo/session",
            json={},
            headers={"authorization": f"Bearer {t2}", "x-forwarded-for": "2.2.2.2"},
        )
        assert r.status_code == 201
        second = r.json()
        await c.delete(
            f"/api/demo/session/{second['id']}",
            headers={"authorization": f"Bearer {second['token']}"},
        )
        r = await c.post(
            "/api/demo/session",
            json={},
            headers={"authorization": f"Bearer {t2}", "x-forwarded-for": "3.3.3.3"},
        )
        assert r.status_code == 429
        assert r.json()["error"] == "daily_limit"


async def test_tickets_lapse_and_can_be_handed_back(showcase):
    settings, runner, upstream, clock, book, app = showcase
    async with await client_for(app) as c:
        ticket = (await sign_in(c, "someone@example.com")).json()["ticket"]
        auth = {"authorization": f"Bearer {ticket}"}
        assert (await c.get("/api/demo/me", headers=auth)).status_code == 200
        clock.now += settings.visitor_ttl_s + 1
        r = await c.get("/api/demo/me", headers=auth)
        assert r.status_code == 401
        assert r.json()["error"] == "signin_expired"

        ticket = (await sign_in(c, "someone@example.com")).json()["ticket"]
        auth = {"authorization": f"Bearer {ticket}"}
        assert (await c.post("/api/demo/signout", headers=auth)).status_code == 204
        assert (await c.get("/api/demo/me", headers=auth)).status_code == 401
        # a made-up ticket is no ticket
        r = await c.get("/api/demo/me", headers={"authorization": "Bearer nonsense"})
        assert r.status_code == 401


async def test_password_sign_in_is_gone(showcase):
    settings, runner, upstream, clock, book, app = showcase
    async with await client_for(app) as c:
        r = await c.post(
            "/api/demo/signin/login",
            json={"identifier": "someone@example.com", "password": "correct horse"},
        )
        assert r.status_code == 404


async def test_switched_off_means_the_old_way(showcase):
    # DEMO_SIGNIN_REQUIRED=0: a visitor is a visitor, as before
    settings = make_settings(demo_signin_required=False)
    runner = FakeRunner()
    upstream = Upstream()
    clock = Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    manager = SessionManager(settings, runner, http=client, clock=clock)
    app = create_app(settings, manager, client=client)
    async with await client_for(app) as c:
        info = (await c.get("/api/demo/info")).json()
        assert info["signin_required"] is False
        r = await c.post("/api/demo/session", json={}, headers={"x-forwarded-for": "1.2.3.4"})
        assert r.status_code == 201


@pytest.mark.parametrize(
    ("status", "text", "code", "message"),
    [
        # the relay's own shape
        (
            400,
            '{"error": {"code": "code_wrong", "message": "That code is not right"}}',
            "code_wrong",
            "That code is not right",
        ),
        # a JSON list, a string, a bare message and FastAPI's detail: no code to pass on, and
        # no AttributeError on this side of the wire
        (400, "[1, 2]", "relay_error", "nanoMuse Cloud refused the request."),
        (400, '"nope"', "relay_error", "nanoMuse Cloud refused the request."),
        (400, '{"error": "nope"}', "relay_error", "nope"),
        (422, '{"detail": "identifier: too short"}', "relay_error", "identifier: too short"),
        (
            422,
            '{"detail": [{"loc": ["body"]}]}',
            "relay_error",
            "nanoMuse Cloud refused the request.",
        ),
        (502, "<html>bad gateway</html>", "relay_error", "nanoMuse Cloud refused the request."),
    ],
)
async def test_a_refusal_of_any_shape_reaches_the_browser_as_a_refusal(status, text, code, message):
    """relay.py read ``r.json().get("error", {})``: a body that is JSON but not an object
    raised AttributeError in the handler, and the visitor got a 500 in place of the refusal."""
    settings = make_settings(demo_signin_required=True)
    runner = FakeRunner()
    upstream = Upstream()
    clock = Clock()

    def odd_relay(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cloud.example":
            return httpx.Response(status, text=text)
        return upstream.handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(odd_relay))
    manager = SessionManager(settings, runner, http=client, clock=clock)
    book = VisitorBook(settings, VisitorStore(":memory:"), http=client, clock=clock)
    app = create_app(settings, manager, client=client, visitors=book)
    async with await client_for(app) as c:
        r = await c.post("/api/demo/signin/code", json={"identifier": "someone@example.com"})
    # a 5xx of the relay's is a 502 here; a 4xx keeps its status
    assert r.status_code == (status if status < 500 else 502)
    assert r.json() == {"error": code, "message": message}


def test_a_demo_from_a_peer_without_an_address_leaves_the_address_empty():
    """The gateway names a peer it cannot see "?"; the visitor's book must not write that
    string down as an address, in the visit row (it did not) or on the visitor (it did)."""
    settings = make_settings(demo_signin_required=True)
    clock = Clock()
    book = VisitorBook(settings, VisitorStore(":memory:"), clock=clock)
    visitor = Visitor(id="acct-1", hint="so…", channel="email", first_seen=1.0, last_seen=1.0)
    book.store.put(visitor)

    class Sess:
        id = "abc"
        ip = "?"
        byok = None

    book.started(visitor, Sess(), ua="Mozilla/5.0")
    row = book.admin("acct-1")
    assert row["visitor"]["first_ip"] == "" and row["visitor"]["last_ip"] == ""
    assert row["visits"][0]["ip"] == ""
    assert row["visitor"]["sessions"] == 1 and row["visitor"]["last_ua"] == "Mozilla/5.0"
