import { useEffect, useRef, useState, type FocusEvent, type FormEvent, type ReactNode } from 'react';
import { useKeyboard } from '@/os/keyboard';
import { dragonIdle, IcBack, IcChat, IcCloud, IcHands, IcKey, IcReach, IcRetry } from '../res/icons';
import { parseServerInput, useNanoMuseStore } from '../state';
import { useNanoMuseGestures } from '../hooks/useNanoMuseGestures';
import { NANOMUSE_CONFIG } from '../data';
import { setStarter } from '../host';
import {
  DemoError,
  fetchDemoInfo,
  requestSignInCode,
  signOut,
  startDemoSession,
  verifySignInCode,
  type DemoInfo,
  type DemoProvider,
} from '../demo';
import { fmt, useNanoMuseStrings } from '../res/strings';

/**
 * The first page — the Android app's welcome page (ui/onboarding/FirstRunSetup.kt), with the
 * same face, title, line, three rows and pill. On the hosted showcase (`demoGateway` set at
 * build time) the pill is the Android app's door — "Sign in — free", nanoMuse Cloud's sign-in,
 * then the meet page — when the showcase asks for a sign-in, and starts a private Muse on the
 * showcase's server otherwise; "I have my own API key" opens the three fields for one's own
 * model. "Connect your own nanoMuse" leads to the form for a server of one's own: paste the
 * link `nanomuse serve` prints (it carries the token), or the address and token separately.
 * The token is checked by opening the server's WebSocket once — the same thing the
 * notification bridge does — so no CORS setup is needed. In a normal checkout that form is
 * the whole page.
 */
export default function SetupPage() {
  const current = useNanoMuseStore((s) => s.serverUrl);
  const demo = useNanoMuseStore((s) => s.demo);
  const gateway = NANOMUSE_CONFIG.demoGateway;
  const [own, setOwn] = useState(!gateway || Boolean(current && !demo));

  if (gateway && !own) {
    return <ShowcaseFlow gateway={gateway} onOwnServer={() => setOwn(true)} />;
  }
  return <OwnServerPage onBack={gateway ? () => setOwn(false) : undefined} />;
}

/** Where the real thing is: the Android app and the desktop, on the project site. */
const DOWNLOAD_URL = 'https://nanomuse.cn/#download';

type Phase = 'idle' | 'starting' | 'failed';
type Step = 'welcome' | 'signin' | 'meet';

/**
 * The pages before a Muse on the showcase server. The welcome page first; when the showcase
 * asks for a sign-in (visitors.py: the project wants to know who is trying), the pill leads
 * to nanoMuse Cloud's sign-in and then to the meet page, the Android app's order; a visitor
 * the browser remembers goes straight from the welcome page to a Muse.
 */
function ShowcaseFlow({ gateway, onOwnServer }: { gateway: string; onOwnServer: () => void }) {
  const configure = useNanoMuseStore((s) => s.configure);
  const ticket = useNanoMuseStore((s) => s.ticket);
  const visitor = useNanoMuseStore((s) => s.visitor);
  const setVisitor = useNanoMuseStore((s) => s.setVisitor);
  const { go } = useNanoMuseGestures();
  const s = useNanoMuseStrings();
  const [info, setInfo] = useState<DemoInfo | null>(null);
  const [step, setStep] = useState<Step>('welcome');
  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState('');
  const [ownKey, setOwnKey] = useState(false);
  const [provider, setProvider] = useState<DemoProvider>({
    base_url: 'https://api.deepseek.com',
    api_key: '',
    model: 'deepseek-flash',
  });

  useEffect(() => {
    let alive = true;
    fetchDemoInfo(gateway)
      .then((i) => alive && setInfo(i))
      .catch(() => alive && setInfo(null));
    return () => {
      alive = false;
    };
  }, [gateway]);

  const signinRequired = info?.signin_required ?? false;
  const signedIn = Boolean(ticket);

  const start = async (withProvider?: DemoProvider): Promise<boolean> => {
    setPhase('starting');
    setMessage('');
    try {
      const session = await startDemoSession(gateway, withProvider, ticket || undefined);
      configure(session.serverUrl, session.token, { id: session.id, expiresAt: session.expiresAt, byok: session.byok });
      go('muse.open');
      return true;
    } catch (err) {
      setPhase('failed');
      if (err instanceof DemoError) {
        const known = (s as Record<string, string>)[`demo_${err.code}`];
        setMessage(known ?? err.message);
        // no demo key on this server: the visitor has to bring one
        if (err.code === 'no_model') setOwnKey(true);
        // the browser's sign-in is no longer good: through the sign-in again
        if (err.code === 'signin_required' || err.code === 'signin_expired') {
          setVisitor('', null);
          setStep('signin');
        }
      } else {
        setMessage(s.hosted_failed);
      }
      return false;
    }
  };

  // The page around the phone may press Start itself (host.ts `start()`), once it has
  // reason to think a person is looking: only while the pill is the one on the screen, with
  // the showcase's own model, and never past a sign-in the showcase asks for or after a try
  // that failed (the person reads the message and taps Try again).
  const startable =
    info !== null && step !== 'signin' && !(signinRequired && !signedIn) && !ownKey && phase === 'idle';
  const startRef = useRef(start);
  startRef.current = start;
  useEffect(() => {
    setStarter(startable ? () => startRef.current() : null);
    return () => setStarter(null);
  }, [startable]);

  const signOutHere = () => {
    const old = ticket;
    setVisitor('', null);
    if (old) void signOut(gateway, old);
    setStep('welcome');
  };

  const minutes = info ? Math.round(info.session_ttl_s / 60) : null;
  const canOwnKey = info?.byok ?? true;
  const filled = Boolean(provider.base_url.trim() && provider.api_key.trim() && provider.model.trim());
  const hostedFinePrint = minutes ? fmt(s.hosted_fine_print, minutes) : s.hosted_fine_print_a_while;

  const keyFields = (
    <div className="flex flex-col gap-2.5">
      <Field type="url" value={provider.base_url} onChange={(v) => setProvider({ ...provider, base_url: v })} placeholder={s.hosted_base_url} />
      <Field type="text" value={provider.model} onChange={(v) => setProvider({ ...provider, model: v })} placeholder={s.hosted_model} />
      <Field type="password" value={provider.api_key} onChange={(v) => setProvider({ ...provider, api_key: v })} placeholder={s.hosted_api_key} />
    </div>
  );
  const startPill = (
    <Pill busy={phase === 'starting'} disabled={ownKey && !filled} onClick={() => void start(ownKey ? provider : undefined)}>
      {phase === 'starting' ? s.hosted_starting : phase === 'failed' ? s.hosted_retry : s.setup_start}
    </Pill>
  );
  const ownKeyToggle = canOwnKey ? (
    <TextButton onClick={() => setOwnKey(!ownKey)}>{ownKey ? s.hosted_showcase_model : s.setup_own_key}</TextButton>
  ) : null;

  if (step === 'signin') {
    return (
      <SignInPage
        gateway={gateway}
        onBack={() => setStep('welcome')}
        onSignedIn={(t, v) => {
          setVisitor(t, v);
          setMessage('');
          setStep('meet');
        }}
      />
    );
  }

  if (step === 'meet') {
    return (
      <Page
        hero={<FaceDisc />}
        title={fmt(s.meet_title, 'nanoMuse')}
        subtitle={s.meet_sub}
        message={message}
        primary={startPill}
        secondary={ownKeyToggle}
        finePrint={ownKey ? s.hosted_key_fine_print : hostedFinePrint}
        link={visitor ? <TextLink onClick={signOutHere}>{`${fmt(s.signin_signed_in_as, visitor.hint)} · ${s.signin_sign_out}`}</TextLink> : null}
      >
        {ownKey ? keyFields : <p className="text-[13px] text-app-text-muted text-center leading-snug px-2">{s.meet_fine_print}</p>}
      </Page>
    );
  }

  // the welcome page: the Android app's, with the one door — the account — when the
  // showcase asks for it, and the pill straight to a Muse otherwise
  const needsSignIn = signinRequired && !signedIn;
  return (
    <Page
      hero={<FaceDisc />}
      title={s.setup_title}
      subtitle={s.welcome_tagline}
      message={message}
      primary={
        needsSignIn ? (
          <Pill onClick={() => setStep('signin')}>{s.welcome_signin}</Pill>
        ) : (
          startPill
        )
      }
      secondary={needsSignIn ? null : ownKeyToggle}
      finePrint={needsSignIn ? s.welcome_signin_fine_print : ownKey ? s.hosted_key_fine_print : hostedFinePrint}
      link={
        <>
          {signedIn && visitor && (
            <TextLink onClick={signOutHere}>{`${fmt(s.signin_signed_in_as, visitor.hint)} · ${s.signin_sign_out}`}</TextLink>
          )}
          <TextLink onClick={() => window.open(DOWNLOAD_URL, '_blank', 'noopener')}>{s.welcome_get_app}</TextLink>
          <TextLink onClick={onOwnServer}>{s.setup_own_server}</TextLink>
        </>
      }
    >
      {ownKey && !needsSignIn ? (
        keyFields
      ) : (
        <div className="flex flex-col gap-2.5">
          <FeatureRow icon={<IcChat size={20} />} title={s.welcome_feat_chat} subtitle={s.welcome_feat_chat_sub} />
          <FeatureRow icon={<IcHands size={20} />} title={s.welcome_feat_hands} subtitle={s.welcome_feat_hands_sub} />
          <FeatureRow icon={<IcReach size={20} />} title={s.welcome_feat_reach} subtitle={s.welcome_feat_reach_sub} />
          <NoticeCard title={s.welcome_notice_title} body={s.welcome_notice} closing={s.welcome_notice_closing} />
        </div>
      )}
    </Page>
  );
}

/** Where the project explains itself: the open-source section of the site (the APK's NOTICE_URL). */
const NOTICE_URL = 'https://nanomuse.cn/#open-source';

/** The Android welcome page's card (FirstRunSetup.kt NoticeCard): free, open source, non-profit; a tap opens the site. */
function NoticeCard({ title, body, closing }: { title: string; body: string; closing: string }) {
  return (
    <button
      type="button"
      onClick={() => window.open(NOTICE_URL, '_blank', 'noopener')}
      className="w-full text-left rounded-2xl p-4 mt-1.5 active:opacity-80"
      style={{ background: 'var(--nm-fill)' }}
    >
      <p className="text-[14px] font-semibold leading-[19px]">{title}</p>
      <p className="text-[13px] text-app-text-muted leading-[18px] mt-1.5">{body}</p>
      <p className="text-[13px] text-app-text-muted leading-[18px] mt-1.5">{closing}</p>
    </button>
  );
}

/**
 * nanoMuse Cloud's sign-in, the Android app's (ui/cloud/*): a phone number or an e-mail, a
 * code sent to it. The relay's refusals come back by code and are said in the phone's language.
 */
function SignInPage({
  gateway,
  onBack,
  onSignedIn,
}: {
  gateway: string;
  onBack: () => void;
  onSignedIn: (ticket: string, visitor: { hint: string; channel: string }) => void;
}) {
  const s = useNanoMuseStrings();
  const [identifier, setIdentifier] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [wait, setWait] = useState(0);
  const [busy, setBusy] = useState<'send' | 'verify' | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (wait <= 0) return;
    const timer = setTimeout(() => setWait(wait - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  const said = (err: unknown): string => {
    if (err instanceof DemoError) {
      const known = (s as Record<string, string>)[`relay_${err.code}`];
      return known ?? err.message ?? s.relay_generic;
    }
    return s.relay_generic;
  };

  const send = async () => {
    if (!identifier.trim()) {
      setError(s.relay_bad_identifier);
      return;
    }
    setBusy('send');
    setError('');
    try {
      await requestSignInCode(gateway, identifier.trim());
      setSent(true);
      setWait(60);
    } catch (err) {
      setError(said(err));
    } finally {
      setBusy(null);
    }
  };

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!identifier.trim()) {
      setError(s.relay_bad_identifier);
      return;
    }
    if (!/^\d{6}$/.test(code.trim())) {
      setError(s.relay_code_wrong);
      return;
    }
    setBusy('verify');
    setError('');
    try {
      const result = await verifySignInCode(gateway, identifier.trim(), code.trim());
      onSignedIn(result.ticket, { hint: result.visitor.hint, channel: result.visitor.channel });
    } catch (err) {
      setError(said(err));
    } finally {
      setBusy(null);
    }
  };

  const canVerify = sent && code.trim().length === 6;

  return (
    <Page
      onBack={onBack}
      hero={
        <div className="w-[72px] h-[72px] rounded-full flex items-center justify-center" style={{ background: 'var(--nm-fill)' }}>
          <IcCloud size={28} />
        </div>
      }
      title={s.signin_title}
      subtitle={s.signin_sub}
      message={error}
      primary={
        <Pill busy={busy === 'verify'} disabled={!canVerify} onClick={() => void submit()}>
          {s.signin_verify}
        </Pill>
      }
      finePrint={s.signin_fine_print}
    >
      <form onSubmit={submit} className="flex flex-col gap-2.5">
        <Field
          type="text"
          value={identifier}
          onChange={setIdentifier}
          placeholder={s.signin_identifier_hint}
          label={s.signin_identifier}
          inputMode="email"
        />
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Field type="text" value={code} onChange={setCode} placeholder="······" label={s.signin_code} inputMode="numeric" />
          </div>
          <button
            type="button"
            disabled={busy === 'send' || wait > 0}
            onClick={() => void send()}
            className="h-12 px-4 rounded-2xl text-[14px] font-medium text-app-primary disabled:text-app-text-muted flex-none"
            style={{ background: 'var(--nm-fill)' }}
          >
            {wait > 0 ? fmt(s.signin_resend_in, wait) : sent ? s.signin_resend : s.signin_send_code}
          </button>
        </div>
        <button type="submit" className="hidden" />
      </form>
    </Page>
  );
}

/** The form for a nanoMuse of one's own: the link `nanomuse serve` prints, or address + token. */
function OwnServerPage({ onBack }: { onBack?: () => void }) {
  const current = useNanoMuseStore((s) => s.serverUrl);
  const currentToken = useNanoMuseStore((s) => s.token);
  const demo = useNanoMuseStore((s) => s.demo);
  const configure = useNanoMuseStore((s) => s.configure);
  const { go } = useNanoMuseGestures();
  const s = useNanoMuseStrings();

  // a hosted session's address is not something to type back
  const [address, setAddress] = useState(current && !demo ? current : 'http://127.0.0.1:8787');
  const [token, setToken] = useState(demo ? '' : currentToken);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const parsed = parseServerInput(address);
    const finalToken = (parsed.token || token).trim();
    if (!parsed.serverUrl) {
      setError(s.setup_need_address);
      return;
    }
    setBusy(true);
    setError('');
    const result = await probe(parsed.serverUrl, finalToken);
    setBusy(false);
    if (result !== 'ok') {
      setError(result === 'unauthorized' ? s.setup_refused : fmt(s.setup_unreachable, parsed.serverUrl));
      return;
    }
    configure(parsed.serverUrl, finalToken);
    go('muse.open');
  };

  return (
    <Page
      onBack={onBack}
      hero={
        <div className="w-[72px] h-[72px] rounded-full flex items-center justify-center" style={{ background: 'var(--nm-fill)' }}>
          <IcKey size={28} />
        </div>
      }
      title={s.setup_title_own}
      subtitle={s.setup_sub_own}
      message={error}
      primary={
        <Pill busy={busy} onClick={() => void submit()}>
          {busy ? s.setup_connecting : s.setup_connect}
        </Pill>
      }
      finePrint={s.setup_own_fine_print}
    >
      <form onSubmit={submit} className="flex flex-col gap-2.5">
        <Field type="url" value={address} onChange={setAddress} placeholder="http://127.0.0.1:8787/#token=…" label={s.setup_address} />
        <Field type="text" value={token} onChange={setToken} placeholder={s.setup_token_hint} label={s.setup_token} />
        <button type="submit" className="hidden" />
      </form>
    </Page>
  );
}

// ---- the pieces of the Android app's Page composable

/** Title, line, content, and the column at the bottom: the pill, a text button, fine print. */
function Page({
  hero,
  title,
  subtitle,
  children,
  message,
  primary,
  secondary,
  finePrint,
  link,
  onBack,
}: {
  hero: ReactNode;
  title: string;
  subtitle: string;
  children: ReactNode;
  message?: string;
  primary: ReactNode;
  secondary?: ReactNode;
  finePrint?: string;
  link?: ReactNode;
  onBack?: () => void;
}) {
  // The simulator's keyboard takes 320 of the 800px and the OS shrinks the page by that much
  // (MobileGym's adjustResize). The fields are in the scroll area between the title and the
  // pill, so with the keyboard up the hero, the text button and the fine print step aside
  // (data-hide-on-keyboard, hidden by the OS) and the field being typed in is scrolled into the
  // scroll area's view — the OS's own scroll-on-focus measures against the page's bottom, not
  // against this scroll area's, so a field hidden under the pill's column stayed hidden.
  const { height: keyboard } = useKeyboard();
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (keyboard <= 0) return;
    let raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => reveal(scroller.current, document.activeElement));
    });
    return () => cancelAnimationFrame(raf);
  }, [keyboard]);
  return (
    <div
      className="h-full w-full flex flex-col bg-app-bg text-app-text pt-10"
      data-status-bar-foreground="dark"
      style={{ ['--nm-fill' as string]: '#f1f1f4', ['--nm-disc' as string]: '#f1efeb' }}
    >
      {onBack && (
        <button type="button" onClick={onBack} className="self-start ml-2 mt-1 p-2 rounded-full text-app-text active:bg-black/5" aria-label="back">
          <IcBack size={24} />
        </button>
      )}
      <div
        ref={scroller}
        className="flex-1 overflow-y-auto px-6 pb-3"
        onFocus={(e: FocusEvent<HTMLDivElement>) => {
          if (keyboard > 0) reveal(scroller.current, e.target);
        }}
      >
        <div className={`${onBack ? 'mt-2' : 'mt-8'} mb-5 flex flex-col items-center text-center`}>
          <div data-hide-on-keyboard className="mb-5">
            {hero}
          </div>
          <h1 className="text-[24px] font-semibold tracking-tight leading-tight">{title}</h1>
          <p className="mt-2 text-[14px] text-app-text-muted leading-snug">{subtitle}</p>
        </div>
        {children}
        {message && <p className="mt-4 text-[13px] text-rose-600 leading-snug">{message}</p>}
      </div>
      <div className="px-6 pb-7 pt-3 flex flex-col items-center gap-2">
        {primary}
        {(secondary || finePrint || link) && (
          <div data-hide-on-keyboard className="flex flex-col items-center gap-2">
            {secondary}
            {finePrint && <p className="text-[12px] text-app-text-muted text-center leading-snug">{finePrint}</p>}
            {link}
          </div>
        )}
      </div>
    </div>
  );
}

/** Scroll `container` the least that shows `target` (with its label) whole, 8px clear of the edges. */
function reveal(container: HTMLElement | null, target: EventTarget | Element | null) {
  if (!container || !(target instanceof HTMLElement) || !container.contains(target)) return;
  const shown = target.closest('label') ?? target;
  const t = shown.getBoundingClientRect();
  const c = container.getBoundingClientRect();
  const below = t.bottom - (c.bottom - 8);
  const above = c.top + 8 - t.top;
  if (below > 0) container.scrollBy({ top: below, behavior: 'smooth' });
  else if (above > 0) container.scrollBy({ top: -above, behavior: 'smooth' });
}

/** The dragon's face at rest on its warm disc — AgentAvatarDisc(mood = IDLE, 104.dp). */
function FaceDisc() {
  return (
    <div className="w-[104px] h-[104px] rounded-full overflow-hidden flex items-center justify-center" style={{ background: 'var(--nm-disc)' }}>
      <img src={dragonIdle} alt="" width={104} height={104} className="w-full h-full object-cover" draggable={false} />
    </div>
  );
}

function FeatureRow({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-3.5">
      <div className="w-10 h-10 rounded-full flex-none flex items-center justify-center text-app-text" style={{ background: 'var(--nm-fill)' }}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[15px] font-medium leading-snug">{title}</p>
        <p className="text-[13px] text-app-text-muted leading-[17px]">{subtitle}</p>
      </div>
    </div>
  );
}

/** The action pill: MuseTones.action, 50dp, fully round. */
function Pill({ busy, disabled, onClick, children }: { busy?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      disabled={busy || disabled}
      onClick={onClick}
      className="w-full h-[50px] rounded-full bg-app-primary text-app-on-primary text-[16px] font-semibold flex items-center justify-center gap-2 active:scale-[0.98] transition disabled:opacity-60"
    >
      {busy && <IcRetry size={18} className="animate-spin" />}
      {children}
    </button>
  );
}

function TextButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="h-10 px-3 text-[14px] font-medium text-app-primary">
      {children}
    </button>
  );
}

function TextLink({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="mt-1 text-[12.5px] text-app-text-muted underline underline-offset-2">
      {children}
    </button>
  );
}

function Field({
  type,
  value,
  onChange,
  placeholder,
  label,
  inputMode,
}: {
  type: 'url' | 'text' | 'password';
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  label?: string;
  inputMode?: 'url' | 'email' | 'numeric';
}) {
  const input = (
    <input
      type={type}
      inputMode={inputMode ?? (type === 'url' ? 'url' : undefined)}
      autoCapitalize="off"
      autoCorrect="off"
      spellCheck={false}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="h-12 w-full rounded-2xl bg-app-surface border border-app-border px-4 text-[15px] outline-none focus:border-app-primary"
    />
  );
  if (!label) return input;
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12.5px] font-medium text-app-text-muted">{label}</span>
      {input}
    </label>
  );
}

/** Open the server's WebSocket once: `hello` means reachable and the token is good. */
function probe(serverUrl: string, token: string): Promise<'ok' | 'unauthorized' | 'unreachable'> {
  return new Promise((resolve) => {
    let url: URL;
    try {
      url = new URL(serverUrl);
    } catch {
      resolve('unreachable');
      return;
    }
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = '/ws';
    url.search = '';
    let done = false;
    let ws: WebSocket | null = null;
    const finish = (r: 'ok' | 'unauthorized' | 'unreachable') => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try {
        ws?.close();
      } catch {
        // ignore
      }
      resolve(r);
    };
    const timer = setTimeout(() => finish('unreachable'), 6000);
    try {
      ws = new WebSocket(url.toString());
    } catch {
      finish('unreachable');
      return;
    }
    ws.onopen = () => {
      if (token) ws?.send(JSON.stringify({ kind: 'auth', token }));
    };
    ws.onmessage = () => finish('ok');
    ws.onclose = (ev) => finish(ev.code === 4401 ? 'unauthorized' : 'unreachable');
    ws.onerror = () => {
      /* onclose follows */
    };
  });
}
