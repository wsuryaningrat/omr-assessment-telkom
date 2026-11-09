import type { supabaseServer } from "@/lib/supabase/server";

export interface RekapRow {
  submission_id: string;
  kelas_id: string;
  prodi: string | null;
  nim: string;
  nama: string | null;
  kode_soal: string | null;
  benar: number | null;
  salah: number | null;
  kosong: number | null;
  total: number | null;
  nilai: number | null;
  status: string;
  flags: number;
  uploaded_by: string;
  uploaded_at: string;
  validated_by: string | null;
  validated_at: string | null;
  jawaban: (string | null)[];
  kuisioner: (string | null)[];
}

export async function loadRekap(supabase: Awaited<ReturnType<typeof supabaseServer>>, ujianId: string) {
  const { data, error } = await supabase.rpc("rekap_nilai", { p_ujian: ujianId });
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as RekapRow[];
  const { data: peserta } = await supabase.from("peserta").select("kelas_id,nim,nama,kelas!inner(ujian_id,prodi)").eq("kelas.ujian_id", ujianId);
  const have = new Set(rows.map((r) => r.nim));
  const belum = ((peserta ?? []) as unknown as { kelas_id: string; nim: string; nama: string; kelas: { prodi: string | null } }[])
    .filter((p) => !have.has(p.nim))
    .map((p) => ({ kelas_id: p.kelas_id, prodi: p.kelas?.prodi ?? null, nim: p.nim, nama: p.nama }));
  return { rows, belum };
}
