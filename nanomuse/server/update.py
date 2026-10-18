"""Is there a newer release? GitHub Releases for WPMG-4The99/hearth, kept for a day, a
failed check for an hour (contract C2: every client shows the installed and the latest
version).

Nothing is downloaded and nothing about the install is sent: the request is the one a
browser makes for ``api.github.com/repos/WPMG-4The99/hearth/releases/latest``. The check
is off when ``server.update_check`` is false, when ``NANOMUSE_NO_UPDATE_CHECK`` is set,
and in a hosted web session (the operator updates those, not the person using them:
those runtimes are the ones handed a ``NANOMUSE_CLOUD_KEY``). The desktop app and the
Android app have checks of their own; this one is for ``pipx`` / Docker installs, which
show it under *Settings → About* with a "Check now" when the answer is stale or failed.
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from nanomuse import __version__
from nanomuse.logger import logger

DOWNLOAD_PAGE = "https://github.com/WPMG-4The99/hearth/releases"
RELEASES_API = "https://api.github.com/repos/WPMG-4The99/hearth/releases/latest"
RELEASES_PAGE = "https://github.com/WPMG-4The99/hearth/releases/latest"
CACHE_S = 24 * 3600
# a check that failed (the runtime came up before the network did) is tried again sooner
RETRY_S = 3600
TIMEOUT_S = 6.0


def parse_version(text: str) -> tuple[int, ...]:
    """``v0.1.21`` → ``(0, 1, 21)``; anything after the numbers (``-rc1``) is ignored,
    a string without numbers is ``()`` and therefore older than everything."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def newer_than(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a) and a > b


def enabled(setting: bool) -> bool:
    if not setting or os.environ.get("NANOMUSE_NO_UPDATE_CHECK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    return not os.environ.get("NANOMUSE_CLOUD_KEY")


class UpdateCheck:
    """The cached answer; :meth:`view` is what ``GET /api/update`` returns."""

    def __init__(self, setting: bool = True, *, current: str = __version__) -> None:
        self.setting = setting
        self.current = current
        self.latest: str | None = None
        self.url: str = RELEASES_PAGE
        self.download_url: str = DOWNLOAD_PAGE
        # monotonic, for the cache; the wall clock, for "checked at" on the About page
        self.checked_at: float | None = None
        self.checked_wall: str | None = None
        self.source: str | None = None
        self.error: str | None = None

    async def view(self, force: bool = False) -> dict[str, Any]:
        if not enabled(self.setting):
            return {
                "current": self.current,
                "enabled": False,
                "latest": None,
                "newer": False,
                "url": self.url,
                "download_url": self.download_url,
                "checked_at": None,
                "source": None,
                "error": None,
            }
        if force or self.checked_at is None or self._stale():
            await self._check()
        return {
            "current": self.current,
            "enabled": True,
            "latest": self.latest,
            "newer": bool(self.latest and newer_than(self.latest, self.current)),
            "url": self.url,
            "download_url": self.download_url,
            "checked_at": self.checked_wall,
            "source": self.source,
            "error": self.error,
        }

    def _stale(self) -> bool:
        """A good answer is kept for a day; a failed one for an hour, so a runtime that
        started before its network was up does not say "could not check" all day."""
        if self.checked_at is None:
            return True
        age = time.monotonic() - self.checked_at
        return age > (RETRY_S if self.error else CACHE_S)

    async def _check(self) -> None:
        self.checked_at = time.monotonic()
        self.checked_wall = datetime.now(UTC).isoformat(timespec="seconds")
        headers = {
            "User-Agent": f"nanoMuse/{self.current}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.get(RELEASES_API, headers=headers, timeout=TIMEOUT_S)
                r.raise_for_status()
                data = r.json()
                tag = str(data.get("tag_name") or "")
                if not parse_version(tag):
                    raise ValueError(f"unexpected tag {tag!r}")
                self._found(tag, str(data.get("html_url") or RELEASES_PAGE), "github")
                return
        except Exception as exc:  # noqa: BLE001 — the answer is "could not check", never a failure of the app
            logger.debug("update check (github) failed: {}", exc)
            self.error = exc.__class__.__name__

    def _found(self, tag: str, url: str, source: str) -> None:
        self.latest = tag.lstrip("v")
        self.url = url
        self.source = source
        self.error = None
