import RekapTable from "@/components/rekap/RekapTable";
import { getUjianAktif, requireAdmin } from "@/lib/auth";
import { loadRekap } from "@/lib/rekap";
import { asset } from "@/lib/basePath";

export default async function RekapPage(props: { searchParams: Promise<{ prodi?: string; kelas?: string; status?: string }> }) {
  const { prodi, kelas, status } = await props.searchParams;
  const { supabase } = await requireAdmin();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return <div className="card p-6 text-sm">Belum ada ujian aktif.</div>;
  const { rows, belum } = await loadRekap(supabase, ujian.id);

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
      {/* filter, urutan & paginasi diproses di klien: pilihan dropdown langsung diterapkan */}
      <RekapTable
        initial={{ prodi: prodi ?? "", kelas: kelas ?? "", status: status ?? "" }}
        belum={belum.map((b) => ({ kelas_id: b.kelas_id, prodi: b.prodi }))}
        rows={rows.map((r) => ({
          submission_id: r.submission_id,
          kelas_id: r.kelas_id,
          prodi: r.prodi,
          nim: r.nim,
          nama: r.nama,
          kode_soal: r.kode_soal,
          benar: r.benar,
          salah: r.salah,
          kosong: r.kosong,
          total: r.total,
          nilai: r.nilai === null ? null : Number(r.nilai),
          status: r.status,
        }))}
      />
    </div>
  );
}
