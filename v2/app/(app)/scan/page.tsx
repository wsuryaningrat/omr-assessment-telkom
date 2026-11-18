import Link from "next/link";
import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ScanTable, { KELAS_FILTER, type KelasFilter } from "@/components/scan/ScanTable";
import type { Progres } from "@/lib/types";

export default async function PilihKelas(props: { searchParams: Promise<{ status?: string }> }) {
  const { status } = await props.searchParams;
  const { supabase, pengawas } = await requirePengawas();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return <div className="card p-6 text-sm">Belum ada ujian aktif.</div>;
  const [{ data }, { data: next }] = await Promise.all([
    supabase.from("progres_kelas").select("*").eq("ujian_id", ujian.id),
    supabase
      .from("submissions")
      .select("id")
      .eq("ujian_id", ujian.id)
      .eq("status", "perlu_validasi")
      .order("flags", { ascending: false })
      .order("uploaded_at")
      .limit(1)
      .maybeSingle(),
  ]);
  const rows = (data ?? []) as Progres[];
  const menunggu = rows.reduce((s, r) => s + r.perlu_validasi, 0);
  const initialFilter = (status && status in KELAS_FILTER ? status : "semua") as KelasFilter;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-bold">Scan &amp; Validasi LJK</h1>
          <p className="text-sm text-muted">
            Ketuk kelas untuk memotret LJK. Buka ☰ untuk memvalidasi LJK satu per satu; kelasmu sendiri maupun kelas lain.
            {menunggu > 0 && <b className="text-warn"> {menunggu} LJK menunggu validasi.</b>}
          </p>
        </div>
        {next && (
          <Link href={`/validasi/s/${next.id}`} className="btn-primary">
            Mulai validasi berikutnya →
          </Link>
        )}
      </div>
      <ScanTable rows={rows} isAdmin={pengawas.role === "admin"} initialFilter={initialFilter} />
    </div>
  );
}
