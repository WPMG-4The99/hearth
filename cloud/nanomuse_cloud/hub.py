"""The hub: the devices of one account find each other here and pass requests
through, whatever network each of them is on.

Every device — a phone, a computer, a browser tab — keeps one WebSocket to
`/v1/hub`, opened outbound, so nothing has to be reachable from the outside.
The relay only forwards; it never runs anything itself and never sees a
command's approval, which is decided on the device that would carry it out.

Frames are JSON text. From a device:

    {"type":"hello", "key": "nm_…"?, "device": {"id","name","kind","os","version","actions":[…]}}
    {"type":"call",   "id","to","action","args"}         ask another device to do something
    {"type":"result", "id","ok", "body" | "error","message"}   the answer to a call received
    {"type":"event",  "id","body"}                        progress on a call still running
    {"type":"devices"}  {"type":"rename","name"}  {"type":"forget","device_id"}  {"type":"ping"}

From the hub:

    {"type":"welcome","device_id","devices":[…],"server":{…}}
    {"type":"devices","devices":[…]}                      whenever a device comes or goes
    {"type":"call","id","from":{id,name,kind},"action","args"}
    {"type":"result", …}  {"type":"event", …}             forwarded to the caller
    {"type":"error","code","message","id"?}               e.g. device_offline, not_controllable, not_allowed, not_linked
    {"type":"pong"}
    {"type":"sync","what":"conversations","cursor","from"}  another device pushed chats; pull (0.19)
    {"type":"working","cid","from","device_name","working","at"}  a turn started or ended there (0.20)

`key` in hello is for browsers, which cannot set an Authorization header. A
`web` device is a front door only: it can call, it cannot be called. Devices of
the other kinds are remembered per account so the console can show them while
they are offline. Binary payloads (files, screenshots) travel base64-encoded
inside `body`, under the frame limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from . import __version__
from . import client as _client
from .db import Database
from .service import Caller, Cloud, CloudError

log = logging.getLogger("nanomuse_cloud.hub")

KINDS = ("phone", "computer", "web")
HELLO_TIMEOUT_S = 15
CALL_TTL_S = 15 * 60
_ID = re.compile(r"^[A-Za-z0-9._:-]{4,80}$")

# What one socket may send: frames and bytes per second, sustained, with twice that as a
# burst (a screen being streamed at a few frames a second with pictures of a megabyte or
# two fits easily). Over it, frames are dropped with one `rate_limited` error a second;
# a socket that keeps flooding after that many dropped frames in a row is closed.
FRAMES_PER_S = 60.0
BYTES_PER_S = 8 * 1024 * 1024.0
FLOOD_CLOSE_AFTER = 600

# What the relay holds for one socket that is not reading: frames queued for it, and
# their bytes. A send never waits on the peer (so a slow phone cannot stall the computer
# calling it); past either bound the socket is closed with 4009 `slow_consumer` and the
# app reconnects with backoff. Dropping frames instead would lose calls and results
# without a word. 32 MiB is four seconds of the sustained rate above, or two frames of
# the largest size.
SEND_QUEUE_FRAMES = 512
SEND_QUEUE_BYTES = 32 * 1024 * 1024


class Bucket:
    """A token bucket: `rate` tokens a second, holding at most `burst`."""

    def __init__(self, rate: float, burst: float, clock=time.monotonic):
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.tokens = burst
        self.at = clock()

    def take(self, n: float = 1.0) -> bool:
        t = self.clock()
        self.tokens = min(self.burst, self.tokens + (t - self.at) * self.rate)
        self.at = t
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


def now() -> int:
    return int(time.time())


@dataclass
class Connection:
    ws: WebSocket
    account_id: str
    key_hash: str
    device_id: str
    name: str
    kind: str
    os: str = ""
    version: str = ""
    actions: list[str] = field(default_factory=list)
    ip: str = ""  # 0.10: where the socket came from, for the operator
    connected_at: int = field(default_factory=now)
    closed: bool = False
    # the outbound queue: send() appends, the writer task drains in order
    queue: deque[str] = field(default_factory=deque)
    queued_bytes: int = 0
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    writer: asyncio.Task | None = None
    overflowed: bool = False  # closed for not reading fast enough
    frames: Bucket = field(default_factory=lambda: Bucket(FRAMES_PER_S, 2 * FRAMES_PER_S))
    bytes: Bucket = field(default_factory=lambda: Bucket(BYTES_PER_S, 2 * BYTES_PER_S))
    dropped: int = 0  # frames dropped in a row for flooding
    warned_at: float = 0.0

    def over_rate(self, size: int) -> bool:
        """Whether this frame is one too many (or too big a stream) for the moment."""
        if self.frames.take() and self.bytes.take(size):
            # the run of drops ends when the sender has eased off, not on the one frame a
            # trickle of refilled tokens lets through mid-flood
            if self.frames.tokens >= self.frames.burst / 2:
                self.dropped = 0
            return False
        self.dropped += 1
        return True

    def brief(self) -> dict:
        return {"id": self.device_id, "name": self.name, "kind": self.kind}

    def start(self) -> None:
        """Begin draining the queue to the socket; called once the connection is attached."""
        self.writer = asyncio.create_task(self._drain(), name=f"hub-writer:{self.device_id[:12]}")

    def stop(self) -> None:
        """No more frames: the writer task ends; what is still queued is dropped (the socket
        is being closed anyway)."""
        self.closed = True
        self.wake.set()
        if self.writer is not None and not self.writer.done():
            self.writer.cancel()

    async def send(self, frame: dict) -> None:
        """Queue a frame for the socket and return at once. Over SEND_QUEUE_FRAMES frames or
        SEND_QUEUE_BYTES bytes waiting, the peer is not reading: the socket is closed with
        4009 and the frame is not kept."""
        if self.closed:
            return
        text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        if len(self.queue) >= SEND_QUEUE_FRAMES or self.queued_bytes + len(text) > SEND_QUEUE_BYTES:
            log.warning(
                "hub: %s is not reading (%d frames, %d bytes waiting); closing", self.device_id[:12], len(self.queue), self.queued_bytes
            )
            self.overflowed = True
            self.stop()
            asyncio.get_running_loop().create_task(self._close(4009, "slow_consumer"))
            return
        self.queue.append(text)
        self.queued_bytes += len(text)
        self.wake.set()

    async def _drain(self) -> None:
        while not self.closed:
            if not self.queue:
                self.wake.clear()
                await self.wake.wait()
                continue
            text = self.queue.popleft()
            self.queued_bytes -= len(text)
            try:
                await self.ws.send_text(text)
            except Exception as e:  # the socket went away under us; the receive loop notices too
                log.debug("send to %s failed: %s", self.device_id[:12], e)
                self.closed = True

    async def _close(self, code: int, reason: str) -> None:
        try:
            await self.ws.close(code=code, reason=reason)
        except Exception as e:  # already gone
            log.debug("close %s: %s", self.device_id[:12], e)


# `to` for a household panic or alert. Anything else addressed here is refused.
HOUSEHOLD_TO = "household"
HOUSEHOLD_ACTIONS = frozenset({"panic", "alert"})


@dataclass
class Pending:
    call_id: str
    caller: str
    target: str
    account_id: str  # the caller's account: that is where the caller socket lives
    started: int = field(default_factory=now)
    # The account the call was delivered to. Same as account_id for a call inside
    # one account. A household panic keys `pending` by this, so the answer finds it.
    target_account: str = ""


class Hub:
    def __init__(self, cloud: Cloud, frame_limit: int = 16 * 1024 * 1024):
        self.cloud = cloud
        self.db: Database = cloud.db
        self.frame_limit = frame_limit
        self.online: dict[str, dict[str, Connection]] = {}
        # keyed (account, call id): ids are the callers' own and only unique within an account
        self.pending: dict[tuple[str, str], Pending] = {}
        # for the operator's self-check: frames dropped and sockets closed for flooding, ever
        self.dropped_total = 0
        self.flood_closes = 0
        # ... and sockets closed because they stopped reading (SEND_QUEUE_*)
        self.slow_closes = 0

    # -- presence -------------------------------------------------------------------

    def online_count(self) -> int:
        """Sockets open right now, across every account (the operator's page)."""
        return sum(len(v) for v in self.online.values())

    def stats(self) -> dict:
        """Aggregates for the operator's self-check: nothing about any one account."""
        return {
            "online": self.online_count(),
            "accounts_online": sum(1 for v in self.online.values() if v),
            "pending_calls": len(self.pending),
            "dropped_frames": self.dropped_total,
            "flood_closes": self.flood_closes,
            "slow_closes": self.slow_closes,
        }

    def devices(self, account_id: str) -> list[dict]:
        """Remembered devices with their presence, then the browser tabs, newest first."""
        live = self.online.get(account_id, {})
        out: list[dict] = []
        seen: set[str] = set()
        for row in self.db.devices_for(account_id):
            c = live.get(row["id"])
            seen.add(row["id"])
            out.append(
                {
                    "id": row["id"],
                    "name": c.name if c else row["name"],
                    "kind": row["kind"],
                    "os": c.os if c else row["os"],
                    "version": c.version if c else row["version"],
                    "actions": c.actions if c else json.loads(row["actions"] or "[]"),
                    "online": c is not None,
                    "last_seen": now() if c else int(row["last_seen"]),
                    "controllable": row["kind"] != "web",
                    "ip": (c.ip if c else "") or row["ip"],
                }
            )
        for c in live.values():
            if c.device_id in seen:
                continue
            out.append(
                {
                    "id": c.device_id,
                    "name": c.name,
                    "kind": c.kind,
                    "os": c.os,
                    "version": c.version,
                    "actions": c.actions,
                    "online": True,
                    "last_seen": now(),
                    "controllable": c.kind != "web",
                    "ip": c.ip,
                }
            )
        out.sort(key=lambda d: (not d["online"], d["kind"] == "web", -d["last_seen"]))
        return out

    async def broadcast_devices(self, account_id: str) -> None:
        conns = list(self.online.get(account_id, {}).values())
        if not conns:
            return
        frame = {"type": "devices", "devices": self.devices(account_id)}
        await asyncio.gather(*(c.send(frame) for c in conns))

    async def broadcast_profile(self, account_id: str, rev: int, device: str) -> None:
        """The account's name or look changed: every connected device of it hears the new
        rev (and who wrote it, so the writer skips its own echo) and fetches the profile."""
        conns = list(self.online.get(account_id, {}).values())
        if not conns:
            return
        frame = {"type": "profile", "rev": rev, "device": device}
        await asyncio.gather(*(c.send(frame) for c in conns))

    async def notify_sync(self, account_id: str, cursor: int, from_device: str) -> None:
        """0.19: a device pushed conversation changes; every *other* connected device of the
        account hears the new cursor and pulls what it is missing (sync.py)."""
        conns = [c for c in self.online.get(account_id, {}).values() if c.device_id != from_device]
        if not conns:
            return
        frame = {"type": "sync", "what": "conversations", "cursor": int(cursor), "from": from_device}
        await asyncio.gather(*(c.send(frame) for c in conns))

    async def notify_working(self, account_id: str, body: dict) -> None:
        """0.20: a device said it is working (or done) on a synced conversation; every *other*
        connected device of the account hears it and shows or clears the line under the
        message. `body` is what sync.Presence.set returned: cid, from, device_name, working, at."""
        conns = [c for c in self.online.get(account_id, {}).values() if c.device_id != str(body.get("from") or "")]
        if not conns:
            return
        frame = {"type": "working", **body}
        await asyncio.gather(*(c.send(frame) for c in conns))

    def forget(self, account_id: str, device_id: str) -> None:
        if device_id in self.online.get(account_id, {}):
            raise CloudError(409, "device_online", "That device is connected right now; sign it out there first")
        self.db.forget_device(account_id, device_id)

    async def drop_account(self, account_id: str) -> None:
        """When an account is deleted or disabled: every socket of it is closed
        (the receive loops then remove themselves) so no device keeps a live line."""
        for c in list(self.online.get(account_id, {}).values()):
            c.stop()
            await c._close(4001, "account_gone")

    async def drop_signed_out(self, account_id: str) -> int:
        """After a sign-out (this device, every other device, one session revoked on the
        account page): every socket of the account whose key no longer opens is closed with
        4001 bad_key, so a device signed out from elsewhere, a lost phone say, loses its live
        line now and not at its next reconnect. Returns how many were closed."""
        n = 0
        for c in list(self.online.get(account_id, {}).values()):
            if self.cloud.key_live(c.key_hash):
                continue
            await c._close(4001, "bad_key")
            # off the account's list now, not when its receive loop notices the close: the
            # other devices hear it left and a call it was carrying out fails as offline
            await self._detach(c)
            n += 1
        return n

    async def drop_all(self, reason: str = "hub_paused") -> int:
        """0.22: the operator turned the hub (or the whole service) off — every socket is
        closed with 4003 and the reason; the apps reconnect with backoff and are refused
        with the same code until it is on again. Returns how many were closed."""
        n = 0
        for conns in list(self.online.values()):
            for c in list(conns.values()):
                c.stop()
                n += 1
                await c._close(4003, reason)
        return n

    # -- one socket ------------------------------------------------------------------

    async def serve(self, ws: WebSocket, caller: Caller | None) -> None:
        """The whole life of one connection: hello, then frames until it drops."""
        await ws.accept()
        try:
            raw = await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S)
        except (TimeoutError, WebSocketDisconnect, KeyError):
            # KeyError: a binary frame — Starlette's receive_text has no text to give
            await self._close(ws, 4000, "hello expected")
            return
        hello = _loads(raw)
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await self._close(ws, 4000, "hello expected")
            return
        if caller is None:
            try:
                caller = self.cloud.authenticate(str(hello.get("key") or ""))
            except CloudError as e:
                await ws.send_text(json.dumps({"type": "error", "code": e.code, "message": e.message}))
                await self._close(ws, 4001, e.code)
                return
        dev = hello.get("device") if isinstance(hello.get("device"), dict) else {}
        try:
            conn = self._connection(ws, caller, dev)
        except CloudError as e:
            await ws.send_text(json.dumps({"type": "error", "code": e.code, "message": e.message}))
            await self._close(ws, 4002, e.code)
            return

        await self._attach(conn)
        try:
            await conn.send(
                {
                    "type": "welcome",
                    "device_id": conn.device_id,
                    "devices": self.devices(conn.account_id),
                    "server": {"version": __version__, "frame_limit": self.frame_limit, "time": now()},
                }
            )
            await self.broadcast_devices(conn.account_id)
            while True:
                try:
                    raw = await ws.receive_text()
                except (WebSocketDisconnect, RuntimeError):
                    # RuntimeError: Starlette's WebSocketDisconnected, when the socket was closed
                    # under this loop (the same device connected again and took its place)
                    break
                except KeyError:
                    # a binary frame: Starlette's receive_text has no text to give. The protocol
                    # is JSON text (docs/hub.md); below it is answered with bad_frame and the
                    # socket stays open — it still counts towards the flood limit.
                    raw = None
                if raw is not None and len(raw) > self.frame_limit:
                    await conn.send({"type": "error", "code": "too_large", "message": f"Frames are capped at {self.frame_limit} bytes"})
                    continue
                if conn.over_rate(len(raw or "")):
                    self.dropped_total += 1
                    if conn.dropped >= FLOOD_CLOSE_AFTER:
                        self.flood_closes += 1
                        log.warning("hub: %s flooded the hub; closing", conn.device_id[:12])
                        await self._close(ws, 4008, "too many frames")
                        break
                    t = time.monotonic()
                    if t - conn.warned_at >= 1.0:
                        conn.warned_at = t
                        await conn.send(
                            {
                                "type": "error",
                                "code": "rate_limited",
                                "message": f"At most {FRAMES_PER_S:.0f} frames and {BYTES_PER_S / 1048576:.0f} MB a second; this frame was dropped",
                            }
                        )
                    continue
                if raw is None:
                    await conn.send({"type": "error", "code": "bad_frame", "message": "Frames are JSON text, not binary"})
                    continue
                frame = _loads(raw)
                if not isinstance(frame, dict):
                    await conn.send({"type": "error", "code": "bad_frame", "message": "Frames are JSON objects"})
                    continue
                await self.route(conn, frame)
        finally:
            await self._detach(conn)

    def _connection(self, ws: WebSocket, caller: Caller, dev: dict) -> Connection:
        kind = str(dev.get("kind") or "")
        if kind not in KINDS:
            raise CloudError(400, "bad_device", "kind must be phone, computer or web")
        device_id = str(dev.get("id") or "")
        if kind == "web" and not device_id:
            device_id = "web-" + uuid.uuid4().hex[:12]
        if not _ID.match(device_id):
            raise CloudError(400, "bad_device", "device id: 4–80 letters, digits, . _ : -")
        name = str(dev.get("name") or "").strip()[:60] or {"phone": "Phone", "computer": "Computer", "web": "Browser"}[kind]
        actions = [str(a)[:40] for a in dev.get("actions") or [] if isinstance(a, str)][:64]
        return Connection(
            ws=ws,
            account_id=caller.account_id,
            key_hash=caller.key_hash,
            device_id=device_id,
            name=name,
            kind=kind,
            os=str(dev.get("os") or "")[:60],
            version=str(dev.get("version") or "")[:40],
            actions=actions,
            ip=_client.current().ip,
        )

    async def _attach(self, conn: Connection) -> None:
        account = self.online.setdefault(conn.account_id, {})
        old = account.get(conn.device_id)
        if old is not None:
            old.stop()
            await self._close(old.ws, 4003, "replaced by a newer connection")
        account[conn.device_id] = conn
        conn.start()
        if conn.kind != "web":
            self.db.upsert_device(conn.account_id, conn.device_id, conn.name, conn.kind, conn.os, conn.version, json.dumps(conn.actions))
        # the id and the account masked like every other line; the device's name is the
        # person's own words and stays out of the log
        log.info("hub: %s %s connected; account %s has %d online", conn.kind, conn.device_id[:12], conn.account_id[:8], len(account))

    async def _detach(self, conn: Connection) -> None:
        conn.stop()
        if conn.overflowed:
            self.slow_closes += 1
        account = self.online.get(conn.account_id, {})
        if account.get(conn.device_id) is conn:
            del account[conn.device_id]
            if not account:
                self.online.pop(conn.account_id, None)
            if conn.kind != "web":
                self.db.touch_device(conn.account_id, conn.device_id)
            log.info("hub: %s %s left; account %s", conn.kind, conn.device_id[:12], conn.account_id[:8])
            await self._fail_pending_for(conn)
            await self.broadcast_devices(conn.account_id)

    async def _fail_pending_for(self, gone: Connection) -> None:
        """A device left: every call it was carrying out is answered `device_offline`; calls it made are dropped.

        A household panic is carried by another account, so the target's account
        is checked as well as the caller's.
        """
        for key, p in list(self.pending.items()):
            target_account = p.target_account or p.account_id
            if target_account == gone.account_id and p.target == gone.device_id:
                del self.pending[key]
                caller = self.online.get(p.account_id, {}).get(p.caller)
                if caller:
                    await caller.send(
                        {
                            "type": "error",
                            "id": p.call_id,
                            "code": "device_offline",
                            "message": f"{gone.name} disconnected before answering",
                        }
                    )
            elif p.account_id == gone.account_id and p.caller == gone.device_id:
                del self.pending[key]

    @staticmethod
    async def _close(ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass

    # -- routing -----------------------------------------------------------------------

    async def route(self, conn: Connection, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "ping":
            await conn.send({"type": "pong", "time": now()})
            # every client pings; a caller with one call outstanding and nothing more to
            # ask would otherwise never hear `timeout` (the sweep ran on new calls only)
            await self._sweep()
        elif kind == "devices":
            await conn.send({"type": "devices", "devices": self.devices(conn.account_id)})
        elif kind == "call":
            await self._call(conn, frame)
        elif kind in ("result", "event"):
            await self._answer(conn, frame)
        elif kind == "rename":
            name = str(frame.get("name") or "").strip()[:60]
            if name:
                conn.name = name
                if conn.kind != "web":
                    self.db.upsert_device(conn.account_id, conn.device_id, name, conn.kind, conn.os, conn.version, json.dumps(conn.actions))
                await self.broadcast_devices(conn.account_id)
        elif kind == "forget":
            try:
                self.forget(conn.account_id, str(frame.get("device_id") or ""))
            except CloudError as e:
                await conn.send({"type": "error", "code": e.code, "message": e.message})
                return
            await self.broadcast_devices(conn.account_id)
        else:
            await conn.send({"type": "error", "code": "bad_frame", "message": f"Unknown frame type {kind!r}"})

    async def _call(self, conn: Connection, frame: dict) -> None:
        call_id = str(frame.get("id") or "")
        to = str(frame.get("to") or "")
        action = str(frame.get("action") or "")
        if not call_id or not to or not action:
            await conn.send({"type": "error", "id": call_id, "code": "bad_frame", "message": "call needs id, to and action"})
            return
        if to == HOUSEHOLD_TO:
            await self._household_call(conn, call_id, action, frame)
            return
        target = self.online.get(conn.account_id, {}).get(to)
        if target is not None and not target.closed:
            await self._deliver_same(conn, call_id, action, frame, target)
            return
        foreign = self._device_elsewhere(to, conn.account_id)
        if foreign is not None:
            await self._cross_to_device(conn, call_id, action, frame, foreign)
            return
        await conn.send({"type": "error", "id": call_id, "code": "device_offline", "message": "That device is not connected"})

    async def _deliver_same(self, conn: Connection, call_id: str, action: str, frame: dict, target: Connection) -> None:
        """A call that stays inside one account. The frame on the wire is unchanged."""
        if target.kind == "web":
            await conn.send(
                {"type": "error", "id": call_id, "code": "not_controllable", "message": "A browser tab cannot be asked to do things"}
            )
            return
        if target is conn:
            await conn.send({"type": "error", "id": call_id, "code": "self_call", "message": "That is this device"})
            return
        await self._sweep()
        self.pending[(conn.account_id, call_id)] = Pending(
            call_id=call_id,
            caller=conn.device_id,
            target=target.device_id,
            account_id=conn.account_id,
            target_account=conn.account_id,
        )
        args = frame.get("args") if isinstance(frame.get("args"), dict) else {}
        await target.send({"type": "call", "id": call_id, "from": conn.brief(), "action": action, "args": args})

    async def _household_call(self, conn: Connection, call_id: str, action: str, frame: dict) -> None:
        """`to` is `household`: a panic or an alert for a linked account, and nothing else."""
        if action not in HOUSEHOLD_ACTIONS:
            await self._not_allowed(conn, call_id)
            return
        partners = self.cloud.household_partner_ids(conn.account_id)
        if not partners:
            await self._not_linked(conn, call_id)
            return
        target = self._newest_partner_device(partners)
        if target is None:
            await conn.send({"type": "error", "id": call_id, "code": "device_offline", "message": "The other phone is not connected"})
            return
        await self._deliver_household(conn, call_id, action, frame, target)

    async def _cross_to_device(self, conn: Connection, call_id: str, action: str, frame: dict, foreign: Connection) -> None:
        """A device id only addresses the caller's own account.

        The one exception is a panic or an alert to a linked partner's phone.
        Anything else is answered `device_offline`, the same as a device that is
        not there, and nothing is delivered: another account's id is not confirmed.
        """
        if action not in HOUSEHOLD_ACTIONS or foreign.account_id not in self.cloud.household_partner_ids(conn.account_id):
            await conn.send({"type": "error", "id": call_id, "code": "device_offline", "message": "That device is not connected"})
            return
        if foreign.kind == "web":
            await conn.send(
                {"type": "error", "id": call_id, "code": "not_controllable", "message": "A browser tab cannot be asked to do things"}
            )
            return
        await self._deliver_household(conn, call_id, action, frame, foreign)

    async def _deliver_household(self, conn: Connection, call_id: str, action: str, frame: dict, target: Connection) -> None:
        await self._sweep()
        self.pending[(target.account_id, call_id)] = Pending(
            call_id=call_id,
            caller=conn.device_id,
            target=target.device_id,
            account_id=conn.account_id,
            target_account=target.account_id,
        )
        args = frame.get("args") if isinstance(frame.get("args"), dict) else {}
        sender = conn.brief()
        who = self.cloud.account_label(conn.account_id)
        if who:
            sender["who"] = who
        await target.send({"type": "call", "id": call_id, "from": sender, "action": action, "args": args})

    def _device_elsewhere(self, device_id: str, caller_account: str) -> Connection | None:
        for account_id, devices in self.online.items():
            if account_id == caller_account:
                continue
            conn = devices.get(device_id)
            if conn is not None and not conn.closed:
                return conn
        return None

    def _newest_partner_device(self, partner_ids: list[str]) -> Connection | None:
        best: Connection | None = None
        for pid in partner_ids:
            for conn in self.online.get(pid, {}).values():
                if conn.closed or conn.kind == "web":
                    continue
                if best is None or conn.connected_at >= best.connected_at:
                    best = conn
        return best

    async def _not_allowed(self, conn: Connection, call_id: str) -> None:
        await conn.send(
            {
                "type": "error",
                "id": call_id,
                "code": "not_allowed",
                "message": "Only a panic or an alert can cross accounts",
            }
        )

    async def _not_linked(self, conn: Connection, call_id: str) -> None:
        await conn.send(
            {
                "type": "error",
                "id": call_id,
                "code": "not_linked",
                "message": "This account is not linked to another",
            }
        )

    async def _answer(self, conn: Connection, frame: dict) -> None:
        call_id = str(frame.get("id") or "")
        p = self.pending.get((conn.account_id, call_id))
        if p is None or p.target != conn.device_id:
            # Unknown or not this device's call: nothing to deliver to. Tell the sender once.
            await conn.send(
                {"type": "error", "id": call_id, "code": "unknown_call", "message": "No call with that id is waiting on this device"}
            )
            return
        caller = self.online.get(p.account_id, {}).get(p.caller)
        if frame.get("type") == "result":
            del self.pending[(conn.account_id, call_id)]
        if caller is None:
            return
        out = {k: v for k, v in frame.items() if k in ("type", "id", "ok", "body", "error", "message")}
        out["from"] = conn.brief()
        await caller.send(out)

    async def _sweep(self) -> None:
        """Calls nobody answered in time are dropped, and the caller hears so instead of waiting for its own clock."""
        cutoff = now() - CALL_TTL_S
        for key, p in list(self.pending.items()):
            if p.started < cutoff:
                del self.pending[key]
                caller = self.online.get(p.account_id, {}).get(p.caller)
                if caller:
                    await caller.send({"type": "error", "id": p.call_id, "code": "timeout", "message": "the device did not answer in time"})


def _loads(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return None
