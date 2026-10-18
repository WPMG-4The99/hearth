"""When the app may ask for a star on GitHub — the policy the relay serves (contract C1).

The moments used to be written into each client. Now the relay says (``GET /v1/nudges``,
and the ``nudges`` field of ``/v1/me``), the operator changes them on the admin page, and
every client follows within a day without a release. This module is the runtime's side of
it: fetch at most once per 24 h, take the copy that rides along with a sign-in, keep the
last good copy on disk under the data directory, and fall back to :data:`DEFAULT_NUDGES`
— the same object the relay ships — when nothing was ever fetched. ``GET /api/nudges``
hands the web app what is current; the web keeps the ledger (how many tasks, which asks
were shown) in its own storage, so this module decides nothing about *showing* an ask.

A person on their own key, without an account, still asks the default relay once a day
(``cloud.base_url``, which is the public relay unless they pointed it elsewhere); a relay
that cannot be reached, or an older one without the route, changes nothing.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import httpx

from nanomuse import __version__
from nanomuse.logger import logger

DEFAULT_NUDGES: dict[str, Any] = {
    "version": 1,
    "star": {
        "enabled": True,
        "url": "https://github.com/WPMG-4The99/hearth",
        "moments": {
            "signed_in": True,
            "tasks": [3, 10, 30],
            "new_look": True,
            "exhausted": True,
            "days_used": [7, 30],
            "goal_done": True,
        },
        "cooldown_days": 7,
        "max_asks": 4,
        # the star card's sentence from the relay (English, and Chinese for a Chinese UI);
        # empty: each client's own sentence for the moment
        "text": "",
        "text_zh": "",
    },
}
# the card's sentence is at most this long; anything longer is dropped
TEXT_MAX = 200

REFRESH_S = 24 * 3600
TIMEOUT_S = 6.0
FILE_NAME = "nudges.json"

_BOOL_MOMENTS = ("signed_in", "new_look", "exhausted", "goal_done")
_LIST_MOMENTS = ("tasks", "days_used")


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_NUDGES)


def _as_bool(value: Any, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _as_int(value: Any, low: int, high: int, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return fallback
    n = int(value)
    return n if low <= n <= high else fallback


def _as_int_list(value: Any, fallback: list[int]) -> list[int]:
    if not isinstance(value, list):
        return fallback
    out: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return fallback
        n = int(item)
        if n < 1:
            return fallback
        out.add(n)
    return sorted(out)


def normalize(body: Any) -> dict[str, Any]:
    """A policy as the client keeps it: the relay's values where they make sense, the
    defaults where they do not, unknown keys dropped. Never raises — a relay that says
    something odd changes only the field it got wrong."""
    out = defaults()
    if not isinstance(body, dict):
        return out
    out["version"] = _as_int(body.get("version"), 1, 1_000_000_000, 1)
    star_in = body.get("star")
    if not isinstance(star_in, dict):
        return out
    star = out["star"]
    star["enabled"] = _as_bool(star_in.get("enabled"), star["enabled"])
    url = star_in.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")) and len(url) <= 200:
        star["url"] = url
    star["cooldown_days"] = _as_int(star_in.get("cooldown_days"), 0, 365, star["cooldown_days"])
    star["max_asks"] = _as_int(star_in.get("max_asks"), 0, 50, star["max_asks"])
    for name in ("text", "text_zh"):
        text = star_in.get(name)
        star[name] = text.strip() if isinstance(text, str) and len(text.strip()) <= TEXT_MAX else ""
    moments_in = star_in.get("moments")
    if isinstance(moments_in, dict):
        moments = star["moments"]
        for name in _BOOL_MOMENTS:
            moments[name] = _as_bool(moments_in.get(name), moments[name])
        for name in _LIST_MOMENTS:
            moments[name] = _as_int_list(moments_in.get(name), moments[name])
    return out


class NudgesPolicy:
    """The policy in force on this device, with when and where it came from.

    ``client`` lets a test hand in an ``httpx.AsyncClient`` over a mock transport; without
    one a short-lived client is made per fetch (one request a day does not need a pool).
    """

    def __init__(
        self,
        data_dir: Path,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock=time.time,  # noqa: ANN001
    ) -> None:
        self.path = data_dir / FILE_NAME
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._clock = clock
        self.policy: dict[str, Any] = defaults()
        # when the copy in force came in, and when the relay was last asked (a failed try
        # counts: one request a day is the contract, whatever it answered)
        self.fetched_at: float | None = None
        self.tried_at: float | None = None
        self.source: str = "default"
        self.error: str | None = None
        self._load()

    # ------------------------------------------------------------------ disk
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.policy = normalize(data.get("policy"))
        at = data.get("fetched_at")
        self.fetched_at = float(at) if isinstance(at, int | float) else None
        tried = data.get("tried_at")
        self.tried_at = float(tried) if isinstance(tried, int | float) else self.fetched_at
        self.source = str(data.get("source") or ("relay" if self.fetched_at else "default"))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "policy": self.policy,
                        "fetched_at": self.fetched_at,
                        "tried_at": self.tried_at,
                        "source": self.source,
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        except OSError as exc:  # pragma: no cover - a read-only data dir
            logger.debug("nudges: could not save: {}", exc)

    # ------------------------------------------------------------------ reading
    @property
    def stale(self) -> bool:
        """The copy in force is older than a day (or there is none but the defaults)."""
        return self.fetched_at is None or self._clock() - self.fetched_at >= REFRESH_S

    @property
    def due(self) -> bool:
        """The relay may be asked: not within the last day, whatever it answered then."""
        return self.tried_at is None or self._clock() - self.tried_at >= REFRESH_S

    def view(self) -> dict[str, Any]:
        """What ``GET /api/nudges`` answers: the policy, and how fresh it is."""
        return {
            "policy": copy.deepcopy(self.policy),
            "fetched_at": self.fetched_at,
            "source": self.source,
            "stale": self.stale,
            "error": self.error,
        }

    # ------------------------------------------------------------------ writing
    def take(self, body: Any, source: str = "me") -> bool:
        """A copy that rode along with ``/v1/me`` (a sign-in, an account refresh): kept as
        today's, so no separate fetch is due. Anything that is not a policy is ignored."""
        if not isinstance(body, dict) or not isinstance(body.get("star"), dict):
            return False
        self.policy = normalize(body)
        self.fetched_at = self.tried_at = self._clock()
        self.source = source
        self.error = None
        self._save()
        return True

    async def refresh(self, force: bool = False) -> dict[str, Any]:
        """Fetch ``GET <relay>/v1/nudges`` when the copy is older than a day (or ``force``);
        a relay that cannot be reached, or an older one without the route, leaves the last
        good copy (or the defaults) in place and says so in ``error``."""
        if not force and not self.due:
            return self.view()
        if not self.base_url:
            return self.view()
        self.tried_at = self._clock()
        url = self.base_url + "/v1/nudges"
        headers = {"User-Agent": f"nanoMuse/{__version__}", "Accept": "application/json"}
        try:
            if self._client is not None:
                r = await self._client.get(url, headers=headers, timeout=TIMEOUT_S)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as c:
                    r = await c.get(url, headers=headers)
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict) or not isinstance(body.get("star"), dict):
                raise ValueError("not a nudges policy")
        except Exception as exc:  # noqa: BLE001 — the answer is "the last copy", never a failure
            self.error = exc.__class__.__name__
            logger.debug("nudges: fetch failed: {}", exc)
            self._save()  # the try is remembered: not again before another day
            return self.view()
        self.policy = normalize(body)
        self.fetched_at = self._clock()
        self.source = "relay"
        self.error = None
        self._save()
        return self.view()


__all__ = ["DEFAULT_NUDGES", "REFRESH_S", "TEXT_MAX", "NudgesPolicy", "defaults", "normalize"]
