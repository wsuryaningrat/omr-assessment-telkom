import Link from "next/link";
import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ProgressBar from "@/components/ProgressBar";
import { HARI_ORDER, type Progres } from "@/lib/types";

export default async function ValidasiIndex(props: { searchParams: Promise<{ semua?: string }> }) {
  const { semua } = await props.searchParams;
  const { supabase } = await requirePengawas();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return <div className="card p-6 text-sm">Belum ada ujian aktif.</div>;
  const { data } = await supabase.from("progres_kelas").select("*").eq("ujian_id", ujian.id);
  const all = (data ?? []) as Progres[];
  const rows = all
    .filter((r) => semua || r.perlu_validasi > 0)
    .sort((a, b) => b.perlu_cek - a.perlu_cek || b.perlu_validasi - a.perlu_validasi || HARI_ORDER.indexOf(a.hari ?? "") - HARI_ORDER.indexOf(b.hari ?? ""));
  const total = all.reduce((s, r) => s + r.perlu_validasi, 0);
  const { data: next } = await supabase
    .from("submissions")
    .select("id")
    .eq("ujian_id", ujian.id)
    .eq("status", "perlu_validasi")
    .order("flags", { ascending: false })
    .order("uploaded_at")
    .limit(1)
    .maybeSingle();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-bold">Validasi</h1>
          <p className="text-sm text-muted">
            Cocokkan hasil baca dengan foto. Kamu boleh memvalidasi kelasmu sendiri maupun kelas lain. {total} LJK menunggu.
          </p>
        </div>
        {next && (
          <Link href={`/validasi/s/${next.id}`} className="btn-primary">
            Mulai validasi berikutnya →
          </Link>
        )}
      </div>
      <div className="flex gap-2 text-sm">
        <Link href="/validasi" className={`pill px-3 py-1 ${!semua ? "bg-ink text-white" : "bg-white text-ink2 ring-1 ring-line"}`}>
          Perlu validasi
        </Link>
        <Link href="/validasi?semua=1" className={`pill px-3 py-1 ${semua ? "bg-ink text-white" : "bg-white text-ink2 ring-1 ring-line"}`}>
          Semua kelas
        </Link>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {rows.map((r) => (
          <Link key={r.kelas_id} href={`/validasi/${encodeURIComponent(r.kelas_id)}`} className="card block p-4 hover:border-brand">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-semibold">{r.kelas_id}</div>
                <div className="truncate text-xs text-muted">
                  {r.prodi} · {r.hari} {r.waktu}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                {r.perlu_cek > 0 && <span className="pill bg-red-100 text-bad">{r.perlu_cek} ditandai</span>}
                {r.perlu_validasi > 0 && <span className="pill bg-warn-bg text-warn">{r.perlu_validasi} menunggu</span>}
              </div>
            </div>
            <div className="mt-3">
              <ProgressBar total={r.total_peserta} done={r.terupload} valid={r.tervalidasi} />
            </div>
            <div className="mt-1.5 text-xs text-muted">
              {r.tervalidasi} valid · {r.terupload} terupload · {r.total_peserta} peserta
            </div>
          </Link>
        ))}
        {rows.length === 0 && (
          <div className="card p-6 text-sm text-muted sm:col-span-2">
            {all.some((r) => r.terupload > 0) ? "Semua LJK sudah divalidasi 🎉" : "Belum ada LJK yang diupload."}
          </div>
        )}
      </div>
    </div>
  );
}
