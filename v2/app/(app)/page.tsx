import Link from "next/link";
import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ProgressBar from "@/components/ProgressBar";
import { HARI_ORDER, type Progres } from "@/lib/types";

export default async function Beranda() {
  const { supabase, pengawas } = await requirePengawas();
  const ujian = await getUjianAktif(supabase);
  if (!ujian) {
    return <div className="card p-6 text-sm">Belum ada ujian aktif. Admin perlu menjalankan seed data terlebih dahulu.</div>;
  }
  const { data } = await supabase.from("progres_kelas").select("*").eq("ujian_id", ujian.id);
  const rows = ((data ?? []) as Progres[]).sort(
    (a, b) => HARI_ORDER.indexOf(a.hari ?? "") - HARI_ORDER.indexOf(b.hari ?? "") || (a.waktu ?? "").localeCompare(b.waktu ?? "") || a.kelas_id.localeCompare(b.kelas_id),
  );
  const tot = rows.reduce(
    (s, r) => ({ peserta: s.peserta + r.total_peserta, up: s.up + r.terupload, valid: s.valid + r.tervalidasi, cek: s.cek + r.perlu_validasi }),
    { peserta: 0, up: 0, valid: 0, cek: 0 },
  );
  const { count: mine } = await supabase
    .from("submissions")
    .select("id", { count: "exact", head: true })
    .eq("uploaded_by", pengawas.email)
    .in("status", ["perlu_validasi", "valid"]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold">Halo, {pengawas.nama.split(" ")[0]} 👋</h1>
        <p className="text-sm text-muted">{ujian.nama}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Peserta" value={tot.peserta} />
        <Stat label="Terupload" value={tot.up} sub={`${tot.peserta ? Math.round((tot.up / tot.peserta) * 100) : 0}%`} />
        <Stat label="Perlu validasi" value={tot.cek} tone="warn" />
        <Stat label="Upload saya" value={mine ?? 0} />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Link href="/scan" className="card flex items-center gap-4 p-5 hover:border-brand">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-brand text-2xl text-white">◉</span>
          <div>
            <div className="font-bold">Scan LJK</div>
            <div className="text-sm text-muted">Pilih kelas, foto LJK satu per satu</div>
          </div>
        </Link>
        <Link href="/validasi" className="card flex items-center gap-4 p-5 hover:border-brand">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-ok text-2xl text-white">✓</span>
          <div>
            <div className="font-bold">Validasi</div>
            <div className="text-sm text-muted">Cek hasil baca kelas mana pun</div>
          </div>
        </Link>
      </div>

      <section className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-semibold">Progres per kelas</h2>
          <a href="/ljk/LJK_v2.pdf" className="text-xs font-semibold text-brand hover:underline" target="_blank">
            Unduh template LJK v2 (PDF)
          </a>
        </div>
        <ul className="divide-y divide-line">
          {rows.map((r) => (
            <li key={r.kelas_id} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1.5 px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">{r.kelas_id}</div>
                <div className="truncate text-xs text-muted">
                  {r.prodi} · {r.hari} {r.waktu} · {r.ruangan}
                </div>
              </div>
              <div className="text-right text-xs">
                <b>{r.terupload}</b>/{r.total_peserta}
                {r.perlu_validasi > 0 && <span className="ml-2 pill bg-amber-100 text-warn">{r.perlu_validasi} cek</span>}
              </div>
              <div className="col-span-2">
                <ProgressBar total={r.total_peserta} done={r.terupload} valid={r.tervalidasi} />
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: number; sub?: string; tone?: "warn" }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold text-muted uppercase">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tone === "warn" && value > 0 ? "text-warn" : ""}`}>
        {value.toLocaleString("id-ID")}
        {sub && <span className="ml-1.5 text-sm font-medium text-muted">{sub}</span>}
      </div>
    </div>
  );
}
