import { notFound } from "next/navigation";
import { getUjianAktif, requirePengawas } from "@/lib/auth";
import ScanFlow from "@/components/scan/ScanFlow";
import type { Kelas, Peserta } from "@/lib/types";

export default async function ScanKelas(props: { params: Promise<{ kelasId: string }> }) {
  const { kelasId } = await props.params;
  const id = decodeURIComponent(kelasId);
  const { supabase, pengawas } = await requirePengawas();
  const { data: kelas } = await supabase.from("kelas").select("id,ujian_id,prodi,ruangan,hari,waktu,dosen").eq("id", id).maybeSingle();
  if (!kelas) notFound();
  const ujian = await getUjianAktif(supabase);
  const [{ data: peserta }, { data: subs }] = await Promise.all([
    supabase.from("peserta").select("kelas_id,nim,nama,kelas_asal,status").eq("kelas_id", id).order("nama"),
    supabase
      .from("submissions")
      .select("id,nim,kelas_id,uploaded_by,uploaded_at,status")
      .eq("ujian_id", kelas.ujian_id)
      .in("status", ["perlu_validasi", "valid"]),
  ]);
  return (
    <ScanFlow
      kelas={kelas as Kelas}
      peserta={(peserta ?? []) as Peserta[]}
      existing={(subs ?? []) as { id: string; nim: string; kelas_id: string; uploaded_by: string; uploaded_at: string; status: string }[]}
      pengawasEmail={pengawas.email}
      kodeList={ujian?.kode_soal ?? []}
    />
  );
}
