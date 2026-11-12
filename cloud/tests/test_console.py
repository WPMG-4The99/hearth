"""The console's files as the relay serves them (after 0.22): revalidated on every load, so a
deploy reaches the next reload; and the web console's error table knows every code the
relay can answer a signed-in person with, in both languages."""

from __future__ import annotations

import re
from pathlib import Path

from test_accounts import make

CONSOLE = Path(__file__).resolve().parents[1] / "nanomuse_cloud" / "console"

# what a signed-in person can get from the relay since 0.22 (service.py, api.py), by code
CODES_0_22 = [
    "signup_closed",
    "domain_limited",
    "channel_unsupported",
    "service_paused",
    "sync_paused",
    "hub_paused",
    "account_deleted",
    "too_many_in_flight",
    "rate_limited",
]


async def test_console_files_are_revalidated_on_every_load():
    app, client, *_ = make()
    for path in ("/app/", "/app/app.js", "/app/admin/", "/app/admin/admin.js"):
        r = await client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert r.headers.get("cache-control") == "no-cache", path
        assert r.headers.get("etag"), path
    # the API's own answers keep their own caching
    r = await client.get("/v1/config")
    assert r.headers.get("cache-control") == "public, max-age=60"
    r = await client.get("/v1/nudges")
    assert r.headers.get("cache-control") == "public, max-age=3600"


def test_web_console_translates_every_refusal_in_both_languages():
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    tables = re.findall(r"errors: \{(.*?)\},\n", source, flags=re.S)
    assert len(tables) == 2, "one error table for 简体中文, one for English"
    zh, en = tables
    # account_disabled is the code the relay sends (service.sign_in); the table once said
    # "disabled", which nothing sends, so a disabled person read the server's English
    for code in CODES_0_22 + [
        "allowance_exhausted",
        "bad_key",
        "phone_region",
        "account_disabled",
        "bad_request",
        "too_large",
        "no_session",
        "device_online",
        "sync_off",
        "invite_code",
    ]:
        assert f"{code}:" in zh, f"{code} has no Chinese sentence"
        assert f"{code}:" in en, f"{code} has no English sentence"
    assert "disabled:" not in zh.replace("account_disabled:", "") and "disabled:" not in en.replace("account_disabled:", "")
    # the step marker for a failed step is a word in each language, not a punctuation mark
    assert len(re.findall(r"\bfailed: \"", source)) == 2 and 'k: "!"' not in source
    # a paused allowance (429 with paused: true) is said as paused, not as spent
    assert zh.count("e.paused") == 1 and en.count("e.paused") == 1
    # no exclamation marks in what a person reads
    assert "!" not in re.sub(r"[!=]==?|!\w", "", zh + en) and "！" not in zh


def test_web_console_is_one_hub_device_per_tab():
    """The id the console says hello with lives in sessionStorage, so two tabs of one
    browser are two devices on the hub. Shared through localStorage, as before, the two
    took turns replacing each other (close 4003) and reconnecting for as long as both
    stayed open."""
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert 'sessionStorage.getItem("nm.webid")' in source and 'sessionStorage.setItem("nm.webid"' in source
    assert 'LS.setItem("nm.webid"' not in source and 'LS.getItem("nm.webid")' not in source
    # the hello carries that id; the hub keys its connections on it (hub.py _attach)
    assert "device: { id: webId," in source


def test_console_copy_has_no_dashes_in_sentences():
    """The house rule: what a person reads carries no em dash. Checked on the two string
    tables (`const T = zh ? {...} : {...}`) of each console; the one-character placeholder
    for an empty cell (`none: "—"`, `"—"`) is a glyph, not a sentence, and is let through."""
    for path in (CONSOLE / "app.js", CONSOLE / "admin" / "admin.js"):
        source = path.read_text(encoding="utf-8")
        m = re.search(r"const T = zh \? \{(.*?)\n  \};", source, flags=re.S)
        assert m, f"{path.name}: the T tables were not found"
        tables = m.group(1).replace('"—"', "")
        for line in tables.splitlines():
            assert "—" not in line, f"{path.name}: an em dash in a sentence: {line.strip()[:80]}"


def test_admin_console_names_the_star_card_text_in_both_languages():
    """The star asks card (after relay 0.22) has the two text inputs and their hint, in the
    简体中文 table and in the English one, and the panel draws them."""
    source = (CONSOLE / "admin" / "admin.js").read_text(encoding="utf-8")
    for key in ("ndTextEn", "ndTextZh", "ndTextHint", "ndTextLeft"):
        assert source.count(f"{key}:") == 2, f"{key} is in one table only"
    assert 'ndTextEn: "卡片文字（英文）"' in source and 'ndTextEn: "Card text (English)"' in source
    assert 'ndTextZh: "卡片文字（中文）"' in source and 'ndTextZh: "Card text (中文)"' in source
    assert "留空时各端显示自己的句子。最多 200 字。" in source
    assert "Empty: each app shows its own sentence. At most 200 characters." in source
    panel = source[source.index("function nudgesPanel()") : source.index("function settingsView()")]
    assert 'cardText("text")' in panel and 'cardText("text_zh"' in panel


def test_admin_console_has_the_invites_view_in_both_languages():
    """The Invites view (who brought whom, the inviters that brought most first) is in the
    side navigation, reads `/v1/admin/invites`, and every string it draws is in the 简体中文
    table and in the English one; the account drawer names the inviter and lists the people
    brought."""
    source = (CONSOLE / "admin" / "admin.js").read_text(encoding="utf-8")
    assert '"invites"' in source[source.index("const VIEWS = ") : source.index("const PAGE = ")]
    assert "invites: invitesView" in source and 'invites: () => "/v1/admin/invites?' in source
    keys = (
        "invNote",
        "invSearch",
        "invNone",
        "invNoMatch",
        "invKpiCode",
        "invKpiInviters",
        "invKpiInvited",
        "invKpiActive",
        "invKpiSameIp",
        "invThWho",
        "invThInvited",
        "invThActive",
        "invThSameIp",
        "invThSpent",
        "invThEarned",
        "invThLast",
        "invSort",
        "invRecorded",
        "invSameIp",
        "invGone",
        "invSeeRanking",
        "invMore",
        "invSameIpNote",
        "invitedList",
        "invEarnedLine",
        "invNewest",
        "invPill",
        "invPending",
        "invPendingPill",
        "invSettledPill",
        "invAbuseDisable",
        "invAbuseDelete",
        "invAbuseGo",
        "invAbuseConfirm",
        "invAbuseDone",
    )
    for key in keys:
        assert source.count(f"{key}:") == 2, f"{key} is in one table only"
    assert 'invites: "邀请"' in source and 'invites: "Invites"' in source
    assert 'invThWho: "邀请人"' in source and 'invThWho: "Inviter"' in source
    # the first KPI is the account count, not "made a code": every account has a code
    assert 'invKpiCode: "账号总数"' in source and 'invKpiCode: "accounts"' in source
    for key in keys:
        assert f"T.{key}" in source, f"nothing draws {key}"
    view = source[source.index("function invitesView()") : source.index("function placesView()")]
    assert 'need("invites")' in view and "T.invSameIpNote" in view and "INV_OPEN" in view
    # the pending sub-figure under Brought and the abuse action row under each unfolded inviter
    assert "T.invPending(x.invited_pending)" in view and "inviteAbuseRow(x" in view
    drawer = source[source.index("function drawDrawer()") : source.index("function ledgerLine(")]
    assert "a.inviter" in drawer and "T.invitedList(" in drawer and "inviteeRow(i, a)" in drawer
    assert "inviteAbuseRow(a" in drawer
    # the action posts to the abuse route with both flags, asks first, and refreshes the view
    action = source[source.index("async function doInviteAbuse(") : source.index("function inviteAbuseRow(")]
    assert '"/v1/admin/invites/abuse"' in action and "delete_unused" in action and "confirm(" in action
    assert 'forget("invites")' in action and "T.invAbuseDone(" in action
    # new event kinds are named in both tables of both consoles
    for kind in ("invite.settled", "invite.clawback", "signup.domain_limited"):
        assert source.count(f'"{kind}":') == 2, kind
    app_js = (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert app_js.count('"invite.settled":') == 2


def test_web_console_prints_the_relays_code_lifetime():
    """The relay's ``CODE_TTL_S`` is configurable; the console reads it from ``/v1/config``
    since 0.25 instead of saying ten minutes. Sign-in is a code, so no password length is shown."""
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert '"/v1/config"' in source and "cfg.code_ttl_s" in source
    assert source.count("codeSent: (m) =>") == 2
    assert "pwShort:" not in source and "pwWhy:" not in source
    assert "十分钟" not in source and "ten minutes" not in source
    # the default stands for a relay that does not send the field yet
    assert "code_ttl_s: 600" in source


def test_admin_console_searches_and_filters_further():
    """0.26: the People search reaches more fields and takes several words, three filters
    joined it, the drawer's statement and timeline filter through the relay (`?kind=&q=`),
    the Activity feed pages with `before`, the Demo and Data tables search, and every new
    string is in the 简体中文 table and in the English one. A new page closes the drawer."""
    source = (CONSOLE / "admin" / "admin.js").read_text(encoding="utf-8")
    keys = (
        "searchHint",
        "searchKeys",
        "fJoined",
        "joinedIn",
        "fSource",
        "cameVia",
        "fVersion",
        "lgCredits",
        "lgSearch",
        "tlSearch",
        "noRequestsYet",
        "dvSearch",
        "bSearch",
        "feedLoaded",
    )
    for key in keys:
        assert source.count(f"{key}:") == 2, f"{key} is in one table only"
        assert f"T.{key}" in source, f"nothing draws {key}"
    assert 'fJoined: "注册时间"' in source and 'fJoined: "Joined"' in source
    people = source[source.index("function peopleView()") : source.index("function exportCsv(")]
    # the search: every word, anywhere in the account's text, which includes the devices and the inviter
    assert "matches(haystack(a), ts)" in people and 'class: "toolbar lead"' in people and 'class: "search wide"' in people
    hay = source[source.index("const haystack = ") : source.index("const terms = ")]
    for field in ("a.identifier", "a.id", "a.first_ip", "a.last_ip", "a.last_version", "d.name", "a.invite_code", "identOf(a.invited_by"):
        assert field in hay, f"the search does not reach {field}"
    assert "joinedOf(a) === f.joined" in people and "sourceOk(a, f.source)" in people and "f.version" in people
    for key in ("joined", "source", "version"):
        assert f'{key}: g("{key}")' in source, f"{key} is not a hash parameter"
    assert '"invited_by"' in source[source.index("function exportCsv(") :] and '"version"' in source[source.index("function exportCsv(") :]
    # the drawer's lists filter on the relay, and the filter outlives a redraw
    drawer = source[source.index("function drawDrawer()") : source.index("function ledgerLine(")]
    assert "filteredList(`${a.id}:ledger`" in drawer and "filteredList(`${a.id}:events`" in drawer
    assert "/ledger?limit=200&before=${before}&kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}" in drawer
    assert "/events?limit=200&before=${before}&kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}" in drawer
    assert "T.noRequestsYet" in drawer
    assert (
        "const LISTF = new Map()" in source
        and "LISTF.clear()" in source[source.index("function closeDrawer()") : source.index("function closeDrawer()") + 200]
    )
    # a new view closes the drawer; "/" starts a search
    assert 'window.addEventListener("hashchange", () => { closeDrawer(); route(); draw(); })' in source
    assert 'e.key !== "/"' in source
    # the Activity feed reads on with before=
    assert "/v1/admin/events?limit=200&kind=${encodeURIComponent(filter)}&before=${last}" in source
    # the Demo visitors and the Data by-account tables search
    assert 'params.get("dq")' in source and 'params.get("bq")' in source
    # the Stats page's jump list
    assert "p.id = `st-${m.id}`" in source and "scrollIntoView" in source
