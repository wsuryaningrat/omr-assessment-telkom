import Link from "next/link";
import { notFound } from "next/navigation";
import { requirePengawas } from "@/lib/auth";
import { STATUS_LABEL, type SubmissionStatus } from "@/lib/types";

const TABS: { key: string; label: string; statuses: SubmissionStatus[] }[] = [
  { key: "menunggu", label: "Perlu validasi", statuses: ["perlu_validasi"] },
  { key: "valid", label: "Valid", statuses: ["valid"] },
  { key: "ditolak", label: "Ditolak / diganti", statuses: ["ditolak", "diganti"] },
];

export default async function ValidasiKelas(props: { params: Promise<{ kelasId: string }>; searchParams: Promise<{ tab?: string }> }) {
  const { kelasId } = await props.params;
  const { tab = "menunggu" } = await props.searchParams;
  const id = decodeURIComponent(kelasId);
  const { supabase } = await requirePengawas();
  const { data: kelas } = await supabase.from("kelas").select("id,prodi,hari,waktu,ruangan").eq("id", id).maybeSingle();
  if (!kelas) notFound();
  const t = TABS.find((x) => x.key === tab) ?? TABS[0];
  const [{ data: subs }, { data: peserta }, { data: active }] = await Promise.all([
    supabase
      .from("submissions")
      .select("id,nim,nim_sumber,kode_soal,flags,status,uploaded_by,uploaded_at,validated_by")
      .eq("kelas_id", id)
      .in("status", t.statuses)
      .order("flags", { ascending: false })
      .order("uploaded_at"),
    supabase.from("peserta").select("nim,nama").eq("kelas_id", id).order("nama"),
    supabase.from("submissions").select("nim").eq("kelas_id", id).in("status", ["perlu_validasi", "valid"]),
  ]);
  const nama = new Map((peserta ?? []).map((p) => [p.nim, p.nama]));
  const uploaded = new Set((active ?? []).map((s) => s.nim));
  const belum = (peserta ?? []).filter((p) => !uploaded.has(p.nim));

  return (
    <div className="space-y-4">
      <div>
        <Link href="/validasi" className="text-xs font-semibold text-brand">
          ← Semua kelas
        </Link>
        <h1 className="font-serif text-2xl font-bold">{kelas.id}</h1>
        <p className="text-sm text-muted">
          {kelas.prodi} · {kelas.hari} {kelas.waktu} · {kelas.ruangan}
        </p>
      </div>
      <div className="flex flex-wrap gap-2 text-sm">
        {TABS.map((x) => (
          <Link
            key={x.key}
            href={`?tab=${x.key}`}
            className={`pill px-3 py-1 ${x.key === t.key ? "bg-ink text-white" : "bg-white text-ink2 ring-1 ring-line"}`}
          >
            {x.label}
          </Link>
        ))}
      </div>
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-soft text-left text-xs text-muted uppercase">
            <tr>
              <th className="px-3 py-2">Mahasiswa</th>
              <th className="hidden px-3 py-2 sm:table-cell">Kode</th>
              <th className="px-3 py-2">Tanda</th>
              <th className="hidden px-3 py-2 md:table-cell">Diupload</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {(subs ?? []).map((s) => (
              <tr key={s.id} className="hover:bg-soft">
                <td className="px-3 py-2">
                  <Link href={`/validasi/s/${s.id}`} className="block">
                    <div className="font-medium">{nama.get(s.nim) ?? <span className="text-warn">di luar daftar</span>}</div>
                    <div className="font-mono text-xs text-muted">{s.nim}</div>
                  </Link>
                </td>
                <td className="hidden px-3 py-2 font-mono sm:table-cell">{s.kode_soal ?? "—"}</td>
                <td className="px-3 py-2">
                  {s.flags > 0 ? <span className="pill bg-red-100 text-bad">{s.flags}</span> : <span className="text-xs text-muted">—</span>}
                </td>
                <td className="hidden px-3 py-2 text-xs text-muted md:table-cell">
                  {s.uploaded_by}
                  <br />
                  {new Date(s.uploaded_at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })}
                </td>
                <td className="px-3 py-2">
                  <Link href={`/validasi/s/${s.id}`} className="text-xs font-semibold text-brand">
                    {STATUS_LABEL[s.status as SubmissionStatus]}
                    {s.validated_by ? ` · ${s.validated_by.split("@")[0]}` : ""} →
                  </Link>
                </td>
              </tr>
            ))}
            {(subs ?? []).length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-sm text-muted">
                  Kosong.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {belum.length > 0 && (
        <details className="card p-4">
          <summary className="cursor-pointer text-sm font-semibold">Belum ada LJK ({belum.length} mahasiswa)</summary>
          <ul className="mt-3 grid gap-1 text-sm sm:grid-cols-2">
            {belum.map((p) => (
              <li key={p.nim} className="truncate">
                <span className="font-mono text-xs text-muted">{p.nim}</span> {p.nama}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
