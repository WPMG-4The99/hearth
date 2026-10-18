"""Households the operator linked: who may send a panic to whom.

``HOUSEHOLDS`` is operator config. An account cannot edit it. One household is
two or more e-mail addresses separated by ``|``. Several households are
separated by commas, semicolons or newlines.
"""

from __future__ import annotations

import logging
import re

from .identifiers import BadIdentifier, Identifier, parse

log = logging.getLogger("nanomuse_cloud")

_SPLIT = re.compile(r"[,;\n]+")


def groups(raw: str) -> list[list[Identifier]]:
    """Households of at least two distinct e-mail addresses. Bad entries are skipped."""
    out: list[list[Identifier]] = []
    for chunk in _SPLIT.split(raw or ""):
        if not chunk.strip():
            continue
        members: list[Identifier] = []
        seen: set[str] = set()
        for part in chunk.split("|"):
            part = part.strip()
            if not part:
                continue
            try:
                ident = parse(part)
            except BadIdentifier:
                log.warning("HOUSEHOLDS: skipped an entry that is not an e-mail address")
                continue
            if ident.channel != "email":
                log.warning("HOUSEHOLDS: skipped a phone number; households are e-mail addresses")
                continue
            if ident.value in seen:
                continue
            seen.add(ident.value)
            members.append(ident)
        if len(members) >= 2:
            out.append(members)
        elif members:
            log.warning("HOUSEHOLDS: a household needs at least two addresses")
    return out


def partner_hashes(raw: str, key: bytes, mine_hash: str) -> list[str]:
    """Hashes of the other people in every household that contains ``mine_hash``.

    A member is recognised by the canonical hash or a legacy one (dotted Gmail,
    a ``+tag``). The caller's own hashes are not returned.
    """
    found: list[str] = []
    seen: set[str] = set()
    for members in groups(raw):
        by_member = [(m.hashes(key)) for m in members]
        if not any(mine_hash in hashes for hashes in by_member):
            continue
        for hashes in by_member:
            if mine_hash in hashes:
                continue
            for h in hashes:
                if h not in seen:
                    seen.add(h)
                    found.append(h)
    return found
