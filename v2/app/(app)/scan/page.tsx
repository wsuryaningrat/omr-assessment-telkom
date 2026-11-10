import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ScanTable from "@/components/scan/ScanTable";
import type { Progres } from "@/lib/types";

export default async function PilihKelas() {
  const { supabase, pengawas } = await requirePengawas();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return <div className="card p-6 text-sm">Belum ada ujian aktif.</div>;
  const { data } = await supabase.from("progres_kelas").select("*").eq("ujian_id", ujian.id);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-serif text-2xl font-bold">Scan LJK</h1>
        <p className="text-sm text-muted">Pilih kelas lalu foto LJK satu per satu. Status & aksi tiap kelas ada di kolom kanan.</p>
      </div>
      <ScanTable rows={(data ?? []) as Progres[]} isAdmin={pengawas.role === "admin"} />
    </div>
  );
}
