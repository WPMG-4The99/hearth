import { Download, Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { CloudMe, SyncState } from "../types";
import { cx } from "../util";
import { MuseCaption, MuseCard, MuseDivider, MuseRow, MuseSwitchRow } from "./MuseList";

/** The policy the apps link when the relay has not said where its own is. */
export const PRIVACY_URL = "https://github.com/WPMG-4The99/hearth/blob/main/docs/privacy.md";

/**
 * Settings → Data controls, in the shape of Muse's: one switch, *Help improve nanoMuse's AI
 * models*, with what it means written under it in full — what the relay keeps while it is
 * on (what you wrote, what the model answered, the tool calls it chose), what it never
 * keeps (the system prompt, tool results, pictures, who you are), the relay's default for
 * new accounts, how many turns are kept so far — and the delete. It applies to the nanoMuse
 * Cloud account only: with your own key nothing passes through the relay, and the switch is
 * shown but cannot be moved until you sign in.
 */
export function DataControls({ me: given, onChanged, flush = false }: { me?: CloudMe | null; onChanged?: () => void; /** the parent already pads 16px: pull the cards out to its edge */ flush?: boolean }) {
  const t = useT();
  const { state, toast } = useStore();
  const signedIn = !!state.hub?.account.signed_in;
  const [loaded, setLoaded] = useState<CloudMe | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const me = given ?? loaded;

  const load = async () => {
    if (!signedIn || given) return;
    try {
      setLoaded(await api.cloudMe());
    } catch {
      /* offline: the switch stays as it was */
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, given]);

  const ct = me?.contribute ?? { on: false, samples: 0 };
  const samples = ct.samples ?? 0;
  const privacy = ct.privacy_url || PRIVACY_URL;

  const flip = async (on: boolean) => {
    setBusy(true);
    try {
      await api.cloudContribute(on);
      toast(on ? t("On. Turn it off here at any time.") : t("Off. Nothing more is kept."));
      if (given) onChanged?.();
      else await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const wipe = async () => {
    if (!window.confirm(t("The kept conversations are removed from the server. This cannot be undone."))) return;
    setBusy(true);
    try {
      const r = await api.cloudDeleteSamples();
      toast(t("{n} turns deleted", { n: String(r.deleted) }));
      if (given) onChanged?.();
      else await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** *Download your agent data* (parity note 37): the runtime answers with the zip. */
  const download = async () => {
    setExporting(true);
    try {
      const { blob, name } = await api.exportData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className={cx(flush && "-mx-4")}>
      <SyncControls signedIn={signedIn} />
      <MuseCard>
        <MuseSwitchRow label={t("Help improve nanoMuse's AI models")} checked={signedIn && ct.on} disabled={busy || !signedIn || !me} onChange={(v) => void flip(v)} />
      </MuseCard>
      <MuseCaption>
        {t(
          "While this is on, the text of your chats with the nanoMuse Cloud models (what you wrote, what it answered and the tools it chose to call) is kept on the relay to train the community's own open model. Not your memory or SOUL (the system prompt), not what tools returned, not pictures, and never next to who you are. Your own API key never passes through the relay.",
        )}{" "}
        {!signedIn
          ? t("Sign in to nanoMuse Cloud to use it.")
          : ct.default_on !== undefined && (ct.default_on ? t("New accounts start with it on.") : t("New accounts start with it off."))}{" "}
        {signedIn && samples > 0 && t("{n} turns kept so far.", { n: String(samples) })}{" "}
        <a href={privacy} target="_blank" rel="noopener noreferrer" className="text-accent underline-offset-2 hover:underline">
          {t("Privacy policy")}
        </a>
      </MuseCaption>
      {signedIn && samples > 0 && (
        <MuseCard className="mt-3">
          <MuseRow icon={busy ? <Loader2 size={22} className="animate-spin" /> : <Trash2 size={22} />} label={t("Delete the kept conversations")} value={t("{n} turns", { n: String(samples) })} onClick={() => void wipe()} chevron={false} />
          <MuseDivider inset={16} />
          <MuseCaption className="px-4 pb-2.5 pt-2">{t("Removes every turn kept from this account, whether the switch is on or off now. Turning the switch off keeps what was kept until you delete it here.")}</MuseCaption>
        </MuseCard>
      )}
      <MuseCard className="mt-3">
        <MuseRow icon={exporting ? <Loader2 size={22} className="animate-spin" /> : <Download size={22} />} label={t("Download your agent data")} onClick={() => void download()} chevron={false} />
        <MuseDivider inset={16} />
        <MuseCaption className="px-4 pb-2.5 pt-2">{t("A zip of what this runtime keeps about you: the memory, the goals, the reminders, the triggers, the address book and the chats. It never contains the keys themselves.")}</MuseCaption>
      </MuseCard>
    </div>
  );
}

/**
 * The first card of Data controls (contract C7): *Sync conversations between my devices*, on
 * by default while signed in. Off tells the relay, which deletes what it stores; on pushes
 * this device's chats again. *Delete synced conversations* empties the relay's store and
 * keeps the switch and the chats on every device.
 */
export function SyncControls({ signedIn }: { signedIn: boolean }) {
  const t = useT();
  const { toast } = useStore();
  const [sync, setSync] = useState<SyncState | null>(null);
  const [busy, setBusy] = useState(false);
  const counts = sync?.relay?.counts ?? { conversations: 0, messages: 0 };
  const stored = signedIn && (counts.conversations > 0 || counts.messages > 0);

  const load = async () => {
    try {
      setSync(await api.syncState());
    } catch {
      /* an older runtime without sync: the card shows the switch as unavailable */
    }
  };
  useEffect(() => {
    void load();
  }, [signedIn]);

  const run = async (what: () => Promise<SyncState>, said: string) => {
    setBusy(true);
    try {
      setSync(await what());
      toast(said);
    } catch (e) {
      toast((e as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  };
  const flip = (on: boolean) => {
    if (!on && stored && !window.confirm(t("Off deletes the synced conversations from nanoMuse Cloud. The chats on each device stay."))) return;
    void run(
      () => api.syncSetState(on),
      on ? t("On. Your devices show the same conversations from now on.") : t("Off. Nothing is kept on nanoMuse Cloud any more."),
    );
  };
  const wipe = () => {
    if (!window.confirm(t("The synced conversations are removed from nanoMuse Cloud. The chats on each device stay. This cannot be undone."))) return;
    void run(() => api.syncDelete(), t("Synced conversations deleted."));
  };
  // C9: side chats, per device — off by default; on brings the other devices' over once
  const flipSide = (on: boolean) => {
    void run(
      () => api.syncSetSideChats(on),
      on ? t("On. This device's side chats go to the account; the other devices' are on their way.") : t("Off. Side chats stay on this device from now on."),
    );
  };
  const syncOn = signedIn && !!sync?.enabled;

  return (
    <div className="mb-6">
      <MuseCard>
        <MuseSwitchRow label={t("Sync conversations between my devices")} checked={syncOn} disabled={busy || !signedIn || !sync} onChange={flip} />
      </MuseCard>
      <MuseCaption>
        {t("The text of your chats is kept on nanoMuse Cloud so every device shows the same conversations. Files and images stay on the device they were made on.")}{" "}
        {!signedIn ? t("Sign in to nanoMuse Cloud to use it.") : sync?.paused ? t("Paused: sign in again to continue.") : stored && t("{c} chats, {m} messages kept so far.", { c: String(counts.conversations), m: String(counts.messages) })}
      </MuseCaption>
      {sync && "side_chats" in sync && (
        <>
          <MuseCard className="mt-3">
            <MuseSwitchRow label={t("Also sync side chats")} checked={syncOn && !!sync.side_chats} disabled={busy || !syncOn} onChange={flipSide} />
          </MuseCard>
          <MuseCaption>{t("Off: side chats stay on this device. On: this device's side chats go to the account and the other devices' side chats come here.")}</MuseCaption>
        </>
      )}
      {stored && (
        <MuseCard className="mt-3">
          <MuseRow icon={busy ? <Loader2 size={22} className="animate-spin" /> : <Trash2 size={22} />} label={t("Delete synced conversations")} value={t("{n} chats", { n: String(counts.conversations) })} onClick={wipe} chevron={false} />
          <MuseDivider inset={16} />
          <MuseCaption className="px-4 pb-2.5 pt-2">{t("Removes what nanoMuse Cloud stores for this account; the chats on each device stay, and the switch stays on. Deleting a chat on one device deletes it on all of them.")}</MuseCaption>
        </MuseCard>
      )}
    </div>
  );
}
