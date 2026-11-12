import { Cloud, Hand, MonitorSmartphone, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { BrandMark } from "../components/BrandMark";
import { CommunityNotice } from "../components/CommunityNotice";
import { PRIVACY_URL } from "../components/DataControls";
import { SignIn } from "../components/SignIn";
import { useT } from "../i18n";
import { useStore } from "../store";

/**
 * The door: this runtime insists on a nanoMuse Cloud account (cloud.required, the default),
 * so before anything else the person signs in with a code. What comes with it is said
 * plainly; the choice between the Cloud model and
 * a key of their own follows in setup. The door is not a wall: *Use your own API key
 * instead* steps past it into the setup's own-key path, and the sign-in waits under
 * Connections.
 *
 * On a phone it is one column; in a wide window (the desktop app, a browser from 1024px)
 * the words sit on the left and the form on the right, both centred — a landscape page
 * for a landscape window, not a phone page in the middle of it.
 */
export function SignInGate() {
  const { state, refreshSettings, refreshHub, toast, skipSignIn } = useStore();
  const t = useT();
  const name = state.profile?.name ?? "nanoMuse";
  const perks = (
    <ul className="grid grid-cols-2 gap-2 text-[12.5px] wide:grid-cols-1 wide:gap-1.5 wide:text-[13.5px]">
      <Perk icon={<MonitorSmartphone size={15} />} text={t("Every device of yours, one nanoMuse")} />
      <Perk icon={<Cloud size={15} />} text={t("A model with a free allowance, or your own key")} />
      <Perk icon={<Hand size={15} />} text={t("It uses your phone and computers for you")} />
      <Perk icon={<ShieldCheck size={15} />} text={t("Keys in your vault; nothing you say is kept on the relay")} />
    </ul>
  );
  return (
    <div className="relative mx-auto flex h-[100dvh] w-full max-w-[760px] flex-col overflow-hidden bg-bg sm:border-x sm:border-border wide:max-w-none wide:border-0">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[46vh] bg-[radial-gradient(ellipse_at_top,rgba(0,100,212,0.14),transparent_65%)] dark:bg-[radial-gradient(ellipse_at_top,rgba(23,147,255,0.2),transparent_65%)] wide:inset-y-0 wide:h-auto wide:bg-[radial-gradient(ellipse_at_left,rgba(0,100,212,0.12),transparent_60%)] wide:dark:bg-[radial-gradient(ellipse_at_left,rgba(23,147,255,0.16),transparent_60%)]" />
      <div className="titlebar-room relative" />
      <div className="relative flex-1 overflow-y-auto px-6 pb-8 wide:flex wide:items-center wide:px-12">
        <div className="mx-auto w-full wide:grid wide:max-w-[980px] wide:grid-cols-[1.1fr_1fr] wide:items-center wide:gap-16">
          {/* the words */}
          <div className="safe-top flex flex-col items-center pt-14 text-center wide:items-start wide:pt-0 wide:text-left">
            {/* the door is the app's, so the app's mark — the face comes once there is a Muse to meet */}
            <BrandMark size={88} className="drop-shadow-[0_20px_40px_rgba(0,100,212,0.28)]" />
            <h1 className="mt-6 text-[28px] font-bold tracking-tight wide:text-[34px]">{t("Sign in to {name}", { name })}</h1>
            <p className="mt-2 max-w-sm text-[14.5px] leading-relaxed text-muted wide:max-w-md wide:text-[15.5px]">
              {t("One free account. It is what lets your phone and computers work as one and brings a model to start with.")}
            </p>
            <div className="mt-7 hidden w-full wide:block">{perks}</div>
            <CommunityNotice compact className="mt-6 hidden w-full wide:block" />
          </div>

          {/* the form */}
          <div>
            <div className="mt-8 rounded-[28px] border border-border/70 bg-surface p-5 shadow-sm wide:mt-0 wide:p-7">
              <SignIn
                onSignedIn={async () => {
                  toast(t("Signed in to nanoMuse Cloud."));
                  await Promise.all([refreshSettings(), refreshHub()]);
                }}
              />
            </div>
            <button
              type="button"
              onClick={skipSignIn}
              className="mt-4 w-full rounded-2xl border border-border/70 px-4 py-2.5 text-[13.5px] text-fg/80 hover:bg-surface-2"
              data-testid="sign-in-skip"
            >
              {t("Use your own API key instead")}
            </button>
            <p className="mt-2 text-center text-[11.5px] leading-relaxed text-muted">{t("You can sign in later under Connections, for sync and your devices.")}</p>
            <div className="mt-6 wide:hidden">{perks}</div>
            <CommunityNotice compact className="mt-6 wide:hidden" />
            <p className="mt-4 text-center text-[11.5px] leading-relaxed text-muted wide:mt-5">
              {t("The relay keeps an account id, a masked identifier, usage counts and your agent's name and look; what else, and what is yours to switch off, is in the")}{" "}
              <a href={PRIVACY_URL} target="_blank" rel="noopener noreferrer" className="underline decoration-dotted underline-offset-2 hover:text-fg">
                {t("privacy policy")}
              </a>
              .
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Perk({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <li className="flex items-start gap-2 rounded-2xl bg-surface-2/60 px-3 py-2.5 text-fg/80 wide:bg-transparent wide:px-0 wide:py-1">
      <span className="mt-0.5 text-accent">{icon}</span>
      <span className="leading-snug">{text}</span>
    </li>
  );
}
