"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin, requirePengawas } from "@/lib/auth";

type Result = { ok: true; msg: string } | { ok: false; msg: string };

/** Tandai valid semua LJK kelas ini yang masih menunggu validasi (yang kode soalnya sudah terisi). */
export async function validasiSemua(kelasId: string): Promise<Result> {
  const { supabase } = await requirePengawas();
  const { data, error } = await supabase
    .from("submissions")
    .update({ status: "valid", flags: 0 })
    .eq("kelas_id", kelasId)
    .eq("status", "perlu_validasi")
    .not("kode_soal", "is", null)
    .select("id");
  if (error) return { ok: false, msg: error.message };
  const { count } = await supabase
    .from("submissions")
    .select("id", { count: "exact", head: true })
    .eq("kelas_id", kelasId)
    .eq("status", "perlu_validasi");
  revalidatePath("/scan");
  revalidatePath("/");
  return {
    ok: true,
    msg: `${data?.length ?? 0} LJK ditandai valid.` + (count ? ` ${count} LJK belum punya kode soal — validasi manual.` : ""),
  };
}

/** Kembalikan semua LJK valid di kelas ini ke "perlu validasi". */
export async function batalkanValidasi(kelasId: string): Promise<Result> {
  const { supabase } = await requirePengawas();
  const { data, error } = await supabase
    .from("submissions")
    .update({ status: "perlu_validasi" })
    .eq("kelas_id", kelasId)
    .eq("status", "valid")
    .select("id");
  if (error) return { ok: false, msg: error.message };
  revalidatePath("/scan");
  revalidatePath("/");
  return { ok: true, msg: `${data?.length ?? 0} LJK dikembalikan ke "perlu validasi".` };
}

/**
 * Kirim LJK tervalidasi kelas ini ke Google Sheet (spreadsheet yang sama dengan v1, tab terpisah).
 * Penulisan dilakukan oleh API v1 di VPS (yang memegang kredensial service account) lewat jaringan
 * internal docker; baris dengan Kunci (Kelas|NIM) yang sama diperbarui, bukan diduplikasi.
 */
export async function kirimSheet(kelasId: string): Promise<Result> {
  const { supabase, pengawas } = await requireAdmin();
  const api = process.env.V1_API_URL;
  const token = process.env.V2_SYNC_TOKEN;
  if (!api || !token) return { ok: false, msg: "Kirim ke Sheet belum aktif di server ini (butuh V1_API_URL & V2_SYNC_TOKEN, tersedia saat berjalan di VPS)." };

  const [{ data: kelas }, { data: subs, error }] = await Promise.all([
    supabase.from("kelas").select("id,ujian_id,prodi,hari,waktu,ruangan").eq("id", kelasId).single(),
    supabase.from("submissions").select("id").eq("kelas_id", kelasId).eq("status", "valid"),
  ]);
  if (error || !kelas) return { ok: false, msg: error?.message ?? "Kelas tidak ditemukan" };
  if (!subs?.length) return { ok: false, msg: "Belum ada LJK tervalidasi di kelas ini." };

  const { data: rekap, error: e2 } = await supabase.rpc("rekap_nilai", { p_ujian: kelas.ujian_id }).eq("kelas_id", kelasId).eq("status", "valid");
  if (e2) return { ok: false, msg: e2.message };
  const { data: peserta } = await supabase.from("peserta").select("nim,nama").eq("kelas_id", kelasId);
  const nama = new Map((peserta ?? []).map((p) => [p.nim, p.nama]));
  const now = new Date().toISOString();

  type Row = {
    nim: string; kode_soal: string | null; benar: number | null; salah: number | null; kosong: number | null; nilai: number | null;
    uploaded_by: string; uploaded_at: string; validated_by: string | null; jawaban: (string | null)[]; kuisioner: (string | null)[];
  };
  const records = ((rekap ?? []) as Row[]).map((r) => {
    const rec: Record<string, string | number | null> = {
      Kunci: `${kelasId}|${r.nim}`,
      Kelas: kelasId,
      NIM: r.nim,
      Nama: nama.get(r.nim) ?? "",
      Prodi: kelas.prodi,
      Hari: kelas.hari,
      Waktu: kelas.waktu,
      "Kode Soal": r.kode_soal,
      Benar: r.benar,
      Salah: r.salah,
      Kosong: r.kosong,
      Nilai: r.nilai,
    };
    (r.kuisioner ?? []).forEach((v, i) => (rec[`K${i + 1}`] = v ?? ""));
    (r.jawaban ?? []).forEach((v, i) => (rec[`S${i + 1}`] = v ?? ""));
    rec["Diupload oleh"] = r.uploaded_by;
    rec["Divalidasi oleh"] = r.validated_by;
    rec["Waktu upload"] = r.uploaded_at;
    rec["Dikirim oleh"] = pengawas.email;
    rec["Waktu kirim"] = now;
    return rec;
  });

  try {
    const res = await fetch(`${api.replace(/\/$/, "")}/api/v2/kirim`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-v2-token": token },
      body: JSON.stringify({ kelas_id: kelasId, records }),
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, msg: `Gagal kirim: ${body.detail ?? res.statusText}` };
  } catch (e) {
    return { ok: false, msg: `Gagal menghubungi API Sheet: ${(e as Error).message}` };
  }
  await supabase.from("kirim_log").insert({ kelas_id: kelasId, jumlah: records.length });
  revalidatePath("/scan");
  return { ok: true, msg: `${records.length} LJK terkirim ke Google Sheet.` };
}

async function hapusFoto(supabase: Awaited<ReturnType<typeof requireAdmin>>["supabase"], paths: string[]) {
  for (let i = 0; i < paths.length; i += 100) await supabase.storage.from("ljk").remove(paths.slice(i, i + 100));
}

/** Admin: hapus SEMUA LJK (dan fotonya) di satu kelas — untuk mengulang sesi dari awal. */
export async function hapusSesi(kelasId: string): Promise<Result> {
  const { supabase } = await requireAdmin();
  const { data: subs, error } = await supabase.from("submissions").select("id,photo_path").eq("kelas_id", kelasId);
  if (error) return { ok: false, msg: error.message };
  if (!subs?.length) return { ok: true, msg: "Kelas ini belum punya LJK." };
  const { error: e2 } = await supabase.from("submissions").delete().eq("kelas_id", kelasId);
  if (e2) return { ok: false, msg: e2.message };
  await hapusFoto(supabase, subs.map((s) => s.photo_path).filter(Boolean));
  revalidatePath("/scan");
  revalidatePath("/");
  revalidatePath("/validasi");
  return { ok: true, msg: `${subs.length} LJK di kelas ${kelasId} dihapus.` };
}

/** Admin: hapus satu LJK beserta fotonya (mis. salah foto). */
export async function hapusSubmission(id: string): Promise<Result> {
  const { supabase } = await requireAdmin();
  const { data: sub, error } = await supabase.from("submissions").select("id,kelas_id,photo_path").eq("id", id).single();
  if (error || !sub) return { ok: false, msg: error?.message ?? "LJK tidak ditemukan" };
  const { error: e2 } = await supabase.from("submissions").delete().eq("id", id);
  if (e2) return { ok: false, msg: e2.message };
  if (sub.photo_path) await hapusFoto(supabase, [sub.photo_path]);
  revalidatePath("/scan");
  revalidatePath("/validasi");
  revalidatePath(`/validasi/${encodeURIComponent(sub.kelas_id)}`);
  return { ok: true, msg: "LJK dihapus." };
}
