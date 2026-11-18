import RekapTable from "@/components/rekap/RekapTable";
import { getUjianAktif, requireAdmin } from "@/lib/auth";
import { loadRekap } from "@/lib/rekap";
import { asset } from "@/lib/basePath";

export default async function RekapPage(props: { searchParams: Promise<{ kelas?: string; status?: string }> }) {
  const { kelas, status } = await props.searchParams;
  const { supabase } = await requireAdmin();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return <div className="card p-6 text-sm">Belum ada ujian aktif.</div>;
  const { rows, belum } = await loadRekap(supabase, ujian.id);
  const kelasList = [...new Set(rows.map((r) => r.kelas_id))].sort();
  const shown = rows.filter((r) => (!kelas || r.kelas_id === kelas) && (!status || r.status === status));
  const nilai = shown.map((r) => (r.nilai === null ? null : Number(r.nilai))).filter((x): x is number => x !== null);
  const avg = nilai.length ? nilai.reduce((a, b) => a + b, 0) / nilai.length : 0;
  const tanpaKunci = shown.filter((r) => r.nilai === null).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-bold">Rekap nilai</h1>
          <p className="text-sm text-muted">Nilai dihitung di server dari kunci (hanya admin yang melihat kunci & nilai).</p>
        </div>
        <div className="flex gap-2">
          <a href={asset("/api/rekap?format=xlsx")} className="btn-primary">
            Unduh Excel
          </a>
          <a href={asset("/api/rekap?format=csv")} className="btn-ghost">
            CSV
          </a>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Box label="LJK" value={shown.length} />
        <Box label="Valid" value={shown.filter((r) => r.status === "valid").length} />
        <Box label="Rata-rata nilai" value={Math.round(avg * 10) / 10} />
        <Box label="Belum upload" value={kelas ? belum.filter((b) => b.kelas_id === kelas).length : belum.length} />
      </div>
      {tanpaKunci > 0 && (
        <div className="rounded-lg bg-warn-bg p-3 text-sm text-warn">{tanpaKunci} LJK belum bisa dinilai (kode soal kosong atau kunci belum diisi).</div>
      )}
      <form className="flex flex-wrap gap-2">
        <select name="kelas" defaultValue={kelas ?? ""} className="input max-w-xs">
          <option value="">Semua kelas</option>
          {kelasList.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <select name="status" defaultValue={status ?? ""} className="input max-w-[12rem]">
          <option value="">Semua status</option>
          <option value="valid">Valid</option>
          <option value="perlu_validasi">Perlu validasi</option>
        </select>
        <button className="btn-ghost">Terapkan</button>
      </form>
      <RekapTable
        resetKey={`${kelas ?? ""}|${status ?? ""}`}
        rows={shown.map((r) => ({
          submission_id: r.submission_id,
          kelas_id: r.kelas_id,
          nim: r.nim,
          nama: r.nama,
          kode_soal: r.kode_soal,
          benar: r.benar,
          salah: r.salah,
          kosong: r.kosong,
          total: r.total,
          nilai: r.nilai,
          status: r.status,
        }))}
      />
    </div>
  );
}

function Box({ label, value }: { label: string; value: number }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold text-muted uppercase">{label}</div>
      <div className="mt-1 text-2xl font-bold">{value.toLocaleString("id-ID")}</div>
    </div>
  );
}
