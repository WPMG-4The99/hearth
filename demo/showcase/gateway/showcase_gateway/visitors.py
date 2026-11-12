"""Who is trying the showcase: a nanoMuse Cloud sign-in before a demo Muse.

The phone in the browser is a demo — a MobileGym with a lite web app in it, a long way from
the Android app — and the project wants to know who is trying it and be able to reach them.
So a visitor signs in to nanoMuse Cloud first (a code to a phone or an inbox), the same
account the apps use; a first sign-in creates the account, with its free
allowance, for the day the person installs the app.

The gateway asks the relay to verify the code the way the kept Muses did (accounts.py), notes
the account — its opaque id, the masked identifier the relay gives (``195****0404``,
``g…@gmail.com``), the channel — and hands the browser a *ticket*: a random token the module
keeps and presents when it asks for a demo Muse. The device key the relay issued for the
sign-in is revoked at once; the demo Muse talks to the showcase's model, not to the
account's allowance. Tickets and visitors live in SQLite on the gateway's volume
(``VISITOR_DB``); a ticket lasts ``VISITOR_TTL_S`` (thirty days), so a returning visitor is
not asked twice.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .relay import relay_request
from .sessions import Refused

log = logging.getLogger("showcase.visitors")

DEVICE_NAME = "Showcase"  # what the relay's device list calls the sign-in, briefly


UA_MAX = 160


@dataclass
class Visitor:
    id: str  # the relay's account id (opaque)
    hint: str  # the masked identifier the relay gives — never the identifier itself
    channel: str  # "sms" | "email"
    first_seen: float
    last_seen: float
    sessions: int = 0  # demo Muses started
    created: bool = False  # the first sign-in here created the Cloud account
    # where from and with what (0.3): the address of the first and the latest sign-in or
    # demo, the latest browser string, how many times the person signed in here
    first_ip: str = ""
    last_ip: str = ""
    last_ua: str = ""
    signins: int = 0

    def public(self) -> dict[str, Any]:
        return {"hint": self.hint, "channel": self.channel, "created": self.created}

    def admin(self) -> dict[str, Any]:
        """For the operator (the relay's admin page): everything the gateway knows."""
        return {
            "id": self.id,
            "hint": self.hint,
            "channel": self.channel,
            "created": self.created,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sessions": self.sessions,
            "signins": self.signins,
            "first_ip": self.first_ip,
            "last_ip": self.last_ip,
            "last_ua": self.last_ua,
        }

    def seen(self, ip: str, ua: str, now: float) -> None:
        self.last_seen = now
        if ip:
            self.last_ip = ip
            if not self.first_ip:
                self.first_ip = ip
        if ua:
            self.last_ua = ua[:UA_MAX]


class VisitorStore:
    """SQLite: one row per visitor, one per ticket (hashed), one per demo started (a
    *visit*: when, from where, with what browser, what it used). ``:memory:`` in tests."""

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """CREATE TABLE IF NOT EXISTS visitors (
                 id TEXT PRIMARY KEY, hint TEXT NOT NULL DEFAULT '',
                 channel TEXT NOT NULL DEFAULT '', first_seen REAL NOT NULL,
                 last_seen REAL NOT NULL, sessions INTEGER NOT NULL DEFAULT 0,
                 created INTEGER NOT NULL DEFAULT 0);
               CREATE TABLE IF NOT EXISTS tickets (
                 hash TEXT PRIMARY KEY, visitor TEXT NOT NULL, issued_at REAL NOT NULL,
                 expires_at REAL NOT NULL);
               CREATE TABLE IF NOT EXISTS visits (
                 id TEXT PRIMARY KEY, visitor TEXT NOT NULL DEFAULT '', hint TEXT NOT NULL DEFAULT '',
                 ip TEXT NOT NULL DEFAULT '', ua TEXT NOT NULL DEFAULT '',
                 started REAL NOT NULL, ended REAL, reason TEXT NOT NULL DEFAULT '',
                 requests INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
                 pictures INTEGER NOT NULL DEFAULT 0, clips INTEGER NOT NULL DEFAULT 0,
                 byok INTEGER NOT NULL DEFAULT 0);
               CREATE INDEX IF NOT EXISTS visits_visitor ON visits(visitor, started);"""
        )
        # 0.3: the columns the store did not have before
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(visitors)")}
        for col, ddl in (
            ("first_ip", "TEXT NOT NULL DEFAULT ''"),
            ("last_ip", "TEXT NOT NULL DEFAULT ''"),
            ("last_ua", "TEXT NOT NULL DEFAULT ''"),
            ("signins", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in have:
                self.db.execute(f"ALTER TABLE visitors ADD COLUMN {col} {ddl}")
        self.db.commit()

    def get(self, visitor_id: str) -> Visitor | None:
        row = self.db.execute("SELECT * FROM visitors WHERE id = ?", (visitor_id,)).fetchone()
        return self._visitor(row) if row else None

    def put(self, v: Visitor) -> None:
        self.db.execute(
            """INSERT INTO visitors (id, hint, channel, first_seen, last_seen, sessions, created,
                                     first_ip, last_ip, last_ua, signins)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET hint=excluded.hint, channel=excluded.channel,
                 last_seen=excluded.last_seen, sessions=excluded.sessions,
                 first_ip=CASE WHEN visitors.first_ip='' THEN excluded.first_ip ELSE visitors.first_ip END,
                 last_ip=excluded.last_ip, last_ua=excluded.last_ua, signins=excluded.signins""",
            (
                v.id,
                v.hint,
                v.channel,
                v.first_seen,
                v.last_seen,
                v.sessions,
                int(v.created),
                v.first_ip,
                v.last_ip,
                v.last_ua,
                v.signins,
            ),
        )
        self.db.commit()

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM visitors").fetchone()[0])

    def count_since(self, when: float) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM visitors WHERE last_seen >= ?", (when,))
        return int(row.fetchone()[0])

    def visitors(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM visitors ORDER BY last_seen DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._visitor(r).admin() for r in rows]

    # --- visits: one row per demo Muse started -------------------------------------
    def visit_started(
        self, sid: str, visitor_id: str, hint: str, ip: str, ua: str, now: float, byok: bool
    ) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO visits (id, visitor, hint, ip, ua, started, byok)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, visitor_id, hint, ip, (ua or "")[:UA_MAX], now, int(byok)),
        )
        self.db.commit()

    def visit_ended(
        self,
        sid: str,
        now: float,
        reason: str,
        requests: int,
        tokens: int,
        pictures: int,
        clips: int,
    ) -> None:
        self.db.execute(
            """UPDATE visits SET ended=?, reason=?, requests=?, tokens=?, pictures=?, clips=?
               WHERE id=?""",
            (now, reason[:80], requests, tokens, pictures, clips, sid),
        )
        self.db.commit()

    def visits(
        self, visitor_id: str = "", limit: int = 200, since: float = 0
    ) -> list[dict[str, Any]]:
        if visitor_id:
            rows = self.db.execute(
                "SELECT * FROM visits WHERE visitor=? AND started>=? ORDER BY started DESC LIMIT ?",
                (visitor_id, since, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM visits WHERE started>=? ORDER BY started DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def visit_count(self, visitor_id: str = "") -> int:
        if visitor_id:
            row = self.db.execute("SELECT COUNT(*) FROM visits WHERE visitor=?", (visitor_id,))
        else:
            row = self.db.execute("SELECT COUNT(*) FROM visits")
        return int(row.fetchone()[0])

    def issue(self, visitor_id: str, now: float, ttl_s: int) -> str:
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO tickets (hash, visitor, issued_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash(token), visitor_id, now, now + ttl_s),
        )
        self.db.execute("DELETE FROM tickets WHERE expires_at < ?", (now,))
        self.db.commit()
        return token

    def redeem(self, token: str, now: float) -> Visitor | None:
        row = self.db.execute(
            "SELECT visitor FROM tickets WHERE hash = ? AND expires_at > ?", (_hash(token), now)
        ).fetchone()
        return self.get(row["visitor"]) if row else None

    def revoke(self, token: str) -> None:
        self.db.execute("DELETE FROM tickets WHERE hash = ?", (_hash(token),))
        self.db.commit()

    @staticmethod
    def _visitor(row: sqlite3.Row) -> Visitor:
        return Visitor(
            id=row["id"],
            hint=row["hint"],
            channel=row["channel"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            sessions=int(row["sessions"]),
            created=bool(row["created"]),
            first_ip=row["first_ip"] or "",
            last_ip=row["last_ip"] or "",
            last_ua=row["last_ua"] or "",
            signins=int(row["signins"] or 0),
        )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class VisitorBook:
    """The sign-in and the tickets."""

    def __init__(
        self,
        settings: Settings,
        store: VisitorStore,
        http: httpx.AsyncClient | None = None,
        clock=time.time,
    ) -> None:
        self.s = settings
        self.store = store
        self.http = http or httpx.AsyncClient(timeout=15)
        self.clock = clock

    @property
    def required(self) -> bool:
        return self.s.demo_signin_required

    async def _relay(
        self, method: str, path: str, body: dict | None, ip: str, key: str = "", ua: str = ""
    ):
        return await relay_request(self.http, self.s.web_relay_url, method, path, body, ip, key, ua)

    async def request_code(self, identifier: str, ip: str) -> None:
        await self._relay("POST", "/v1/auth/code", {"identifier": identifier}, ip)

    async def verify(
        self, identifier: str, code: str, ip: str, invite: str = "", ua: str = ""
    ) -> tuple[str, Visitor]:
        """The code the relay sent → a ticket for this browser. ``invite`` is a friend's
        code, passed along only when given."""
        payload: dict[str, str] = {"identifier": identifier, "code": code, "device": DEVICE_NAME}
        if invite:
            payload["invite"] = invite
        data = await self._relay("POST", "/v1/auth/verify", payload, ip, ua=ua)
        return await self._admit(data, ip, ua)

    async def _admit(self, data: dict[str, Any], ip: str, ua: str = "") -> tuple[str, Visitor]:
        raw_account = data.get("account")
        info: dict[str, Any] = raw_account if isinstance(raw_account, dict) else {}
        visitor_id = str(info.get("id") or "")
        if not visitor_id:
            raw = f"{info.get('channel')}|{info.get('hint')}|{info.get('created_at')}"
            visitor_id = hashlib.sha256(raw.encode()).hexdigest()[:24]
        now = self.clock()
        visitor = self.store.get(visitor_id)
        if visitor is None:
            visitor = Visitor(
                id=visitor_id,
                hint=str(info.get("hint") or ""),
                channel=str(info.get("channel") or ""),
                first_seen=now,
                last_seen=now,
                created=bool(data.get("created")),
            )
            log.info("visitor %s signed in for the first time (%s)", visitor.hint, visitor.channel)
        else:
            visitor.hint = str(info.get("hint") or visitor.hint)
            visitor.channel = str(info.get("channel") or visitor.channel)
            log.info("visitor %s is back", visitor.hint)
        visitor.seen(ip if ip != "?" else "", ua, now)
        visitor.signins += 1
        self.store.put(visitor)
        # the key the relay issued for this sign-in is not needed: the demo Muse talks to the
        # showcase's model. Revoke it so the account's device list does not fill up with
        # "Showcase" entries (best effort — an old relay without the route changes nothing).
        key = str(data.get("api_key") or "")
        if key:
            try:
                await self._relay("POST", "/v1/auth/sign-out", None, ip, key, ua)
            except Refused as exc:
                log.info("could not revoke the sign-in key (%s); it idles", exc.code)
        return self.store.issue(visitor.id, now, self.s.visitor_ttl_s), visitor

    def check(self, ticket: str | None) -> Visitor:
        """The visitor behind a ticket, or a refusal the page turns into the sign-in."""
        if not ticket:
            raise Refused(401, "signin_required", "Sign in to nanoMuse Cloud to start a demo.")
        visitor = self.store.redeem(ticket, self.clock())
        if visitor is None:
            raise Refused(401, "signin_expired", "Your sign-in has lapsed; sign in again.")
        return visitor

    def sign_out(self, ticket: str | None) -> None:
        if ticket:
            self.store.revoke(ticket)

    def started(self, visitor: Visitor | None, sess: Any = None, ua: str = "") -> None:
        """A demo Muse started: the visitor's count and whereabouts, and a visit row — also
        for a demo without a sign-in (``visitor`` None), which the operator still sees."""
        now = self.clock()
        # "?" is the gateway's word for a peer it could not name: no address, not that string
        ip = str(getattr(sess, "ip", "") or "") if sess is not None else ""
        if ip == "?":
            ip = ""
        if visitor is not None:
            visitor.sessions += 1
            visitor.seen(ip, ua, now)
            self.store.put(visitor)
        if sess is not None:
            self.store.visit_started(
                sess.id,
                visitor.id if visitor else "",
                visitor.hint if visitor else "",
                ip,
                ua,
                now,
                bool(getattr(sess, "byok", None)),
            )

    def ended(self, sess: Any, reason: str = "") -> None:
        """The session manager's word that a demo is over: what it used goes on its row."""
        try:
            self.store.visit_ended(
                sess.id, self.clock(), reason, sess.requests, sess.tokens, sess.pictures, sess.clips
            )
        except sqlite3.Error as exc:  # the record is for the operator; the demo is not held up
            log.warning("could not close the visit row for %s: %s", sess.id, exc)

    def stats(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "signin_required": self.required,
            "visitors": self.store.count(),
            "visitors_7d": self.store.count_since(now - 7 * 86400),
        }

    def admin(self, visitor_id: str = "", days: int = 30) -> dict[str, Any]:
        """The operator's view (``GET /api/demo/admin``): every visitor with where from and
        with what, and the demos started — one visitor's when ``visitor_id`` is given, else
        the period's newest. The relay's admin page shows it next to the account."""
        now = self.clock()
        if visitor_id:
            v = self.store.get(visitor_id)
            return {
                "visitor": v.admin() if v else None,
                "visits": self.store.visits(visitor_id, limit=500),
                "visits_total": self.store.visit_count(visitor_id),
            }
        since = now - max(1, days) * 86400
        return {
            "signin_required": self.required,
            "visitors": self.store.visitors(),
            "visitors_total": self.store.count(),
            "visits": self.store.visits(limit=500, since=since),
            "visits_total": self.store.visit_count(),
            "days": days,
        }
