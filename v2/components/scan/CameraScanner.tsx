"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { detectSheet } from "@/lib/omr/markers";
import { localScale } from "@/lib/omr/homography";
import { rgbaToGray } from "@/lib/omr/image";
import { quadAngles } from "@/lib/omr/markers";
import { QUALITY_LIMITS } from "@/lib/omr/quality";
import { scanRgba, TEMPLATE, type ScanResult } from "@/lib/omr/scan";
import type { Corners } from "@/lib/omr/types";

/** Sisi terpanjang foto yang disimpan & dibaca (px). */
export const CAPTURE_MAX_SIDE = 2400;
const LIVE_MAX_SIDE = 640;
const STABLE_FRAMES = 6;
const FRAME_INTERVAL_MS = 110;

export interface Capture {
  canvas: HTMLCanvasElement;
  scan: ScanResult;
}

type Tone = "idle" | "warn" | "good" | "busy" | "bad";

export default function CameraScanner({
  title,
  subtitle,
  onCapture,
  onClose,
  paused = false,
  topSlot,
  sheet,
  headerExtra,
}: {
  title: string;
  subtitle?: string;
  onCapture: (c: Capture) => void;
  onClose: () => void;
  /** Saat true: deteksi berhenti & gambar dibekukan (mis. panel hasil sedang tampil). */
  paused?: boolean;
  /** Konten di atas video (mis. strip riwayat sesi). */
  topSlot?: React.ReactNode;
  /** Panel bawah yang menggantikan kontrol kamera (mis. hasil baca). */
  sheet?: React.ReactNode;
  headerExtra?: React.ReactNode;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const smallRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const lastCorners = useRef<Corners | null>(null);
  const stable = useRef(0);
  const busy = useRef(false);
  const lastDetOk = useRef(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const freezeRef = useRef<HTMLCanvasElement>(null);
  // Setelah memotret, auto-jepret dikunci sampai lembar diganti (lembar hilang dari frame
  // beberapa saat) supaya lembar yang sama tidak terpotret dua kali.
  const armed = useRef(true);
  const lostFrames = useRef(0);
  const [frozen, setFrozen] = useState(false);

  const [msg, setMsg] = useState("Menyalakan kamera…");
  const [tone, setTone] = useState<Tone>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [torchAvail, setTorchAvail] = useState(false);
  const [torchOn, setTorchOn] = useState(false);
  const [canManual, setCanManual] = useState(false);
  const [res, setRes] = useState<string>("");
  const [pxmm, setPxmm] = useState<number | null>(null);

  // --- Start / stop kamera
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        setError("Kamera hanya bisa dipakai lewat HTTPS dan browser yang mendukung (Chrome / Safari terbaru).");
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } },
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        const track = stream.getVideoTracks()[0];
        try {
          // fokus kontinu bila didukung
          await track.applyConstraints({ advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet] });
        } catch {}
        const caps = (track.getCapabilities?.() ?? {}) as MediaTrackCapabilities & { torch?: boolean };
        setTorchAvail(!!caps.torch);
        const v = videoRef.current!;
        v.srcObject = stream;
        await v.play();
        setMsg("Arahkan kamera ke LJK. Pastikan 4 kotak hitam di pojok terlihat.");
      } catch (e) {
        const name = (e as DOMException)?.name;
        setError(
          name === "NotAllowedError"
            ? "Izin kamera ditolak. Buka pengaturan browser → izinkan kamera untuk situs ini, lalu muat ulang."
            : name === "NotFoundError"
              ? "Kamera tidak ditemukan di perangkat ini."
              : `Kamera gagal dibuka (${name ?? "error"}).`,
        );
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  // Layar tetap menyala selama kamera terbuka
  useEffect(() => {
    let lock: { release: () => Promise<void> } | null = null;
    const req = async () => {
      try {
        lock = await (navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> } }).wakeLock?.request("screen") ?? null;
      } catch {}
    };
    req();
    const onVis = () => document.visibilityState === "visible" && req();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      lock?.release().catch(() => {});
    };
  }, []);

  // Lanjut dari jeda: buang bekuan gambar, reset stabilitas
  useEffect(() => {
    if (!paused) {
      setFrozen(false);
      stable.current = 0;
      lastCorners.current = null;
    }
  }, [paused]);

  const freezeFrame = () => {
    const v = videoRef.current;
    const f = freezeRef.current;
    if (!v || !f || !v.videoWidth) return;
    const rect = f.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    f.width = Math.round(rect.width * dpr);
    f.height = Math.round(rect.height * dpr);
    const ctx = f.getContext("2d")!;
    const k = Math.max(f.width / v.videoWidth, f.height / v.videoHeight);
    const w = v.videoWidth * k;
    const h = v.videoHeight * k;
    ctx.drawImage(v, (f.width - w) / 2, (f.height - h) / 2, w, h);
    setFrozen(true);
  };

  const toggleTorch = async () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    try {
      await track.applyConstraints({ advanced: [{ torch: !torchOn } as MediaTrackConstraintSet] });
      setTorchOn(!torchOn);
    } catch {}
  };

  // --- Ambil foto resolusi penuh lalu baca OMR
  const capture = useCallback(() => {
    const v = videoRef.current;
    if (!v || busy.current || !v.videoWidth) return;
    busy.current = true;
    armed.current = false;
    lostFrames.current = 0;
    freezeFrame();
    feedback("shutter");
    setTone("busy");
    setMsg("Membaca LJK…");
    setProgress(1);
    // beri waktu UI menampilkan status sebelum kerja berat
    setTimeout(() => {
      try {
        const vw = v.videoWidth;
        const vh = v.videoHeight;
        const s = Math.min(1, CAPTURE_MAX_SIDE / Math.max(vw, vh));
        const cw = Math.round(vw * s);
        const ch = Math.round(vh * s);
        const canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
        ctx.drawImage(v, 0, 0, cw, ch);
        const img = ctx.getImageData(0, 0, cw, ch);
        const scan = scanRgba(img.data, cw, ch);
        if (!scan.ok) {
          fail(`Gagal membaca: ${scan.reason ?? "coba lagi"}`);
        } else if (scan.quality && !scan.quality.ok) {
          fail(scan.quality.issues.join(" "));
        } else {
          feedback("ok");
          onCapture({ canvas, scan });
        }
      } catch (e) {
        fail(`Error: ${(e as Error).message}`);
      } finally {
        stable.current = 0;
        setProgress(0);
        setTimeout(() => {
          busy.current = false;
        }, 700);
      }
    }, 30);
  }, [onCapture]);

  const fail = (m: string) => {
    feedback("error");
    setFrozen(false);
    armed.current = true; // boleh langsung coba lagi
    setTone("bad");
    setMsg(m);
  };

  // --- Loop deteksi live
  useEffect(() => {
    let raf = 0;
    let last = 0;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      if (t - last < FRAME_INTERVAL_MS) return;
      last = t;
      const v = videoRef.current;
      const ov = overlayRef.current;
      if (!v || !ov || !v.videoWidth || pausedRef.current || busy.current) return;

      const vw = v.videoWidth;
      const vh = v.videoHeight;
      setRes(`${vw}×${vh}`);
      const s = Math.min(1, LIVE_MAX_SIDE / Math.max(vw, vh));
      const sw = Math.round(vw * s);
      const sh = Math.round(vh * s);
      if (!smallRef.current) smallRef.current = document.createElement("canvas");
      const sc = smallRef.current;
      if (sc.width !== sw) sc.width = sw;
      if (sc.height !== sh) sc.height = sh;
      const sctx = sc.getContext("2d", { willReadFrequently: true })!;
      sctx.drawImage(v, 0, 0, sw, sh);
      const { gray } = rgbaToGray(sctx.getImageData(0, 0, sw, sh).data, sw, sh);
      const det = detectSheet(gray, TEMPLATE, { minAreaFrac: 0.1 });

      // overlay
      const rect = ov.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      if (ov.width !== Math.round(rect.width * dpr)) ov.width = Math.round(rect.width * dpr);
      if (ov.height !== Math.round(rect.height * dpr)) ov.height = Math.round(rect.height * dpr);
      const ctx = ov.getContext("2d")!;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      const k = Math.max(rect.width / sw, rect.height / sh); // object-cover
      const ox = (rect.width - sw * k) / 2;
      const oy = (rect.height - sh * k) / 2;
      const map = (p: { x: number; y: number }) => ({ x: p.x * k + ox, y: p.y * k + oy });

      lastDetOk.current = det.ok;
      setCanManual(det.ok);
      if (!det.ok || !det.corners || !det.H) {
        if (++lostFrames.current >= 3) armed.current = true;
        stable.current = 0;
        setProgress(0);
        lastCorners.current = null;
        drawGuide(ctx, rect.width, rect.height);
        setPxmm(null);
        setTone("idle");
        setMsg(det.candidates && det.candidates >= 2 ? det.reason ?? "Cari 4 kotak hitam…" : "Arahkan kamera ke LJK. Pastikan 4 kotak hitam di pojok terlihat.");
        return;
      }

      lostFrames.current = 0;
      const c = det.corners;
      const pxPerMmFull = localScale(det.H, 105, 148) / s;
      setPxmm(Math.round(pxPerMmFull * Math.min(1, CAPTURE_MAX_SIDE / Math.max(vw, vh)) * 10) / 10);
      const angles = quadAngles(c);
      const issues: string[] = [];
      if (pxPerMmFull * Math.min(1, CAPTURE_MAX_SIDE / Math.max(vw, vh)) < QUALITY_LIMITS.minPxPerMm + 0.2)
        issues.push("Dekatkan kamera ke lembar");
      if (Math.min(...angles) < QUALITY_LIMITS.minAngle + 4 || Math.max(...angles) > QUALITY_LIMITS.maxAngle - 4)
        issues.push("Posisikan HP sejajar di atas lembar");

      // stabilitas
      const prev = lastCorners.current;
      const tol = 0.012 * Math.min(sw, sh);
      const moved = prev
        ? Math.max(...(["tl", "tr", "br", "bl"] as const).map((key) => Math.hypot(c[key].x - prev[key].x, c[key].y - prev[key].y)))
        : Infinity;
      lastCorners.current = c;
      if (issues.length === 0 && moved < tol) stable.current++;
      else if (issues.length === 0) stable.current = Math.max(0, stable.current - 1);
      else stable.current = 0;

      const good = issues.length === 0;
      const pts = [c.tl, c.tr, c.br, c.bl].map(map);
      ctx.lineWidth = 3;
      ctx.strokeStyle = good ? "#22c55e" : "#f59e0b";
      ctx.fillStyle = good ? "rgba(34,197,94,0.12)" : "rgba(245,158,11,0.10)";
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 9, 0, Math.PI * 2);
        ctx.fillStyle = good ? "#22c55e" : "#f59e0b";
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      if (!armed.current) {
        stable.current = 0;
        setProgress(0);
        setTone("idle");
        setMsg("Ganti ke LJK berikutnya — atau tekan Jepret untuk memotret lembar ini lagi.");
        return;
      }
      const prog = Math.min(1, stable.current / STABLE_FRAMES);
      setProgress(prog);
      if (!good) {
        setTone("warn");
        setMsg(issues.join(" · "));
      } else if (prog < 1) {
        setTone("good");
        setMsg("Tahan sebentar…");
      } else {
        capture();
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [capture]);

  const toneClass: Record<Tone, string> = {
    idle: "bg-black/55 text-white",
    warn: "bg-warn-bg0/95 text-white",
    good: "bg-emerald-600/95 text-white",
    busy: "bg-white text-ink",
    bad: "bg-red-600/95 text-white",
  };
  const R = 34;
  const C = 2 * Math.PI * R;

  return (
    <div className="fixed inset-0 z-50 m-0! overflow-hidden bg-black text-white select-none">
      <video ref={videoRef} className="absolute inset-0 h-full w-full object-cover" playsInline muted autoPlay />
      <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />
      <canvas ref={freezeRef} className={`pointer-events-none absolute inset-0 h-full w-full ${frozen ? "opacity-100" : "opacity-0"}`} />
      {(paused || tone === "busy") && !error && <div className="pointer-events-none absolute inset-0 bg-black/35 transition-opacity" />}
      {error && <div className="absolute inset-0 flex items-center justify-center bg-black/85 p-8 text-center text-sm leading-relaxed">{error}</div>}

      {/* Header */}
      <div className="absolute inset-x-0 top-0 bg-gradient-to-b from-black/75 to-transparent pt-[max(0.75rem,env(safe-area-inset-top))]">
        <div className="flex items-center gap-2 px-3 pb-2">
          <button onClick={onClose} className="cam-btn" aria-label="Tutup kamera">
            ✕
          </button>
          <div className="min-w-0 flex-1 px-1">
            <div className="truncate text-sm font-bold">{title}</div>
            {subtitle && <div className="truncate text-xs text-white/75">{subtitle}</div>}
          </div>
          {headerExtra}
          {torchAvail && (
            <button onClick={toggleTorch} className={`cam-btn ${torchOn ? "bg-yellow-300! text-black" : ""}`} aria-label="Lampu">
              ⚡
            </button>
          )}
        </div>
        {topSlot && <div className="px-3 pb-3">{topSlot}</div>}
      </div>

      {/* Kontrol bawah atau panel hasil */}
      {sheet ? (
        <div className="absolute inset-x-0 bottom-0 max-h-[62dvh] overflow-y-auto rounded-t-2xl bg-white text-ink shadow-2xl animate-sheet-up">{sheet}</div>
      ) : (
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 via-black/50 to-transparent px-4 pt-10 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
          <div className="mb-5 flex justify-center">
            <div className={`max-w-sm rounded-full px-4 py-2 text-center text-[13px] leading-snug font-semibold backdrop-blur transition-colors ${toneClass[tone]}`}>
              {tone === "busy" && <span className="mr-2 inline-block h-3 w-3 animate-spin rounded-full border-2 border-ink/30 border-t-ink align-[-1px]" />}
              {msg}
            </div>
          </div>
          <div className="flex items-center justify-center">
            <button
              onClick={capture}
              disabled={!canManual || paused || tone === "busy"}
              className="relative h-[84px] w-[84px] rounded-full disabled:opacity-50"
              aria-label="Jepret"
              title="Otomatis memotret saat 4 kotak hitam stabil. Tekan untuk memotret manual."
            >
              <svg viewBox="0 0 84 84" className="absolute inset-0 -rotate-90">
                <circle cx="42" cy="42" r={R} fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="5" />
                <circle
                  cx="42"
                  cy="42"
                  r={R}
                  fill="none"
                  stroke="#22c55e"
                  strokeWidth="5"
                  strokeLinecap="round"
                  strokeDasharray={C}
                  strokeDashoffset={C * (1 - progress)}
                  style={{ transition: "stroke-dashoffset 120ms linear" }}
                />
              </svg>
              <span className={`absolute inset-[13px] rounded-full transition ${canManual ? "bg-white" : "bg-white/60"} active:scale-90`} />
            </button>
          </div>
          <div className="mt-3 text-center text-[11px] text-white/55">
            Jepret otomatis saat bingkai hijau · kamera {res || "-"}
            {pxmm !== null && ` · ${pxmm} px/mm`}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Umpan balik (getar + bunyi)
let audioCtx: AudioContext | null = null;
export function soundEnabled(): boolean {
  try {
    return localStorage.getItem("ljk-sound") !== "0";
  } catch {
    return true;
  }
}
export function setSoundEnabled(on: boolean) {
  try {
    localStorage.setItem("ljk-sound", on ? "1" : "0");
  } catch {}
}
export function feedback(kind: "shutter" | "ok" | "saved" | "error") {
  const vib: Record<typeof kind, number | number[]> = { shutter: 25, ok: 50, saved: [30, 40, 30], error: [120, 60, 120] };
  try {
    navigator.vibrate?.(vib[kind]);
  } catch {}
  if (!soundEnabled()) return;
  try {
    audioCtx ??= new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    const ctx = audioCtx;
    const notes: Record<typeof kind, [number, number][]> = {
      shutter: [[1400, 0.03]],
      ok: [[880, 0.07]],
      saved: [[660, 0.06], [990, 0.09]],
      error: [[220, 0.14], [180, 0.16]],
    };
    let t = ctx.currentTime;
    for (const [f, d] of notes[kind]) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = kind === "error" ? "square" : "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(kind === "error" ? 0.05 : 0.12, t + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + d);
      o.connect(g).connect(ctx.destination);
      o.start(t);
      o.stop(t + d + 0.02);
      t += d + 0.03;
    }
  } catch {}
}

/** Bingkai panduan A4 tegak saat lembar belum terdeteksi. */
function drawGuide(ctx: CanvasRenderingContext2D, w: number, h: number) {
  const aspect = 210 / 297;
  let gh = h * 0.86;
  let gw = gh * aspect;
  if (gw > w * 0.9) {
    gw = w * 0.9;
    gh = gw / aspect;
  }
  const x = (w - gw) / 2;
  const y = (h - gh) / 2;
  const L = Math.min(gw, gh) * 0.12;
  ctx.strokeStyle = "rgba(255,255,255,0.85)";
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  const corner = (cx: number, cy: number, dx: number, dy: number) => {
    ctx.beginPath();
    ctx.moveTo(cx + dx * L, cy);
    ctx.lineTo(cx, cy);
    ctx.lineTo(cx, cy + dy * L);
    ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ctx.fillRect(cx + (dx > 0 ? 6 : -6 - L * 0.35), cy + (dy > 0 ? 6 : -6 - L * 0.35), L * 0.35, L * 0.35);
  };
  corner(x, y, 1, 1);
  corner(x + gw, y, -1, 1);
  corner(x + gw, y + gh, -1, -1);
  corner(x, y + gh, 1, -1);
}
