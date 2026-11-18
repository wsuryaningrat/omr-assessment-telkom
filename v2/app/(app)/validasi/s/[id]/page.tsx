import { notFound } from "next/navigation";
import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ValidationEditor from "@/components/validasi/ValidationEditor";
import type { Peserta, Submission } from "@/lib/types";
import { TABS, tabOfStatus } from "@/lib/validasi";

export default async function ValidasiDetail(props: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string }> }) {
  const { id } = await props.params;
  const { tab } = await props.searchParams;
  const { supabase, pengawas } = await requirePengawas();
  const { data: sub } = await supabase.from("submissions").select("*").eq("id", id).maybeSingle();
  if (!sub) notFound();
  const s = sub as Submission;
  const ujian = await getUjianAktif(supabase);
  // Urutan sebelumnya/berikutnya = urutan daftar LJK kelas di tab yang sedang dibuka.
  // Bila LJK ini sudah pindah tab (mis. baru divalidasi), pakai tab sesuai statusnya sekarang.
  const asked = TABS.find((t) => t.key === tab);
  const t = asked && asked.statuses.includes(s.status) ? asked : tabOfStatus(s.status);
  const [{ data: signed }, { data: peserta }, { data: audit }, { data: next }, { data: others }, { data: urutan }] = await Promise.all([
    supabase.storage.from("ljk").createSignedUrl(s.photo_path, 60 * 60),
    supabase.from("peserta").select("kelas_id,nim,nama").eq("kelas_id", s.kelas_id).order("nama"),
    supabase.from("audit_log").select("at,actor,action,detail").eq("submission_id", id).order("at", { ascending: false }).limit(30),
    supabase
      .from("submissions")
      .select("id")
      .eq("kelas_id", s.kelas_id)
      .eq("status", "perlu_validasi")
      .neq("id", id)
      .order("flags", { ascending: false })
      .order("uploaded_at")
      .limit(1)
      .maybeSingle(),
    supabase.from("submissions").select("nim").eq("ujian_id", s.ujian_id).in("status", ["perlu_validasi", "valid"]).neq("id", id),
    supabase
      .from("submissions")
      .select("id")
      .eq("kelas_id", s.kelas_id)
      .in("status", t.statuses)
      .order("flags", { ascending: false })
      .order("uploaded_at")
      .order("id"),
  ]);
  const ids = (urutan ?? []).map((u) => u.id as string);
  const pos = ids.indexOf(id);
  return (
    <ValidationEditor
      sub={s}
      photoUrl={signed?.signedUrl ?? null}
      peserta={(peserta ?? []) as Peserta[]}
      audit={(audit ?? []) as { at: string; actor: string; action: string; detail: Record<string, unknown> }[]}
      nextId={next?.id ?? null}
      nav={{
        tab: t.key,
        tabLabel: t.label,
        pos: pos + 1,
        total: ids.length,
        prevId: pos > 0 ? ids[pos - 1] : null,
        nextId: pos >= 0 && pos < ids.length - 1 ? ids[pos + 1] : null,
      }}
      takenNims={(others ?? []).map((o) => o.nim)}
      me={pengawas.email}
      kodeList={ujian?.kode_soal ?? []}
      isAdmin={pengawas.role === "admin"}
    />
  );
}
