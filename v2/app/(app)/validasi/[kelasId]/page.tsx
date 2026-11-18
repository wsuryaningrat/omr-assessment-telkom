import Link from "next/link";
import { notFound } from "next/navigation";
import { requirePengawas } from "@/lib/auth";
import LjkTable from "@/components/validasi/LjkTable";
import type { SubmissionStatus } from "@/lib/types";
import { TABS } from "@/lib/validasi";


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
        <Link href="/scan?status=perlu" className="text-xs font-semibold text-brand">
          ← Semua kelas
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-2">
          <h1 className="font-serif text-2xl font-bold">{kelas.id}</h1>
          <Link href={`/scan/${encodeURIComponent(kelas.id)}`} className="btn-ghost px-3 py-1.5 text-xs">
            ◉ Scan kelas ini
          </Link>
        </div>
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
      <LjkTable
        tab={t.key}
        rows={(subs ?? []).map((x) => ({ ...x, status: x.status as SubmissionStatus, nama: nama.get(x.nim) ?? null }))}
      />
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
