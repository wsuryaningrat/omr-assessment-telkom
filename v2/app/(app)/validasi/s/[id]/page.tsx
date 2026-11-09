import { notFound } from "next/navigation";
import { requirePengawas } from "@/lib/auth";
import ValidationEditor from "@/components/validasi/ValidationEditor";
import type { Peserta, Submission } from "@/lib/types";

export default async function ValidasiDetail(props: { params: Promise<{ id: string }> }) {
  const { id } = await props.params;
  const { supabase, pengawas } = await requirePengawas();
  const { data: sub } = await supabase.from("submissions").select("*").eq("id", id).maybeSingle();
  if (!sub) notFound();
  const s = sub as Submission;
  const [{ data: signed }, { data: peserta }, { data: audit }, { data: next }, { data: others }] = await Promise.all([
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
  ]);
  return (
    <ValidationEditor
      sub={s}
      photoUrl={signed?.signedUrl ?? null}
      peserta={(peserta ?? []) as Peserta[]}
      audit={(audit ?? []) as { at: string; actor: string; action: string; detail: Record<string, unknown> }[]}
      nextId={next?.id ?? null}
      takenNims={(others ?? []).map((o) => o.nim)}
      me={pengawas.email}
    />
  );
}
