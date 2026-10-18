"""How a code reaches the person: the log (development), SMTP, or Aliyun SMS.

`send(identifier, code)` raises `SendError` when the message could not go out
so the API can say so instead of letting the user wait for nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import smtplib
import time
import urllib.parse
import uuid
from email.message import EmailMessage
from typing import Protocol

import httpx

from .config import Settings
from .identifiers import Identifier

log = logging.getLogger("nanomuse_cloud.send")


class SendError(RuntimeError):
    pass


class CodeSender(Protocol):
    def send(self, ident: Identifier, code: str) -> None: ...

    def accepts(self, ident: Identifier) -> bool:
        """Can a code reach this identifier at all? Asked before one is made, so the
        answer is a plain 400 and not a failed send."""
        ...


class LogSender:
    """Development: the code goes to the log, and stays reachable for tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[Identifier, str]] = []

    def accepts(self, ident: Identifier) -> bool:
        return True

    def send(self, ident: Identifier, code: str) -> None:
        self.sent.append((ident, code))
        log.warning("verification code for %s (%s): %s", ident.hint, ident.channel, code)


class SmtpSender:
    def __init__(self, s: Settings) -> None:
        if not (s.smtp_host and s.smtp_from):
            raise ValueError("SMTP_HOST and SMTP_FROM are required for CODE_SENDER=smtp")
        self.s = s

    def accepts(self, ident: Identifier) -> bool:
        return ident.channel == "email"

    def send(self, ident: Identifier, code: str) -> None:
        if ident.channel != "email":
            raise SendError("this deployment sends codes by e-mail only")
        # the same rounding as the SMS template: a lifetime under a minute still reads as one
        msg = compose_code_mail(self.s.smtp_from, ident.value, code, max(1, self.s.code_ttl_s // 60))
        smtp_send(self.s, msg)


def smtp_send(s: Settings, msg: EmailMessage) -> None:
    """One message out through the relay's SMTP settings; SendError when it did not go."""
    if not (s.smtp_host and s.smtp_from):
        raise SendError("mail: SMTP_HOST and SMTP_FROM are not set")
    try:
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            server.starttls()
        with server:
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("smtp send failed: %s", smtp_error_text(e))
        raise SendError("mail") from e


def smtp_error_text(e: BaseException) -> str:
    """What the log may say about a failed send: the error's kind and the server's code and
    words, never the recipient. ``str()`` of an SMTPRecipientsRefused is a dict keyed by the
    addresses refused, and SMTPSenderRefused carries the From address."""
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        codes = sorted({str(v[0]) for v in e.recipients.values()} if isinstance(e.recipients, dict) else set())
        return f"SMTPRecipientsRefused ({len(e.recipients)} recipient(s); {', '.join(codes) or 'no code'})"
    if isinstance(e, smtplib.SMTPResponseException):
        error = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return f"{type(e).__name__} {e.smtp_code} {error[:120]}"
    return f"{type(e).__name__}: {str(e)[:160]}"


def compose_notice_mail(sender: str, to: str, subject: str, lines: list[str]) -> EmailMessage:
    """A plain note to the operator (0.22: a threshold rule fired). Text only, both
    languages' worth of facts in the lines the caller gives; nothing personal in it."""
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"Hearth <{sender}>"
    msg["To"] = to
    msg["Subject"] = f"Hearth · {subject}"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content("\n".join(lines) + "\n\nHearth · automated, no reply / 自动发送，请勿回复\n")
    return msg


def compose_code_mail(sender: str, to: str, code: str, minutes: int) -> EmailMessage:
    """The verification mail: plain text first (what every client can show),
    an HTML part on top with the code large enough to read off a phone.
    Chinese and English in one message — the relay does not know the reader's
    language, and a code mail should not need a translation either way."""
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"Hearth <{sender}>"
    msg["To"] = to
    msg["Subject"] = f"Hearth 验证码 {code} · Your Hearth code"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(
        f"你的 Hearth 验证码是 {code}，{minutes} 分钟内有效。\n"
        "在 Hearth 里填入即可登录；不要把它告诉任何人。\n\n"
        f"Your Hearth code is {code}; it expires in {minutes} minutes.\n"
        "Enter it in the Hearth app to sign in. Do not share it with anyone.\n\n"
        "如果这不是你本人的操作，忽略这封邮件即可。\n"
        "If you did not ask for this, you can ignore this message.\n\n"
        "Hearth · https://github.com/WPMG-4The99/hearth · 自动发送，请勿回复 / automated, no reply\n"
    )
    spaced = " ".join(code)
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Helvetica Neue',Arial,sans-serif;color:#1d1d1f">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="420" cellpadding="0" cellspacing="0" style="max-width:420px;background:#ffffff;border-radius:16px;padding:32px 28px">
<tr><td style="font-size:15px;font-weight:600;color:#6e6e73;padding-bottom:18px">Hearth</td></tr>
<tr><td style="font-size:16px;line-height:24px">你的验证码 / Your code</td></tr>
<tr><td style="font-size:40px;font-weight:700;letter-spacing:6px;padding:14px 0 18px;font-variant-numeric:tabular-nums">{spaced}</td></tr>
<tr><td style="font-size:14px;line-height:22px;color:#3a3a3c">{minutes} 分钟内有效。在 Hearth 里填入即可登录；不要把它告诉任何人。</td></tr>
<tr><td style="font-size:14px;line-height:22px;color:#3a3a3c;padding-top:8px">It expires in {minutes} minutes. Enter it in the Hearth app to sign in; do not share it with anyone.</td></tr>
<tr><td style="font-size:12px;line-height:18px;color:#8e8e93;padding-top:22px">如果这不是你本人的操作，忽略这封邮件即可。<br>If you did not ask for this, you can ignore this message.</td></tr>
</table>
<div style="font-size:12px;color:#8e8e93;padding-top:16px">Hearth · <a href="https://github.com/WPMG-4The99/hearth" style="color:#8e8e93">github.com/WPMG-4The99/hearth</a> · 自动发送，请勿回复 / automated, no reply</div>
</td></tr></table>
</body></html>
"""
    msg.add_alternative(html, subtype="html")
    return msg


def _pct(v: str) -> str:
    return urllib.parse.quote(v, safe="~")


def aliyun_common_params(access_key_id: str) -> dict[str, str]:
    """What every RPC-style Aliyun call (dypnsapi, dysmsapi; API version 2017-05-25) carries."""
    return {
        "AccessKeyId": access_key_id,
        "Format": "JSON",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2017-05-25",
    }


def aliyun_signed_query(access_key_secret: str, params: dict[str, str]) -> str:
    """The query string of a GET, signed the RPC way: HMAC-SHA1 over the sorted, encoded pairs."""
    canonical = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    string_to_sign = "GET&%2F&" + _pct(canonical)
    digest = hmac.new((access_key_secret + "&").encode(), string_to_sign.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()
    return canonical + "&Signature=" + _pct(signature)


class AliyunSmsSender:
    """Aliyun SMS, signed the RPC way (HMAC-SHA1), no SDK. Two services:

    - `ALIYUN_SMS_API=dypns` (default): 号码认证服务's `SendSmsVerifyCode`
      (dypnsapi). Its ready-made templates take `code` and `min`; our own code
      is passed in `TemplateParam`, so the relay still verifies it locally.
    - `ALIYUN_SMS_API=dysms`: 短信服务's `SendSms` (dysmsapi) with a template
      whose one variable is `code`, e.g. 「您的验证码为${code}，10分钟内有效。」.

    Mainland numbers are sent without +86; dysms takes others with the country
    code and no plus, dypns only mainland numbers.
    """

    ENDPOINTS = {"dypns": "https://dypnsapi.aliyuncs.com/", "dysms": "https://dysmsapi.aliyuncs.com/"}

    def __init__(self, s: Settings) -> None:
        if not (s.aliyun_access_key_id and s.aliyun_access_key_secret and s.aliyun_sms_sign and s.aliyun_sms_template):
            raise ValueError("ALIYUN_ACCESS_KEY_ID/SECRET, ALIYUN_SMS_SIGN and ALIYUN_SMS_TEMPLATE are required for CODE_SENDER=aliyun")
        if s.aliyun_sms_api not in self.ENDPOINTS:
            raise ValueError("ALIYUN_SMS_API must be dypns or dysms")
        self.s = s

    @property
    def api(self) -> str:
        return self.s.aliyun_sms_api

    @property
    def endpoint(self) -> str:
        return self.ENDPOINTS[self.api]

    def _signed_query(self, params: dict[str, str]) -> str:
        return aliyun_signed_query(self.s.aliyun_access_key_secret, params)

    def accepts(self, ident: Identifier) -> bool:
        """Phones only; 号码认证 (dypns) reaches mainland numbers only, 短信服务 (dysms) any."""
        return ident.channel == "phone" and (self.api == "dysms" or ident.value.startswith("+86"))

    def params(self, ident: Identifier, code: str) -> dict[str, str]:
        """The request for one code, before signing; a mainland number without +86."""
        if ident.channel != "phone":
            raise SendError("this deployment sends codes by SMS only")
        mainland = ident.value.startswith("+86")
        number = ident.value[3:] if mainland else ident.value.lstrip("+")
        minutes = str(max(1, self.s.code_ttl_s // 60))
        common = {
            **aliyun_common_params(self.s.aliyun_access_key_id),
            "SignName": self.s.aliyun_sms_sign,
            "TemplateCode": self.s.aliyun_sms_template,
        }
        if self.api == "dypns":
            if not mainland:
                raise SendError("this deployment sends codes to mainland numbers only")
            return {
                **common,
                "Action": "SendSmsVerifyCode",
                "PhoneNumber": number,
                "CountryCode": "86",
                "TemplateParam": json.dumps({"code": code, "min": minutes}),
                "ValidTime": str(self.s.code_ttl_s),
                "ReturnVerifyCode": "false",
            }
        return {
            **common,
            "Action": "SendSms",
            "PhoneNumbers": number,
            "TemplateParam": json.dumps({"code": code}),
        }

    def send(self, ident: Identifier, code: str) -> None:
        url = self.endpoint + "?" + self._signed_query(self.params(ident, code))
        try:
            r = httpx.get(url, timeout=15)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            # the signed query carries the number and the code; an error that quotes the
            # URL is cut at the question mark before it reaches the log
            log.error("aliyun sms request failed: %s: %s", type(e).__name__, str(e).split("?", 1)[0][:160])
            raise SendError("sms") from e
        if body.get("Code") != "OK":
            # never the number: BUSINESS_LIMIT_CONTROL (too many for one number), FREQUENCY_FAIL,
            # MOBILE_NUMBER_ILLEGAL, FUNCTION_NOT_OPENED, isv.* — all are the operator's to read
            log.error("aliyun sms (%s) rejected: %s %s", self.api, body.get("Code"), body.get("Message"))
            raise SendError("sms")


def make_sender(s: Settings) -> CodeSender:
    if s.sender == "log":
        if s.signup_open and not s.public_base.startswith(("http://127.", "http://localhost")):
            # a relay with a public address and open sign-up that writes the codes to its log:
            # nobody receives a code, and whoever reads the log can sign in as anyone — say so at startup
            log.error(
                "CODE_SENDER=log with SIGNUP_OPEN at %s: verification codes go to this log and nobody receives them. Set CODE_SENDER=smtp or aliyun.",
                s.public_base,
            )
        return LogSender()
    if s.sender == "smtp":
        return SmtpSender(s)
    if s.sender == "aliyun":
        return AliyunSmsSender(s)
    if s.sender == "both":
        return BothSender(SmtpSender(s), AliyunSmsSender(s))
    raise ValueError(f"unknown CODE_SENDER {s.sender!r}")


class BothSender:
    """SMS for phones, mail for addresses — the normal production setup."""

    def __init__(self, mail: SmtpSender, sms: AliyunSmsSender) -> None:
        self.mail, self.sms = mail, sms

    def _for(self, ident: Identifier) -> SmtpSender | AliyunSmsSender:
        return self.sms if ident.channel == "phone" else self.mail

    def accepts(self, ident: Identifier) -> bool:
        return self._for(ident).accepts(ident)

    def send(self, ident: Identifier, code: str) -> None:
        self._for(ident).send(ident, code)
