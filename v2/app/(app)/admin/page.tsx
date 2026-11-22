import { getUjianAktif, requireAdmin } from "@/lib/auth";
import { TEMPLATE } from "@/lib/omr/scan";
import { hapusKode, simpanKunci, tambahPengawas } from "./actions";
import PengawasTable from "@/components/admin/PengawasTable";
import BacaUlangSemua from "@/components/admin/BacaUlangSemua";
import { asset } from "@/lib/basePath";
import BatchSwitch from "@/components/BatchSwitch";

export default async function AdminPage() {
  const { supabase, pengawas: me } = await requireAdmin();
  const ujian = await getUjianAktif(supabase);
  const [{ data: pengawas }, { data: kunci }, { data: counts }, { count: belumValid }] = await Promise.all([
    supabase.from("pengawas").select("email,nama,role,aktif,keterangan").order("role").order("nama"),
    ujian ? supabase.from("kunci").select("kode_soal,jawaban,updated_by,updated_at").eq("ujian_id", ujian.id) : Promise.resolve({ data: [] }),
    supabase.from("submissions").select("uploaded_by").in("status", ["perlu_validasi", "valid"]),
    ujian
      ? supabase.from("submissions").select("id", { count: "exact", head: true }).eq("ujian_id", ujian.id).eq("status", "perlu_validasi")
      : Promise.resolve({ count: 0 }),
  ]);
  const uploads = new Map<string, number>();
  (counts ?? []).forEach((c) => uploads.set(c.uploaded_by, (uploads.get(c.uploaded_by) ?? 0) + 1));
  const kunciMap = new Map((kunci ?? []).map((k) => [k.kode_soal, k]));
  const kodeList = [...new Set([...(ujian?.kode_soal ?? []), ...kunciMap.keys()])].sort();

  return (
    <div className="space-y-6">
      <BatchSwitch active={2} />
      <div>
        <h1 className="font-serif text-2xl font-bold">Admin</h1>
        <p className="text-sm text-muted">
          Ujian aktif: <b>{ujian?.nama ?? "—"}</b> ({ujian?.id ?? "-"}) · template {TEMPLATE.version} ·{" "}
          <a href={asset("/ljk/LJK_v2.pdf")} target="_blank" className="font-semibold text-brand">
            unduh LJK v2
          </a>
        </p>
      </div>

      <BacaUlangSemua total={belumValid ?? 0} />

      <section className="card p-4">
        <h2 className="mb-1 font-semibold">Whitelist pengawas</h2>
        <p className="mb-3 text-xs text-muted">Hanya Gmail di daftar ini yang bisa login. Satu baris per orang: <code>email, Nama Lengkap</code>.</p>
        <form action={tambahPengawas} className="grid gap-2 md:grid-cols-[1fr_auto]">
          <textarea name="daftar" className="input min-h-20 font-mono text-xs" placeholder={"budi@gmail.com, Budi Santoso\nani@student.telkomuniversity.ac.id, Ani"} />
          <div className="flex flex-col gap-2">
            <select name="role" className="input">
              <option value="pengawas">Pengawas</option>
              <option value="admin">Admin</option>
            </select>
            <button className="btn-primary">Tambah</button>
          </div>
        </form>
        <PengawasTable
          me={me.email}
          rows={(pengawas ?? []).map((p) => ({ email: p.email, nama: p.nama, role: p.role, aktif: p.aktif, upload: uploads.get(p.email) ?? 0 }))}
        />
      </section>

      <section className="card p-4">
        <h2 className="mb-1 font-semibold">Kunci jawaban</h2>
        <p className="mb-3 text-xs text-muted">
          Kode soal 3 digit (diarsir mahasiswa di LJK). Tempel satu nomor per baris: <code>1 B</code>, <code>2 A,C</code> (lebih dari satu benar),{" "}
          <code>3 *</code> (bonus). Hanya nomor yang ada di kunci yang dinilai. Daftar kode terlihat pengawas, isi kunci tidak.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          {kodeList.map((kode) => {
            const k = kunciMap.get(kode);
            const text = k
              ? Object.entries(k.jawaban as Record<string, string[]>)
                  .sort((a, b) => Number(a[0]) - Number(b[0]))
                  .map(([no, v]) => `${no} ${v.length === 4 ? "*" : v.join(",")}`)
                  .join("\n")
              : "";
            return (
              <form key={kode} action={simpanKunci} className="space-y-2">
                <input type="hidden" name="kode" value={kode} />
                <div className="flex items-baseline justify-between">
                  <span className="font-mono font-bold">Kode {kode}</span>
                  <span className="text-xs text-muted">
                    {k ? `${Object.keys(k.jawaban as object).length} soal · ${k.updated_by ?? ""}` : "belum diisi"}
                  </span>
                </div>
                <textarea name="kunci" defaultValue={text} className="input h-40 font-mono text-xs" />
                <div className="flex gap-2">
                  <button className="btn-ghost flex-1">Simpan kunci {kode}</button>
                  <button formAction={hapusKode} className="btn-ghost text-bad" title="Hapus kode & kuncinya">
                    Hapus
                  </button>
                </div>
              </form>
            );
          })}
          <form action={simpanKunci} className="space-y-2 rounded-lg border border-dashed border-line p-3">
            <div className="font-semibold">Tambah kode soal baru</div>
            <input name="kode" className="input max-w-32 font-mono" inputMode="numeric" maxLength={3} placeholder="mis. 518" required />
            <textarea name="kunci" className="input h-32 font-mono text-xs" placeholder={"1 A\n2 C\n3 B"} required />
            <button className="btn-primary w-full">Tambah</button>
          </form>
        </div>
      </section>
    </div>
  );
}
