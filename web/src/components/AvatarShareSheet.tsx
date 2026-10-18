import { Download, Loader2, Share2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { dragonUrl, isDragon, studioUrl } from "../avatars";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { Profile } from "../types";
import { cx } from "../util";
import { primaryBtn, secondaryBtn } from "./Form";
import { Sheet } from "./Sheet";

/**
 * "Share my avatar", as the Android app has it (`avatar/AvatarShare.kt`): five cards — a
 * background, one of the face's poses, a speech bubble introducing it, the app's tagline —
 * drawn on a canvas so the picture is the same wherever it goes. Pick one; it goes out
 * through the browser's share sheet where there is one (phones, Safari), and downloads as
 * a PNG otherwise.
 */

interface Palette {
  id: string;
  background: string;
  accent: string;
  mood: string;
}

const PALETTES: Palette[] = [
  { id: "pink", background: "#F9D9E3", accent: "#B5476A", mood: "idle" },
  { id: "blue", background: "#D6E6FA", accent: "#2F5FA8", mood: "working" },
  { id: "yellow", background: "#FBEFC7", accent: "#9A6B12", mood: "waiting" },
  { id: "purple", background: "#E4DDF7", accent: "#5D44A6", mood: "happy" },
  { id: "green", background: "#D9F0DF", accent: "#2E7D4F", mood: "error" },
];

const REPO = "github.com/WPMG-4The99/hearth";
// the card, in CSS pixels; drawn at 2× for a crisp picture
const W = 540;
const H = 720;

function faceUrl(profile: Profile | null, mood: string): string | null {
  if (!profile || isDragon(profile.avatar)) return dragonUrl(mood);
  if (profile.avatar) return studioUrl(profile.avatar, mood);
  return null;
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  // by words where there are spaces, by character otherwise (中文)
  const units = text.includes(" ") ? text.split(" ") : Array.from(text);
  const joiner = text.includes(" ") ? " " : "";
  const lines: string[] = [];
  let line = "";
  for (const u of units) {
    const probe = line ? line + joiner + u : u;
    if (ctx.measureText(probe).width > maxWidth && line) {
      lines.push(line);
      line = u;
    } else {
      line = probe;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Draw one card; returns the canvas. */
async function render(palette: Palette, profile: Profile | null, name: string, bubble: string, tagline: string, scale = 2): Promise<HTMLCanvasElement> {
  const canvas = document.createElement("canvas");
  canvas.width = W * scale;
  canvas.height = H * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;
  ctx.scale(scale, scale);
  // the background
  ctx.fillStyle = palette.background;
  roundRect(ctx, 0, 0, W, H, 36);
  ctx.fill();
  // the speech bubble at the top
  ctx.font = "600 22px system-ui, -apple-system, 'Segoe UI', sans-serif";
  const lines = wrap(ctx, bubble, W - 140);
  const textWidth = Math.max(...lines.map((l) => ctx.measureText(l).width));
  const padX = 24;
  const padY = 18;
  const lineH = 30;
  const bubbleW = textWidth + padX * 2;
  const bubbleH = lines.length * lineH + padY * 2;
  const bx = (W - bubbleW) / 2;
  const by = 56;
  ctx.fillStyle = "#ffffff";
  roundRect(ctx, bx, by, bubbleW, bubbleH, 24);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(W / 2 - 14, by + bubbleH - 1);
  ctx.lineTo(W / 2, by + bubbleH + 18);
  ctx.lineTo(W / 2 + 14, by + bubbleH - 1);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = palette.accent;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  lines.forEach((l, i) => ctx.fillText(l, W / 2, by + padY + i * lineH + 3));
  // the face, in a white ring
  const faceSize = 300;
  const fx = (W - faceSize) / 2;
  const fy = by + bubbleH + 54;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(W / 2, fy + faceSize / 2, faceSize / 2 + 8, 0, Math.PI * 2);
  ctx.fill();
  const url = faceUrl(profile, palette.mood);
  const img = url ? await loadImage(url) : null;
  ctx.save();
  ctx.beginPath();
  ctx.arc(W / 2, fy + faceSize / 2, faceSize / 2, 0, Math.PI * 2);
  ctx.clip();
  if (img) {
    ctx.fillStyle = "#f1efeb";
    ctx.fillRect(fx, fy, faceSize, faceSize);
    ctx.drawImage(img, fx, fy, faceSize, faceSize);
  } else {
    ctx.fillStyle = profile?.color ?? "#0064d4";
    ctx.fillRect(fx, fy, faceSize, faceSize);
    ctx.font = `${faceSize * 0.5}px system-ui, 'Apple Color Emoji', 'Segoe UI Emoji', sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#ffffff";
    ctx.fillText(profile?.emoji ?? "✨", W / 2, fy + faceSize / 2 + 6);
  }
  ctx.restore();
  // the name and the tagline
  ctx.fillStyle = palette.accent;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.font = "700 30px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText(name, W / 2, fy + faceSize + 34);
  ctx.font = "500 15px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.globalAlpha = 0.8;
  wrap(ctx, tagline, W - 80).forEach((l, i) => ctx.fillText(l, W / 2, H - 76 + i * 22));
  ctx.globalAlpha = 1;
  return canvas;
}

function toBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

export function AvatarShareSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { state, toast } = useStore();
  const t = useT();
  const profile = state.profile;
  const name = profile?.name ?? "nanoMuse";
  const [selected, setSelected] = useState(0);
  const [previews, setPreviews] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const bubble = t("Hi, I'm {name}, a personal AI agent. Meet nanoMuse: open source, runs on your phone.", { name });
  const tagline = t("Your personal AI agent, open source · {repo}", { repo: REPO });

  useEffect(() => {
    alive.current = true;
    if (!open) return;
    setSelected(0);
    void (async () => {
      const urls: string[] = [];
      for (const p of PALETTES) {
        const canvas = await render(p, profile, name, bubble, tagline, 1);
        urls.push(canvas.toDataURL("image/png"));
      }
      if (alive.current) setPreviews(urls);
    })();
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, profile?.avatar, profile?.emoji, profile?.color, name]);

  const share = async () => {
    setBusy(true);
    try {
      const palette = PALETTES[selected] ?? PALETTES[0];
      const canvas = await render(palette, profile, name, bubble, tagline, 2);
      const blob = await toBlob(canvas);
      if (!blob) throw new Error(t("The card could not be drawn."));
      const file = new File([blob], `${name}-nanomuse.png`, { type: "image/png" });
      const text = `${bubble}\nhttps://${REPO}`;
      const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
      if (typeof nav.share === "function" && (!nav.canShare || nav.canShare({ files: [file] }))) {
        try {
          await nav.share({ files: [file], text, title: name });
          onClose();
          return;
        } catch (e) {
          if ((e as Error).name === "AbortError") return;
        }
      }
      // no share sheet here (most desktop browsers): the picture is saved instead
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = file.name;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      toast(t("Saved the card as a picture."));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} title={t("Share my avatar")}>
      <p className="mb-3 text-[13px] text-muted">{t("Pick a card. It goes out through the system share sheet, or saves as a picture where there is none.")}</p>
      <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-5">
        {PALETTES.map((p, i) => (
          <button
            key={p.id}
            type="button"
            aria-label={t("Card {n}", { n: i + 1 })}
            aria-pressed={selected === i}
            onClick={() => setSelected(i)}
            className={cx("overflow-hidden rounded-2xl border-2 transition", selected === i ? "border-accent" : "border-transparent")}
            style={{ background: p.background, aspectRatio: `${W} / ${H}` }}
          >
            {previews[i] ? <img src={previews[i]} alt="" className="h-full w-full object-cover" /> : <Loader2 size={18} className="mx-auto my-8 animate-spin text-muted" />}
          </button>
        ))}
      </div>
      {previews[selected] && (
        <img src={previews[selected]} alt="" className="mx-auto mt-4 w-[260px] rounded-[20px] shadow-md" />
      )}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onClose} className={secondaryBtn}>
          {t("Later")}
        </button>
        <button type="button" disabled={busy || !previews.length} onClick={() => void share()} className={primaryBtn}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : typeof navigator.share === "function" ? <Share2 size={15} /> : <Download size={15} />}{" "}
          {typeof navigator.share === "function" ? t("Share") : t("Save picture")}
        </button>
      </div>
    </Sheet>
  );
}
