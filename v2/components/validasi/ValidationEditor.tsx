"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import AnswerGrid from "@/components/AnswerGrid";
import KodePicker from "@/components/KodePicker";
import OmrImage from "@/components/OmrImage";
import StudentPicker from "@/components/StudentPicker";
import { nimToDigits } from "@/lib/omr/store";
import type { OverlayFocus } from "@/lib/omr/overlay";
import { supabaseBrowser } from "@/lib/supabase/client";
import { STATUS_LABEL, type Peserta, type Submission, type SubmissionStatus } from "@/lib/types";

export default function ValidationEditor({
  sub,
  photoUrl,
  peserta,
  audit,
  nextId,
  takenNims,
  me,
  kodeList,
}: {
  kodeList: string[];
  sub: Submission;
  photoUrl: string | null;
  peserta: Peserta[];
  audit: { at: string; actor: string; action: string; detail: Record<string, unknown> }[];
  nextId: string | null;
  takenNims: string[];
  me: string;
}) {
  const router = useRouter();
  const omr = sub.omr;
  const [nim, setNim] = useState(sub.nim);
  const [nimSumber, setNimSumber] = useState(sub.nim_sumber);
  const [editNim, setEditNim] = useState(false);
  const [kode, setKode] = useState<string | null>(sub.kode_soal);
  const [soal, setSoal] = useState<(string | null)[]>(sub.jawaban);
  const [kuis, setKuis] = useState<(string | null)[]>(sub.kuisioner);
  const [catatan, setCatatan] = useState(sub.catatan ?? "");
  const [onlyFlagged, setOnlyFlagged] = useState(
    [...omr.soal, ...omr.kuisioner].some((i) => i.status === "ragu" || i.status === "multi"),
  );
  const [focus, setFocus] = useState<OverlayFocus | null>(null);
  const [overlay, setOverlay] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pesertaByNim = useMemo(() => new Map(peserta.map((p) => [p.nim, p])), [peserta]);
  const taken = useMemo(() => new Set(takenNims), [takenNims]);
  const student = pesertaByNim.get(nim);
  const locked = sub.status === "diganti";
  const dirty =
    nim !== sub.nim ||
    kode !== sub.kode_soal ||
    JSON.stringify(soal) !== JSON.stringify(sub.jawaban) ||
    JSON.stringify(kuis) !== JSON.stringify(sub.kuisioner) ||
    catatan !== (sub.catatan ?? "");

  const soalStatus = omr.soal.map((i) => i.status);
  const kuisStatus = omr.kuisioner.map((i) => i.status);

  const save = async (status?: SubmissionStatus, goNext = false) => {
    setErr(null);
    if (!/^\d{12}$/.test(nim)) return setErr("NIM harus 12 digit.");
    if (status === "valid" && !kode) return setErr("Pilih kode soal sebelum menandai valid.");
    if (status === "ditolak" && !catatan.trim()) return setErr("Tulis alasan penolakan di catatan (mis. foto buram, minta foto ulang).");
    if (nim !== sub.nim && taken.has(nim)) return setErr("NIM ini sudah dipakai LJK lain yang aktif.");
    setBusy(true);
    const { error } = await supabaseBrowser()
      .from("submissions")
      .update({
        nim,
        nim_sumber: nim !== sub.nim ? "validator" : nimSumber,
        kode_soal: kode,
        jawaban: soal,
        kuisioner: kuis,
        catatan: catatan.trim() || null,
        ...(status ? { status } : {}),
        ...(status === "valid" ? { flags: 0 } : {}),
      })
      .eq("id", sub.id);
    setBusy(false);
    if (error) return setErr(error.message);
    if (goNext && nextId) router.push(`/validasi/s/${nextId}`);
    else if (goNext) router.push(`/validasi/${encodeURIComponent(sub.kelas_id)}`);
    else router.refresh();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`/validasi/${encodeURIComponent(sub.kelas_id)}`} className="text-xs font-semibold text-brand">
          ← {sub.kelas_id}
        </Link>
        <span
          className={`pill ${
            sub.status === "valid" ? "bg-green-100 text-ok" : sub.status === "perlu_validasi" ? "bg-amber-100 text-warn" : "bg-slate-200 text-slate-600"
          }`}
        >
          {STATUS_LABEL[sub.status]}
          {sub.validated_by ? ` · ${sub.validated_by}` : ""}
        </span>
        <span className="text-xs text-muted">
          diupload {sub.uploaded_by} · {new Date(sub.uploaded_at).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <div className="space-y-2 lg:sticky lg:top-20 lg:self-start">
          {photoUrl ? (
            <OmrImage
              src={photoUrl}
              corners={omr.corners}
              showOverlay={overlay}
              focus={focus}
              values={{ nim: nimToDigits(sub.nim_terbaca ?? ""), kode, kuisioner: kuis, soal }}
              statuses={{ nim: omr.nim.map((i) => i.status), kode: omr.kode_items?.map((i) => i.status), kuisioner: kuisStatus, soal: soalStatus }}
            />
          ) : (
            <div className="card p-6 text-sm text-muted">Foto tidak bisa dimuat.</div>
          )}
          <div className="flex items-center justify-between text-xs text-muted">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} /> tampilkan overlay
            </label>
            <span>
              {omr.quality.pxPerMm} px/mm · tajam {omr.quality.sharpness} · {omr.template_version}
            </span>
          </div>
          <div className="flex flex-wrap gap-3 text-[11px] text-muted">
            <Legend color="bg-green-600" label="terbaca" />
            <Legend color="bg-amber-500" label="ragu" />
            <Legend color="bg-red-600" label="ganda" />
            <Legend color="bg-blue-600" label="dikoreksi / disorot" />
          </div>
        </div>

        <div className="space-y-4">
          <section className="card space-y-3 p-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold">Mahasiswa</h3>
              <span className="font-mono text-xs text-muted">NIM terbaca: {sub.nim_terbaca ?? "—"}</span>
            </div>
            <div className={`flex items-center gap-3 rounded-lg p-3 ${student ? "bg-green-50" : "bg-amber-50"}`}>
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold">{student?.nama ?? "Di luar daftar kelas"}</div>
                <div className="font-mono text-xs text-muted">
                  {nim} · sumber: {nim !== sub.nim ? "validator" : nimSumber}
                </div>
              </div>
              {!locked && (
                <button className="text-xs font-semibold text-brand" onClick={() => setEditNim((v) => !v)}>
                  {editNim ? "Tutup" : "Ganti"}
                </button>
              )}
            </div>
            {editNim && (
              <StudentPicker
                peserta={peserta}
                value={nim}
                uploaded={taken}
                onChange={(n, src) => {
                  setNim(n);
                  setNimSumber(src);
                  if (src === "dropdown") setEditNim(false);
                }}
              />
            )}
          </section>

          <section className="card p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-bold">Kode soal</h3>
              <span className="font-mono text-xs text-muted">
                terbaca: {omr.kode_items ? omr.kode_items.map((i) => i.value ?? "?").join("") : (omr.kode_soal.value ?? "—")}
              </span>
            </div>
            <KodePicker kodeList={kodeList} value={kode} read={omr.kode_soal.value} onChange={setKode} disabled={locked} />
          </section>

          <section className="card space-y-4 p-4">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted">Arahkan kursor ke nomor untuk menyorotnya di foto.</span>
              <label className="flex items-center gap-1.5 text-xs font-semibold">
                <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
                hanya yang ditandai
              </label>
            </div>
            <AnswerGrid
              title="Jawaban soal"
              options={["A", "B", "C", "D"]}
              values={soal}
              original={sub.jawaban}
              statuses={soalStatus}
              onlyFlagged={onlyFlagged}
              readOnly={locked}
              onFocus={(i) => setFocus({ field: "soal", index: i })}
              onChange={(i, v) => setSoal((s) => s.map((x, j) => (j === i ? v : x)))}
            />
            <AnswerGrid
              title="Kuisioner"
              options={["A", "B", "C", "D"]}
              values={kuis}
              original={sub.kuisioner}
              statuses={kuisStatus}
              onlyFlagged={onlyFlagged}
              readOnly={locked}
              onFocus={(i) => setFocus({ field: "kuisioner", index: i })}
              onChange={(i, v) => setKuis((s) => s.map((x, j) => (j === i ? v : x)))}
            />
          </section>

          <section className="card p-4">
            <label className="label">Catatan</label>
            <textarea
              className="input min-h-16"
              value={catatan}
              disabled={locked}
              onChange={(e) => setCatatan(e.target.value)}
              placeholder="Wajib diisi jika menolak (mis. foto buram, LJK salah orang)."
            />
          </section>

          {err && <div className="rounded-lg bg-red-50 p-3 text-sm text-bad">{err}</div>}
          {!locked && (
            <div className="sticky bottom-16 z-10 flex flex-wrap gap-2 rounded-xl border border-line bg-white p-3 shadow-sm md:bottom-2">
              <button className="btn-ghost text-bad" disabled={busy} onClick={() => save("ditolak", true)}>
                Tolak
              </button>
              <button className="btn-ghost" disabled={busy || !dirty} onClick={() => save()}>
                Simpan
              </button>
              <button className="btn-ok flex-1" disabled={busy} onClick={() => save("valid", true)}>
                {busy ? "Menyimpan…" : `Valid${nextId ? " & berikutnya →" : ""}`}
              </button>
            </div>
          )}

          <details className="card p-4 text-xs">
            <summary className="cursor-pointer font-semibold">Riwayat perubahan ({audit.length})</summary>
            <ul className="mt-2 space-y-1.5">
              {audit.map((a, i) => (
                <li key={i} className="text-muted">
                  <b className="text-ink">{a.actor === me ? "kamu" : a.actor}</b> · {a.action} ·{" "}
                  {new Date(a.at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })}
                  <code className="mt-0.5 block truncate rounded bg-slate-50 px-1.5 py-0.5">{JSON.stringify(a.detail)}</code>
                </li>
              ))}
            </ul>
          </details>
        </div>
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} /> {label}
    </span>
  );
}
