"use client";

import { useEffect, useRef, useState } from "react";
import { cornersToH } from "@/lib/omr/markers";
import { drawOmrOverlay, type OverlayFocus, type OverlayStatuses, type OverlayValues } from "@/lib/omr/overlay";
import { TEMPLATE } from "@/lib/omr/scan";
import type { ItemOffsets } from "@/lib/omr/align";
import type { Corners } from "@/lib/omr/types";

/**
 * Foto LJK + overlay hasil baca. Sumber gambar: URL (validasi) atau canvas (review setelah jepret).
 * Klik untuk zoom 2.5x di titik yang diklik.
 */
export default function OmrImage({
  src,
  sourceCanvas,
  corners,
  values,
  statuses,
  focus,
  showOverlay = true,
  offsets,
}: {
  src?: string;
  sourceCanvas?: HTMLCanvasElement;
  corners: Corners;
  values: OverlayValues;
  statuses: OverlayStatuses;
  focus?: OverlayFocus | null;
  showOverlay?: boolean;
  offsets?: ItemOffsets | null;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [img, setImg] = useState<CanvasImageSource | null>(sourceCanvas ?? null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(
    sourceCanvas ? { w: sourceCanvas.width, h: sourceCanvas.height } : null,
  );
  const [zoom, setZoom] = useState<{ x: number; y: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (sourceCanvas || !src) return;
    const im = new Image();
    im.crossOrigin = "anonymous";
    im.onload = () => {
      setImg(im);
      setDims({ w: im.naturalWidth, h: im.naturalHeight });
    };
    im.onerror = () => setErr("Foto gagal dimuat.");
    im.src = src;
  }, [src, sourceCanvas]);

  useEffect(() => {
    const c = ref.current;
    if (!c || !img || !dims) return;
    const maxW = 1400;
    const scale = Math.min(1, maxW / dims.w);
    c.width = Math.round(dims.w * scale);
    c.height = Math.round(dims.h * scale);
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0, c.width, c.height);
    if (showOverlay) {
      const H = cornersToH(TEMPLATE, corners);
      if (H) drawOmrOverlay(ctx, H, TEMPLATE, values, statuses, scale, focus, offsets);
    }
  }, [img, dims, corners, values, statuses, focus, showOverlay, offsets]);

  if (err) return <div className="flex aspect-[3/4] items-center justify-center rounded-xl bg-soft text-sm text-muted">{err}</div>;
  if (!dims) return <div className="aspect-[3/4] animate-pulse rounded-xl bg-soft" />;

  return (
    <div
      className="relative overflow-hidden rounded-xl border border-line bg-slate-900"
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        setZoom(zoom ? null : { x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 });
      }}
    >
      <canvas
        ref={ref}
        className="block h-auto w-full cursor-zoom-in transition-transform duration-200"
        style={zoom ? { transform: "scale(2.5)", transformOrigin: `${zoom.x}% ${zoom.y}%`, cursor: "zoom-out" } : undefined}
      />
      <div className="pointer-events-none absolute right-2 bottom-2 rounded bg-black/60 px-2 py-0.5 text-[10px] text-white">
        {zoom ? "klik untuk keluar zoom" : "klik untuk zoom"}
      </div>
    </div>
  );
}
