import { getUjianAktif, requireAdmin } from "@/lib/auth";
import { TEMPLATE } from "@/lib/omr/scan";
import { hapusKode, setPengawas, simpanKunci, tambahPengawas } from "./actions";

export default async function AdminPage() {
  const { supabase, pengawas: me } = await requireAdmin();
  const ujian = await getUjianAktif(supabase);
  const [{ data: pengawas }, { data: kunci }, { data: counts }] = await Promise.all([
    supabase.from("pengawas").select("email,nama,role,aktif,keterangan").order("role").order("nama"),
    ujian ? supabase.from("kunci").select("kode_soal,jawaban,updated_by,updated_at").eq("ujian_id", ujian.id) : Promise.resolve({ data: [] }),
    supabase.from("submissions").select("uploaded_by").in("status", ["perlu_validasi", "valid"]),
  ]);
  const uploads = new Map<string, number>();
  (counts ?? []).forEach((c) => uploads.set(c.uploaded_by, (uploads.get(c.uploaded_by) ?? 0) + 1));
  const kunciMap = new Map((kunci ?? []).map((k) => [k.kode_soal, k]));
  const kodeList = [...new Set([...(ujian?.kode_soal ?? []), ...kunciMap.keys()])].sort();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Admin</h1>
        <p className="text-sm text-muted">
          Ujian aktif: <b>{ujian?.nama ?? "—"}</b> ({ujian?.id ?? "-"}) · template {TEMPLATE.version} ·{" "}
          <a href="/ljk/LJK_v2.pdf" target="_blank" className="font-semibold text-brand">
            unduh LJK v2
          </a>
        </p>
      </div>

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
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted uppercase">
              <tr>
                <th className="py-2">Nama</th>
                <th className="py-2">Email</th>
                <th className="py-2">Peran</th>
                <th className="py-2 text-right">Upload</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {(pengawas ?? []).map((p) => (
                <tr key={p.email} className={p.aktif ? "" : "opacity-50"}>
                  <td className="py-2 pr-2">{p.nama}</td>
                  <td className="py-2 pr-2 font-mono text-xs">{p.email}</td>
                  <td className="py-2 pr-2">
                    <span className={`pill ${p.role === "admin" ? "bg-brand-soft text-brand" : "bg-slate-100 text-slate-600"}`}>{p.role}</span>
                    {!p.aktif && <span className="ml-1 pill bg-slate-200 text-slate-600">nonaktif</span>}
                  </td>
                  <td className="py-2 pr-2 text-right tabular-nums">{uploads.get(p.email) ?? 0}</td>
                  <td className="py-2 text-right whitespace-nowrap">
                    {p.email !== me.email && (
                      <form action={setPengawas} className="inline-flex gap-1">
                        <input type="hidden" name="email" value={p.email} />
                        <input type="hidden" name="aktif" value={String(p.aktif)} />
                        <button name="action" value="toggle" className="btn-ghost px-2 py-1 text-xs">
                          {p.aktif ? "Nonaktifkan" : "Aktifkan"}
                        </button>
                        <button name="action" value={p.role === "admin" ? "demote" : "promote"} className="btn-ghost px-2 py-1 text-xs">
                          {p.role === "admin" ? "Jadikan pengawas" : "Jadikan admin"}
                        </button>
                      </form>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
