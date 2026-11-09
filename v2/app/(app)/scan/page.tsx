import Link from "next/link";
import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ProgressBar from "@/components/ProgressBar";
import { HARI_ORDER, type Progres } from "@/lib/types";

export default async function PilihKelas(props: { searchParams: Promise<{ q?: string }> }) {
  const { q } = await props.searchParams;
  const { supabase } = await requirePengawas();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return <div className="card p-6 text-sm">Belum ada ujian aktif.</div>;
  const { data } = await supabase.from("progres_kelas").select("*").eq("ujian_id", ujian.id);
  const s = (q ?? "").toLowerCase();
  const rows = ((data ?? []) as Progres[])
    .filter((r) => !s || `${r.kelas_id} ${r.prodi} ${r.ruangan} ${r.hari}`.toLowerCase().includes(s))
    .sort(
      (a, b) =>
        HARI_ORDER.indexOf(a.hari ?? "") - HARI_ORDER.indexOf(b.hari ?? "") ||
        (a.waktu ?? "").localeCompare(b.waktu ?? "") ||
        a.kelas_id.localeCompare(b.kelas_id),
    );
  const byHari = new Map<string, Progres[]>();
  rows.forEach((r) => byHari.set(r.hari ?? "-", [...(byHari.get(r.hari ?? "-") ?? []), r]));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Pilih kelas yang diawasi</h1>
        <p className="text-sm text-muted">Setelah memilih kelas, foto LJK mahasiswa satu per satu dengan kamera.</p>
      </div>
      <form className="flex gap-2">
        <input name="q" defaultValue={q} className="input" placeholder="Cari kode kelas, prodi, ruangan…" />
        <button className="btn-ghost">Cari</button>
      </form>
      {[...byHari.entries()].map(([hari, list]) => (
        <section key={hari}>
          <h2 className="mb-2 text-xs font-bold tracking-wider text-muted uppercase">{hari}</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            {list.map((r) => (
              <Link key={r.kelas_id} href={`/scan/${encodeURIComponent(r.kelas_id)}`} className="card block p-4 hover:border-brand">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-semibold">{r.kelas_id}</div>
                    <div className="truncate text-xs text-muted">
                      {r.prodi} · {r.waktu} · {r.ruangan}
                    </div>
                  </div>
                  <div className="shrink-0 text-right text-sm">
                    <b>{r.terupload}</b>
                    <span className="text-muted">/{r.total_peserta}</span>
                  </div>
                </div>
                <div className="mt-3">
                  <ProgressBar total={r.total_peserta} done={r.terupload} valid={r.tervalidasi} />
                </div>
              </Link>
            ))}
          </div>
        </section>
      ))}
      {rows.length === 0 && <div className="card p-6 text-sm text-muted">Tidak ada kelas yang cocok.</div>}
    </div>
  );
}
