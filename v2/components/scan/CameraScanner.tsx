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
}: {
  title: string;
  subtitle?: string;
  onCapture: (c: Capture) => void;
  onClose: () => void;
  paused?: boolean;
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
    setTone("busy");
    setMsg("Memproses foto…");
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
          setTone("bad");
          setMsg(`Gagal membaca: ${scan.reason ?? "coba lagi"}`);
        } else if (scan.quality && !scan.quality.ok) {
          setTone("bad");
          setMsg(scan.quality.issues.join(" "));
        } else {
          navigator.vibrate?.(60);
          onCapture({ canvas, scan });
        }
      } catch (e) {
        setTone("bad");
        setMsg(`Error: ${(e as Error).message}`);
      } finally {
        stable.current = 0;
        setProgress(0);
        setTimeout(() => {
          busy.current = false;
        }, 700);
      }
    }, 30);
  }, [onCapture]);

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
        stable.current = 0;
        setProgress(0);
        lastCorners.current = null;
        drawGuide(ctx, rect.width, rect.height);
        setPxmm(null);
        setTone("idle");
        setMsg(det.candidates && det.candidates >= 2 ? det.reason ?? "Cari 4 kotak hitam…" : "Arahkan kamera ke LJK. Pastikan 4 kotak hitam di pojok terlihat.");
        return;
      }

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

      const prog = Math.min(1, stable.current / STABLE_FRAMES);
      setProgress(prog);
      if (!good) {
        setTone("warn");
        setMsg(issues.join(" · "));
      } else if (prog < 1) {
        setTone("good");
        setMsg("4 kotak terdeteksi — tahan HP tetap diam…");
      } else {
        capture();
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [capture]);

  const toneClass: Record<Tone, string> = {
    idle: "bg-black/60 text-white",
    warn: "bg-amber-500 text-white",
    good: "bg-green-600 text-white",
    busy: "bg-blue-600 text-white",
    bad: "bg-red-600 text-white",
  };

  return (
    <div className="fixed inset-0 z-50 m-0! flex flex-col bg-black text-white">
      <div className="flex items-center gap-3 px-4 py-3">
        <button onClick={onClose} className="rounded-full bg-white/15 px-3 py-1.5 text-sm font-semibold">
          ✕ Tutup
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold">{title}</div>
          {subtitle && <div className="truncate text-xs text-white/70">{subtitle}</div>}
        </div>
        {torchAvail && (
          <button onClick={toggleTorch} className={`rounded-full px-3 py-1.5 text-sm font-semibold ${torchOn ? "bg-yellow-400 text-black" : "bg-white/15"}`}>
            ⚡ Lampu
          </button>
        )}
      </div>

      <div className="relative flex-1 overflow-hidden">
        <video ref={videoRef} className="absolute inset-0 h-full w-full object-cover" playsInline muted autoPlay />
        <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />
        {error && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 p-6 text-center text-sm">{error}</div>
        )}
        {paused && !error && <div className="absolute inset-0 bg-black/50" />}
      </div>

      <div className="space-y-3 px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <div className={`rounded-xl px-4 py-2.5 text-center text-sm font-semibold ${toneClass[tone]}`}>{msg}</div>
        <div className="flex items-center gap-3">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/20">
            <div className="h-full bg-green-500 transition-all" style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
          <button
            onClick={capture}
            disabled={!canManual || paused}
            className="rounded-full bg-white px-5 py-2.5 text-sm font-bold text-black disabled:opacity-40"
            title="Hanya aktif jika 4 kotak hitam terdeteksi"
          >
            Jepret
          </button>
        </div>
        <div className="text-center text-[11px] text-white/50">
          Otomatis memotret saat 4 kotak hitam stabil · kamera {res || "-"}
          {pxmm !== null && ` · ${pxmm} px/mm (min ${QUALITY_LIMITS.minPxPerMm})`}
        </div>
      </div>
    </div>
  );
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
