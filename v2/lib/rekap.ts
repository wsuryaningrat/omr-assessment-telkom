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

// PostgREST memotong hasil di 1000 baris (max_rows Supabase), jadi ambil per halaman.
const PAGE = 1000;
async function fetchAll<T>(page: (from: number, to: number) => PromiseLike<{ data: unknown; error: { message: string } | null }>): Promise<T[]> {
  const out: T[] = [];
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await page(from, from + PAGE - 1);
    if (error) throw new Error(error.message);
    const batch = (data ?? []) as T[];
    out.push(...batch);
    if (batch.length < PAGE) return out;
  }
}

export async function loadRekap(supabase: Awaited<ReturnType<typeof supabaseServer>>, ujianId: string) {
  const rows = await fetchAll<RekapRow>((a, b) => supabase.rpc("rekap_nilai", { p_ujian: ujianId }).order("submission_id").range(a, b));
  const peserta = await fetchAll<unknown>((a, b) =>
    supabase.from("peserta").select("kelas_id,nim,nama,kelas!inner(ujian_id,prodi)").eq("kelas.ujian_id", ujianId).order("nim").range(a, b),
  );
  const have = new Set(rows.map((r) => r.nim));
  const belum = ((peserta ?? []) as unknown as { kelas_id: string; nim: string; nama: string; kelas: { prodi: string | null } }[])
    .filter((p) => !have.has(p.nim))
    .map((p) => ({ kelas_id: p.kelas_id, prodi: p.kelas?.prodi ?? null, nim: p.nim, nama: p.nama }));
  return { rows, belum };
}
