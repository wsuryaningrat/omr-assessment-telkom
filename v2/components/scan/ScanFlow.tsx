"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import AnswerGrid from "@/components/AnswerGrid";
import OmrImage from "@/components/OmrImage";
import StudentPicker from "@/components/StudentPicker";
import { matchNim } from "@/lib/omr/nim";
import { countOmrFlags, nimToDigits, toStoredOmr } from "@/lib/omr/store";
import { TEMPLATE } from "@/lib/omr/scan";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { Kelas, Peserta } from "@/lib/types";
import CameraScanner, { type Capture } from "./CameraScanner";

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
  soal: (string | null)[];
  kuis: (string | null)[];
  ganti: boolean;
}

const KODE = TEMPLATE.fields.kode_soal.options;

export default function ScanFlow({
  kelas,
  peserta,
  existing: initialExisting,
  pengawasEmail,
  demo = false,
}: {
  kelas: Kelas;
  peserta: Peserta[];
  existing: Existing[];
  pengawasEmail: string;
  /** Mode demo: tidak upload / simpan ke database. */
  demo?: boolean;
}) {
  const [existing, setExisting] = useState(initialExisting);
  const [camera, setCamera] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [sessionCount, setSessionCount] = useState(0);
  const [showGrid, setShowGrid] = useState(false);
  const [q, setQ] = useState("");

  const byNim = useMemo(() => new Map(existing.map((e) => [e.nim, e])), [existing]);
  const uploadedHere = useMemo(() => new Set(existing.filter((e) => e.kelas_id === kelas.id).map((e) => e.nim)), [existing, kelas.id]);
  const pesertaByNim = useMemo(() => new Map(peserta.map((p) => [p.nim, p])), [peserta]);
  const done = peserta.filter((p) => uploadedHere.has(p.nim)).length;

  const onCapture = useCallback(
    (capture: Capture) => {
      const d = capture.scan.decoded!;
      const m = matchNim(d.nim.value, peserta);
      setShowGrid(false);
      setError(null);
      setReview({
        capture,
        nimRead: d.nim.value,
        nim: m.exact?.nim ?? "",
        nimSource: m.exact ? "omr" : "dropdown",
        suggestions: m.suggestions.map((s) => s.nim),
        kode: d.kode_soal.status === "ok" || d.kode_soal.status === "ragu" ? d.kode_soal.value : null,
        soal: d.soal.map((i) => i.value),
        kuis: d.kuisioner.map((i) => i.value),
        ganti: false,
      });
    },
    [peserta],
  );

  const save = async () => {
    if (!review) return;
    const { capture, nim } = review;
    if (!/^\d{12}$/.test(nim)) return setError("Pilih / konfirmasi NIM mahasiswa dulu.");
    if (!review.kode) return setError("Pilih kode soal.");
    const dup = byNim.get(nim);
    if (dup && !review.ganti) return setError("NIM ini sudah punya LJK. Centang 'ganti' jika foto ini menggantikan yang lama.");
    setSaving(true);
    setError(null);
    if (demo) {
      setExisting((prev) => [
        ...prev.filter((e) => e.nim !== nim),
        { id: crypto.randomUUID(), nim, kelas_id: kelas.id, uploaded_by: pengawasEmail, uploaded_at: new Date().toISOString(), status: "perlu_validasi" },
      ]);
      setSessionCount((n) => n + 1);
      setToast(`(demo, tidak disimpan) ${pesertaByNim.get(nim)?.nama ?? nim}`);
      setTimeout(() => setToast(null), 2500);
      setReview(null);
      setSaving(false);
      setCamera(true);
      return;
    }
    try {
      const supabase = supabaseBrowser();
      const blob: Blob = await new Promise((res, rej) =>
        capture.canvas.toBlob((b) => (b ? res(b) : rej(new Error("Gagal membuat JPEG"))), "image/jpeg", 0.85),
      );
      const path = `${kelas.ujian_id}/${kelas.id}/${nim}-${Date.now()}-${crypto.randomUUID().slice(0, 8)}.jpg`;
      const up = await supabase.storage.from("ljk").upload(path, blob, { contentType: "image/jpeg", upsert: false });
      if (up.error) throw new Error(`Upload foto gagal: ${up.error.message}`);
      const omr = toStoredOmr(capture.scan, { w: capture.canvas.width, h: capture.canvas.height });
      const matched = review.nimSource === "omr" || pesertaByNim.has(nim);
      const payload = {
        ujian_id: kelas.ujian_id,
        kelas_id: kelas.id,
        nim,
        nim_terbaca: review.nimRead,
        nim_sumber: review.nimSource,
        kode_soal: review.kode,
        jawaban: review.soal,
        kuisioner: review.kuis,
        omr,
        flags: countOmrFlags(omr, review.nimSource === "omr") + (matched ? 0 : 1),
        photo_path: path,
      };
      const { data: id, error: rpcErr } = await supabase.rpc("simpan_ljk", { payload, ganti: review.ganti });
      if (rpcErr) {
        await supabase.storage.from("ljk").remove([path]).catch(() => {});
        throw new Error(
          rpcErr.message.includes("submissions_one_active_per_nim")
            ? "NIM ini baru saja diupload pengawas lain. Muat ulang halaman lalu pilih 'ganti' jika perlu."
            : rpcErr.message,
        );
      }
      setExisting((prev) => [
        ...prev.filter((e) => e.nim !== nim),
        { id: id as string, nim, kelas_id: kelas.id, uploaded_by: pengawasEmail, uploaded_at: new Date().toISOString(), status: "perlu_validasi" },
      ]);
      setSessionCount((n) => n + 1);
      const nama = pesertaByNim.get(nim)?.nama ?? nim;
      setToast(`Tersimpan: ${nama}`);
      setTimeout(() => setToast(null), 2500);
      setReview(null);
      setCamera(true); // lanjut foto berikutnya
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const retake = () => {
    setReview(null);
    setError(null);
    setCamera(true);
  };

  // ------------------------------------------------------------------ Review
  if (review) {
    const d = review.capture.scan.decoded!;
    const q0 = review.capture.scan.quality!;
    const student = pesertaByNim.get(review.nim);
    const dup = review.nim ? byNim.get(review.nim) : undefined;
    const soalStatus = d.soal.map((i) => i.status);
    const ragu = soalStatus.filter((s) => s === "ragu").length;
    const multi = soalStatus.filter((s) => s === "multi").length;
    return (
      <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-50">
        <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-line bg-white px-4 py-3">
          <button onClick={retake} className="btn-ghost px-3 py-1.5 text-xs">
            ↺ Foto ulang
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold">Periksa hasil baca</div>
            <div className="truncate text-xs text-muted">
              {kelas.id} · {review.capture.scan.ms} ms · {q0.pxPerMm} px/mm
            </div>
          </div>
        </div>
        <div className="mx-auto grid max-w-5xl gap-4 p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="md:sticky md:top-20 md:self-start">
            <OmrImage
              sourceCanvas={review.capture.canvas}
              corners={review.capture.scan.corners!}
              values={{ nim: nimToDigits(review.nimRead), kode: review.kode, kuisioner: review.kuis, soal: review.soal }}
              statuses={{
                nim: d.nim.items.map((i) => i.status),
                kode: d.kode_soal.status,
                kuisioner: d.kuisioner.map((i) => i.status),
                soal: soalStatus,
              }}
            />
          </div>

          <div className="space-y-4">
            {/* NIM */}
            <section className="card space-y-3 p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold">Mahasiswa</h3>
                <span className="font-mono text-xs text-muted">NIM terbaca: {review.nimRead}</span>
              </div>
              {review.nimSource === "omr" && student ? (
                <div className="flex items-center gap-3 rounded-lg bg-green-50 p-3">
                  <span className="text-xl text-ok">✓</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-semibold">{student.nama}</div>
                    <div className="font-mono text-xs text-muted">{student.nim}</div>
                  </div>
                  <button className="text-xs font-semibold text-brand" onClick={() => setReview({ ...review, nimSource: "dropdown" })}>
                    Bukan dia?
                  </button>
                </div>
              ) : (
                <>
                  <div className="rounded-lg bg-amber-50 p-3 text-sm text-warn">
                    {review.nim
                      ? `Dipilih: ${student?.nama ?? "(di luar daftar)"} — ${review.nim}`
                      : "NIM tidak terbaca / tidak ada di kelas ini. Cocokkan dengan tulisan di LJK lalu pilih mahasiswanya."}
                  </div>
                  <StudentPicker
                    peserta={peserta}
                    value={review.nim}
                    suggestions={review.suggestions}
                    uploaded={uploadedHere}
                    onChange={(nim, source) => setReview({ ...review, nim, nimSource: source, ganti: false })}
                  />
                </>
              )}
              {dup && (
                <label className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
                  <input type="checkbox" className="mt-0.5" checked={review.ganti} onChange={(e) => setReview({ ...review, ganti: e.target.checked })} />
                  <span>
                    NIM ini sudah punya LJK ({dup.status === "valid" ? "sudah divalidasi" : "menunggu validasi"}, diupload {dup.uploaded_by}
                    {dup.kelas_id !== kelas.id ? ` di kelas ${dup.kelas_id}` : ""}). <b>Ganti dengan foto ini?</b>
                  </span>
                </label>
              )}
            </section>

            {/* Kode soal */}
            <section className="card p-4">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-bold">Kode soal</h3>
                {d.kode_soal.status !== "ok" && <span className="pill bg-amber-100 text-warn">cek manual</span>}
              </div>
              <div className="grid grid-cols-4 gap-2">
                {KODE.map((k) => (
                  <button
                    key={k}
                    onClick={() => setReview({ ...review, kode: k })}
                    className={`rounded-lg border py-2.5 font-mono text-sm font-bold ${review.kode === k ? "border-brand bg-brand text-white" : "border-line bg-white"}`}
                  >
                    {k}
                  </button>
                ))}
              </div>
            </section>

            {/* Ringkasan */}
            <section className="card space-y-3 p-4">
              <div className="grid grid-cols-3 gap-2 text-center">
                <Mini label="Soal terisi" value={`${review.soal.filter(Boolean).length}/75`} />
                <Mini label="Ragu" value={String(ragu)} tone={ragu ? "warn" : undefined} />
                <Mini label="Ganda" value={String(multi)} tone={multi ? "bad" : undefined} />
              </div>
              <p className="text-xs text-muted">
                Jawaban tidak wajib dikoreksi di sini — semua LJK tetap melewati tahap validasi. Pastikan saja foto jelas dan mahasiswanya benar.
              </p>
              <button className="text-sm font-semibold text-brand" onClick={() => setShowGrid((v) => !v)}>
                {showGrid ? "Sembunyikan jawaban" : "Lihat / koreksi jawaban"}
              </button>
              {showGrid && (
                <div className="space-y-4">
                  <AnswerGrid
                    title="Jawaban soal"
                    options={["A", "B", "C", "D"]}
                    values={review.soal}
                    original={d.soal.map((i) => i.value)}
                    statuses={soalStatus}
                    onChange={(i, v) => setReview({ ...review, soal: review.soal.map((x, j) => (j === i ? v : x)) })}
                  />
                  <AnswerGrid
                    title="Kuisioner"
                    options={["A", "B", "C", "D"]}
                    values={review.kuis}
                    original={d.kuisioner.map((i) => i.value)}
                    statuses={d.kuisioner.map((i) => i.status)}
                    onChange={(i, v) => setReview({ ...review, kuis: review.kuis.map((x, j) => (j === i ? v : x)) })}
                  />
                </div>
              )}
            </section>

            {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-bad">{error}</div>}
            <div className="sticky bottom-0 -mx-4 flex gap-2 border-t border-line bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] md:static md:mx-0 md:border-0 md:bg-transparent md:p-0">
              <button className="btn-ghost flex-1" onClick={retake} disabled={saving}>
                Foto ulang
              </button>
              <button className="btn-primary flex-[2]" onClick={save} disabled={saving || !review.nim || !review.kode || (!!dup && !review.ganti)}>
                {saving ? "Menyimpan…" : "Simpan & foto berikutnya"}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------ Halaman kelas
  const s = q.trim().toLowerCase();
  const list = peserta.filter((p) => !s || p.nama.toLowerCase().includes(s) || p.nim.includes(s));
  return (
    <div className="space-y-4">
      {camera && (
        <CameraScanner
          title={kelas.id}
          subtitle={`${done}/${peserta.length} terupload${sessionCount ? ` · sesi ini ${sessionCount}` : ""}`}
          onCapture={(c) => {
            setCamera(false);
            onCapture(c);
          }}
          onClose={() => setCamera(false)}
        />
      )}
      {toast && (
        <div className="fixed top-4 left-1/2 z-[60] m-0! -translate-x-1/2 rounded-full bg-ok px-4 py-2 text-sm font-semibold text-white shadow-lg">{toast}</div>
      )}

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {demo ? (
            <span className="pill bg-amber-100 text-warn">MODE DEMO — tidak tersimpan</span>
          ) : (
            <Link href="/scan" className="text-xs font-semibold text-brand">
              ← Ganti kelas
            </Link>
          )}
          <h1 className="truncate text-xl font-bold">{kelas.id}</h1>
          <p className="text-sm text-muted">
            {kelas.prodi} · {kelas.hari} {kelas.waktu} · {kelas.ruangan}
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold">
            {done}
            <span className="text-base text-muted">/{peserta.length}</span>
          </div>
          <div className="text-xs text-muted">terupload</div>
        </div>
      </div>

      <button onClick={() => setCamera(true)} className="btn-primary w-full py-4 text-base">
        ◉ Buka kamera & scan LJK
      </button>
      <div className="card p-4 text-xs leading-relaxed text-slate-600">
        <b>Tips:</b> letakkan LJK di permukaan datar & terang, HP tegak lurus di atas lembar, keempat kotak hitam masuk frame. Kamera akan memotret otomatis
        saat bingkai berubah hijau dan stabil. Satu foto = satu mahasiswa.
      </div>

      <section className="card overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <h2 className="flex-1 font-semibold">Daftar mahasiswa</h2>
          <input className="input max-w-48 py-1.5" placeholder="Cari…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <ul className="divide-y divide-line">
          {list.map((p) => {
            const e = byNim.get(p.nim);
            const here = e && e.kelas_id === kelas.id;
            return (
              <li key={p.nim} className="flex items-center gap-3 px-4 py-2.5">
                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${here ? "bg-ok text-white" : "bg-slate-100 text-slate-400"}`}>
                  {here ? "✓" : "·"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{p.nama}</div>
                  <div className="font-mono text-xs text-muted">{p.nim}</div>
                </div>
                {e && (
                  <span className={`pill ${e.status === "valid" ? "bg-green-100 text-ok" : "bg-amber-100 text-warn"}`}>
                    {e.status === "valid" ? "valid" : "menunggu validasi"}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function Mini({ label, value, tone }: { label: string; value: string; tone?: "warn" | "bad" }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2">
      <div className={`text-lg font-bold ${tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : ""}`}>{value}</div>
      <div className="text-[11px] text-muted">{label}</div>
    </div>
  );
}
