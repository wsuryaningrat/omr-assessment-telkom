import { notFound } from "next/navigation";
import ValidationEditor from "@/components/validasi/ValidationEditor";
import type { Submission } from "@/lib/types";
import sample from "./sample.json";

/** Demo halaman validasi dengan contoh LJK sintetis. Aktif hanya jika NEXT_PUBLIC_DEMO_MODE=1. */
export default function DemoValidasi() {
  if (process.env.NEXT_PUBLIC_DEMO_MODE !== "1") notFound();
  const omr = sample.omr as unknown as Submission["omr"];
  const sub: Submission = {
    id: "demo",
    ujian_id: "demo",
    kelas_id: "DEMO-01",
    nim: sample.nim,
    nim_terbaca: sample.nim,
    nim_sumber: "omr",
    kode_soal: omr.kode_soal.value,
    jawaban: omr.soal.map((i) => i.value),
    kuisioner: omr.kuisioner.map((i) => i.value),
    omr,
    flags: [...omr.soal, ...omr.kuisioner].filter((i) => i.status === "ragu" || i.status === "multi").length,
    photo_path: "demo",
    uploaded_by: "pengawas.demo@gmail.com",
    uploaded_at: new Date().toISOString(),
    status: "perlu_validasi",
    validated_by: null,
    validated_at: null,
    catatan: null,
  };
  return (
    <main className="mx-auto max-w-6xl px-4 py-5">
      <p className="mb-3 pill bg-amber-100 text-warn">MODE DEMO — tombol simpan tidak terhubung ke database</p>
      <ValidationEditor
        sub={sub}
        photoUrl="/ljk/contoh_foto_ljk_v2.jpg"
        peserta={[
          { kelas_id: "DEMO-01", nim: "103052600003", nama: "Citra Dewi" },
          { kelas_id: "DEMO-01", nim: "103052600004", nama: "Dimas Pratama" },
        ]}
        audit={[{ at: new Date().toISOString(), actor: "pengawas.demo@gmail.com", action: "upload", detail: { nim: sample.nim, flags: 2 } }]}
        nextId={null}
        takenNims={[]}
        me="demo@local"
      />
    </main>
  );
}
