import jpeg from "jpeg-js";
import { NextResponse } from "next/server";
import { getUjianAktif } from "@/lib/auth";
import { gabungBacaUlang } from "@/lib/omr/bacaUlang";
import { scanRgba } from "@/lib/omr/scan";
import { supabaseServer } from "@/lib/supabase/server";
import type { StoredOmr } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Baca ulang foto LJK yang BELUM tervalidasi dengan pembaca OMR terbaru, di server.
 * Dipanggil berulang dari halaman Admin per kelompok kecil (`limit`) supaya tiap permintaan singkat:
 * body { after?: id terakhir yang sudah diproses, limit?: 1-10, autoValid?: boolean }.
 * Jawaban yang sudah terisi (hasil baca lama / koreksi validator) tidak diubah; hanya nomor kosong yang diisi.
 * Hanya admin. Perubahan tercatat di audit_log atas nama admin yang menjalankan.
 */
export async function POST(request: Request) {
  const supabase = await supabaseServer();
  const { data: auth } = await supabase.auth.getClaims();
  const email = ((auth?.claims?.email as string | undefined) ?? "").toLowerCase();
  if (!email) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const { data: me } = await supabase.from("pengawas").select("role,aktif").eq("email", email).maybeSingle();
  if (!me?.aktif || me.role !== "admin") return NextResponse.json({ error: "khusus admin" }, { status: 403 });
  const ujian = await getUjianAktif(supabase);
  if (!ujian) return NextResponse.json({ error: "tidak ada ujian aktif" }, { status: 404 });

  const body = (await request.json().catch(() => ({}))) as { after?: string; limit?: number; autoValid?: boolean };
  const limit = Math.max(1, Math.min(10, Math.round(body.limit ?? 4)));
  let q = supabase
    .from("submissions")
    .select("id,kelas_id,nim,photo_path,flags,jawaban,kuisioner,omr")
    .eq("ujian_id", ujian.id)
    .eq("status", "perlu_validasi")
    .order("id")
    .limit(limit);
  if (body.after) q = q.gt("id", body.after);
  const { data, error } = await q;
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  const rows = (data ?? []) as {
    id: string;
    kelas_id: string;
    nim: string;
    photo_path: string;
    flags: number;
    jawaban: (string | null)[];
    kuisioner: (string | null)[];
    omr: StoredOmr;
  }[];

  const results: { id: string; kelas: string; nim: string; terisi: number; flagsLama: number; flags: number; valid: boolean; error?: string }[] = [];
  for (const r of rows) {
    const base = { id: r.id, kelas: r.kelas_id, nim: r.nim, flagsLama: r.flags };
    try {
      const dl = await supabase.storage.from("ljk").download(r.photo_path);
      if (dl.error || !dl.data) throw new Error(dl.error?.message ?? "foto tidak ditemukan");
      const img = jpeg.decode(new Uint8Array(await dl.data.arrayBuffer()), { useTArray: true, maxMemoryUsageInMB: 512 });
      const scan = scanRgba(img.data, img.width, img.height);
      if (!scan.ok || !scan.decoded) throw new Error(scan.reason ?? "LJK tidak terbaca");
      const g = gabungBacaUlang(r, scan.decoded);
      const valid = !!body.autoValid && g.flags === 0;
      if (g.terisi > 0 || g.flags !== r.flags || valid) {
        const { error: upErr } = await supabase
          .from("submissions")
          .update({ jawaban: g.jawaban, kuisioner: g.kuisioner, flags: g.flags, ...(valid ? { status: "valid" } : {}) })
          .eq("id", r.id)
          .eq("status", "perlu_validasi"); // jangan menimpa yang baru saja divalidasi orang lain
        if (upErr) throw new Error(upErr.message);
      }
      results.push({ ...base, terisi: g.terisi, flags: g.flags, valid });
    } catch (e) {
      results.push({ ...base, terisi: 0, flags: r.flags, valid: false, error: (e as Error).message });
    }
  }
  const last = rows.length ? rows[rows.length - 1].id : null;
  return NextResponse.json({ results, next: rows.length === limit ? last : null });
}
