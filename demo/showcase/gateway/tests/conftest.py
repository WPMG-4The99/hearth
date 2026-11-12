from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

import httpx
import pytest

from showcase_gateway.app import create_app
from showcase_gateway.config import Lane, Settings
from showcase_gateway.sessions import SessionManager
from showcase_gateway.trials import TrialManager, TrialStore


class FakeRunner:
    """Containers that exist only as names; every one comes up at 10.0.0.<n>."""

    def __init__(self) -> None:
        self.running: dict[str, dict[str, str]] = {}
        self.stopped: list[str] = []

    async def start(self, name: str, env: dict[str, str]) -> str:
        self.running[name] = env
        return f"10.0.0.{len(self.running) + 1}"

    async def stop(self, name: str) -> None:
        self.running.pop(name, None)
        self.stopped.append(name)

    async def leftovers(self) -> list[str]:
        return []

    async def gateway_address(self) -> str | None:
        return "10.0.0.1"

    # kept containers (nanoMuse Web): created once, then stopped and started by name
    @property
    def kept(self) -> dict[str, dict]:
        if not hasattr(self, "_kept"):
            self._kept: dict[str, dict] = {}
        return self._kept

    async def start_persistent(self, name, env, *, volumes, network, memory, cpus, pids, image):
        c = self.kept.get(name)
        if c is None:
            c = self.kept[name] = {
                "env": env,
                "volumes": volumes,
                "network": network,
                "image": image,
                "address": f"10.0.1.{len(self.kept) + 1}",
                "starts": 0,
            }
        c["running"] = True
        c["starts"] += 1
        return c["address"]

    async def stop_only(self, name) -> None:
        if name in self.kept:
            self.kept[name]["running"] = False

    async def remove(self, name) -> None:
        self.kept.pop(name, None)

    async def exists(self, name) -> bool:
        return name in self.kept

    async def address_of(self, name) -> str | None:
        c = self.kept.get(name)
        return c["address"] if c and c.get("running") else None


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


def make_settings(**over: Any) -> Settings:
    base = Settings.from_env()
    values: dict[str, Any] = dict(
        public_scheme="http",
        site_host="localhost",
        session_domain="s.localhost",
        public_port=":8000",
        trust_proxy=True,
        # the test clients connect as 127.0.0.1 (httpx) or "testclient" (Starlette)
        trusted_proxies=("127.0.0.1", "testclient"),
        site_dir="",
        cdn_dir="",
        image="nanomuse:test",
        internal_url="",
        session_ttl_s=600,
        idle_ttl_s=120,
        start_timeout_s=5,
        max_sessions=3,
        per_ip_active=1,
        per_ip_daily=3,
        main=Lane("openai", "demo-model", "https://models.example", "sk-demo"),
        gui=Lane("openai", "gui-model", "https://gui.example", "sk-gui"),
        session_requests=3,
        session_tokens=1000,
        daily_requests=100,
        daily_tokens=100_000,
        byok_hosts=("models.example", "byok.example", "localhost"),
        image_model="qwen-image-3.0",
        image_api_key="sk-draw",
        image_base_url="https://draw.example/api/v1",
        image_per_session=2,
        daily_images=3,
        video_model="wan2.2-i2v-flash",
        video_api_key="sk-draw",
        video_base_url="https://draw.example/api/v1",
        clips_per_session=2,
        daily_clips=3,
        trial_enabled=True,
        trial_db=":memory:",
        trial_tokens=1000,
        trial_per_ip_daily=2,
        trial_daily_new=3,
        trial_daily_tokens=100_000,
        trial_rpm=5,
        web_enabled=True,
        web_relay_url="https://cloud.example",
        web_relay_internal_url="http://relay:8787",
        web_network="web-net",
        web_db=":memory:",
        web_image="nanomuse:web",
        web_max_accounts=2,
        web_max_running=1,
        web_idle_stop_s=3600,
        web_key_ttl_s=7 * 86400,
        demo_signin_required=False,
        per_account_active=1,
        per_account_daily=2,
        visitor_db=":memory:",
        visitor_ttl_s=3600,
    )
    values.update(over)
    return replace(base, **values)


class Wire(httpx.AsyncByteStream):
    """A response body that is still on the wire, as the gateway sees real ones."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        yield self.data


def wire(status: int, text: str, **headers: str) -> httpx.Response:
    return httpx.Response(status, stream=Wire(text.encode()), headers=headers)


class Upstream:
    """Stands in for the containers (``/api/health``, the app) and the model providers."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.usage_total = 10
        self.stream = False
        self.codes: dict[str, str] = {}  # the relay's: identifier → code
        self.keys_issued = 0
        self.session_keys = 0
        self.revoked: list[str] = []  # keys signed out again (the visitors' sign-in does)
        self.invites: list[str] = []  # the invite field of each verify, "" when none
        self.drawn: list[dict] = []  # Model Studio's picture requests
        self.draw_busy = 0  # how many 429s the drawing endpoint answers first
        self.draw_refusal: tuple[int, str] | None = None  # a status and body to answer instead
        self.animated: list[httpx.Request] = []  # Model Studio's video tasks, as submitted
        self.down = False  # the containers do not answer (a session whose runtime died)
        self.polls: dict[str, int] = {}  # task id → how often it was asked about
        self.animate_busy = 0  # how many 429s the video endpoint answers first

    def relay(self, request: httpx.Request) -> httpx.Response:
        """A little nanoMuse Cloud: any identifier gets the code 246810."""
        data = json.loads(request.content or b"{}")
        ident = str(data.get("identifier", "")).lower()
        if request.url.path == "/v1/auth/code":
            if "@" not in ident and not ident.isdigit():
                return wire(
                    400,
                    json.dumps(
                        {
                            "error": {
                                "code": "bad_identifier",
                                "message": "Enter a mobile number or an e-mail address",
                            }
                        }
                    ),
                )
            self.codes[ident] = "246810"
            return httpx.Response(204)
        if request.url.path == "/v1/auth/sign-out":
            auth = request.headers.get("authorization", "")
            self.revoked.append(auth[7:] if auth.lower().startswith("bearer ") else "")
            return httpx.Response(204)
        if request.url.path == "/v1/auth/session-key":
            # a key that lapses on its own, for the key in the header (0.13 relays)
            auth = request.headers.get("authorization", "")
            standing = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not standing.startswith("nm_key") or standing in self.revoked:
                return wire(401, json.dumps({"error": {"code": "bad_key", "message": "No."}}))
            self.session_keys += 1
            return wire(
                200,
                json.dumps(
                    {
                        "api_key": f"nm_sess{self.session_keys}",
                        "expires_at": int(time.time()) + int(data.get("ttl_s") or 0),
                    }
                ),
                **{"content-type": "application/json"},
            )
        if request.url.path == "/v1/auth/verify":
            if self.codes.get(ident) != data.get("code"):
                return wire(
                    400,
                    json.dumps(
                        {"error": {"code": "code_wrong", "message": "That code is not right"}}
                    ),
                )
        if request.url.path == "/v1/auth/verify":
            self.keys_issued += 1
            self.invites.append(str(data.get("invite") or ""))
            account = {
                "id": "acct-" + ident.replace("@", "-at-"),
                "channel": "email" if "@" in ident else "sms",
                "hint": ident[:2] + "…",
                "created_at": 1_700_000_000,
                "member": False,
            }
            return wire(
                200,
                json.dumps(
                    {
                        "api_key": f"nm_key{self.keys_issued}",
                        "created": self.keys_issued == 1,
                        "account": account,
                    }
                ),
                **{"content-type": "application/json"},
            )
        return wire(404, "{}")

    def draw(self, request: httpx.Request) -> httpx.Response:
        """A little Model Studio: every picture is at its storage, as a URL; a video task is
        pending the first time it is asked about and done the second."""
        if request.url.path.endswith("/services/aigc/video-generation/video-synthesis"):
            self.animated.append(request)
            if self.animate_busy:
                self.animate_busy -= 1
                return wire(429, json.dumps({"message": "Throttling.RateQuota"}))
            task = f"task-{len(self.animated)}"
            return wire(
                200,
                json.dumps({"output": {"task_status": "PENDING", "task_id": task}}),
                **{"content-type": "application/json"},
            )
        if "/api/v1/tasks/" in request.url.path:
            task = request.url.path.rsplit("/", 1)[-1]
            self.polls[task] = self.polls.get(task, 0) + 1
            out: dict = {"task_id": task, "task_status": "RUNNING"}
            if self.polls[task] >= 2:
                out = {
                    "task_id": task,
                    "task_status": "SUCCEEDED",
                    "video_url": f"https://oss.draw.example/v/{task}.mp4",
                }
            return wire(200, json.dumps({"output": out}), **{"content-type": "application/json"})
        if not request.url.path.endswith("/services/aigc/multimodal-generation/generation"):
            return wire(404, "{}")
        self.drawn.append(json.loads(request.content))
        if self.draw_refusal is not None:
            status, text = self.draw_refusal
            return wire(status, text, **{"content-type": "application/json"})
        if self.draw_busy:
            self.draw_busy -= 1
            return wire(429, json.dumps({"message": "Throttling.RateQuota"}))
        return wire(
            200,
            json.dumps(
                {
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "content": [{"image": "https://oss.draw.example/p/1.png"}]
                                }
                            }
                        ]
                    }
                }
            ),
            **{"content-type": "application/json"},
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/api/health":
            return wire(200, '{"ok": true}', **{"content-type": "application/json"})
        if request.url.host.startswith("10.0."):
            if self.down:
                raise httpx.ConnectError("refused", request=request)
            return wire(200, f"container says {request.url.path}", **{"x-upstream": "yes"})
        if request.url.host == "cloud.example":
            return self.relay(request)
        if request.url.host == "draw.example":
            return self.draw(request)
        if request.url.host == "oss.draw.example":
            if request.url.path.endswith(".mp4"):
                return wire(200, "MP4BYTES", **{"content-type": "video/mp4"})
            return wire(200, "PNGBYTES", **{"content-type": "image/png"})
        # a model provider
        if self.stream:
            body = (
                'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                f'data: {{"choices":[],"usage":{{"total_tokens":{self.usage_total}}}}}\n\n'
                "data: [DONE]\n\n"
            )
            return wire(200, body, **{"content-type": "text/event-stream"})
        return wire(
            200,
            json.dumps(
                {
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"total_tokens": self.usage_total},
                }
            ),
            **{"content-type": "application/json"},
        )


@pytest.fixture
def world():
    settings = make_settings()
    runner = FakeRunner()
    upstream = Upstream()
    clock = Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    manager = SessionManager(settings, runner, http=client, clock=clock)
    manager.resolve = lambda host, port: ["93.184.216.34"]
    trials = TrialManager(settings, TrialStore(":memory:"), clock=clock)
    app = create_app(settings, manager, client=client, trials=trials)
    return settings, runner, upstream, clock, manager, app


def body(resp: httpx.Response) -> dict:
    return json.loads(resp.content)
