"""The sign-in page of nanoMuse Web (``/web/``), one file, in both languages.

Two steps: a phone number or an e-mail address, then the six-digit code nanoMuse Cloud sends
(and, the first time, a friend's invite code if there is one — ``?invite=`` in the address
fills it in). On success the
browser goes to the account's own Muse at ``<slug>.<SESSION_DOMAIN>``. The page
is served by the gateway itself so that it has no build step and no assets to keep in step.

One column on a phone; from 900px the words, the perks and the notices sit on the left and
the form on the right, both centred — the desktop app's sign-in page, not a phone column in
the middle of a wide window.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nanoMuse Web</title>
<meta name="description" content="nanoMuse in the browser — sign in with a phone number or an e-mail, nothing to install. Free, open source, non-profit.">
<link rel="icon" href="https://nanomuse.cn/assets/icon-512.png">
<script>try{var t=localStorage.getItem("nanomuse_theme")||"light";if(t==="system")t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{--bg:#F3F3F5;--card:#fff;--ink:#1C1B22;--muted:#6E6B7A;--line:#E4E3EA;--accent:#5B4EE6;--accent-ink:#fff;--warn:#B23B3B}
html[data-theme=dark]{--bg:#121216;--card:#1B1B21;--ink:#F1F0F5;--muted:#9C99AA;--line:#2C2B34;--accent:#8A7DFF;--accent-ink:#0F0E16;color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,"PingFang SC","Noto Sans SC","Segoe UI",system-ui,sans-serif}
main{min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px}
/* one column on a phone; from 900px the words on the left and the form on the right, like the desktop app */
.page{width:100%;max-width:440px;display:flex;flex-direction:column;gap:18px}
.words{padding:0 4px}
.card{order:1;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:28px;box-shadow:0 10px 40px rgba(0,0,0,.06)}
.perks{order:2;display:grid;grid-template-columns:1fr 1fr;gap:8px;list-style:none;margin:0;padding:0}
.perks li{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;line-height:1.4;color:var(--ink);opacity:.85;padding:9px 11px;border-radius:14px;background:color-mix(in srgb,var(--line) 45%,transparent)}
.perks svg{flex:none;width:16px;height:16px;color:var(--accent);margin-top:1px}
.more{order:3}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.brand img{width:44px;height:44px;border-radius:12px}
.brand b{font-size:20px;letter-spacing:-.01em}
.brand small{display:block;color:var(--muted);font-size:13px;margin-top:1px}
h1{font-size:22px;margin:0 0 6px;letter-spacing:-.01em}
p{margin:0 0 18px;color:var(--muted);font-size:14.5px}
.words p{margin-bottom:0}
label{display:block;font-size:13px;color:var(--muted);margin:0 0 6px}
input{width:100%;font:inherit;font-size:17px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:transparent;color:var(--ink);outline:none}
input:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 20%,transparent)}
input.code{letter-spacing:.35em;text-align:center;font-variant-numeric:tabular-nums}
button{width:100%;margin-top:14px;font:inherit;font-weight:600;font-size:16px;padding:13px 16px;border:0;border-radius:12px;background:var(--accent);color:var(--accent-ink);cursor:pointer}
button[disabled]{opacity:.55;cursor:default}
button.ghost{background:transparent;color:var(--muted);font-weight:500;margin-top:6px;padding:8px}
.msg{min-height:22px;font-size:14px;margin-top:12px;color:var(--muted)}
.msg.err{color:var(--warn)}
.foot{margin-top:16px;font-size:12.5px;color:var(--muted);line-height:1.6}
.foot a{color:inherit}
.notice{margin:0 0 12px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;font-size:13px;line-height:1.55;color:var(--ink)}
.notice b{display:block;margin-bottom:2px}
.notice b a{color:var(--accent);margin:0}
.notice a{color:var(--accent);text-decoration:none;margin-right:12px}
details.inv{margin-top:12px;font-size:13px;color:var(--muted)}
details.inv summary{cursor:pointer}
details.inv input{margin-top:8px;font-size:15px;text-transform:uppercase;letter-spacing:.12em}
.lang{position:fixed;top:14px;right:16px;font-size:13px;color:var(--muted);background:none;border:1px solid var(--line);border-radius:999px;padding:4px 12px;cursor:pointer;width:auto;margin:0;font-weight:500}
.tabs{display:flex;gap:4px;padding:4px;margin:0 0 16px;border-radius:14px;background:color-mix(in srgb,var(--line) 55%,transparent)}
.tabs .tab{flex:1;margin:0;padding:8px 10px;font-size:14px;font-weight:500;border-radius:10px;background:transparent;color:var(--muted)}
.tabs .tab.on{background:var(--card);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.hint{font-size:12.5px;color:var(--muted);margin-top:8px;line-height:1.5}
#pass-box{margin-top:14px}
.notice.tip{border-color:color-mix(in srgb,var(--accent) 45%,var(--line));background:color-mix(in srgb,var(--accent) 7%,var(--card))}
.notice.tip a{display:inline-block;margin:6px 0 0;font-weight:600}
.hidden{display:none}
i.en,i.zh{font-style:normal}
[data-lang="zh"] i.en,[data-lang="en"] i.zh{display:none}
@media (min-width:900px){
  body{background:radial-gradient(ellipse at 18% 50%,color-mix(in srgb,var(--accent) 11%,transparent),transparent 58%) var(--bg)}
  main{padding:40px 56px}
  .page{max-width:1060px;display:grid;grid-template-columns:minmax(0,1.15fr) minmax(360px,420px);grid-template-areas:"words card" "perks card" "more card";column-gap:72px;row-gap:24px;align-items:center}
  .words{grid-area:words;align-self:end;padding:0}
  .perks{grid-area:perks;display:block}
  .perks li{font-size:14.5px;line-height:1.45;padding:0;margin:0 0 10px;background:none;opacity:.9}
  .perks svg{width:18px;height:18px;margin-top:3px}
  .more{grid-area:more;align-self:start;max-width:34em}
  .card{grid-area:card;padding:32px;align-self:center}
  .brand{margin-bottom:28px}
  h1.title{font-size:34px;margin-bottom:10px}
  .words p{font-size:16px;max-width:30em}
  .notice{font-size:13px}
}
</style>
</head>
<body data-lang="zh">
<button class="lang" id="lang" type="button">English</button>
<main>
<div class="page">
  <section class="words">
    <div class="brand"><img src="https://nanomuse.cn/assets/icon-512.png" alt=""><div><b>nanoMuse Web</b>
    <small><i class="zh">打开网页就能用，不用下载</i><i class="en">In the browser, nothing to install</i></small></div></div>
    <h1 class="title"><i class="zh">试试 nanoMuse</i><i class="en">Try nanoMuse</i></h1>
    <p><i class="zh">手机号或邮箱收个验证码，一分钟后就有一台属于你的 nanoMuse，模型自带。</i><i class="en">A phone number or an e-mail, a code, and a minute later a nanoMuse of your own is here, model included.</i></p>
  </section>

  <ul class="perks">
    <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h8"/><path d="M10 19v-3.96 3.15"/><path d="M7 19h5"/><rect width="6" height="10" x="16" y="12" rx="2"/></svg><span><i class="zh">不用下载，打开浏览器就能用</i><i class="en">Nothing to install — it opens in the browser</i></span></li>
    <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/></svg><span><i class="zh">自带模型和免费额度，也可以换自己的 key</i><i class="en">A model with a free allowance, or your own key</i></span></li>
    <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/></svg><span><i class="zh">一直保存着，下次登录还在</i><i class="en">It keeps everything for your next visit</i></span></li>
    <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></svg><span><i class="zh">同一个账号，手机和电脑上就是同一个 nanoMuse</i><i class="en">The same account on the phone and the desktop is the same nanoMuse</i></span></li>
  </ul>

  <section class="card">
  <form id="step1">
    <label for="ident"><i class="zh">中国大陆手机号或邮箱</i><i class="en">Mainland China phone number or e-mail</i></label>
    <input id="ident" name="identifier" autocomplete="username" required autofocus>
    <div class="hint" id="code-hint"><i class="zh">中国大陆手机号收短信验证码，其他的发到邮箱。</i><i class="en">A mainland China number gets the code by SMS; anything else by e-mail.</i></div>
    <details class="inv" id="inv">
      <summary><i class="zh">有邀请码？</i><i class="en">Have an invite code?</i></summary>
      <input id="invite" name="invite" maxlength="8" autocomplete="off" spellcheck="false" placeholder="ABCD2345">
    </details>
    <button id="send" type="submit"><i class="zh">发送验证码</i><i class="en">Send the code</i></button>
    <div class="msg" id="msg1"></div>
  </form>

  <form id="step2" class="hidden">
    <h1><i class="zh">输入验证码</i><i class="en">Enter the code</i></h1>
    <p><i class="zh">六位数字已发到 </i><i class="en">Six digits were sent to </i><b id="to"></b></p>
    <label for="code"><i class="zh">验证码</i><i class="en">Code</i></label>
    <input id="code" class="code" name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" required>
    <button id="go" type="submit"><i class="zh">进入我的 nanoMuse</i><i class="en">Open my nanoMuse</i></button>
    <button id="back" class="ghost" type="button"><i class="zh">换个账号</i><i class="en">Use another account</i></button>
    <div class="msg" id="msg2"></div>
  </form>
  </section>

  <section class="more">
  <div class="notice tip">
    <b><i class="zh">浏览器版适合先试一试</i><i class="en">The browser version is for a first try</i></b>
    <i class="zh">要天天用，推荐装手机 App——功能最全，智能体整个跑在手机上；电脑上装桌面版。同一个账号登录，就是同一个 nanoMuse。<a href="https://nanomuse.cn/#download">下载手机 App 和桌面版</a></i>
    <i class="en">For every day, install the phone app — the fullest, the whole agent runs on the phone — and the desktop app on your computer. The same account is the same nanoMuse everywhere. <a href="https://nanomuse.cn/#download">Get the phone and desktop apps</a></i>
  </div>
  <div class="notice">
    <b><a href="https://nanomuse.cn/#open-source" rel="noopener"><i class="zh">免费、开源、非营利</i><i class="en">Free, open source, non-profit</i></a></b>
    <i class="zh">每个账号都有一份由开发者承担的免费模型额度，用完换自己的 key：中国大陆用阿里云百炼，海外用 OpenRouter。登录后，对话文字会保存在 nanoMuse Cloud，让你的几台设备看到同样的对话——「数据控制」里一个开关就能关掉；数据不会出售。</i>
    <i class="en">Every account starts with an allowance of model use paid by the developer; after that, your own key — Alibaba Cloud Bailian in mainland China, OpenRouter elsewhere. With an account, the text of your conversations is kept on nanoMuse Cloud so that your devices show the same chats — a switch in Data controls turns it off; nothing is sold.</i>
    <br><a href="https://github.com/nano-muse/nanoMuse" rel="noopener"><i class="zh">GitHub</i><i class="en">GitHub</i></a><a href="https://nanomuse.cn/own-key" rel="noopener"><i class="zh">换自己的 key</i><i class="en">Bring your own key</i></a>
  </div>
  <div class="foot">
    <i class="zh">你的 nanoMuse 运行在我们的服务器上，数据只有你能访问；长时间不用会休眠，登录即唤醒。</i>
    <i class="en">Your nanoMuse runs on our server and only you can reach it; it sleeps after a long quiet spell and wakes when you sign in.</i>
    <br><i class="zh">nanoMuse 是社区项目，与 Meta 无关。</i><i class="en">nanoMuse is a community project, not affiliated with Meta.</i>
  </div>
  </section>
</div>
</main>
<script>
(function(){
  var zh = /^zh/i.test(navigator.language || "");
  try { var saved = localStorage.getItem("nm-lang"); if (saved) zh = saved === "zh"; } catch (e) {}
  var body = document.body, langBtn = document.getElementById("lang");
  function setLang(z){ zh = z; body.dataset.lang = z ? "zh" : "en"; langBtn.textContent = z ? "English" : "中文";
    document.documentElement.lang = z ? "zh-CN" : "en"; try { localStorage.setItem("nm-lang", z ? "zh" : "en"); } catch (e) {} }
  setLang(zh);
  langBtn.onclick = function(){ setLang(!zh); };

  var s1 = document.getElementById("step1"), s2 = document.getElementById("step2");
  var ident = document.getElementById("ident"), code = document.getElementById("code");
  var inv = document.getElementById("inv");
  var invite = document.getElementById("invite");
  var msg1 = document.getElementById("msg1"), msg2 = document.getElementById("msg2");
  var send = document.getElementById("send"), go = document.getElementById("go");
  // digits get the phone keypad, anything else the e-mail layout
  ident.oninput = function(){ ident.inputMode = /^\\s*[+\\d]/.test(ident.value) ? "tel" : "email"; };
  function cleanInvite(v){ return (v || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8); }
  try {
    var fromUrl = cleanInvite(new URLSearchParams(location.search).get("invite"));
    if (fromUrl) { invite.value = fromUrl; inv.open = true; }
  } catch (e) {}
  var T = {
    sending: ["正在发送…", "Sending…"], sent: ["已发送", "Sent"],
    starting: ["正在准备你的 nanoMuse，第一次大约需要十几秒…", "Getting your nanoMuse ready — the first time takes ten seconds or so…"],
    ready: ["好了，正在进入…", "Ready, opening…"],
    network: ["网络不通，请稍后再试。", "Could not reach the server. Try again in a moment."]
  };
  function t(k){ return T[k][zh ? 0 : 1]; }
  // the relay's refusals in Chinese; English shows the relay's own sentence
  var E = {
    phone_region: "短信验证码目前只支持中国大陆手机号，海外用户请用邮箱登录。", bad_identifier: "请输入中国大陆手机号或邮箱。",
    code_wrong: "验证码不对。", code_expired: "验证码已过期，请重新发送。", code_too_often: "发送太频繁了，请几分钟后再试。",
    send_failed: "验证码没发出去，请稍后再试。", not_invited: "这个 relay 是私有的，这个账号不在名单上。",
    web_off: "这里没有开启网页版。", web_full: "网页版的名额已满。装 App 吧，它跑在你自己的设备上。",
    web_busy: "网页版现在很忙，请几分钟后再试，或者装 App。", start_failed: "没能启动你的 Muse，请稍后再试。",
    start_timeout: "你的 Muse 启动太久了，请稍后再试。", sign_in_again: "登录已失效，请重新登录。",
    relay_error: "nanoMuse Cloud 没有返回 key，请稍后再试。", relay_unreachable: "连不上 nanoMuse Cloud，请稍后再试。"
  };
  function errText(r){ return (zh && E[r.body.error]) || r.body.message || ("HTTP " + r.status); }
  function show(el, text, err){ el.textContent = text || ""; el.className = "msg" + (err ? " err" : ""); }
  function post(url, data){
    return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)})
      .then(function(r){ return r.text().then(function(txt){ var j = {}; try { j = txt ? JSON.parse(txt) : {}; } catch (e) {} return {ok: r.ok, status: r.status, body: j}; }); });
  }
  function enter(r, btn, msg){
    if (!r.ok) { btn.disabled = false; show(msg, errText(r), true); return; }
    show(msg, t("ready")); location.href = r.body.url;
  }
  s1.onsubmit = function(ev){
    ev.preventDefault();
    send.disabled = true; show(msg1, t("sending"));
    post("/api/web/code", {identifier: ident.value.trim()}).then(function(r){
      send.disabled = false;
      if (!r.ok) { show(msg1, errText(r), true); return; }
      document.getElementById("to").textContent = ident.value.trim();
      s1.classList.add("hidden"); s2.classList.remove("hidden"); show(msg2, t("sent")); code.value = ""; code.focus();
    }).catch(function(){ send.disabled = false; show(msg1, t("network"), true); });
  };
  s2.onsubmit = function(ev){
    ev.preventDefault(); go.disabled = true; show(msg2, t("starting"));
    post("/api/web/verify", {identifier: ident.value.trim(), code: code.value.trim(), invite: cleanInvite(invite.value)}).then(function(r){ enter(r, go, msg2); })
      .catch(function(){ go.disabled = false; show(msg2, t("network"), true); });
  };
  document.getElementById("back").onclick = function(){ s2.classList.add("hidden"); s1.classList.remove("hidden"); show(msg1, ""); ident.focus(); };
})();
</script>
</body>
</html>
"""
