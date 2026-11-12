import { ArrowRight, CloudCog, KeyRound, LogIn } from "lucide-react";
import { useEffect, useId, useState, useRef } from "react";
import { useT } from "../i18n";
import { useFocusTrap } from "./useFocusTrap";
import { useStore } from "../store";
import { cx } from "../util";
import { ownKeyWay } from "../region";
import { openOwnKeySetup } from "./AllowanceWays";
import { primaryBtn, secondaryBtn } from "./Form";

/**
 * Which model answers, right after an account is created: the account's own, or a key of
 * one's own. It can be changed later under Connections. The flag lives in sessionStorage
 * so a reload does not lose the step, and is cleared once it is done.
 */
const FLAG = "nm.cloud.first_sign_in";
const EVENT = "nm:first-sign-in";

/** Called by the sign-in form when the relay reports a new account. */
export function markFirstSignIn(): void {
  try {
    sessionStorage.setItem(FLAG, "1");
  } catch {
    /* storage may be off: the steps are simply not shown */
  }
  window.dispatchEvent(new Event(EVENT));
}

function clearFirstSignIn(): void {
  try {
    sessionStorage.removeItem(FLAG);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event(EVENT));
}

function pending(): boolean {
  try {
    return sessionStorage.getItem(FLAG) === "1";
  } catch {
    return false;
  }
}

/** True while the two steps are owed to a freshly created account. */
export function useFirstSignIn(): boolean {
  const [on, setOn] = useState(pending);
  useEffect(() => {
    const sync = () => setOn(pending());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return on;
}

export function FirstSignInSteps() {
  const t = useT();
  const { refreshHub, setTab, state } = useStore();
  const way = ownKeyWay(state.hub?.account);

  const done = async () => {
    clearFirstSignIn();
    await refreshHub().catch(() => undefined);
  };

  const ownKey = async () => {
    await done();
    openOwnKeySetup(setTab, way.preset);
  };

  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusTrap(panel, true);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-[2px] sm:items-center" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div ref={panel} className="safe-bottom w-full max-w-[520px] rounded-t-[28px] bg-bg p-6 shadow-2xl sm:rounded-[28px]">
          <div className="space-y-4">
            <div className="flex items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent/12 text-accent">
                <CloudCog size={20} />
              </span>
              <div>
                <h2 id={titleId} className="text-[20px] font-bold tracking-tight">{t("Which model answers?")}</h2>
                <p className="mt-1 text-[13.5px] leading-relaxed text-muted">{t("Your account already brings one. You can add your own API key as well and switch at any time.")}</p>
              </div>
            </div>
            <ul className="space-y-2 text-[13px] leading-snug">
              <li className="flex items-start gap-2.5 rounded-2xl bg-surface-2 px-3 py-2.5">
                <CloudCog size={16} className="mt-0.5 shrink-0 text-accent" />
                <span>
                  <span className="font-medium">{t("Use the nanoMuse Cloud model")}</span>
                  <span className="block text-muted">{t("DeepSeek for the chat and Qwen for the hands, with a free allowance per account paid by the developer. Nothing to configure.")}</span>
                </span>
              </li>
              <li className="flex items-start gap-2.5 rounded-2xl bg-surface-2 px-3 py-2.5">
                <KeyRound size={16} className="mt-0.5 shrink-0 text-accent" />
                <span>
                  <span className="font-medium">{t("I have my own API key")}</span>
                  <span className="block text-muted">
                    {way.preset === "qwen"
                      ? t("Alibaba Cloud Bailian first (one key covers chat, the hands, pictures and clips), then DeepSeek, Kimi, Zhipu, OpenAI, OpenRouter and any OpenAI-compatible endpoint. The key stays on this device.")
                      : t("OpenRouter first (one account, one key, pay as you go), then OpenAI, Anthropic, Gemini, DeepSeek and any OpenAI-compatible endpoint. The key stays on this device.")}
                  </span>
                </span>
              </li>
              <li className="flex items-start gap-2.5 rounded-2xl bg-surface-2 px-3 py-2.5">
                <LogIn size={16} className="mt-0.5 shrink-0 text-accent" />
                <span>
                  <span className="font-medium">{t("Sign in with a plan you already pay for")}</span>
                  <span className="block text-muted">{t("A ChatGPT plan signs in from Connections and answers the chat and the hands. Pictures and clips still want a key.")}</span>
                </span>
              </li>
            </ul>
            <div className="flex gap-2">
              <button type="button" onClick={() => void ownKey()} className={secondaryBtn}>
                {t("I have my own API key")}
              </button>
              <button type="button" onClick={() => void done()} className={cx(primaryBtn, "flex-1 py-3")}>
                {t("Use the nanoMuse Cloud model")} <ArrowRight size={16} />
              </button>
            </div>
          </div>
      </div>
    </div>
  );
}
