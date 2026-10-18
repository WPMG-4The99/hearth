"""The nudges policy (relay 0.18): when the apps may ask for a star on GitHub.

The apps used to carry the moments in their code — after the first task, after the tenth —
and changing them meant a release on every platform. Now the relay says: ``GET /v1/nudges``
(public, cached an hour) and the ``nudges`` field of ``/v1/me``. The operator edits it on the
admin page (``PUT /v1/admin/nudges``); it lives in the ``settings`` table under ``nudges``.
Every client keeps :data:`DEFAULT_NUDGES` built in for when the relay cannot be reached.

What the fields mean (the same on every platform):

* ``star.enabled`` — false: no asks at all (the "Star on GitHub" rows in Settings stay).
* ``star.moments.tasks`` — ask when the person's finished-task count reaches each figure.
  A task is a turn the person started that ended in a reply; the first conversation and
  background runs never count.
* ``star.moments.days_used`` — ask on the n-th distinct day the app was opened.
* ``signed_in`` / ``new_look`` / ``exhausted`` / ``goal_done`` — the moments by name.
* ``star.cooldown_days`` — at least that many days between two asks of any kind.
* ``star.max_asks`` — lifetime cap of asks per device ("Not now" counts; starring ends them).
* ``star.text`` / ``star.text_zh`` — the sentence on the card, English and 简体中文, at most
  :data:`TEXT_MAX` code points each, empty by default. A Chinese UI takes ``text_zh`` when it
  is set, else ``text``; any other UI takes ``text``; an empty value means the app's own
  sentence. Only the body changes — title and buttons stay the app's. Apps built against
  relay 0.22 and earlier ignore both.
"""

from __future__ import annotations

import copy
from typing import Any

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
        "text": "",
        "text_zh": "",
    },
}

_BOOL_MOMENTS = ("signed_in", "new_look", "exhausted", "goal_done")
_LIST_MOMENTS = ("tasks", "days_used")
_TEXTS = ("text", "text_zh")
URL_MAX = 200
TEXT_MAX = 200
COOLDOWN_MAX = 365
MAX_ASKS_MAX = 50


class BadNudges(ValueError):
    """The body is not a policy; ``str(e)`` is the plain message for the 400."""


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_NUDGES)


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "on", "off", "1", "0"):
        return value.strip().lower() in ("true", "on", "1")
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise BadNudges(f"{name} is true or false")


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise BadNudges(f"{name} is a whole number from {low} to {high}")
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or not low <= value <= high:
        raise BadNudges(f"{name} is a whole number from {low} to {high}")
    return value


def _int_list(value: Any, name: str) -> list[int]:
    """Distinct positive whole numbers, sorted ascending; a comma-separated string is taken
    too (the page sends what was typed). Empty means: never at that kind of moment."""
    if isinstance(value, str):
        value = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
    if not isinstance(value, list):
        raise BadNudges(f"{name} is a list of positive whole numbers")
    out: set[int] = set()
    for item in value:
        n = _int(item, name, 1, 1_000_000)
        out.add(n)
    if len(out) > 20:
        raise BadNudges(f"{name} has at most 20 entries")
    return sorted(out)


def _url(value: Any) -> str:
    if not isinstance(value, str):
        raise BadNudges("url is an http(s) address")
    url = value.strip()
    if not url.lower().startswith(("http://", "https://")) or len(url) > URL_MAX or any(c.isspace() for c in url):
        raise BadNudges(f"url is an http(s) address of at most {URL_MAX} characters")
    return url


def _text(value: Any, name: str) -> str:
    """The card's sentence: trimmed, at most :data:`TEXT_MAX` code points (``len`` of a Python
    string counts code points, so CJK is counted the same as Latin); ``null`` is empty."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BadNudges(f"{name} is a sentence of at most {TEXT_MAX} characters")
    text = value.strip()
    if len(text) > TEXT_MAX:
        raise BadNudges(f"{name} is a sentence of at most {TEXT_MAX} characters")
    return text


def validate(body: Any) -> dict[str, Any]:
    """The body as a policy: unknown keys dropped, every value checked, what is missing taken
    from the defaults. ``version`` is accepted and kept (the server bumps it on a PUT)."""
    if not isinstance(body, dict):
        raise BadNudges("The policy is a JSON object")
    out = defaults()
    if "version" in body:
        out["version"] = _int(body["version"], "version", 1, 1_000_000_000)
    star_in = body.get("star", {})
    if star_in is None:
        star_in = {}
    if not isinstance(star_in, dict):
        raise BadNudges("star is an object")
    star = out["star"]
    if "enabled" in star_in:
        star["enabled"] = _bool(star_in["enabled"], "star.enabled")
    if "url" in star_in:
        star["url"] = _url(star_in["url"])
    if "cooldown_days" in star_in:
        star["cooldown_days"] = _int(star_in["cooldown_days"], "star.cooldown_days", 0, COOLDOWN_MAX)
    if "max_asks" in star_in:
        star["max_asks"] = _int(star_in["max_asks"], "star.max_asks", 0, MAX_ASKS_MAX)
    for name in _TEXTS:
        if name in star_in:
            star[name] = _text(star_in[name], f"star.{name}")
    moments_in = star_in.get("moments", {})
    if moments_in is None:
        moments_in = {}
    if not isinstance(moments_in, dict):
        raise BadNudges("star.moments is an object")
    moments = star["moments"]
    for name in _BOOL_MOMENTS:
        if name in moments_in:
            moments[name] = _bool(moments_in[name], f"star.moments.{name}")
    for name in _LIST_MOMENTS:
        if name in moments_in:
            moments[name] = _int_list(moments_in[name], f"star.moments.{name}")
    return out


def merge(stored: Any) -> dict[str, Any]:
    """What the relay serves: the stored policy over the defaults, or the defaults alone
    when nothing is stored or the stored row no longer parses (never a 500 for a client)."""
    if not stored:
        return defaults()
    try:
        return validate(stored)
    except BadNudges:
        return defaults()
