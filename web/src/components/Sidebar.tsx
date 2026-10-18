import { Bug, LayoutGrid, Lightbulb, Menu, MessageCircle, MonitorSmartphone, Newspaper, Search, SlidersHorizontal, SquareCheckBig, TerminalSquare, UserRound, Wrench, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { desktopBridge } from "../desktop";
import { useDeveloperTools } from "../devtools";
import { useT } from "../i18n";
import { ThreadList } from "../screens/ChatScreen";
import { MuseSheet } from "../screens/MuseSheet";
import { useStore, type Tab } from "../store";
import { cx } from "../util";
import { Avatar } from "./Avatar";
import { useMenuKeys } from "./useMenuKeys";

/** Where a bug goes, with what the page knows filled in. */
function bugReportUrl(version: string): string {
  const bridge = desktopBridge();
  const where = bridge ? `Desktop app (${bridge.platform}, shell ${bridge.version})` : `Web app (${navigator.userAgent})`;
  const params = new URLSearchParams({ template: "bug_report.yml", version: version || "", os: where });
  return `https://github.com/WPMG-4The99/hearth/issues/new?${params.toString()}`;
}

/**
 * The left side on a wide screen (the desktop window, a browser at full width), the way
 * Muse lays it out: a rail of icons — the agent on top, the five sections, the devices —
 * and, when the chat is open, the chats beside it (search, the main chat, the side chats).
 * Account, Settings and a small menu (a bug report, the version, the developer side) sit
 * at the bottom of the rail. Same store, same screens as the phone; nothing here is a
 * second implementation of anything.
 */
export function Sidebar() {
  const { state, setTab, openThread } = useStore();
  const t = useT();
  const [activityOpen, setActivityOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const developer = useDeveloperTools();
  const { profile, status } = state;
  const name = profile?.name ?? "nanoMuse";

  const pendingApprovals = state.pendingApprovals.length;
  const proposals = state.goals.filter((g) => g.proposal && g.status !== "cancelled").length;
  const feedUnseen = state.pendingApprovals.filter((a) => a.ts > state.feedSeenAt).length;
  const online = (state.hub?.devices ?? []).filter((d) => !d.this && d.kind !== "web" && d.online).length;

  const statusLine =
    pendingApprovals > 0
      ? pendingApprovals > 1
        ? t("{n} approvals waiting for you", { n: pendingApprovals })
        : t("1 approval waiting for you")
      : status.state === "idle"
        ? t("Idle")
        : status.detail || (status.state === "waiting" ? t("Waiting for you") : t("Working…"));

  // the menu closes on a click elsewhere or Escape; arrow keys walk its entries
  useMenuKeys(menuRef, menuOpen, () => setMenuOpen(false));

  const rail: Array<{ id: Tab; label: string; icon: ReactNode; badge?: number; dot?: boolean }> = [
    { id: "chat", label: "Chat", icon: <MessageCircle size={21} />, badge: pendingApprovals },
    { id: "feed", label: "Feed", icon: <Newspaper size={21} />, badge: feedUnseen },
    { id: "ideas", label: "Ideas", icon: <Lightbulb size={21} /> },
    { id: "goals", label: "Goals", icon: <SquareCheckBig size={21} />, badge: proposals },
    { id: "library", label: "Library", icon: <LayoutGrid size={21} /> },
    { id: "devices", label: "Devices", icon: <MonitorSmartphone size={21} />, dot: online > 0 },
    ...(developer ? [{ id: "coding" as Tab, label: "Coding", icon: <TerminalSquare size={21} /> }] : []),
  ];

  const q = query.trim().toLowerCase();
  const threads = q
    ? state.threads.filter((th) => th.id === "main" || (th.id === "main" ? t(th.title) : th.title).toLowerCase().includes(q) || (th.device_name ?? "").toLowerCase().includes(q))
    : state.threads;

  const railButton = (active: boolean) =>
    cx(
      "relative flex w-[60px] flex-col items-center gap-[3px] rounded-xl py-1.5 text-[10.5px] font-medium leading-none transition",
      active ? "bg-surface-2 text-fg" : "text-fg/65 hover:bg-surface-2/60 hover:text-fg",
    );

  return (
    <div className="flex h-full shrink-0">
      {/* the rail */}
      <aside className="relative flex h-full w-[76px] shrink-0 flex-col items-center border-r border-border bg-surface/70">
        <div className="titlebar-room" />
        <button
          type="button"
          onClick={() => setActivityOpen(true)}
          className="relative mt-3 rounded-full transition hover:scale-[1.04]"
          aria-label={t("Activity")}
          title={`${name} · ${statusLine}`}
        >
          <Avatar profile={profile} status={status} size={44} />
          {pendingApprovals > 0 && (
            <span className="pointer-events-none absolute -right-1 -top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-2 border-surface bg-rose-500 px-1 text-[10.5px] font-bold text-white">
              {pendingApprovals}
            </span>
          )}
        </button>
        <span className="mt-1.5 max-w-[68px] truncate px-1 text-[11px] font-semibold leading-tight">{name}</span>

        <nav className="mt-3 flex w-full flex-1 flex-col items-center gap-0.5 overflow-y-auto overflow-x-hidden px-2">
          {rail.map((item) => {
            const active = state.tab === item.id;
            return (
              <button key={item.id} type="button" onClick={() => setTab(item.id)} aria-current={active ? "page" : undefined} className={railButton(active)}>
                <span className={cx(active ? "text-accent" : "text-fg/60")}>{item.icon}</span>
                <span className="max-w-full truncate">{t(item.label)}</span>
                {item.badge ? (
                  <span className="absolute right-1.5 top-1 flex h-[17px] min-w-[17px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white">
                    {item.badge}
                  </span>
                ) : item.dot ? (
                  <>
                    <span aria-hidden="true" className="absolute right-3 top-2 h-2 w-2 rounded-full border-2 border-surface bg-emerald-500" />
                    <span className="sr-only">{t("{n} online", { n: online })}</span>
                  </>
                ) : null}
              </button>
            );
          })}
        </nav>

        <div className="flex w-full flex-col items-center gap-0.5 px-2 pb-3 pt-2">
          <button
            type="button"
            onClick={() => setTab("account")}
            aria-current={state.tab === "account" ? "page" : undefined}
            className={railButton(state.tab === "account")}
            title={state.hub?.account.signed_in ? state.hub.account.hint : t("Account")}
          >
            <UserRound size={21} className={state.tab === "account" ? "text-accent" : "text-fg/60"} />
            <span className="max-w-full truncate">{t("Account")}</span>
          </button>
          <button type="button" onClick={() => setTab("you")} aria-current={state.tab === "you" ? "page" : undefined} className={railButton(state.tab === "you")}>
            <SlidersHorizontal size={21} className={state.tab === "you" ? "text-accent" : "text-fg/60"} />
            <span className="max-w-full truncate">{t("Settings")}</span>
          </button>
          <div ref={menuRef} className="relative w-full">
            <button type="button" onClick={() => setMenuOpen((v) => !v)} aria-expanded={menuOpen} aria-haspopup="menu" className={cx(railButton(menuOpen), "mx-auto")}>
              <Menu size={21} className="text-fg/60" />
              <span>{t("More")}</span>
            </button>
            {menuOpen && (
              <div role="menu" className="absolute bottom-0 left-[68px] z-30 w-[232px] rounded-2xl border border-border bg-surface p-1.5 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.3)]">
                <a
                  role="menuitem"
                  href={bugReportUrl(state.version)}
                  target="_blank"
                  rel="noreferrer"
                  onClick={() => setMenuOpen(false)}
                  className="flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-[13px] font-medium hover:bg-surface-2"
                >
                  <Bug size={16} className="text-fg/60" /> {t("Report a bug")}
                </a>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setTab("you");
                    window.location.hash = "developer";
                  }}
                  className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13px] font-medium hover:bg-surface-2"
                >
                  <Wrench size={16} className="text-fg/60" />
                  <span className="flex-1">{t("Developer tools")}</span>
                  <span className="text-[11.5px] font-normal text-muted">{developer ? t("On") : t("Off")}</span>
                </button>
                {state.version && <div className="px-2.5 pb-1 pt-1.5 text-[11.5px] text-muted">nanoMuse {state.version}</div>}
              </div>
            )}
          </div>
        </div>
        <MuseSheet open={activityOpen} onClose={() => setActivityOpen(false)} />
      </aside>

      {/* the chats, beside the rail while the chat is open */}
      {state.tab === "chat" && (
        <section className="flex h-full w-[248px] shrink-0 flex-col border-r border-border bg-surface/40" aria-label={t("Chats")}>
          <div className="titlebar-room" />
          <div className="px-3 pb-2 pt-4">
            <h2 className="px-1 text-[15px] font-semibold tracking-tight">{t("Chats")}</h2>
            <label className="mt-2 flex items-center gap-2 rounded-xl bg-surface-2 px-2.5 py-1.5 text-muted focus-within:ring-2 focus-within:ring-accent/40">
              <Search size={14} className="shrink-0" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search")}
                aria-label={t("Search chats")}
                className="min-w-0 flex-1 bg-transparent text-[13px] text-fg outline-none placeholder:text-muted"
              />
              {query && (
                <button type="button" onClick={() => setQuery("")} aria-label={t("Clear")} className="shrink-0 hover:text-fg">
                  <X size={14} />
                </button>
              )}
            </label>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            <ThreadList threads={threads} active={state.activeThread} onPick={(id) => openThread(id)} compact grouped />
          </div>
        </section>
      )}
    </div>
  );
}
