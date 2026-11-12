import { Cloud, Loader2, LogOut } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { CloudAccount } from "../types";
import { Card, primaryBtn, secondaryBtn } from "./Form";
import { MuseSwitchRow } from "./MuseList";
import { SignIn } from "./SignIn";

/**
 * nanoMuse Cloud: the free account that gives every device of yours one place to meet
 * (the hub) and a model to start with (the relay). The sign-in is the one form the whole
 * app uses (`SignIn`: a code, the invite code, the SMS note); the key lands
 * in the vault on this machine.
 */
export function CloudCard({
  account,
  onChange,
  compact = false,
  /** after signing in, make the relay the model provider (first-run setup) */
  useAsModel = false,
}: {
  account: CloudAccount | null;
  onChange: () => void;
  compact?: boolean;
  useAsModel?: boolean;
}) {
  const { toast } = useStore();
  const t = useT();
  const [open, setOpen] = useState(compact || !account?.signed_in);
  const [busy, setBusy] = useState<"model" | "out" | "switch" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const signedIn = !!account?.signed_in;

  const makeModel = async () => {
    setBusy("model");
    setError(null);
    try {
      await api.cloudUseAsModel();
      toast(t("The Cloud model is in use."));
      onChange();
    } catch (e) {
      setError(t((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  // the account's models as a source; the sign-in stays whatever this says
  const modelsOn = account?.models !== false;
  const setModels = async (on: boolean) => {
    setBusy("switch");
    setError(null);
    try {
      await api.cloudModels(on);
      onChange();
    } catch (e) {
      setError(t((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const signOut = async () => {
    if (!window.confirm(t("Sign out of nanoMuse Cloud on this device? The hub and the Cloud model stop working here until you sign in again."))) return;
    setBusy("out");
    try {
      await api.cloudSignOut();
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const status = signedIn ? { text: t("Signed in"), tone: "ok" } : { text: t("Not signed in"), tone: "off" };
  const summary = signedIn
    ? account?.is_model
      ? t("{hint} · model and hub", { hint: account.hint })
      : t("{hint} · hub", { hint: account?.hint ?? "" })
    : t("Free. One account for all your devices, and a model to start with.");

  return (
    <Card
      icon={<Cloud size={20} />}
      title={t("nanoMuse Cloud")}
      summary={summary}
      status={status}
      open={open}
      onToggle={compact ? undefined : () => setOpen((o) => !o)}
    >
      {signedIn ? (
        <div className="space-y-3">
          <p className="text-[13px] text-muted leading-relaxed">
            {t("Signed in as {hint}. Your devices meet here; the Cloud model comes with a free allowance.", { hint: account?.hint ?? "" })}
          </p>
          <div className="-mx-4 border-y border-border/60">
            <MuseSwitchRow label={t("Use nanoMuse Cloud models")} checked={modelsOn} disabled={busy !== null} onChange={(v) => void setModels(v)} />
          </div>
          <p className="text-[12.5px] text-muted leading-relaxed">
            {modelsOn
              ? t("nanoMuse Cloud is one of the sources for the chat, the hands, pictures and clips; what runs on it comes off your allowance.")
              : t("Off: nothing runs on nanoMuse Cloud unless you choose it yourself. You stay signed in for sync and your devices.")}
          </p>
          <div className="flex flex-wrap gap-2">
            {!account?.is_model && (
              <button type="button" disabled={busy !== null} onClick={() => void makeModel()} className={primaryBtn}>
                {busy === "model" ? <Loader2 size={15} className="animate-spin" /> : null} {t("Use the Cloud model")}
              </button>
            )}
            <button type="button" disabled={busy !== null} onClick={() => void signOut()} className={secondaryBtn}>
              <LogOut size={15} /> {t("Sign out")}
            </button>
          </div>
          {error && <ErrorLine text={error} />}
        </div>
      ) : (
        <SignIn
          useAsModel={useAsModel}
          autoFocus={false}
          onSignedIn={() => {
            toast(t("Signed in to nanoMuse Cloud."));
            onChange();
          }}
        />
      )}
    </Card>
  );
}

function ErrorLine({ text }: { text: string }) {
  return <div className="rounded-2xl bg-rose-500/12 px-3 py-2 text-[12.5px] text-rose-700 dark:text-rose-300">{text}</div>;
}
