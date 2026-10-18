import { Check, Clapperboard, Copy, DatabaseZap, Gift, Image as ImageIcon, KeyRound, Loader2, LogOut, MessageCircle, Phone, Share2, ShieldCheck, Smartphone, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { AllowanceWays } from "../components/AllowanceWays";
import { PageBar } from "../components/BackBar";
import { inputCls, primaryBtn, secondaryBtn } from "../components/Form";
import { SignIn } from "../components/SignIn";
import { StarNudgeOnce, useCloudConfig } from "../components/StarNudge";
import { useT, intlLocale } from "../i18n";
import { useStore } from "../store";
import { ownKeyLine } from "../region";
import type { CloudAccount, CloudEvent, CloudMe, CloudSession, UsageRow } from "../types";
import { cx, relativeSeconds } from "../util";

/**
 * Your nanoMuse Cloud account: who you are signed in as, what has been used (by kind — chat,
 * pictures, clips — and by model), the password, every device signed in, and the
 * way out (sign out here, everywhere, or delete the account). Everything the relay knows
 * about you is on this one screen; what the relay keeps of your chats is one switch away, under
 * Settings → Data controls.
 */
export function AccountScreen() {
  const { state, toast, setTab } = useStore();
  const t = useT();
  const account: CloudAccount | null = state.hub?.account ?? null;
  const [me, setMe] = useState<CloudMe | null>(null);
  const [sessions, setSessions] = useState<CloudSession[] | null>(null);
  const [events, setEvents] = useState<CloudEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const signedIn = !!account?.signed_in;
  const cfg = useCloudConfig();

  const load = async () => {
    if (!signedIn) return;
    setLoading(true);
    try {
      const [m, s, e] = await Promise.all([api.cloudMe(), api.cloudSessions(), api.cloudEvents(40)]);
      setMe(m);
      setSessions(s.sessions);
      setEvents(e.events);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, account?.has_password]);

  return (
    <div className="flex h-full flex-col">
      <PageBar title={t("Account")} description={signedIn ? t("nanoMuse Cloud · one account for all your devices") : t("Sign in to nanoMuse Cloud")} />

      <div className="flex-1 overflow-y-auto px-4 pb-8 space-y-4">
        {!signedIn ? (
          <Section>
            <p className="text-[13.5px] text-muted leading-relaxed">
              {t("Free. One account for all your devices, with a model and ¥{allowance} of use to start; a code the first time, a password afterwards if you like.", {
                allowance: (cfg.allowance_cny ?? 10).toFixed(0),
              })}
            </p>
            <SignIn onSignedIn={() => toast(t("Signed in to nanoMuse Cloud."))} />
          </Section>
        ) : (
          <>
            <Identity account={account} me={me} />
            {me && (
              <StarNudgeOnce moment="signed_in" />
            )}
            {me && <Allowance me={me} onChanged={() => void load()} />}
            {me?.invite?.code && <Invite invite={me.invite} />}
            {me && <Usage me={me} />}
            {me && <DataControlsLink me={me} />}
            <Password account={account} onChanged={() => void load()} />
            <Sessions sessions={sessions} loading={loading} onChanged={() => void load()} />
            {events && events.length > 0 && <Timeline events={events} />}
            <SignOut account={account} onDone={() => setTab("chat")} />
          </>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ pieces
function Section({ title, children, tone }: { title?: string; children: ReactNode; tone?: "danger" }) {
  return (
    <section className={cx("rounded-3xl border p-4 space-y-3 shadow-sm", tone === "danger" ? "border-rose-500/25 bg-rose-500/[0.04]" : "border-border/70 bg-surface")}>
      {title && <h2 className="text-[12px] font-semibold uppercase tracking-wide text-muted">{title}</h2>}
      {children}
    </section>
  );
}

function Identity({ account, me }: { account: CloudAccount | null; me: CloudMe | null }) {
  const t = useT();
  const hint = account?.hint ?? "";
  const since = me?.account.created_at ? new Date(me.account.created_at * 1000) : null;
  return (
    <section className="relative overflow-hidden rounded-3xl border border-border/70 bg-surface p-5 shadow-sm">
      <div className="pointer-events-none absolute -right-16 -top-20 h-48 w-48 rounded-full bg-accent/15 blur-3xl" />
      <div className="flex items-center gap-4">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-accent to-sky-400 text-[22px] font-bold text-white shadow-md">
          {(hint.replace(/[^a-z0-9]/gi, "").slice(0, 1) || "n").toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[18px] font-semibold tracking-tight">{hint}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12.5px] text-muted">
            <span>{account?.channel === "phone" || account?.channel === "sms" ? t("Mobile number") : t("E-mail")}</span>
            {since && <span>· {t("since {date}", { date: since.toLocaleDateString(intlLocale()) })}</span>}
            {me?.account.member && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/12 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
                <ShieldCheck size={11} /> {t("Unlimited")}
              </span>
            )}
          </div>
        </div>
      </div>
      {account?.account_id && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-2xl bg-surface-2/70 px-3 py-2 text-[12px]">
          <span className="text-muted">{t("Account id")}</span>
          <code className="truncate font-mono text-[11.5px] text-fg/80">{account.account_id}</code>
        </div>
      )}
    </section>
  );
}

function Allowance({ me, onChanged }: { me: CloudMe; onChanged: () => void }) {
  const t = useT();
  const spend = me.spend;
  // relay 0.5: one pool for good; a 0.4 relay still answers with the day's cap in the old names
  const grant = spend.grant ?? spend.daily_cap ?? 0;
  const spent = spend.grant !== undefined ? spend.total : spend.today;
  const left = spend.left ?? (spend.unlimited ? null : Math.max(0, grant - spent));
  const limited = !spend.unlimited && grant > 0;
  const pct = limited ? Math.min(100, Math.round((spent / grant) * 100)) : 0;
  const rate = spend.usd_cny ?? 0;
  const usd = (cny: number) => (rate > 0 ? ` ≈ $${(cny / rate).toFixed(2)}` : "");
  const exhausted = limited && (left ?? 0) <= 0;
  const warn = limited && !exhausted && (spend.warn ?? pct >= 80);
  const info = {
    left,
    grant,
    invite_url: me.invite?.url,
    invite_bonus_cny: spend.invite_bonus_cny ?? me.invite?.bonus_cny,
    invitee_bonus_cny: spend.invitee_bonus_cny ?? me.invite?.invitee_bonus_cny,
    own_key_docs: spend.own_key_docs,
    guidance: spend.guidance,
  };
  return (
    <Section title={t("Free allowance")}>
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-[28px] font-bold tracking-tight leading-none">
            {limited ? (
              <>
                ¥{(left ?? 0).toFixed(2)}
                <span className="text-[14px] font-medium text-muted"> {t("left of ¥{grant}", { grant: grant.toFixed(0) })}</span>
              </>
            ) : (
              <>¥{spent.toFixed(2)}</>
            )}
          </div>
          <div className="mt-1 text-[12.5px] text-muted">
            {limited
              ? t("¥{spent} used{usd}; the allowance does not reset.", { spent: spent.toFixed(2), usd: usd(spent) })
              : t("No limit on this account.")}
          </div>
        </div>
        <div className="text-right text-[12.5px] text-muted">
          <div>{t("All time")}</div>
          <div className="text-[15px] font-semibold text-fg">¥{spend.total.toFixed(2)}</div>
          {rate > 0 && <div className="text-[11.5px]">≈ ${(spend.total / rate).toFixed(2)}</div>}
        </div>
      </div>
      {limited && (
        <div className="h-2 overflow-hidden rounded-full bg-surface-2">
          <div className={cx("h-full rounded-full transition-all", pct >= 100 ? "bg-rose-500" : pct >= 80 ? "bg-amber-500" : "bg-accent")} style={{ width: `${pct}%` }} />
        </div>
      )}
      {limited && (
        <p className="text-[12.5px] text-muted">
          {t("¥{allowance} to start, +¥{invite} for each friend you invite, and +¥{invite} for them; after that, your own key keeps the model going.", {
            allowance: (spend.allowance_cny ?? 10).toFixed(0),
            invite: (info.invite_bonus_cny ?? 5).toFixed(0),
          })}{" "}
          {!exhausted && !warn && ownKeyLine(t, { signed_in: true, channel: me.account.channel, region: me.account.region })}
        </p>
      )}
      {(exhausted || warn) && <AllowanceWays info={info} exhausted={exhausted} onChanged={onChanged} />}
    </Section>
  );
}

/**
 * Where the one switch over what the relay keeps lives: Settings → Data controls. The account
 * page only says how it stands and leads there.
 */
function DataControlsLink({ me }: { me: CloudMe }) {
  const t = useT();
  const { setTab } = useStore();
  const ct = me.contribute ?? { on: false, samples: 0 };
  const open = () => {
    window.location.hash = "data";
    setTab("you");
  };
  return (
    <Section title={t("Data controls")}>
      <button type="button" onClick={open} className="flex w-full items-center gap-3 text-left">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-fg">
          <DatabaseZap size={18} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-medium">{t("Help improve nanoMuse's AI models")}</span>
          <span className="block text-[12.5px] text-muted">
            {ct.on ? t("On") : t("Off")}
            {ct.samples > 0 && ` · ${t("{n} turns kept so far", { n: String(ct.samples) })}`}
          </span>
        </span>
        <span className="text-[13px] font-medium text-accent">{t("Open")}</span>
      </button>
    </Section>
  );
}

/** Invite a friend: the code and link, what each friend's first request adds, and what came of it so far. */
function Invite({ invite: inv }: { invite: NonNullable<CloudMe["invite"]> }) {
  const t = useT();
  const { toast } = useStore();
  const link = inv.url || `https://hearth-cloud-the99.zocomputer.io/app/?invite=${inv.code}`;
  const earned = inv.earned_cny ?? inv.invites * inv.bonus_cny;
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast(t("Copied"));
    } catch {
      toast(text);
    }
  };
  const share = async () => {
    const text = t("Try nanoMuse with me, a fully open-source personal agent, free to use. Sign up with my code {code}: {link}", { code: inv.code, link });
    const nav = navigator as Navigator & { share?: (data: { text: string }) => Promise<void> };
    if (nav.share) {
      try {
        await nav.share({ text });
        return;
      } catch {
        /* cancelled — fall through to the clipboard */
      }
    }
    await copy(text);
  };
  return (
    <Section title={t("Invite a friend")}>
      <p className="text-[12.5px] text-muted">
        {t("When a friend signs up with your code and makes their first request, you both get ¥{bonus}. It never expires.", { bonus: inv.bonus_cny.toFixed(0) })}
      </p>
      <div className="flex items-center justify-between gap-3 rounded-2xl bg-surface-2/70 px-3 py-2">
        <div>
          <div className="text-[11px] text-muted">{t("Your code")}</div>
          <code className="font-mono text-[18px] font-semibold tracking-[0.2em]">{inv.code}</code>
        </div>
        <button type="button" onClick={() => void copy(inv.code)} className={cx(secondaryBtn, "inline-flex items-center gap-1.5")}>
          <Copy size={14} /> {t("Copy")}
        </button>
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={() => void share()} className={cx(primaryBtn, "inline-flex flex-1 items-center justify-center gap-1.5 py-2.5")}>
          <Share2 size={14} /> {t("Share the link")}
        </button>
        <button type="button" onClick={() => void copy(link)} className={cx(secondaryBtn, "inline-flex items-center gap-1.5")}>
          <Gift size={14} /> {t("Copy the link")}
        </button>
      </div>
      <p className="text-[12.5px] text-muted">
        {t("{n} friends joined", { n: String(inv.invites) })}
        {earned > 0 && <> · {t("¥{amount} added by invitations", { amount: earned.toFixed(0) })}</>}
      </p>
    </Section>
  );
}

const KIND_META: Record<string, { label: string; icon: ReactNode; tone: string }> = {
  chat: { label: "Chat", icon: <MessageCircle size={15} />, tone: "bg-accent" },
  image: { label: "Pictures", icon: <ImageIcon size={15} />, tone: "bg-violet-500" },
  video: { label: "Clips", icon: <Clapperboard size={15} />, tone: "bg-pink-500" },
  realtime: { label: "Calls", icon: <Phone size={15} />, tone: "bg-emerald-500" },
};

/** By kind (today and all time) and by model: what the account has actually been used for. */
function Usage({ me }: { me: CloudMe }) {
  const t = useT();
  const [range, setRange] = useState<"today" | "total">("today");
  const rows: UsageRow[] = range === "today" ? me.usage.today.by_kind : me.usage.total.by_kind;
  const kinds = me.usage.kinds.length ? me.usage.kinds : ["chat", "image", "video", "realtime"];
  const byKind = new Map(rows.map((r) => [r.kind, r]));
  const total = rows.reduce((n, r) => n + r.cost_cny, 0);
  const models = me.usage.total.by_model.slice(0, 8);
  return (
    <Section title={t("Usage")}>
      <div className="flex items-center justify-between">
        <p className="text-[12.5px] text-muted">{t("Every kind of use is counted, not just the chat model.")}</p>
        <div className="flex rounded-full bg-surface-2/80 p-0.5 text-[12px] font-medium">
          {(["today", "total"] as const).map((r) => (
            <button key={r} type="button" onClick={() => setRange(r)} className={cx("rounded-full px-2.5 py-1 transition", range === r ? "bg-surface shadow-sm" : "text-muted")}>
              {r === "today" ? t("Today") : t("All time")}
            </button>
          ))}
        </div>
      </div>
      {total > 0 && (
        <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-2">
          {kinds.map((k) => {
            const r = byKind.get(k);
            if (!r || r.cost_cny <= 0) return null;
            return <div key={k} className={cx("h-full", KIND_META[k]?.tone ?? "bg-fg/40")} style={{ width: `${(r.cost_cny / total) * 100}%` }} title={t(KIND_META[k]?.label ?? k)} />;
          })}
        </div>
      )}
      <ul className="grid grid-cols-2 gap-2">
        {kinds.map((k) => {
          const r = byKind.get(k);
          const meta = KIND_META[k];
          return (
            <li key={k} className="rounded-2xl bg-surface-2/60 p-3">
              <div className="flex items-center gap-1.5 text-[12.5px] text-muted">
                <span className={cx("flex h-5 w-5 items-center justify-center rounded-md text-white", meta?.tone ?? "bg-fg/40")}>{meta?.icon}</span>
                {t(meta?.label ?? k)}
              </div>
              <div className="mt-1.5 text-[17px] font-semibold tracking-tight">¥{(r?.cost_cny ?? 0).toFixed(2)}</div>
              <div className="text-[11.5px] text-muted">
                {r
                  ? k === "realtime"
                    ? t("{n} answers · {tokens} tokens", { n: r.requests, tokens: fmtTokens(r.prompt_tokens + r.completion_tokens) })
                    : k === "chat"
                      ? t("{n} requests · {tokens} tokens", { n: r.requests, tokens: fmtTokens(r.prompt_tokens + r.completion_tokens) })
                      : t("{n} requests", { n: r.requests })
                  : t("Nothing yet")}
              </div>
            </li>
          );
        })}
      </ul>
      {models.length > 0 && (
        <div>
          <div className="mb-1.5 text-[12px] font-medium text-muted">{t("By model, all time")}</div>
          <ul className="divide-y divide-border/60 rounded-2xl border border-border/60">
            {models.map((m) => (
              <li key={`${m.model}-${m.kind}`} className="flex items-center gap-2 px-3 py-2 text-[12.5px]">
                <span className={cx("h-2 w-2 shrink-0 rounded-full", KIND_META[m.kind]?.tone ?? "bg-fg/40")} />
                <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{m.model}</span>
                <span className="text-muted">{fmtTokens(m.prompt_tokens + m.completion_tokens)}</span>
                <span className="w-16 text-right font-medium">¥{m.cost_cny.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}k`;
  return String(n);
}

function Password({ account, onChanged }: { account: CloudAccount | null; onChanged: () => void }) {
  const t = useT();
  const { toast } = useStore();
  const has = !!account?.has_password;
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (remove = false) => {
    if (!remove && (next.length < 8 || next !== again)) return;
    setBusy(true);
    setError(null);
    try {
      await api.cloudPassword(remove ? "" : next, has ? current : undefined);
      toast(remove ? t("Password removed.") : has ? t("Password changed.") : t("Password set."));
      setOpen(false);
      setCurrent("");
      setNext("");
      setAgain("");
      onChanged();
    } catch (e) {
      setError(t((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={t("Password")}>
      <div className="flex items-center gap-3">
        <span className={cx("flex h-9 w-9 items-center justify-center rounded-xl", has ? "bg-emerald-500/12 text-emerald-600 dark:text-emerald-300" : "bg-surface-2 text-muted")}>
          <KeyRound size={17} />
        </span>
        <div className="min-w-0 flex-1 text-[13.5px]">
          <div className="font-medium">{has ? t("A password is set") : t("No password yet")}</div>
          <div className="text-[12.5px] text-muted">{has ? t("Sign in with it on a new device, or with a code as before.") : t("Optional. With one, a new device can sign in without waiting for a code.")}</div>
        </div>
        {!open && (
          <button type="button" onClick={() => setOpen(true)} className={cx(secondaryBtn, "shrink-0 px-3 py-2 text-[13px]")}>
            {has ? t("Change") : t("Set")}
          </button>
        )}
      </div>
      {open && (
        <form
          className="space-y-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {has && <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} placeholder={t("Current password")} aria-label={t("Current password")} autoComplete="current-password" className={inputCls} />}
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} placeholder={t("New password (8 characters or more)")} aria-label={t("New password (8 characters or more)")} autoComplete="new-password" autoFocus className={inputCls} />
          <input type="password" value={again} onChange={(e) => setAgain(e.target.value)} placeholder={t("Once more")} aria-label={t("Once more")} autoComplete="new-password" className={inputCls} />
          {again && next !== again && <p className="text-[12px] text-rose-600 dark:text-rose-300">{t("The two do not match.")}</p>}
          {error && <p className="rounded-2xl bg-rose-500/12 px-3 py-2 text-[12.5px] text-rose-700 dark:text-rose-300">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={busy || next.length < 8 || next !== again || (has && !current)} className={cx(primaryBtn, "flex-1")}>
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} {has ? t("Change password") : t("Set password")}
            </button>
            {has && (
              <button type="button" disabled={busy || !current} onClick={() => void save(true)} className={secondaryBtn}>
                {t("Remove")}
              </button>
            )}
            <button type="button" onClick={() => setOpen(false)} className={secondaryBtn}>
              {t("Cancel")}
            </button>
          </div>
        </form>
      )}
    </Section>
  );
}

function Sessions({ sessions, loading, onChanged }: { sessions: CloudSession[] | null; loading: boolean; onChanged: () => void }) {
  const t = useT();
  const { toast } = useStore();
  const [busy, setBusy] = useState<string | null>(null);
  const revoke = async (s: CloudSession) => {
    if (!window.confirm(t("Sign out {device}? It will need to sign in again.", { device: s.device || s.prefix }))) return;
    setBusy(s.prefix);
    try {
      await api.cloudRevokeSession(s.prefix);
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Section title={t("Signed in on")}>
      {!sessions ? (
        <div className="flex justify-center py-3 text-muted">{loading ? <Loader2 size={18} className="animate-spin" /> : null}</div>
      ) : (
        <ul className="divide-y divide-border/60">
          {sessions.map((s) => (
            <li key={s.prefix} className="flex items-center gap-3 py-2.5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-fg/70">
                <Smartphone size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[13.5px] font-medium">
                  <span className="truncate">{s.device || t("Unnamed device")}</span>
                  {s.current && <span className="rounded-full bg-accent/12 px-2 py-0.5 text-[10.5px] font-semibold text-accent">{t("This one")}</span>}
                </div>
                <div className="truncate text-[12px] text-muted">
                  {s.via === "password" ? t("password") : t("code")} · {t("since {date}", { date: new Date(s.created_at * 1000).toLocaleDateString(intlLocale()) })}
                  {s.last_used_at ? ` · ${t("used {when}", { when: relativeSeconds(s.last_used_at) })}` : ""}
                </div>
              </div>
              {!s.current && (
                <button type="button" disabled={busy !== null} onClick={() => void revoke(s)} className="rounded-full px-3 py-1.5 text-[12.5px] font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-300">
                  {busy === s.prefix ? <Loader2 size={14} className="animate-spin" /> : t("Sign out")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

const EVENT_LABELS: Record<string, string> = {
  "account.created": "Account created",
  "sign_in.code": "Signed in with a code",
  "sign_in.password": "Signed in with the password",
  "sign_in.failed": "A sign-in attempt failed",
  "sign_out": "Signed out",
  "sign_out.all": "Signed out everywhere",
  "password.set": "Password set",
  "password.changed": "Password changed",
  "password.cleared": "Password removed",
  "budget.refused": "A request was refused: the allowance is used up",
  "call.ended": "Call ended",
  "upstream.error": "The model provider returned an error",
  "credit.granted": "Allowance added",
  "pool.set": "Allowance adjusted",
  "invite.accepted": "Signed up with a friend's code",
  "invite.used": "A friend signed up with your code",
  "invite.unknown": "An invite code was not recognised",
  "contribute.default": "Data controls: on for new accounts",
  "contribute.on": "Data controls: help improve turned on",
  "contribute.off": "Data controls: help improve turned off",
  "contribute.deleted": "Data controls: kept conversations deleted",
  "profile.put": "Agent's name and look saved",
  "profile.clear": "Agent's name and look cleared",
};

function Timeline({ events }: { events: CloudEvent[] }) {
  const t = useT();
  const [all, setAll] = useState(false);
  const shown = all ? events : events.slice(0, 6);
  return (
    <Section title={t("Recent activity")}>
      <ul className="space-y-2">
        {shown.map((e, i) => (
          <li key={`${e.ts}-${i}`} className="flex gap-3 text-[12.5px]">
            <span className="w-[74px] shrink-0 text-muted">{relativeSeconds(e.ts)}</span>
            <span className="min-w-0 flex-1">
              <span className="font-medium">{t(EVENT_LABELS[e.kind] ?? e.kind)}</span>
              {e.detail && <span className="ml-1.5 break-all text-muted">{e.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
      {events.length > 6 && (
        <button type="button" onClick={() => setAll((a) => !a)} className="text-[12.5px] font-medium text-accent">
          {all ? t("Show less") : t("Show all {n}", { n: events.length })}
        </button>
      )}
    </Section>
  );
}

function SignOut({ account, onDone }: { account: CloudAccount | null; onDone: () => void }) {
  const t = useT();
  const { toast } = useStore();
  const [busy, setBusy] = useState<"here" | "all" | "delete" | null>(null);
  const run = async (what: "here" | "all" | "delete") => {
    const ask =
      what === "here"
        ? t("Sign out of nanoMuse Cloud on this device? The hub and the Cloud model stop working here until you sign in again.")
        : what === "all"
          ? t("Sign out on every device, including this one?")
          : t("Delete the account? The relay forgets your identifier, your devices and your usage. This cannot be undone.");
    if (!window.confirm(ask)) return;
    if (what === "delete" && !window.confirm(t("Really delete it?"))) return;
    setBusy(what);
    try {
      if (what === "here") await api.cloudSignOut();
      else if (what === "all") await api.cloudSignOutAll(true);
      else await api.cloudDelete();
      toast(what === "delete" ? t("Account deleted.") : t("Signed out."));
      onDone();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Section tone="danger">
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy !== null} onClick={() => void run("here")} className={cx(secondaryBtn, "flex-1")}>
          {busy === "here" ? <Loader2 size={15} className="animate-spin" /> : <LogOut size={15} />} {t("Sign out")}
        </button>
        <button type="button" disabled={busy !== null} onClick={() => void run("all")} className={cx(secondaryBtn, "flex-1")}>
          {busy === "all" ? <Loader2 size={15} className="animate-spin" /> : <Smartphone size={15} />} {t("Sign out everywhere")}
        </button>
      </div>
      <button type="button" disabled={busy !== null} onClick={() => void run("delete")} className="flex w-full items-center justify-center gap-1.5 rounded-2xl py-2 text-[13px] font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-300">
        {busy === "delete" ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />} {t("Delete the account")}
      </button>
      {account?.is_model && <p className="text-center text-[11.5px] text-muted">{t("The Cloud model is in use; after signing out, pick another under Connections.")}</p>}
    </Section>
  );
}
