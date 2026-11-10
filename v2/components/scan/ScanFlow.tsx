"use client";

import { tandaiHadir } from "@/app/(app)/scan/actions";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AnswerGrid from "@/components/AnswerGrid";
import KodePicker from "@/components/KodePicker";
import OmrImage from "@/components/OmrImage";
import StudentPicker from "@/components/StudentPicker";
import { matchNim } from "@/lib/omr/nim";
import { countOmrFlags, nimToDigits, toStoredOmr } from "@/lib/omr/store";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { Kelas, Peserta } from "@/lib/types";
import CameraScanner, { feedback, setSoundEnabled, soundEnabled, type Capture } from "./CameraScanner";

interface Existing {
  id: string;
  nim: string;
  kelas_id: string;
  uploaded_by: string;
  uploaded_at: string;
  status: string;
}

interface Review {
  capture: Capture;
  nimRead: string;
  nim: string;
  nimSource: "omr" | "dropdown" | "manual";
  suggestions: string[];
  kode: string | null;
  kodeFromOmr: boolean;
  soal: (string | null)[];
  kuis: (string | null)[];
  ganti: boolean;
}

type QStatus = "antri" | "upload" | "ok" | "gagal";
interface QItem {
  key: string;
  review: Review;
  nama: string;
  status: QStatus;
  error?: string;
  prev?: Existing; // entri lama yang diganti (untuk dikembalikan bila gagal)
  at: number;
}

const AUTO_MS = 1400;

function readPref(key: string, def: boolean) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? def : v === "1";
  } catch {
    return def;
  }
}
function writePref(key: string, v: boolean) {
  try {
    localStorage.setItem(key, v ? "1" : "0");
  } catch {}
}

export default function ScanFlow({
  kelas,
  peserta,
  existing: initialExisting,
  pengawasEmail,
  kodeList,
  demo = false,
  ulangNim,
}: {
  /** Upload ulang: kamera langsung dibuka; foto NIM ini otomatis menggantikan yang lama. */
  ulangNim?: string;
  kelas: Kelas;
  /** Kode soal yang berlaku di ujian aktif. */
  kodeList: string[];
  peserta: Peserta[];
  existing: Existing[];
  pengawasEmail: string;
  /** Mode demo: tidak upload / simpan ke database. */
  demo?: boolean;
}) {
  const [existing, setExisting] = useState(initialExisting);
  const [camera, setCamera] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [detail, setDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QItem[]>([]);
  const [autoSave, setAutoSave] = useState(true);
  const [sound, setSound] = useState(true);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [showQueue, setShowQueue] = useState(false);
  const [filter, setFilter] = useState<"belum" | "sudah" | "semua">("belum");
  const [q, setQ] = useState("");
  const working = useRef(false);
  const autoCancelled = useRef(false);

  useEffect(() => {
    if (ulangNim) setCamera(true);
  }, [ulangNim]);

  useEffect(() => {
    setAutoSave(readPref("ljk-autosave", true));
    setSound(soundEnabled());
  }, []);

  const byNim = useMemo(() => new Map(existing.map((e) => [e.nim, e])), [existing]);
  const uploadedHere = useMemo(() => new Set(existing.filter((e) => e.kelas_id === kelas.id).map((e) => e.nim)), [existing, kelas.id]);
  const pesertaByNim = useMemo(() => new Map(peserta.map((p) => [p.nim, p])), [peserta]);
  const done = peserta.filter((p) => uploadedHere.has(p.nim)).length;
  const pending = queue.filter((i) => i.status === "antri" || i.status === "upload").length;
  const failed = queue.filter((i) => i.status === "gagal").length;
  const savedSession = queue.filter((i) => i.status !== "gagal").length;

  // ---------------------------------------------------------------- hasil jepret
  const onCapture = useCallback(
    (capture: Capture) => {
      const d = capture.scan.decoded!;
      const m = matchNim(d.nim.value, peserta);
      const kodeOk = !!d.kode_soal.value && kodeList.includes(d.kode_soal.value);
      setError(null);
      setDetail(false);
      autoCancelled.current = false;
      setReview({
        capture,
        nimRead: d.nim.value,
        nim: m.exact?.nim ?? "",
        nimSource: m.exact ? "omr" : "dropdown",
        suggestions: m.suggestions.map((s) => s.nim),
        kode: kodeOk ? d.kode_soal.value : null,
        kodeFromOmr: kodeOk,
        soal: d.soal.map((i) => i.value),
        kuis: d.kuisioner.map((i) => i.value),
        ganti: !!ulangNim && m.exact?.nim === ulangNim,
      });
    },
    [peserta, kodeList, ulangNim],
  );

  const dup = review?.nim ? byNim.get(review.nim) : undefined;
  const sameSession = !!review?.nim && queue.some((i) => i.review.nim === review.nim && i.status !== "gagal");
  const canAuto = !!review && review.nimSource === "omr" && review.kodeFromOmr && !!review.kode && !dup;
  const blockers: string[] = [];
  if (review) {
    if (!review.nim) blockers.push(review.nimRead.includes("?") ? "NIM tidak terbaca lengkap" : "NIM tidak ada di daftar kelas ini");
    if (!review.kode) blockers.push("Kode soal belum dipilih");
    if (dup && !review.ganti) blockers.push(sameSession ? "LJK ini baru saja disimpan di sesi ini" : "NIM ini sudah punya LJK");
  }

  // ---------------------------------------------------------------- simpan (masuk antrean, upload di latar)
  const enqueue = useCallback(() => {
    if (!review) return;
    if (!/^\d{12}$/.test(review.nim)) return setError("Pilih / konfirmasi mahasiswanya dulu.");
    if (!review.kode) return setError("Pilih kode soal.");
    const old = byNim.get(review.nim);
    if (old && !review.ganti) return setError("NIM ini sudah punya LJK. Centang 'ganti' bila foto ini menggantikan yang lama.");
    const item: QItem = {
      key: crypto.randomUUID(),
      review,
      nama: pesertaByNim.get(review.nim)?.nama ?? review.nim,
      status: "antri",
      prev: old,
      at: Date.now(),
    };
    setExisting((prev) => [
      ...prev.filter((e) => e.nim !== review.nim),
      { id: item.key, nim: review.nim, kelas_id: kelas.id, uploaded_by: pengawasEmail, uploaded_at: new Date().toISOString(), status: "perlu_validasi" },
    ]);
    setQueue((qq) => [item, ...qq]);
    feedback("saved");
    setReview(null);
    setDetail(false);
    setCountdown(null);
    setError(null);
  }, [review, byNim, pesertaByNim, kelas.id, pengawasEmail]);

  const retake = () => {
    setReview(null);
    setDetail(false);
    setCountdown(null);
    setError(null);
  };

  // hitung mundur simpan otomatis
  useEffect(() => {
    if (!review || !autoSave || !canAuto || detail || autoCancelled.current) {
      setCountdown(null);
      return;
    }
    const t0 = performance.now();
    setCountdown(0);
    let raf = 0;
    const step = () => {
      if (autoCancelled.current) return setCountdown(null);
      const p = (performance.now() - t0) / AUTO_MS;
      if (p >= 1) {
        enqueue();
        return;
      }
      setCountdown(p);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [review?.capture, autoSave, canAuto, detail]);
  const stopCountdown = () => {
    autoCancelled.current = true;
    setCountdown(null);
  };

  // ---------------------------------------------------------------- pekerja antrean
  const upload = useCallback(
    async (item: QItem) => {
      const r = item.review;
      if (demo) {
        await new Promise((res) => setTimeout(res, 600));
        return item.key;
      }
      const supabase = supabaseBrowser();
      const blob: Blob = await new Promise((res, rej) =>
        r.capture.canvas.toBlob((b) => (b ? res(b) : rej(new Error("Gagal membuat JPEG"))), "image/jpeg", 0.85),
      );
      const path = `${kelas.ujian_id}/${kelas.id}/${r.nim}-${Date.now()}-${crypto.randomUUID().slice(0, 8)}.jpg`;
      const up = await supabase.storage.from("ljk").upload(path, blob, { contentType: "image/jpeg", upsert: false });
      if (up.error) throw new Error(`Upload foto gagal: ${up.error.message}`);
      const omr = toStoredOmr(r.capture.scan, { w: r.capture.canvas.width, h: r.capture.canvas.height });
      const matched = r.nimSource === "omr" || pesertaByNim.has(r.nim);
      const payload = {
        ujian_id: kelas.ujian_id,
        kelas_id: kelas.id,
        nim: r.nim,
        nim_terbaca: r.nimRead,
        nim_sumber: r.nimSource,
        kode_soal: r.kode,
        jawaban: r.soal,
        kuisioner: r.kuis,
        omr,
        flags: countOmrFlags(omr, r.nimSource === "omr", omr.kode_soal.value === r.kode && kodeList.includes(r.kode!)) + (matched ? 0 : 1),
        photo_path: path,
      };
      const { data: id, error: rpcErr } = await supabase.rpc("simpan_ljk", { payload, ganti: r.ganti });
      if (rpcErr) {
        await supabase.storage.from("ljk").remove([path]).catch(() => {});
        throw new Error(
          rpcErr.message.includes("submissions_one_active_per_nim")
            ? "NIM ini baru saja diupload pengawas lain. Buka detail lalu pilih 'ganti' jika perlu."
            : rpcErr.message,
        );
      }
      return id as string;
    },
    [demo, kelas.ujian_id, kelas.id, pesertaByNim, kodeList],
  );

  useEffect(() => {
    if (working.current) return;
    const next = [...queue].reverse().find((i) => i.status === "antri");
    if (!next) return;
    working.current = true;
    setQueue((qq) => qq.map((i) => (i.key === next.key ? { ...i, status: "upload" } : i)));
    upload(next)
      .then((id) => {
        // lepas memori foto resolusi penuh (±20 MB per lembar) setelah terupload
        next.review.capture.canvas.width = 0;
        next.review.capture.canvas.height = 0;
        setQueue((qq) => qq.map((i) => (i.key === next.key ? { ...i, status: "ok", error: undefined } : i)));
        setExisting((prev) => prev.map((e) => (e.id === next.key ? { ...e, id } : e)));
        // tandai hadir di spreadsheet kehadiran (latar belakang; kegagalan tidak mengganggu scan)
        if (!demo) tandaiHadir(kelas.id).catch(() => {});
      })
      .catch((e: Error) => {
        feedback("error");
        setQueue((qq) => qq.map((i) => (i.key === next.key ? { ...i, status: "gagal", error: e.message } : i)));
        setExisting((prev) => [...prev.filter((x) => x.id !== next.key), ...(next.prev ? [next.prev] : [])]);
      })
      .finally(() => {
        working.current = false;
        setQueue((qq) => [...qq]); // picu pemeriksaan item berikutnya
      });
  }, [queue, upload, demo, kelas.id]);

  const retry = (key: string) => {
    const it = queue.find((i) => i.key === key);
    if (!it) return;
    setExisting((prev) => [
      ...prev.filter((e) => e.nim !== it.review.nim),
      { id: it.key, nim: it.review.nim, kelas_id: kelas.id, uploaded_by: pengawasEmail, uploaded_at: new Date().toISOString(), status: "perlu_validasi" },
    ]);
    setQueue((qq) => qq.map((i) => (i.key === key ? { ...i, status: "antri", error: undefined } : i)));
  };
  const reopen = (key: string) => {
    const it = queue.find((i) => i.key === key);
    if (!it) return;
    setQueue((qq) => qq.filter((i) => i.key !== key));
    setReview({ ...it.review, ganti: true });
    setShowQueue(false);
    setCamera(true);
  };

  // jangan tutup tab saat masih ada upload
  useEffect(() => {
    if (!pending && !failed) return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [pending, failed]);

  // ---------------------------------------------------------------- UI kamera
  const sessionStrip = queue.length > 0 && (
    <button onClick={() => setShowQueue(true)} className="flex w-full items-center gap-2 overflow-hidden text-left">
      <span className="shrink-0 rounded-full bg-white/15 px-2.5 py-1 text-xs font-semibold backdrop-blur">
        Sesi {savedSession}
        {pending > 0 && ` · ⟳ ${pending}`}
        {failed > 0 && <span className="text-red-300"> · ⚠ {failed}</span>}
      </span>
      <span className="flex min-w-0 gap-1.5 overflow-hidden">
        {queue.slice(0, 6).map((i) => (
          <span
            key={i.key}
            className={`shrink-0 truncate rounded-full px-2 py-1 text-[11px] font-medium backdrop-blur ${
              i.status === "gagal" ? "bg-bad-bg0/80" : i.status === "ok" ? "bg-emerald-500/70" : "bg-white/20"
            }`}
            style={{ maxWidth: 120 }}
          >
            {i.status === "ok" ? "✓" : i.status === "gagal" ? "⚠" : "⟳"} {i.nama.split(" ").slice(0, 2).join(" ")}
          </span>
        ))}
      </span>
    </button>
  );

  const headerExtra = (
    <>
      <button
        onClick={() => {
          setAutoSave((v) => (writePref("ljk-autosave", !v), !v));
        }}
        className={`cam-btn text-xs ${autoSave ? "bg-emerald-500/80!" : ""}`}
        title="Simpan otomatis bila NIM & kode soal terbaca pasti"
      >
        Auto {autoSave ? "ON" : "OFF"}
      </button>
      <button
        onClick={() => {
          setSound((v) => (setSoundEnabled(!v), !v));
        }}
        className="cam-btn"
        aria-label="Bunyi"
      >
        {sound ? "🔊" : "🔇"}
      </button>
    </>
  );

  const sheet = review && !detail && (
    <ResultSheet
      review={review}
      student={pesertaByNim.get(review.nim)}
      blockers={blockers}
      dup={dup}
      kelasId={kelas.id}
      peserta={peserta}
      uploaded={uploadedHere}
      kodeList={kodeList}
      countdown={countdown}
      error={error}
      onTouch={stopCountdown}
      onChange={(r) => {
        stopCountdown();
        setReview(r);
      }}
      onSave={enqueue}
      onRetake={retake}
      onDetail={() => {
        stopCountdown();
        setDetail(true);
      }}
    />
  );

  // ---------------------------------------------------------------- Halaman kelas
  const s = q.trim().toLowerCase();
  const list = peserta.filter(
    (p) =>
      (!s || p.nama.toLowerCase().includes(s) || p.nim.includes(s)) &&
      (filter === "semua" || (filter === "sudah" ? uploadedHere.has(p.nim) : !uploadedHere.has(p.nim))),
  );
  const pct = peserta.length ? Math.round((done / peserta.length) * 100) : 0;

  return (
    <div className="space-y-4">
      {camera && (
        <CameraScanner
          title={kelas.id}
          subtitle={ulangNim ? `Upload ulang NIM ${ulangNim}` : `${done}/${peserta.length} terupload${demo ? " · DEMO" : ""}`}
          paused={!!review}
          onCapture={onCapture}
          onClose={() => {
            setCamera(false);
            setReview(null);
            setCountdown(null);
          }}
          topSlot={sessionStrip || undefined}
          headerExtra={headerExtra}
          sheet={sheet || undefined}
        />
      )}

      {review && detail && (
        <DetailReview
          review={review}
          kelasId={kelas.id}
          peserta={peserta}
          uploaded={uploadedHere}
          kodeList={kodeList}
          student={pesertaByNim.get(review.nim)}
          dup={dup}
          error={error}
          onChange={setReview}
          onBack={() => setDetail(false)}
          onRetake={retake}
          onSave={enqueue}
        />
      )}

      {showQueue && (
        <QueuePanel
          queue={queue}
          onClose={() => setShowQueue(false)}
          onRetry={retry}
          onReopen={reopen}
        />
      )}

      {/* Kepala kelas */}
      <section className="card overflow-hidden">
        <div className="hero-band px-4 py-3 text-white sm:px-5 sm:py-5">
          {demo ? (
            <span className="pill bg-amber-300 text-ink">MODE DEMO — tidak tersimpan</span>
          ) : (
            <Link href="/scan" className="text-xs font-semibold text-white/80 hover:text-white">
              ← Ganti kelas
            </Link>
          )}
          <div className="mt-1 flex items-end justify-between gap-3">
            <div className="min-w-0">
              <h1 className="font-serif truncate text-xl font-bold sm:text-2xl">{kelas.id}</h1>
              <p className="text-sm text-white/75">
                {kelas.prodi} · {kelas.hari} {kelas.waktu} · {kelas.ruangan}
              </p>
            </div>
            <div className="shrink-0 text-right">
              <div className="text-3xl font-bold tabular-nums">
                {done}
                <span className="text-base font-medium text-white/70">/{peserta.length}</span>
              </div>
              <div className="text-xs text-white/70">terupload</div>
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/20">
            <div className="h-full rounded-full bg-white transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className="space-y-2.5 p-3 sm:p-4">
          <button onClick={() => setCamera(true)} className="btn-primary w-full py-3.5 text-base">
            <span className="text-lg">◉</span> {queue.length ? "Lanjutkan scan" : "Mulai scan LJK"}
          </button>
          {queue.length > 0 && (
            <button onClick={() => setShowQueue(true)} className="flex w-full items-center justify-between rounded-xl bg-soft px-4 py-3 text-sm">
              <span>
                Sesi ini: <b>{savedSession}</b> LJK
                {pending > 0 && <span className="text-info"> · {pending} sedang diupload</span>}
                {failed > 0 && <span className="text-bad"> · {failed} gagal</span>}
              </span>
              <span className="text-muted">Lihat →</span>
            </button>
          )}
          <details className="rounded-xl bg-soft px-4 py-3 text-sm text-ink2">
            <summary className="cursor-pointer font-semibold text-ink">Cara cepat memotret banyak LJK</summary>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>Taruh LJK di meja yang terang, HP tegak lurus di atasnya, 4 kotak hitam masuk layar.</li>
              <li>Kamera memotret sendiri saat bingkai hijau. Hasil langsung muncul di bawah.</li>
              <li>
                Kalau NIM & kode soal terbaca pasti, LJK tersimpan otomatis (<b>Auto ON</b>). Kalau ada yang perlu dicek, perbaiki lalu ketuk{" "}
                <b>Simpan &amp; lanjut</b>.
              </li>
              <li>Ganti dengan LJK berikutnya — kamera siap memotret lagi. Upload berjalan di latar belakang.</li>
            </ol>
          </details>
        </div>
      </section>

      {/* Daftar mahasiswa */}
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <div className="flex rounded-lg bg-soft p-0.5 text-xs font-semibold">
            {(
              [
                ["belum", `Belum ${peserta.length - done}`],
                ["sudah", `Sudah ${done}`],
                ["semua", "Semua"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setFilter(k)}
                className={`rounded-md px-3 py-1.5 ${filter === k ? "bg-white text-ink shadow-sm" : "text-ink2"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <input className="input ml-auto max-w-48 py-1.5" placeholder="Cari nama / NIM…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <ul className="divide-y divide-line">
          {list.length === 0 && <li className="px-4 py-6 text-center text-sm text-muted">{filter === "belum" ? "Semua mahasiswa sudah terupload 🎉" : "Tidak ada."}</li>}
          {list.map((p) => {
            const e = byNim.get(p.nim);
            const here = e && e.kelas_id === kelas.id;
            const qi = queue.find((i) => i.review.nim === p.nim);
            return (
              <li key={p.nim} className="flex items-center gap-3 px-3 py-2 sm:px-4 sm:py-2.5">
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${here ? "bg-ok text-white" : "bg-soft text-ink3"}`}
                >
                  {here ? "✓" : "·"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{p.nama}</div>
                  <div className="font-mono text-xs text-muted">{p.nim}</div>
                </div>
                {qi && (qi.status === "antri" || qi.status === "upload") ? (
                  <span className="pill bg-info-bg text-info">mengupload…</span>
                ) : qi?.status === "gagal" ? (
                  <span className="pill bg-bad-bg text-bad">gagal</span>
                ) : (
                  e && (
                    <span className={`pill ${e.status === "valid" ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>
                      {e.status === "valid" ? "valid" : "menunggu validasi"}
                    </span>
                  )
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

// ======================================================================= Panel hasil (di atas kamera)
function ResultSheet({
  review,
  student,
  blockers,
  dup,
  kelasId,
  peserta,
  uploaded,
  kodeList,
  countdown,
  error,
  onTouch,
  onChange,
  onSave,
  onRetake,
  onDetail,
}: {
  review: Review;
  student?: Peserta;
  blockers: string[];
  dup?: Existing;
  kelasId: string;
  peserta: Peserta[];
  uploaded: Set<string>;
  kodeList: string[];
  countdown: number | null;
  error: string | null;
  onTouch: () => void;
  onChange: (r: Review) => void;
  onSave: () => void;
  onRetake: () => void;
  onDetail: () => void;
}) {
  const d = review.capture.scan.decoded!;
  const st = d.soal.map((i) => i.status);
  const multi = st.filter((s) => s === "multi").length;
  const ragu = st.filter((s) => s === "ragu").length;
  const kosong = review.soal.filter((v) => !v).length;
  const ok = blockers.length === 0;
  const [pickNim, setPickNim] = useState(false);
  const showPicker = pickNim || !review.nim || review.nimSource !== "omr";

  return (
    <div onPointerDown={onTouch} className="pb-[max(0.5rem,env(safe-area-inset-bottom))]">
      <div className="mx-auto mt-1.5 mb-1 h-1 w-10 rounded-full bg-line" />
      <div className="space-y-2 px-3 pt-1">
        {/* status + kode + ringkasan dalam satu baris */}
        <div className={`flex items-center gap-2 rounded-xl px-2.5 py-2 ${ok ? "bg-ok-bg" : "bg-warn-bg"}`}>
          <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white ${ok ? "bg-ok" : "bg-warn"}`}>
            {ok ? "✓" : "!"}
          </span>
          <div className="min-w-0 flex-1 leading-tight">
            <div className={`truncate text-[13px] font-bold ${ok ? "text-ok" : "text-warn"}`}>{ok ? "Terbaca jelas" : "Perlu dicek"}</div>
            <div className="truncate text-[11px] text-ink2">{ok ? `terisi ${75 - kosong} · ragu ${ragu} · ganda ${multi}` : blockers.join(" · ")}</div>
          </div>
          <div className="shrink-0 text-right leading-tight">
            <div className="text-[9px] font-semibold tracking-wide text-ink3 uppercase">Kode</div>
            <div className={`font-mono text-lg font-bold tracking-wider ${review.kode ? "" : "text-warn"}`}>{review.kode ?? "???"}</div>
          </div>
        </div>

        {/* mahasiswa */}
        <div className="rounded-xl border border-line px-2.5 py-2">
          {review.nim && !pickNim ? (
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1 leading-tight">
                <div className="truncate text-sm font-semibold">{student?.nama ?? "(di luar daftar kelas)"}</div>
                <div className="truncate font-mono text-[11px] text-muted">
                  {review.nim}
                  {review.nimRead !== review.nim ? ` · terbaca ${review.nimRead}` : ""}
                </div>
              </div>
              <button className="shrink-0 rounded-lg px-2 py-1 text-xs font-semibold text-brand" onClick={() => setPickNim(true)}>
                Ganti
              </button>
            </div>
          ) : (
            <div className="mb-1 text-[11px] font-semibold text-ink3">Pilih mahasiswa · terbaca <span className="font-mono">{review.nimRead}</span></div>
          )}
          {showPicker && (pickNim || !review.nim) && (
            <div className="mt-1">
              <StudentPicker
                peserta={peserta}
                value={review.nim}
                suggestions={review.suggestions}
                uploaded={uploaded}
                onChange={(nim, source) => {
                  onChange({ ...review, nim, nimSource: source, ganti: false });
                  setPickNim(false);
                }}
              />
            </div>
          )}
          {dup && (
            <label className="mt-1.5 flex items-start gap-2 rounded-lg bg-bad-bg p-2 text-xs text-ink">
              <input type="checkbox" className="mt-0.5" checked={review.ganti} onChange={(e) => onChange({ ...review, ganti: e.target.checked })} />
              <span>
                Sudah ada LJK NIM ini ({dup.status === "valid" ? "tervalidasi" : "menunggu validasi"}
                {dup.kelas_id !== kelasId ? `, kelas ${dup.kelas_id}` : ""}). <b>Ganti?</b>
              </span>
            </label>
          )}
        </div>

        {!review.kodeFromOmr && (
          <div className="rounded-xl border border-warn-line bg-warn-bg/40 p-2">
            <div className="mb-1.5 text-xs text-ink2">
              Kode terbaca <b className="font-mono">{d.kode_items.map((i) => i.value ?? "?").join("")}</b> — pilih yang benar:
            </div>
            <KodePicker kodeList={kodeList} value={review.kode} read={d.kode_soal.value} onChange={(k) => onChange({ ...review, kode: k })} />
          </div>
        )}

        {error && <div className="rounded-lg bg-bad-bg p-2 text-xs text-bad">{error}</div>}

        <div className="flex gap-2">
          <button className="btn-ghost px-3 py-3" onClick={onRetake} aria-label="Foto ulang">
            ↺
          </button>
          <button className="btn-ghost flex-1 px-2 py-3 text-xs" onClick={onDetail}>
            Foto &amp; jawaban
          </button>
          <button className="btn-primary relative flex-[2] overflow-hidden py-3 text-[15px]" onClick={onSave} disabled={!ok}>
            {countdown !== null && <span className="absolute inset-y-0 left-0 bg-white/25" style={{ width: `${Math.round(countdown * 100)}%` }} />}
            <span className="relative">{countdown !== null ? "Menyimpan…" : "Simpan & lanjut"}</span>
          </button>
        </div>
        {countdown !== null && <p className="text-center text-[10px] text-muted">Sentuh panel untuk membatalkan simpan otomatis</p>}
      </div>
    </div>
  );
}

// ======================================================================= Detail (foto + koreksi jawaban)
function DetailReview({
  review,
  kelasId,
  peserta,
  uploaded,
  kodeList,
  student,
  dup,
  error,
  onChange,
  onBack,
  onRetake,
  onSave,
}: {
  review: Review;
  kelasId: string;
  peserta: Peserta[];
  uploaded: Set<string>;
  kodeList: string[];
  student?: Peserta;
  dup?: Existing;
  error: string | null;
  onChange: (r: Review) => void;
  onBack: () => void;
  onRetake: () => void;
  onSave: () => void;
}) {
  const d = review.capture.scan.decoded!;
  const soalStatus = d.soal.map((i) => i.status);
  return (
    <div className="fixed inset-0 z-[60] m-0! overflow-y-auto bg-bg">
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-line bg-white px-4 py-3 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <button onClick={onBack} className="btn-ghost px-3 py-1.5 text-xs">
          ← Kembali
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold">{student?.nama ?? "Periksa hasil baca"}</div>
          <div className="truncate text-xs text-muted">
            {kelasId} · NIM terbaca {review.nimRead}
          </div>
        </div>
      </div>
      <div className="mx-auto grid max-w-5xl gap-3 p-3 md:gap-4 md:p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="photo-fit md:sticky md:top-20 md:w-full md:self-start">
          <OmrImage
            sourceCanvas={review.capture.canvas}
            corners={review.capture.scan.corners!}
            values={{ nim: nimToDigits(review.nimRead), kode: review.kode, kuisioner: review.kuis, soal: review.soal }}
            statuses={{
              nim: d.nim.items.map((i) => i.status),
              kode: d.kode_items.map((i) => i.status),
              kuisioner: d.kuisioner.map((i) => i.status),
              soal: soalStatus,
            }}
          />
        </div>
        <div className="space-y-4">
          <section className="card space-y-3 p-4">
            <h3 className="text-sm font-bold">Mahasiswa</h3>
            <StudentPicker
              peserta={peserta}
              value={review.nim}
              suggestions={review.suggestions}
              uploaded={uploaded}
              onChange={(nim, source) => onChange({ ...review, nim, nimSource: source, ganti: false })}
            />
            {dup && (
              <label className="flex items-start gap-2 rounded-lg bg-bad-bg p-3 text-sm">
                <input type="checkbox" className="mt-0.5" checked={review.ganti} onChange={(e) => onChange({ ...review, ganti: e.target.checked })} />
                <span>
                  NIM ini sudah punya LJK ({dup.status === "valid" ? "sudah divalidasi" : "menunggu validasi"}, diupload {dup.uploaded_by}
                  {dup.kelas_id !== kelasId ? ` di kelas ${dup.kelas_id}` : ""}). <b>Ganti dengan foto ini?</b>
                </span>
              </label>
            )}
          </section>
          <section className="card p-4">
            <h3 className="mb-2 text-sm font-bold">Kode soal</h3>
            <KodePicker kodeList={kodeList} value={review.kode} read={d.kode_soal.value} onChange={(k) => onChange({ ...review, kode: k })} />
          </section>
          <section className="card space-y-4 p-4">
            <p className="text-xs text-muted">Koreksi jawaban tidak wajib di sini — semua LJK tetap melewati tahap validasi.</p>
            <AnswerGrid
              title="Kuisioner"
              options={["A", "B", "C", "D"]}
              values={review.kuis}
              original={d.kuisioner.map((i) => i.value)}
              statuses={d.kuisioner.map((i) => i.status)}
              onChange={(i, v) => onChange({ ...review, kuis: review.kuis.map((x, j) => (j === i ? v : x)) })}
            />
            <AnswerGrid
              title="Jawaban soal"
              options={["A", "B", "C", "D"]}
              values={review.soal}
              original={d.soal.map((i) => i.value)}
              statuses={soalStatus}
              onChange={(i, v) => onChange({ ...review, soal: review.soal.map((x, j) => (j === i ? v : x)) })}
            />
          </section>
          {error && <div className="rounded-lg bg-bad-bg p-3 text-sm text-bad">{error}</div>}
          <div className="sticky bottom-0 -mx-4 flex gap-2 border-t border-line bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] md:static md:mx-0 md:border-0 md:bg-transparent md:p-0">
            <button className="btn-ghost flex-1" onClick={onRetake}>
              ↺ Foto ulang
            </button>
            <button className="btn-primary flex-[2]" onClick={onSave} disabled={!review.nim || !review.kode || (!!dup && !review.ganti)}>
              Simpan &amp; lanjut
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ======================================================================= Riwayat sesi
function QueuePanel({
  queue,
  onClose,
  onRetry,
  onReopen,
}: {
  queue: QItem[];
  onClose: () => void;
  onRetry: (k: string) => void;
  onReopen: (k: string) => void;
}) {
  return (
    <div className="fixed inset-0 z-[70] m-0! flex items-end bg-black/40 md:items-center md:justify-center" onClick={onClose}>
      <div
        className="max-h-[80dvh] w-full overflow-y-auto rounded-t-3xl bg-white pb-[max(1rem,env(safe-area-inset-bottom))] md:max-w-lg md:rounded-3xl animate-sheet-up"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between border-b border-line bg-white px-5 py-4">
          <h3 className="font-bold">LJK sesi ini ({queue.length})</h3>
          <button onClick={onClose} className="text-sm font-semibold text-muted">
            Tutup
          </button>
        </div>
        <ul className="divide-y divide-line">
          {queue.map((i) => (
            <li key={i.key} className="flex items-center gap-3 px-5 py-3">
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                  i.status === "ok" ? "bg-ok text-white" : i.status === "gagal" ? "bg-bad text-white" : "bg-info-bg text-info"
                }`}
              >
                {i.status === "ok" ? "✓" : i.status === "gagal" ? "!" : <span className="h-3 w-3 animate-spin rounded-full border-2 border-info/30 border-t-info" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{i.nama}</div>
                <div className="truncate text-xs text-muted">
                  {i.review.nim} · kode {i.review.kode} · {new Date(i.at).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
                </div>
                {i.error && <div className="text-xs text-bad">{i.error}</div>}
              </div>
              {i.status === "gagal" && (
                <div className="flex shrink-0 gap-1">
                  <button className="btn-ghost px-2.5 py-1.5 text-xs" onClick={() => onReopen(i.key)}>
                    Buka
                  </button>
                  <button className="btn-primary px-2.5 py-1.5 text-xs" onClick={() => onRetry(i.key)}>
                    Ulangi
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
